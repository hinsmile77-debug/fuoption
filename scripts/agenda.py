"""회의 안건 자동 생성기 — Ver 2.0 §7.1 "안건은 시스템이 만든다".

일간·주간회의 전에 실행하면 다음을 집계해 마크다운 안건을 출력한다:
  1) dev_memory/NEXT_TODO.md 미결항목 (에이징: 30일↑ 강조, 60일↑ 양자택일 강제)
  2) DECISION_LOG.md의 "라이브 미검증" 항목 (검증 기한 추적, L15)
  3) 로그 파일의 마지막 세션 마커 이후 WARN/CRITICAL 집계 (L24 — 회전 경계 오탐 방지)

사용:  python scripts/agenda.py [--logs logs/messiah.log] [--out dev_memory/agenda_today.md]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from messiah.core.timeutil import now_kst  # noqa: E402

DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def collect_todos(next_todo: Path, today: datetime) -> list[str]:
    """미결 체크박스 수집 + 에이징 판정 (섹션 날짜 또는 항목 내 날짜 기준)."""
    if not next_todo.exists():
        return ["(NEXT_TODO.md 없음)"]
    lines: list[str] = []
    for line in next_todo.read_text(encoding="utf-8").splitlines():
        if not line.strip().startswith("- [ ]"):
            continue
        item = line.strip()[6:].strip()
        marker = ""
        mdate = DATE_RE.search(item)
        if mdate:
            age = (today.date() - date.fromisoformat(mdate.group(1))).days
            if age > 60:
                marker = f" **[에이징 {age}일 — 폐기/즉시착수 양자택일]**"
            elif age > 30:
                marker = f" **[에이징 {age}일 — 최상단 배치]**"
        lines.append(f"- {item}{marker}")
    return lines or ["(미결항목 없음)"]


def collect_unverified(decision_log: Path) -> list[str]:
    """'라이브 미검증' 태그 항목 — 검증 기한 없는 것은 그 자체를 안건화."""
    if not decision_log.exists():
        return ["(DECISION_LOG.md 없음)"]
    out: list[str] = []
    for i, line in enumerate(decision_log.read_text(encoding="utf-8").splitlines(), 1):
        if "라이브 미검증" in line:
            has_deadline = bool(DATE_RE.search(line))
            suffix = "" if has_deadline else " ← **검증 기한 미기재 (L15 위반)**"
            out.append(f"- L{i}: {line.strip()[:100]}{suffix}")
    return out or ["(라이브 미검증 항목 없음)"]


def collect_log_alerts(log_path: Path) -> list[str]:
    """마지막 SessionStart 마커 이후의 WARN/CRITICAL만 집계 (L24)."""
    if not log_path.exists():
        return [f"(로그 없음: {log_path})"]
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    # 마지막 세션 경계 탐색
    start_idx = 0
    for i, ln in enumerate(lines):
        if '"tag": "SessionStart"' in ln or '"tag":"SessionStart"' in ln:
            start_idx = i
    counter: Counter[tuple[str, str]] = Counter()
    for ln in lines[start_idx:]:
        try:
            rec = json.loads(ln)
        except (json.JSONDecodeError, ValueError):
            continue
        if rec.get("level") in ("WARNING", "ERROR", "CRITICAL"):
            counter[(rec["level"], rec.get("tag", "-"))] += 1
    if not counter:
        return ["(현 세션 경보 없음)"]
    return [f"- {lvl} [{tag}] × {n}" for (lvl, tag), n in counter.most_common(20)]


def build_agenda(root: Path, log_path: Path) -> str:
    today = now_kst()
    sections = [
        f"# 회의 안건 (자동 생성) — {today.strftime('%Y-%m-%d %H:%M KST')}",
        "",
        "> 결론은 3가지만 허용: 채택(티켓화) / 보류(기한 명시) / 폐기(사유 기록) — Ver 2.0 §7.1",
        "",
        "## 1. 미결항목 (NEXT_TODO)",
        *collect_todos(root / "dev_memory" / "NEXT_TODO.md", today),
        "",
        "## 2. 라이브 미검증 (DECISION_LOG)",
        *collect_unverified(root / "dev_memory" / "DECISION_LOG.md"),
        "",
        "## 3. 현 세션 경보 집계 (세션 마커 이후만)",
        *collect_log_alerts(log_path),
        "",
    ]
    return "\n".join(sections)


def main() -> int:
    ap = argparse.ArgumentParser(description="MESSIAH meeting agenda generator")
    ap.add_argument("--logs", default="logs/messiah.log")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    agenda = build_agenda(root, Path(args.logs))
    if args.out:
        Path(args.out).write_text(agenda, encoding="utf-8")
        print(f"안건 저장: {args.out}")
    else:
        print(agenda)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
