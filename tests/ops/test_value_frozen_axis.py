"""「값이 안 변한다」를 계기 고장 후보로 센다 — 2026-08-20 F-F · G-E.

2026-08-20 장중에 meta 통과확률이 3사이클 연속 **비트 단위로 동일**했다. 08-19는 9사이클이
전부 달랐다. 그런데 확률만 로그에 남고 그 확률을 만든 입력은 계산 직후 버려져서,
「같은 입력이라 같은 확률」과 「계기가 얼어붙었다」를 영원히 가를 수 없었다.

후자는 **침묵보다 나쁘다.** 침묵은 보이지만 얼어붙은 값은 정상으로 보인다.

이 저장소는 이미 두 얼굴을 축으로 세웠다 — 「0건」의 두 뜻(2026-08-19 G-4 negative control)과
「고쳤는데 새 경로가 한 번도 안 쓰였다」(2026-08-20 G-A 폴백 사용률). 값 정지가 세 번째다.
"""

from __future__ import annotations

from messiah.ops.integrity_report import (
    VALUE_FROZEN_RUN_RATIO,
    constant_run_length,
)
from messiah.strategy.futures.service import _features_digest

# ---------------------------------------------------------------- 연속 동일값


def test_empty_series_has_no_run() -> None:
    assert constant_run_length([]) == 0


def test_all_distinct_is_run_of_one() -> None:
    """2026-08-19가 이 모양이었다 — 9사이클이 전부 달랐다."""
    assert constant_run_length([0.71, 0.68, 0.73, 0.66]) == 1


def test_longest_run_is_reported_not_the_last() -> None:
    """중간에 얼어붙었다 풀린 날을 놓치면 안 된다."""
    assert constant_run_length([0.7, 0.7, 0.7, 0.62, 0.7, 0.7]) == 3


def test_float_comparison_is_bitwise() -> None:
    """0.7000000000000001과 0.7은 **다른 값**이다.

    이 축이 묻는 것이 정확히 "비트 단위로 같은가"이므로 반올림해서 비교하면 안 된다 —
    미세하게 움직이는 정상 계기를 「얼어붙었다」로 읽게 된다.
    """
    assert constant_run_length([0.7, 0.7000000000000001, 0.7]) == 1


def test_frozen_threshold_needs_at_least_two() -> None:
    """표본이 1~2건뿐인 날에 비율만 보면 항상 참이 된다 — 하한 2를 함께 건다."""
    probs = [0.7, 0.7]
    assert constant_run_length(probs) >= max(2, VALUE_FROZEN_RUN_RATIO * len(probs))


# ---------------------------------------------------------------- 입력 지문


def test_digest_is_order_independent() -> None:
    """dict 순서가 바뀌었다고 「입력이 바뀌었다」가 되면 축이 무의미해진다."""
    a = _features_digest({"vol": 0.21, "spread": 1.5, "hour": 10.0})
    b = _features_digest({"hour": 10.0, "vol": 0.21, "spread": 1.5})
    assert a == b


def test_digest_changes_on_a_tiny_input_change() -> None:
    """반올림하면 미세하게 다른 입력이 같은 지문을 받는다 — 그러면 진탐을 놓친다."""
    a = _features_digest({"vol": 0.21})
    b = _features_digest({"vol": 0.2100000000000001})
    assert a != b


def test_digest_is_short_and_stable() -> None:
    digest = _features_digest({"vol": 0.21, "spread": 1.5})
    assert len(digest) == 8
    assert digest == _features_digest({"vol": 0.21, "spread": 1.5})


def test_digest_of_empty_features_is_defined() -> None:
    """입력이 비는 경로가 있어도 예외를 던지면 안 된다 — 관측이 판단을 죽이면 본말전도다."""
    assert len(_features_digest({})) == 8


# ------------------------------------------- 리포트가 두 축을 함께 낸다


def _meta_records(probs: list[float], digests: list[str] | None) -> list[dict]:
    records = []
    for index, prob in enumerate(probs):
        record = {
            "level": "INFO",
            "tag": "MetaGateEvaluated",
            "probability": prob,
            "threshold": 0.7,
            "passed": prob >= 0.7,
        }
        if digests is not None:
            record["meta_features_digest"] = digests[index]
        records.append(record)
    return records


def test_report_says_both_the_value_run_and_the_input_run(tmp_path) -> None:
    """확률이 얼어붙었는데 **입력도 같았다면** 계기 고장이 아니다.

    두 축이 함께 있어야 이 질문에 답이 된다 — 그래서 F-F와 G-E는 같은 커밋이다.
    """
    from messiah.ops.integrity_report import analyze_logs

    log = tmp_path / "g2.log"
    records = _meta_records([0.7, 0.7, 0.7, 0.62], ["aaaa1111"] * 3 + ["bbbb2222"])
    log.write_text(
        "\n".join(__import__("json").dumps(r, ensure_ascii=False) for r in records),
        encoding="utf-8",
    )
    meta = analyze_logs([log])["meta_gate"]
    assert meta is not None
    assert meta["frozen_run"] == 3
    assert meta["input_frozen_run"] == 3, "입력도 3연속 같았다 — 계기 고장이 아니다"


def test_input_run_is_none_before_the_digest_existed(tmp_path) -> None:
    """지문이 없던 날(계측 이전)을 False로 적으면 「입력이 달랐다」가 되어 거짓이다 (L18)."""
    from messiah.ops.integrity_report import analyze_logs

    log = tmp_path / "g2.log"
    records = _meta_records([0.7, 0.7, 0.7], None)
    log.write_text(
        "\n".join(__import__("json").dumps(r, ensure_ascii=False) for r in records),
        encoding="utf-8",
    )
    meta = analyze_logs([log])["meta_gate"]
    assert meta is not None
    assert meta["frozen_run"] == 3
    assert meta["input_frozen_run"] is None
