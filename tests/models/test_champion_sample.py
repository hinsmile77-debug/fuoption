"""F-27 — 승격 표본을 롤 경계에서 끊지 않는다 (2026-08-24 이상점 1-15).

선물 월물은 한 달에 한 번 바뀌는데 성적표는 종목별로 셌다. Ver 1.1 §8 G2 통과기준의
40거래일은 롤 주기(20~22거래일)보다 길므로 **그 시험은 지금 구조로 영원히 끝나지 않는다.**
2026-08-24 실측: 파일 18행 · 집계 표본 6개 · 다음 롤(2026-09-11)이면 다시 0장.
"""

from __future__ import annotations

import io
import json

from messiah.models.self_evaluation import champion_sample


def _rows(*specs):
    out = []
    for spec in specs:
        row = {"date": spec[0], "symbol": spec[1], "return": spec[2]}
        if len(spec) > 3:
            row.update(spec[3])
        out.append(row)
    return out


def test_roll_day_is_the_only_thing_dropped_across_a_roll():
    sample = champion_sample(
        _rows(
            ("2026-08-12", "A05608", 0.01),
            ("2026-08-13", "A05608", 0.02),
            ("2026-08-14", "A05609", 0.03),  # 롤 당일 — 두 계약이 섞인 하루
            ("2026-08-18", "A05609", 0.04),
        )
    )
    assert sample.returns == [0.01, 0.02, 0.04]
    assert sample.window["rows_total"] == 4
    assert sample.window["rows_counted"] == 3
    assert sample.window["excluded"]["roll_day"] == 1
    assert sample.window["from"] == "2026-08-12"


def test_first_row_is_not_a_roll():
    """비교할 앞이 없으면 롤이 아니다 — 첫 행을 버리면 매 기산일이 하루씩 밀린다."""
    sample = champion_sample(_rows(("2026-07-29", "A05608", 0.01)))
    assert sample.returns == [0.01]
    assert sample.window["excluded"]["roll_day"] == 0


def test_missing_countable_key_is_not_false():
    """「거래가 없었다」와 「그 시절엔 안 쟀다」는 다른 사실이다 (L18).

    키 부재를 `False`로 채우면 기존 18행이 소급해서 「셀 수 없는 날」이 된다.
    """
    sample = champion_sample(_rows(("2026-08-20", "A05609", 0.0)))
    assert sample.returns == [0.0]
    assert sample.window["excluded"]["not_countable"] == 0
    assert sample.window["legacy_rows_without_countable"] == 1


def test_explicit_false_is_excluded_and_named():
    sample = champion_sample(
        _rows(
            ("2026-08-20", "A05609", 0.01, {"countable": True}),
            ("2026-08-21", "A05609", 0.02, {"countable": False}),
        )
    )
    assert sample.returns == [0.01]
    assert sample.window["excluded"]["not_countable"] == 1
    assert sample.window["legacy_rows_without_countable"] == 0


def test_missing_return_is_not_read_as_break_even():
    """값이 없는 행을 0.0으로 읽으면 「본전인 날」이 하나 생긴다."""
    sample = champion_sample([{"date": "2026-08-20", "symbol": "A05609"}])
    assert sample.returns == []
    assert sample.window["excluded"]["not_countable"] == 1


def test_no_symbol_filter_at_all():
    """종목 필터가 사라진 것이 이 함수가 생긴 이유다."""
    sample = champion_sample(
        _rows(
            ("2026-08-12", "A05608", 0.01),
            ("2026-08-14", "A05609", 0.02),
            ("2026-08-18", "A05609", 0.03),
        )
    )
    # 롤 당일(08-14) 하나만 빠지고 직전 월물 성적은 그대로 이어진다.
    assert sample.returns == [0.01, 0.03]


def test_live_file_yields_seventeen_not_six():
    """2026-08-24 실측 — 종전 필터는 6, 롤 연속 집계는 17이다."""
    rows = [
        json.loads(line)
        for line in io.open("logs/g2_daily_returns.jsonl", encoding="utf-8")
        if line.strip()
    ]
    sample = champion_sample(rows)
    legacy_filter = [r for r in rows if r.get("symbol") == rows[-1].get("symbol")]
    assert len(legacy_filter) == 6
    assert sample.window["rows_counted"] == 17
    assert sample.window["excluded"]["roll_day"] == 1
    # 오늘까지 전 행이 return 0.0이라 **값 자체는 안 바뀐다** — 값이 생기는 날부터 달라진다.
    assert set(sample.returns) == {0.0}


# ---- F-17 흡수분: 검증받지 않은 번들이 낸 성적에 표식을 박는다 ----


def test_unvalidated_live_gates_names_the_blocking_gates():
    """R18이 차단 계층을 3개로 고정하므로 **네 번째 차단 계층을 신설하지 않는다.**
    막을 것은 오늘의 거래가 아니라 오늘의 성적이 승격 근거로 쓰이는 일이다."""
    from messiah.core.messages import Horizon
    from messiah.models.registry import unvalidated_live_gates

    class _Gate:
        def __init__(self, name, passed):
            self.name, self.passed = name, passed

    class _Manifest:
        def __init__(self, gates):
            self._gates = gates

        def blocking_gates(self):
            return tuple(g for g in self._gates if not g.passed)

    class _Record:
        def __init__(self, bundle_id, gates):
            self.bundle_id = bundle_id
            self._gates = gates

        def manifest(self):
            return _Manifest(self._gates)

    class _Registry:
        def __init__(self, by_horizon):
            self._by = by_horizon

        def get_live(self, horizon):
            return self._by.get(horizon)

    reg = _Registry(
        {
            Horizon.M30: _Record(
                "real-20260820-2053-30m",
                [_Gate("sharpe", False), _Gate("auc", True), _Gate("max_drawdown", False)],
            )
        }
    )
    assert unvalidated_live_gates(reg) == {"real-20260820-2053-30m": ["sharpe", "max_drawdown"]}


def test_unreadable_manifest_is_not_a_pass():
    """판정 재료를 못 읽는 것은 「관문을 통과했다」가 아니다 (L18)."""
    from messiah.core.messages import Horizon
    from messiah.models.registry import unvalidated_live_gates

    class _Record:
        bundle_id = "broken"

        def manifest(self):
            raise RuntimeError("yaml 깨짐")

    class _Registry:
        def get_live(self, horizon):
            return _Record() if horizon is Horizon.M5 else None

    result = unvalidated_live_gates(_Registry())
    assert list(result) == ["broken"]
    assert result["broken"][0].startswith("manifest_unreadable")


def test_clean_registry_yields_empty_marker():
    from messiah.models.registry import unvalidated_live_gates

    class _Registry:
        def get_live(self, horizon):
            return None

    assert unvalidated_live_gates(_Registry()) == {}
