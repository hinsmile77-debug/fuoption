"""번들 매니페스트를 F-14 스키마로 옮긴다 — 일회성 마이그레이션 (2026-08-21 F-14 ㉡).

옛 매니페스트는 `gates_passed: {name: value}`로 **통과 관문만** 담았고, 현재 상태를
주장하는 `status:` 키를 가지고 있었다. 새 스키마는 `gates: [{name, passed, measured,
value, threshold}]` + `initial_status:`다 — 통과·미달·미측정 셋을 전부 적고, 현재
상태는 Registry(`data/models/registry.db`)가 정본이다.

**유예(grandfather)로 때우지 않는다.** 관문 일곱 개의 실제 결과가 같은 번들 디렉터리의
`validation_report.json`에 그대로 남아 있기 때문이다 — 그 파일을 읽으면 미달·미측정까지
복원할 수 있다. 재료가 있는데 "판정할 재료가 없다"고 적는 것은 거짓말이다.

같은 김에 `validation_report.json`의 `NaN`을 `null`로 눕힌다 — 종전 파일은 엄밀한
JSON이 아니라 jq·브라우저가 거부한다.

사용:  python scripts/migrate_bundle_manifests.py [--dry-run] [--root data/models/bundles]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import yaml

# Windows 콘솔 기본 코드페이지(cp949)가 한글 출력을 깨뜨리는 것 방지 (self_check.py와 동일)
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def _clean(x: object) -> float | None:
    if x is None:
        return None
    try:
        value = float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if math.isnan(value) or math.isinf(value) else value


def _gate_rows(report_path: Path) -> list[dict] | None:
    """`validation_report.json`에서 관문 일곱 줄을 읽는다. 못 읽으면 None."""
    if not report_path.exists():
        return None
    # 옛 파일은 `NaN`을 담고 있어 엄밀 파서로는 못 읽는다 — Python 기본 파서는 읽는다.
    try:
        rows = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(rows, list):
        return None
    out: list[dict] = []
    for row in rows:
        value = _clean(row.get("value"))
        threshold = _clean(row.get("threshold"))
        passed = bool(row.get("passed"))
        # 옛 스키마에는 `measured`가 없다. **미측정의 지문은 값이 NaN이었다는 것**이다
        # (`_deferred_performance_gates()`가 그렇게 적었다) — 통과했는데 값이 없는 관문은
        # 없으므로, 미통과 + 값 없음이면 미측정으로 읽는 것이 정확하다.
        measured = bool(row.get("measured", passed or value is not None))
        out.append(
            {
                "name": str(row.get("name")),
                "passed": passed,
                "measured": measured,
                "value": value,
                "threshold": threshold,
            }
        )
    return out or None


def migrate_bundle(bundle_dir: Path, *, dry_run: bool) -> str:
    manifest_path = bundle_dir / "manifest.yaml"
    if not manifest_path.exists():
        return f"{bundle_dir.name}: manifest.yaml 없음 — 건너뜀"
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if "gates" in data:
        return f"{bundle_dir.name}: 이미 새 스키마 — 건너뜀"

    rows = _gate_rows(bundle_dir / "validation_report.json")
    source = "validation_report.json"
    if rows is None:
        # 재료가 없으면 통과분만 복원한다 — 그 경우 `legacy_gates`가 아니게 되므로
        # 미달·미측정이 없다고 **주장**하게 된다. 그건 거짓말이라 하지 않는다.
        return f"{bundle_dir.name}: validation_report.json을 못 읽음 — 유예 상태 유지"

    data["gates"] = rows
    data.pop("gates_passed", None)
    data["initial_status"] = data.pop("status", "candidate")
    # 키 순서를 to_yaml_dict()와 맞춘다 — 사람이 두 파일을 나란히 읽는다.
    ordered = {
        key: data[key]
        for key in (
            "bundle_id",
            "horizon",
            "trained_range",
            "run_id",
            "feature_set",
            "validation_report",
            "gates",
            "initial_status",
        )
        if key in data
    }
    ordered.update({k: v for k, v in data.items() if k not in ordered})

    blocking = [g["name"] for g in rows if not g["passed"]]
    summary = f"{bundle_dir.name}: 관문 {len(rows)}개 복원 ({source}) · 미통과 {blocking or '없음'}"
    if dry_run:
        return f"[dry-run] {summary}"

    manifest_path.write_text(
        yaml.safe_dump(ordered, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    # `NaN` 소거 — 엄밀 JSON으로 다시 쓴다.
    (bundle_dir / "validation_report.json").write_text(
        json.dumps(
            [
                {
                    **row,
                    "detail": _detail_of(bundle_dir, row["name"]),
                }
                for row in rows
            ],
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ),
        encoding="utf-8",
    )
    return summary


def _detail_of(bundle_dir: Path, name: str) -> str:
    """옛 `validation_report.json`의 `detail` 문자열을 그대로 보존한다."""
    try:
        rows = json.loads((bundle_dir / "validation_report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    for row in rows:
        if row.get("name") == name:
            return str(row.get("detail", ""))
    return ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default="data/models/bundles")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    root = Path(args.root)
    if not root.exists():
        print(f"경로 없음: {root}")
        return 1
    for bundle_dir in sorted(d for d in root.iterdir() if d.is_dir()):
        print(migrate_bundle(bundle_dir, dry_run=args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
