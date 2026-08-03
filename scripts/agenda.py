"""회의 안건 자동 생성기 — Ver 2.0 §7.1 "안건은 시스템이 만든다".

일간·주간회의 전에 실행하면 다음을 집계해 마크다운 안건을 출력한다:
  1) dev_memory/NEXT_TODO.md 미결항목 (에이징: 30일↑ 강조, 60일↑ 양자택일 강제)
  2) DECISION_LOG.md의 "라이브 미검증" 항목 (검증 기한 추적, L15)
  3) 로그 파일들의 (파일별) 마지막 세션 마커 이후 WARN/CRITICAL 집계 (L24 — 회전 경계 오탐 방지)
  4) Self Evaluation 일일 리포트 (Phase 5, Ver 2.0 §9 W35~36 신설 — `logs/self_eval_*.json`)
  5) Shadow → Live 승격 제안 중 사람 결정이 필요한 것 (Ver 1.1 §6-4 신설 —
     `logs/promotion_proposals.jsonl`)

**로그 경로 갭 정정 (2026-07-27)**: `core/logging.py`의 `mlog.setup()`은 stdout
StreamHandler만 붙이고 파일에 직접 쓰지 않는다 — 실제 로그는 `run_l1_daily.bat`가
PowerShell로 stdout을 날짜별 파일(`logs/l1_daily_YYYYMMDD.log`)에 tee한 것뿐이다. 이전
기본값 `logs/messiah.log`는 아무도 써 본 적 없는 경로라 이 섹션이 항상 "(로그 없음)"만
찍었다(2026-07-27 일일점검에서 발견). 기본값을 실제 운영 로그 glob 패턴으로 교체하고,
날짜별로 파일이 나뉘는 실제 구조에 맞춰 여러 파일을 한꺼번에 집계하도록 확장했다 — 파일
하나 = 하루 세션이 자연 경계이므로 각 파일 안에서 독립적으로 마지막 SessionStart를 찾는다
(기존 L24 원칙 그대로, 파일 여러 개로 일반화했을 뿐).

사용:  python scripts/agenda.py [--logs logs/l1_daily_*.log] [--days 1]
       [--out dev_memory/agenda_today.md]
       일간 회의는 --days 1(기본, 오늘 하루), 주간 회의(금)는 --days 5 권장.

**"라이브 미검증" 오탐 정정 (2026-07-27)**: `collect_unverified()`가 볼드 마커 없이 그
단어를 언급만 하는 문장(파일 상단 형식 설명 등)까지 실제 태그로 오인해 잡던 버그를 발견 —
"**라이브 미검증**"(볼드) 매칭으로 좁혀 해결했다. 같은 점검에서 DECISION_LOG.md의 Redis
실서버 연동 항목(검증 기한 2026-07-24)이 그 기한이 오기도 전인 2026-07-21 당일에 이미
해소됐는데도 태그가 안 지워져 계속 안건에 잡히던 것도 함께 정리했다(dev_memory/
DECISION_LOG.md 참고) — 자동 안건화는 "닫힌 항목은 태그를 실제로 지운다"는 규율에 의존한다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

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
    """'라이브 미검증' 태그 항목 — 검증 기한 없는 것은 그 자체를 안건화.

    실제 태그는 항상 볼드(`**라이브 미검증**`) 표기라는 게 이 파일 전체의 실제 관례다(모든
    실사용례가 그렇다) — 평문/인용부호로 이 단어를 언급만 하는 문장(예: 파일 상단의 형식
    설명 자체, 또는 이 설명을 인용하는 다른 회고 문단)까지 안건으로 잡히던 오탐을 2026-07-27
    agenda.py 실행 결과에서 발견(L4·이 문서 자체의 회고 절 등). 볼드 마커를 요구해 실제
    태그만 매칭한다."""
    if not decision_log.exists():
        return ["(DECISION_LOG.md 없음)"]
    out: list[str] = []
    for i, line in enumerate(decision_log.read_text(encoding="utf-8").splitlines(), 1):
        if "**라이브 미검증**" in line:
            has_deadline = bool(DATE_RE.search(line))
            suffix = "" if has_deadline else " ← **검증 기한 미기재 (L15 위반)**"
            out.append(f"- L{i}: {line.strip()[:100]}{suffix}")
    return out or ["(라이브 미검증 항목 없음)"]


def resolve_log_paths(root: Path, pattern: str, days: int) -> list[Path]:
    """`--logs` glob 패턴을 실제 로그 파일 목록(오래된 것 → 최신 것 순)으로 해석한다.

    파일명이 `l1_daily_YYYYMMDD.log`라 문자열 정렬이 곧 날짜 정렬이다. `days`가 양수면
    최신 파일부터 그만큼만 남긴다(일간 회의=1, 주간 회의=5 등). 패턴에 glob 메타문자(`*`/`?`)가
    없으면 과거 방식(단일 고정 경로) 호환을 위해 그 경로 하나를 그대로 반환한다 — 존재 여부는
    호출자(collect_log_alerts)가 판단한다."""
    if "*" not in pattern and "?" not in pattern:
        p = Path(pattern)
        return [p if p.is_absolute() else root / p]
    matches = sorted(root.glob(pattern))
    if days > 0:
        matches = matches[-days:]
    return matches


def collect_log_alerts(log_paths: Path | Sequence[Path]) -> list[str]:
    """각 로그 파일에서 그 파일의 마지막 SessionStart 마커 이후 WARN 이상만 집계해 합산한다.

    파일 하나 = 하루 세션이 자연 경계이므로(운영 로그가 날짜별로 나뉘어 쌓임), 파일마다
    독립적으로 마커를 찾은 뒤 전체를 합산한다(L24 — 회전 경계 오탐 방지 원칙을 다중 파일로
    일반화)."""
    paths = [log_paths] if isinstance(log_paths, Path) else list(log_paths)
    existing = [p for p in paths if p.exists()]
    if not existing:
        shown = ", ".join(str(p) for p in paths) or "패턴에 매치되는 파일 없음"
        return [f"(로그 없음: {shown})"]
    counter: Counter[tuple[str, str]] = Counter()
    for log_path in existing:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        # 마지막 세션 경계 탐색 (파일별 독립)
        start_idx = 0
        for i, ln in enumerate(lines):
            if '"tag": "SessionStart"' in ln or '"tag":"SessionStart"' in ln:
                start_idx = i
        for ln in lines[start_idx:]:
            try:
                rec = json.loads(ln)
            except (json.JSONDecodeError, ValueError):
                continue
            if rec.get("level") in ("WARNING", "ERROR", "CRITICAL"):
                counter[(rec["level"], rec.get("tag", "-"))] += 1
    if not counter:
        scanned = ", ".join(p.name for p in existing)
        return [f"(현 세션 경보 없음 — 스캔: {scanned})"]
    return [f"- {lvl} [{tag}] × {n}" for (lvl, tag), n in counter.most_common(20)]


def collect_self_eval(root: Path, days: int) -> list[str]:
    """일일 Self Evaluation 리포트(Ver 2.0 §9 W35~36, `models/self_evaluation.py`가 산출)
    집계 — `logs/self_eval_YYYY-MM-DD.json`, 파일 하나 = `SelfEvalReport` 1건(현재 운영
    스크립트가 직접 파일로 쓴다는 전제, `scripts/run_g2_paper_trading.py` 참고)."""
    paths = resolve_log_paths(root, "logs/self_eval_*.json", days)
    existing = [p for p in paths if p.exists()]
    if not existing:
        return ["(Self Evaluation 리포트 없음 — G2 페이퍼트레이딩 미개시 또는 로그 미생성)"]
    lines: list[str] = []
    for path in existing:
        try:
            r = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            lines.append(f"- {path.name}: (파싱 실패)")
            continue
        # 2026-07-31 이전 리포트는 `n_trades`(= 실제로는 표본 수)라는 옛 이름을 쓴다 — 이미
        # 쌓인 파일을 다시 못 쓰므로 둘 다 읽고, 화면 문구는 정정된 의미로 적는다.
        samples = r.get("n_return_samples", r.get("n_trades", 0))
        fills = r.get("n_fills")
        fills_text = "체결 미집계" if fills is None else f"체결 {fills}건"
        # 실현 슬리피지 None = "잴 수 없었다"(체결 0건) — 0.00틱으로 찍으면 성과처럼 읽힌다.
        realized = r.get("slippage_realized_ticks")
        realized_text = "미측정" if realized is None else f"{realized:.2f}틱"
        head = (
            f"- {r.get('date', '?')} {r.get('symbol', '?')}: "
            f"표본 {samples}개 · {fills_text} · Shadow {r.get('n_shadow_bundles', 0)}개 · "
            f"슬리피지(예측/실현) {r.get('slippage_predicted_ticks', 0):.2f}/{realized_text}"
        )
        # 손익 지표는 **측정 가능할 때만** 보여준다(2026-08-03 고도화 C). 결선이 안 된 날의
        # `Sharpe 0.00`을 그대로 찍으면 "수익도 손실도 없었다"는 성적처럼 읽히는데, 실제로는
        # 모델이 하나도 안 붙은 채 파이프라인만 돈 것이다 — 4거래일 연속 그랬다.
        # `pnl_measurable` 필드가 없는 옛 리포트(08-03 이전)는 판정 근거가 없으므로 역시
        # 손익을 주장하지 않는다(모르는 것을 좋은 쪽으로 가정하지 않는다).
        if r.get("pnl_measurable"):
            lines.append(
                f"{head} · 승률 {r.get('win_rate', 0):.0%} · "
                f"PF {r.get('profit_factor', 0):.2f} · Sharpe {r.get('sharpe', 0):.2f} · "
                f"MDD {r.get('max_drawdown', 0):.1%}"
            )
        else:
            stage = r.get("wiring_stage") or "결선 상태 미기록"
            lines.append(
                f"{head}\n  - ⚠ 손익 지표 미측정({stage}) — 승률·PF·Sharpe·MDD는 자리표시자"
            )
    return lines


def collect_promotion_proposals(root: Path) -> list[str]:
    """미결 승격 제안(Ver 1.1 §6-4 "자동 제안 + 사람 승인") —
    `logs/promotion_proposals.jsonl`(append-only, `models/shadow_manager.py`의
    `evaluate_promotion()` 산출물을 운영 스크립트가 매일 追記). `recommended=true`만
    사람 결정이 필요한 안건이다(Ver 2.0 §7.1 "허용되는 결론은 3가지" — 채택/보류/폐기)."""
    path = root / "logs" / "promotion_proposals.jsonl"
    if not path.exists():
        return ["(승격 제안 없음)"]
    recommended: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            p = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if p.get("recommended"):
            recommended.append(
                f"- **{p.get('bundle_id', '?')}** ({p.get('horizon', '?')}): "
                f"{p.get('rationale', '')}"
            )
    return recommended or ["(사람 검토가 필요한 승격 제안 없음)"]


def collect_fix_verifications(root: Path, today: datetime) -> list[str]:
    """ "고쳤다"고 판정한 수정이 실제로 들었는지 — 매일 실측으로 채점한 결과
    (`messiah/ops/fix_verification.py`).

    §2의 "라이브 미검증"이 **사람이 판단하는** 항목이라면 이 절은 **수치로 자동 판정되는**
    항목이다. 자동 판정이 가능한 것을 사람 규율에 맡겨 뒀던 게 2026-07-29~08-03에 같은
    UI 크래시를 세 번 "고쳤다"고 오판한 원인이었다.
    """
    from messiah.ops import fix_verification as fv

    try:
        verdicts = fv.run(today=today.date(), log_dir=root / "logs")
    except Exception as exc:  # noqa: BLE001 — 안건 생성 전체를 막지 않는다
        return [f"- (검증 실패: {exc})"]

    if not verdicts:
        return ["- (등록된 검증 대기 수정 없음)"]
    # 재발·기한초과를 맨 위로 — 회의에서 먼저 봐야 할 순서가 곧 목록 순서여야 한다.
    verdicts.sort(key=lambda v: (not v.needs_attention, v.id))
    return [f"- **[{v.status}]** `{v.id}` — {v.summary}\n  - {v.detail}" for v in verdicts]


def build_agenda(root: Path, log_paths: Path | Sequence[Path], days: int = 1) -> str:
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
        "## 2-1. 수정 유효성 자동 검증 (고도화 B — 재발은 최우선 안건)",
        *collect_fix_verifications(root, today),
        "",
        "## 3. 현 세션 경보 집계 (세션 마커 이후만)",
        *collect_log_alerts(log_paths),
        "",
        "## 4. Self Evaluation 일일 리포트 (Phase 5, Ver 2.0 §7)",
        *collect_self_eval(root, days),
        "",
        "## 5. Shadow → Live 승격 제안 (Ver 1.1 §6-4)",
        *collect_promotion_proposals(root),
        "",
    ]
    return "\n".join(sections)


def main() -> int:
    ap = argparse.ArgumentParser(description="MESSIAH meeting agenda generator")
    ap.add_argument(
        "--logs",
        default="logs/l1_daily_*.log",
        help="로그 파일 glob 패턴(저장소 루트 기준 상대경로). 기본값은 실제 운영 로그 위치",
    )
    ap.add_argument(
        "--days",
        type=int,
        default=1,
        help="최근 며칠치 로그를 집계할지 (기본 1 = 일간 회의용 오늘 하루, 주간 회의는 5 권장)",
    )
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    log_paths = resolve_log_paths(root, args.logs, args.days)
    agenda = build_agenda(root, log_paths, args.days)
    if args.out:
        Path(args.out).write_text(agenda, encoding="utf-8")
        print(f"안건 저장: {args.out}")
    else:
        print(agenda)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
