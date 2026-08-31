"""현역 번들의 메타 임계 **출처**를 사후 기입한다 — F-18 (2026-08-24).

## 무엇을 고치나

2026-08-21 F-6이 `thresholds.yaml`에 `meta_labeler_threshold_source`를 적기 시작했다.
그런데 **현역 번들은 그보다 먼저(2026-08-20 20:53) 패킹돼 그 키가 없다.** 그래서 임계
0.0이 「학습이 고른 값」인지 「지지도 하한을 못 채워 격자 첫 칸으로 떨어진 폴백」인지를
저장 상태만 보고는 알 수 없고, 그 둘은 **대처가 정반대**다 — 앞이면 모델을 다시
만들어야 하고 뒤면 자료를 더 모으면 된다.

이 스크립트는 그 답을 **가능하면 복원하고, 불가능하면 불가능하다고 적는다.**

## 어디서 찾나 (순서대로)

1. `logs/bundle_build_*.json`의 `threshold_selection` — F-18 이후 빌드는 여기 남는다.
2. 번들 디렉터리의 `meta_labeler.json` — 값만 있고 출처는 없다. 값 대조에만 쓴다.

둘 다 못 찾으면 **`unrecorded_pre_f6`을 명시적으로 적는다.** `unknown`(키가 아예 없다)과
다른 값이어야 한다 — 안 그러면 「옛 번들이라 없다」와 「새 번들인데 기입이 실패했다」가
같은 문자열로 접힌다.

## 이 스크립트가 못 하는 것

**산출물이 없으면 "학습이 고른 0인가 폴백 0인가"는 이 작업으로도 답이 안 나온다.**
그때는 재학습이 유일한 답이고, 그 사실 자체를 기록으로 남기는 것이 여기서 할 수 있는
전부다 — 이 항목을 영원히 「확인 필요」로 떠 있게 두지 않는다.

## 실행

    python scripts/backfill_threshold_source.py            # 무엇을 할지 보여만 준다
    python scripts/backfill_threshold_source.py --apply    # 실제로 쓴다(백업 후)

`--apply`는 **현역 번들 파일을 고치는 작업**이다. 원본을 `thresholds.yaml.bak-YYYYMMDD`로
남기고, 쓴 뒤 `load_threshold_selection()` 왕복을 확인한다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import yaml  # noqa: E402

from messiah.core.console import ensure_utf8_console  # noqa: E402
from messiah.core.messages import Horizon  # noqa: E402
from messiah.core.timeutil import now_kst  # noqa: E402
from messiah.models.registry import (  # noqa: E402
    THRESHOLD_SOURCE_UNKNOWN,
    THRESHOLD_SOURCE_UNRECORDED,
    ModelRegistry,
    load_threshold_selection,
)

_REGISTRY_DB = Path("data/models/registry.db")
_LOG_DIR = Path("logs")


def recorded_selection(bundle_id: str, log_dir: Path = _LOG_DIR) -> dict | None:
    """빌드 기록에서 이 번들의 `threshold_selection`을 찾는다 — 없으면 None."""
    for path in sorted(log_dir.glob("bundle_build_*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for row in data.get("results", []):
            if row.get("bundle_id") != bundle_id:
                continue
            selection = row.get("threshold_selection")
            # 값이 있어도 출처가 `unknown`이면 복원할 것이 없다 — 그 기록 자체가
            # F-18 이전 것이다.
            if isinstance(selection, dict) and selection.get("source") not in (
                None,
                THRESHOLD_SOURCE_UNKNOWN,
            ):
                return selection
    return None


def plan_for(bundle_dir: Path, bundle_id: str, log_dir: Path = _LOG_DIR) -> dict:
    """이 번들에 무엇을 쓸 것인가 — 쓰기 없이 판단만 한다."""
    current = load_threshold_selection(bundle_dir)
    if current["measured"]:
        return {"action": "skip", "reason": "이미 출처가 적혀 있다", "current": current}

    found = recorded_selection(bundle_id, log_dir)
    if found is not None:
        return {
            "action": "restore",
            "reason": "빌드 기록에서 학습 산출물을 찾았다",
            "current": current,
            "write": {
                "meta_labeler_threshold_source": found.get("source"),
                "meta_labeler_threshold_support": found.get("support"),
                "meta_labeler_threshold_total": found.get("total"),
                "meta_labeler_threshold_min_support": found.get("min_support"),
            },
        }
    return {
        "action": "mark_unrecorded",
        # **한계를 값으로 적는다.** 이 문장이 없으면 다음 사람이 같은 조사를 처음부터
        # 다시 한다 — 2026-08-21과 2026-08-24에 실제로 두 번 했다.
        "reason": "학습 산출물이 남아 있지 않다 — 재학습만이 답할 수 있다",
        "current": current,
        "write": {
            "meta_labeler_threshold_source": THRESHOLD_SOURCE_UNRECORDED,
            "meta_labeler_threshold_note": (
                "2026-08-24 F-18 사후 조사: 이 번들은 F-6(출처 기록) 이전에 패킹됐고 "
                "빌드 기록·번들 어디에도 threshold_selection이 남아 있지 않다. "
                "임계가 최적화인지 폴백인지는 재학습으로만 확인할 수 있다."
            ),
        },
    }


def apply_plan(bundle_dir: Path, plan: dict) -> None:
    """계획대로 `thresholds.yaml`에 기입한다 — 원본은 백업한다."""
    path = bundle_dir / "thresholds.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    backup = path.with_suffix(f".yaml.bak-{now_kst():%Y%m%d}")
    if not backup.exists():
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    data.update(plan["write"])
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

    # 왕복 확인 — 쓴 것을 읽는 쪽이 실제로 그렇게 읽는가.
    after = load_threshold_selection(bundle_dir)
    if not after["measured"] or after["source"] != plan["write"]["meta_labeler_threshold_source"]:
        raise SystemExit(f"왕복 실패: {bundle_dir} — 쓴 값과 읽은 값이 다르다({after})")


def main() -> int:
    ensure_utf8_console()  # 판정보다 출력이 먼저 죽는 것을 막는다 (1-13)
    parser = argparse.ArgumentParser(description="현역 번들의 메타 임계 출처 사후 기입(F-18)")
    parser.add_argument("--apply", action="store_true", help="실제로 쓴다(기본은 보여만 준다)")
    parser.add_argument("--registry", default=str(_REGISTRY_DB))
    args = parser.parse_args()

    registry = ModelRegistry(Path(args.registry))
    try:
        records = [
            record for horizon in Horizon if (record := registry.get_live(horizon)) is not None
        ]
    finally:
        registry.close()

    if not records:
        print("현역 번들 없음 — 할 일이 없다")
        return 0

    for record in records:
        plan = plan_for(record.bundle_dir, record.bundle_id)
        current = plan["current"]
        print(f"\n{record.bundle_id}")
        print(f"  지금:   임계 {current['threshold']} · 출처 {current['source']}")
        print(f"  판단:   {plan['action']} — {plan['reason']}")
        if plan["action"] == "skip":
            continue
        for key, value in plan["write"].items():
            print(f"  쓸 것:  {key} = {value}")
        if args.apply:
            apply_plan(record.bundle_dir, plan)
            print("  적용:   완료(원본은 .bak-YYYYMMDD로 남겼다) · 왕복 확인 통과")
        else:
            print("  적용:   생략(--apply를 주면 쓴다)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
