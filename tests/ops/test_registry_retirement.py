"""통과 확정된 등록부 항목을 내릴 때 **남은 항목의 판정이 흔들리지 않는가** (F-91).

`no-silent-process-death`는 2026-09-09 장후에 4거래일 연속 기준 충족으로 공식 종결됐고,
그날 자동조치가 `configs/pending_verifications.yaml`에서 블록을 내렸다.

## 왜 「지웠다」가 아니라 「판정 불변」을 재나

등록부는 채점 입력이다. 한 항목을 빼는 것이 다른 항목의 판정을 바꾸면 그것은 정리가
아니라 **채점 결과 변경**이고, 그러면 사람이 결정할 사안이 된다. 그래서 삭제 전(23개)
상태를 되살려 채점하고, 지금(22개)의 판정과 항목별로 맞춰 본다.

`_reject_shared_metrics`(2026-08-19 F-4)가 지표 공유를 금지하므로 항목들은 원리적으로
독립이지만, 그 독립성은 **등록부가 그 규율을 지키는 동안만** 참이다. 그래서 실제 등록부로
잰다.
"""

from __future__ import annotations

from pathlib import Path

from messiah.ops.fix_verification import (
    DEFAULT_REGISTRY_PATH,
    evaluate,
    load_daily_reports,
    load_registry,
)

_RETIRED = "no-silent-process-death"

#: 2026-09-09에 내린 블록 — 판정 불변을 재려면 삭제 전 상태가 필요하다.
#: `metric: abnormal_exits`가 이 항목의 고유 지표였다(지금은 아무도 쓰지 않는다).
_RETIRED_BLOCK = """
  - id: no-silent-process-death
    summary: "프로세스가 죽고 안 돌아온 날을 리포트가 말하는가(P0-3·2026-08-19 F-1)"
    registered: 2026-08-07
    since: 2026-08-19
    negative_control:
      metric: observation_gap_minutes_max
      min: 5
      summary: "관측 공백이 5분을 넘은 날에 비정상 종료가 0이면 이 축이 눈이 먼 것이다"
    metric: abnormal_exits
    max: 0
    consecutive_days: 3
    deadline: 2026-08-28
"""


def test_retired_item_is_gone_from_the_shipped_registry():
    ids = [item.id for item in load_registry()]
    assert _RETIRED not in ids, "통과 확정 후 내린 항목이 등록부에 다시 올라왔다"
    assert len(ids) == len(set(ids)), "등록부에 중복 id가 있다"


def test_removing_the_retired_item_changes_no_other_verdict(tmp_path: Path):
    """**판정 불변** — 남은 22개의 판정이 삭제 전과 동일한가."""
    reports = load_daily_reports()
    if not reports:  # 당일 산출물이 없는 환경에서는 잴 것이 없다
        return
    today = max(reports)

    after = load_registry()
    restored = tmp_path / "before.yaml"
    restored.write_text(
        DEFAULT_REGISTRY_PATH.read_text(encoding="utf-8") + _RETIRED_BLOCK,
        encoding="utf-8",
    )
    before = load_registry(restored)

    assert len(before) == len(after) + 1, "삭제 전 상태 복원에 실패했다"

    def scored(registry):
        return {
            v.id: (v.status, v.clean_days, v.detail)
            for v in evaluate(registry, reports, today=today)
        }

    verdicts_before = scored(before)
    verdicts_after = scored(after)

    assert _RETIRED in verdicts_before and _RETIRED not in verdicts_after
    del verdicts_before[_RETIRED]
    assert verdicts_before == verdicts_after
