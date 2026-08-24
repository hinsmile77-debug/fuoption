"""등록부 항목의 `fix_committed` **후보**를 뽑아 사람 앞에 놓는다 — F-20 (2026-08-24).

## 자동으로 채우지 않는다

어느 커밋이 그 항목을 고쳤는지는 **사람이 판단할 일**이다. 자동 추정은 틀린 sha를
권위 있게 만들고, 그 sha는 나중에 「이 수정이 왜 안 들었나」를 되짚을 때 조사를 엉뚱한
곳으로 보낸다. `fix_committed`의 존재 이유가 정확히 그 되짚기이므로, 틀린 값은
빈 값보다 나쁘다.

그래서 이 도구는 **후보를 나열하고 끝난다.** 확정은 사람이 `configs/pending_verifications.yaml`에
직접 적는다.

## 어떻게 찾나

항목의 `summary`에 대개 그날의 고침 코드가 적혀 있다(`P0-1`, `F-5`, `A-3`, `고도화 2` …).
등록일 **이전 7일 ~ 등록일** 사이의 커밋 중 그 코드나 항목 id의 조각을 제목·본문에
가진 것을 뽑는다. 등록일 이전을 보는 이유는 등록부 규칙 그대로다 —
*"수정은 그날 장 마감 후에 들어가므로 등록일 리포트는 수정 이전의 세계다."*

    python scripts/suggest_fix_commits.py            # 미기입 항목 전부
    python scripts/suggest_fix_commits.py --id ui-crash-isolation
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from messiah.ops.fix_verification import load_registry  # noqa: E402

#: 요약문에서 고침 코드를 뽑는 패턴 — `P0-1b` · `F-5` · `A-3` · `고도화 2` 형태.
_CODE = re.compile(r"(?:P\d-\d[a-z]?|[A-Z]-\d+|F-\d+|고도화\s*\d+)")
#: 이미 요약문에 sha가 적혀 있는 항목 — 그건 후보가 아니라 답이다.
_SHA_IN_SUMMARY = re.compile(r"커밋\s+([0-9a-f]{7,40})")

_LOOKBACK_DAYS = 7


def _git_log(since: str, until: str) -> list[tuple[str, str]]:
    out = subprocess.run(
        ["git", "log", f"--since={since}", f"--until={until}", "--format=%H%x1f%s%x1f%b%x1e"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    rows = []
    for chunk in out.stdout.split("\x1e"):
        parts = chunk.strip().split("\x1f")
        if len(parts) >= 2:
            rows.append((parts[0], parts[1] + " " + (parts[2] if len(parts) > 2 else "")))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="fix_committed 후보 제안(F-20)")
    parser.add_argument("--id", help="이 항목 하나만")
    args = parser.parse_args()

    items = [
        item
        for item in load_registry()
        if not item.fix_state_declared and (args.id is None or item.id == args.id)
    ]
    if not items:
        print("미기입 항목 없음")
        return 0

    print(f"미기입 {len(items)}건 — **후보일 뿐이다. 확정은 사람이 한다.**\n")
    for item in items:
        print(f"■ {item.id}  (등록 {item.registered})")
        print(f"  {item.summary}")

        known = _SHA_IN_SUMMARY.search(item.summary)
        if known:
            print(f"  → 요약문이 이미 sha를 말한다: {known.group(1)}  ← 이건 후보가 아니라 답이다")
            print()
            continue

        codes = sorted(set(_CODE.findall(item.summary)))
        since = (item.registered - timedelta(days=_LOOKBACK_DAYS)).isoformat()
        until = (item.registered + timedelta(days=1)).isoformat()
        needles = [c.replace(" ", "") for c in codes]
        hits = []
        for sha, text in _git_log(since, until):
            flat = text.replace(" ", "")
            if any(n in flat for n in needles):
                hits.append((sha[:7], text.strip().splitlines()[0][:80]))
        if not codes:
            print("  → 요약문에 고침 코드가 없다 — git으로 좁힐 근거가 없다(사람이 직접)")
        elif hits:
            print(f"  코드 {', '.join(codes)} · 후보 {len(hits)}건:")
            for sha, subject in hits[:6]:
                print(f"    {sha}  {subject}")
        else:
            print(f"  코드 {', '.join(codes)} · {since}~{until} 범위에 일치 커밋 없음")
        print()
    print("확정하려면 configs/pending_verifications.yaml의 해당 항목에 직접 적을 것:")
    print("    fix_committed: <sha>        # 또는 null(= 이 항목은 계측이라 고침 커밋이 없다)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
