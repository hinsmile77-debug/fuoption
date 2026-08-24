"""F-18 — 합격선 0의 출처를 「기록이 없다」와 「기록해 보니 폴백이다」로 가른다.

2026-08-21 F-6이 `thresholds.yaml`에 출처를 적기 시작했는데 **현역 번들은 그보다 먼저
패킹돼 그 키가 없다.** 그래서 임계 0.0이 「학습이 고른 값」인지 「지지도 하한을 못 채워
격자 첫 칸으로 떨어진 폴백」인지를 오늘도 알 수 없었다. 둘은 대처가 정반대다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path("scripts").resolve()))

import backfill_threshold_source as backfill  # noqa: E402

from messiah.models.registry import (  # noqa: E402
    THRESHOLD_SOURCE_UNKNOWN,
    THRESHOLD_SOURCE_UNRECORDED,
    load_threshold_selection,
)


def _bundle(tmp_path: Path, **extra) -> Path:
    d = tmp_path / "bundle"
    d.mkdir()
    data = {"meta_labeler_threshold": 0.0}
    data.update(extra)
    (d / "thresholds.yaml").write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return d


def test_two_kinds_of_missing_are_different_strings():
    """**「키가 없다」와 「없다는 사실을 확인해 적었다」는 다른 사실이다.**

    안 가르면 「옛 번들이라 없다」와 「새 번들인데 기입이 실패했다」가 같은 문자열로
    접힌다(2026-08-21 G-11의 「미측정의 표현형」과 같은 형태).
    """
    assert THRESHOLD_SOURCE_UNKNOWN != THRESHOLD_SOURCE_UNRECORDED


def test_a_bundle_that_already_says_its_source_is_left_alone(tmp_path: Path):
    d = _bundle(tmp_path, meta_labeler_threshold_source="optimized")
    plan = backfill.plan_for(d, "b1", log_dir=tmp_path)
    assert plan["action"] == "skip"


def test_a_recorded_selection_is_restored(tmp_path: Path):
    (tmp_path / "bundle_build_20260901.json").write_text(
        json.dumps(
            {
                "results": [
                    {
                        "bundle_id": "b1",
                        "threshold_selection": {
                            "source": "fallback",
                            "support": 3,
                            "total": 40,
                            "min_support": 8,
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    d = _bundle(tmp_path)
    plan = backfill.plan_for(d, "b1", log_dir=tmp_path)
    assert plan["action"] == "restore"
    assert plan["write"]["meta_labeler_threshold_source"] == "fallback"

    backfill.apply_plan(d, plan)
    after = load_threshold_selection(d)
    assert after["source"] == "fallback"
    assert after["measured"] is True
    assert after["support"] == 3
    assert (d / "thresholds.yaml").with_suffix(".yaml.bak-20260824").exists() or list(
        d.glob("thresholds.yaml.bak-*")
    ), "현역 파일을 고치기 전에 원본을 남긴다"


def test_a_build_record_that_itself_says_unknown_restores_nothing(tmp_path: Path):
    """F-18 이전의 빌드 기록은 복원할 것이 없다 — 그 기록 자체가 모른다."""
    (tmp_path / "bundle_build_20260820.json").write_text(
        json.dumps(
            {"results": [{"bundle_id": "b1", "threshold_selection": {"source": "unknown"}}]}
        ),
        encoding="utf-8",
    )
    plan = backfill.plan_for(_bundle(tmp_path), "b1", log_dir=tmp_path)
    assert plan["action"] == "mark_unrecorded"


def test_no_artifact_writes_the_limit_as_a_value(tmp_path: Path):
    """**한계를 값으로 적는다** — 이 항목을 영원히 「확인 필요」로 두지 않는다."""
    d = _bundle(tmp_path)
    plan = backfill.plan_for(d, "b1", log_dir=tmp_path)
    assert plan["action"] == "mark_unrecorded"
    assert "재학습" in plan["reason"]

    backfill.apply_plan(d, plan)
    after = load_threshold_selection(d)
    assert after["source"] == THRESHOLD_SOURCE_UNRECORDED
    assert after["measured"] is True, "이제 '적힌 적이 없다'가 아니라 '없다고 적혀 있다'"
    # 임계값 자체는 건드리지 않는다 — 이 작업은 **출처만** 기입한다.
    assert after["threshold"] == 0.0


def test_the_live_bundle_now_says_why_it_cannot_answer():
    """실제 현역 번들 — 2026-08-24에 사후 기입한 결과."""
    live = Path("data/models/bundles/real-20260820-2053-30m")
    if not live.exists():  # pragma: no cover — 다른 PC에는 번들이 없을 수 있다
        return
    selection = load_threshold_selection(live)
    assert selection["source"] == THRESHOLD_SOURCE_UNRECORDED
    assert selection["threshold"] == 0.0
