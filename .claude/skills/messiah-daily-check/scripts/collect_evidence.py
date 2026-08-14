#!/usr/bin/env python3
"""MESSIAH 일일 점검 — 증거 수집기 (stdlib only).

로그·산출물 JSON·git 이력·dev_memory를 한 번에 훑어서
사람이(그리고 Claude가) 바로 읽을 수 있는 마크다운 다이제스트를 만든다.

원본 로그는 파일당 130~240KB라 그대로 읽으면 컨텍스트가 죽는다.
이 스크립트의 존재 이유는 "무엇을 볼지"가 아니라 "무엇을 안 볼지"를 정하는 것이다.

사용:
    python3 scripts/collect_evidence.py --phase post
    python3 scripts/collect_evidence.py --phase intra --date 2026-08-11
    python3 scripts/collect_evidence.py --phase post --date 2026-07-23 --out logs/dailycheck/ev.md

Python 3.8+ / 외부 의존성 없음 / Windows·Linux 공통.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import date as _date
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))

# ---------------------------------------------------------------- 기본 설정
# configs/dailycheck_anchors.json 이 있으면 그 값이 우선한다.
DEFAULT_CONFIG = {
    # 하루의 뼈대. 이 시각들에 무엇이 있어야 하는지가 점검의 척추다.
    # procs: 이 앵커가 적용되는 프로세스. 비면 전부.
    "anchors": [
        {"at": "08:15", "label": "기동 창 개시", "phase": "pre", "procs": ["l1_daily"]},
        {
            "at": "08:20",
            "label": "Messiah(L1) 스케줄 트리거",
            "phase": "pre",
            "procs": ["l1_daily"],
        },
        {"at": "08:25", "label": "Messiah-G2 스케줄 트리거", "phase": "pre", "procs": ["g2_daily"]},
        {
            "at": "08:40",
            "label": "장전 준비(옵션체인·마스터·번들)",
            "phase": "pre",
            "procs": ["l1_daily"],
        },
        {
            "at": "09:00",
            "label": "정규장 개장",
            "phase": "intra",
            "procs": ["l1_daily", "g2_daily"],
        },
        {"at": "12:00", "label": "장중 중간점", "phase": "intra", "procs": ["l1_daily"]},
        {"at": "15:35", "label": "정규장 마감", "phase": "post", "procs": ["l1_daily", "g2_daily"]},
        {
            "at": "15:40",
            "label": "Messiah-Shutdown",
            "phase": "post",
            "procs": ["shutdown_watchdog"],
        },
        {"at": "15:45", "label": "Messiah-Postmarket", "phase": "post", "procs": ["postmarket"]},
    ],
    "anchor_window_minutes": 4,
    # 이 구간 안에서 로그가 끊기면 의심한다.
    "gap_scan_window": ["08:15", "15:40"],
    "gap_threshold_minutes": 10,
    "market_hours": ["09:00", "15:35"],
    # 프로세스별 로그 파일 패턴 ({d}=YYYYMMDD, {D}=YYYY-MM-DD)
    "process_logs": {
        "l1_daily": "logs/l1_daily_{d}.log",
        "g2_daily": "logs/g2_daily_{d}.log",
        "ui": "logs/ui_{d}.log",
        "ui_err": "logs/ui_{d}.err.log",
        "postmarket": "logs/postmarket_{d}.log",
    },
    "rolling_logs": {
        "shutdown_watchdog": "logs/shutdown_watchdog.log",
    },
    # 국면별로 "그 시각까지 있어야 하는" 산출물
    "expected_artifacts": {
        "pre": [
            "logs/l1_daily_{d}.log",
            "logs/g2_daily_{d}.log",
            "logs/ui_{d}.log",
        ],
        "intra": [
            "logs/l1_daily_{d}.log",
            "logs/g2_daily_{d}.log",
            "logs/status_snapshot.json",
        ],
        "post": [
            "logs/l1_daily_{d}.log",
            "logs/g2_daily_{d}.log",
            "logs/postmarket_{d}.log",
            "logs/daily_integrity_{d}.json",
            "logs/self_eval_{D}.json",
            "logs/vol_scorecard_{d}.json",
            "logs/volume_check_{d}.json",
        ],
    },
    # 요약해서 실을 JSON 산출물
    "json_artifacts": [
        "logs/status_snapshot.json",
        "logs/daily_integrity_{d}.json",
        "logs/self_eval_{D}.json",
        "logs/vol_scorecard_{d}.json",
        "logs/volume_check_{d}.json",
        "logs/kill_switch_verification_{d}.json",
        "logs/command_center_ui.json",
        "logs/g1_walk_forward_{d}.json",
    ],
    # 이 태그가 보이면 무조건 전량 인용한다 — 조용히 넘어가면 안 되는 것들
    "always_quote_tags": [
        "SessionStart",
        "SessionEnd",
        "FixVerificationRecurred",
        "FixVerificationFailed",
        "KillSwitch",
        "KillSwitchTripped",
        "CircuitBreaker",
        "GatewayHalted",
        "UnmatchedFill",
        "ReconcileMismatch",
        "LaunchWindowRefused",
        "CrashForensicsArmed",
        "ForcedFlat",
    ],
    "max_error_samples_per_tag": 3,
    "max_warn_tags": 25,
    "max_warn_samples_per_tag": 1,
    "max_raw_preamble_lines": 60,
    "msg_truncate": 260,
}

LEVEL_ORDER = ["CRITICAL", "FATAL", "ERROR", "WARNING", "WARN", "INFO", "DEBUG"]
TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2}):(\d{2})")


# ---------------------------------------------------------------- 유틸
def eprint(*a):
    print(*a, file=sys.stderr)


def find_repo_root(start: Path) -> Path:
    """SYSTEM.md 와 dev_memory/ 를 함께 가진 첫 조상을 리포 루트로 본다."""
    cur = start.resolve()
    for cand in [cur, *cur.parents]:
        if (cand / "SYSTEM.md").exists() and (cand / "dev_memory").is_dir():
            return cand
    for cand in [cur, *cur.parents]:
        if (cand / ".git").exists():
            return cand
    return cur


def parse_date(s: str | None) -> _date:
    if not s:
        return datetime.now(KST).date()
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%m/%d", "%m-%d"):
        try:
            # naive로 두는 것이 맞다 — 아래에서 .date()만 꺼내 쓰고 datetime 자체는
            # 밖으로 나가지 않는다. 날짜 인자에 tz를 붙이면 그 tz가 KST가 아닐 때
            # 하루가 밀린다 (SYSTEM.md R3의 대상은 '시각'이지 '달력 날짜'가 아니다).
            d = datetime.strptime(s, fmt)  # noqa: DTZ007
        except ValueError:
            continue
        if fmt in ("%m/%d", "%m-%d"):
            d = d.replace(year=datetime.now(KST).year)
        return d.date()
    raise SystemExit(f"날짜 형식을 못 읽었다: {s!r} (예: 2026-08-11 / 20260811 / 7/23)")


def hhmm_to_minutes(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n}B"


def truncate(s, n):
    s = str(s).replace("\n", " ⏎ ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def run_git(root: Path, args, timeout=25):
    try:
        p = subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        return (
            p.stdout.strip()
            if p.returncode == 0
            else f"(git 실패 rc={p.returncode}) {p.stderr.strip()[:300]}"
        )
    except Exception as e:  # noqa: BLE001
        return f"(git 실행 불가) {e}"


def read_text(path: Path, limit=None) -> str:
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            return f.read() if limit is None else f.read(limit)
    except Exception as e:  # noqa: BLE001
        return f"(읽기 실패) {e}"


# ---------------------------------------------------------------- 로그 파싱
class LogDigest:
    """로그 한 파일을 훑고 남길 것만 남긴다."""

    def __init__(self, name: str, path: Path, cfg: dict, day: _date):
        self.name = name
        self.path = path
        self.cfg = cfg
        self.day = day
        self.exists = path.exists()
        self.size = path.stat().st_size if self.exists else 0
        self.mtime = (
            datetime.fromtimestamp(path.stat().st_mtime, KST).strftime("%H:%M:%S")
            if self.exists
            else None
        )
        self.total_lines = 0
        # 기동 창 가드가 되돌려보낸 기동 (2026-08-12 F-6) — `session_markers()`가 채운다.
        self.refused_starts: list = []
        self.json_lines = 0
        self.raw_preamble: list[str] = []
        self.records: list[dict] = []  # 시각 있는 JSON 레코드
        self.level_counts: dict[str, int] = {}
        self.tag_counts: dict[str, int] = {}
        self.by_level_tag: dict[tuple, list] = {}
        self.selfcheck: list[str] = []
        self.quoted: dict[str, list] = {}
        self.parse_errors = 0

    def scan(self):
        if not self.exists:
            return self
        cap_pre = self.cfg["max_raw_preamble_lines"]
        with open(self.path, "r", encoding="utf-8-sig", errors="replace") as f:
            for line in f:
                line = line.rstrip("\n").rstrip("\r")
                if not line.strip():
                    continue
                self.total_lines += 1
                stripped = line.lstrip()
                if stripped.startswith("{"):
                    try:
                        rec = json.loads(stripped)
                    except Exception:  # noqa: BLE001
                        self.parse_errors += 1
                        continue
                    if not isinstance(rec, dict):
                        continue
                    self.json_lines += 1
                    self._ingest(rec)
                else:
                    if stripped.startswith(("[OK ]", "[WARN", "[FAIL", "[ERR", "self-check")):
                        self.selfcheck.append(stripped)
                    elif len(self.raw_preamble) < cap_pre:
                        self.raw_preamble.append(stripped)
        return self

    def _ingest(self, rec: dict):
        level = str(rec.get("level", "?")).upper()
        tag = str(rec.get("tag", "?"))
        ts = rec.get("ts") or rec.get("timestamp") or ""
        self.level_counts[level] = self.level_counts.get(level, 0) + 1
        self.tag_counts[tag] = self.tag_counts.get(tag, 0) + 1
        m = TS_RE.search(str(ts))
        hhmm = f"{m.group(2)}:{m.group(3)}:{m.group(4)}" if m else "??:??:??"
        minutes = int(m.group(2)) * 60 + int(m.group(3)) if m else None
        entry = {
            "hhmm": hhmm,
            "minutes": minutes,
            "level": level,
            "tag": tag,
            "msg": rec.get("msg", ""),
            "extra": {
                k: v for k, v in rec.items() if k not in ("ts", "timestamp", "level", "tag", "msg")
            },
        }
        if minutes is not None:
            self.records.append(entry)
        key = (level, tag)
        self.by_level_tag.setdefault(key, []).append(entry)
        if tag in self.cfg["always_quote_tags"]:
            self.quoted.setdefault(tag, []).append(entry)

    # --- 파생 지표 -------------------------------------------------
    def session_markers(self):
        """유효 기동·정상 종료. **기동 창이 거절한 기동은 기동이 아니다** (2026-08-12 F-6).

        `ops/session_guard.py`가 되돌려보낸 프로세스도 `SessionStart`는 이미 찍은 뒤다 —
        로깅 설정이 프로세스 최초에 일어나기 때문이다. 그것을 그대로 세면 정시 기동 하나뿐인
        정상일이 **매일 "중복 기동 2회"**로 찍힌다(2026-08-12 실측: 07:23:34 거절 +
        08:20:28 정시 기동 → 적신호 2건이 전부 가짜였다).

        `ops/integrity_report.py`는 이미 옳게 센다(`_drop_refused_starts`, 2026-08-07 P0-4)
        — 그 fix가 리포트 쪽에만 반영되고 이 점검 도구에는 안 들어와 있었다. 짝짓기 규칙도
        그쪽과 맞춘다: 거절 하나마다 **그 시각 이하의 가장 늦은 기동** 하나를 지운다.
        개수만 빼면 순서가 반대인 날(정시 기동 뒤 장 마감 직후 부팅 트리거 발화)에
        살아 있어야 할 기동이 지워진다.
        """
        starts = [e for e in self.records if e["tag"] == "SessionStart"]
        ends = [e for e in self.records if e["tag"] == "SessionEnd"]
        refused = sorted(e["hhmm"] for e in self.records if e["tag"] == "LaunchWindowRefused")

        remaining = sorted(starts, key=lambda e: e["hhmm"])
        dropped = []
        for moment in refused:
            candidates = [e for e in remaining if e["hhmm"] <= moment]
            if candidates:
                remaining.remove(candidates[-1])
                dropped.append(candidates[-1])
        # 거절 자체는 버리지 않는다 — 관측 가치가 있다(정시 트리거 시각이 어긋나면
        # 이 값이 먼저 는다). §9가 "기동 창 거절 n회(정상)"로 따로 표기한다.
        self.refused_starts = dropped
        return remaining, ends

    def cadence_minutes(self):
        """이 프로세스의 **설계 발행 주기**(분) — 로그에서 유도한다 (2026-08-14 F-13).

        고정 10분 임계는 매 봉 무언가를 찍는 `l1_daily`에는 맞지만, 30분 격자로만 판단하는
        `g2_daily`에는 안 맞는다. 2026-08-14 다이제스트 §9의 자동 적신호 11개 중 **8개**가
        g2의 30분 공백이었고 전부 정상 동작이었다 — 그 8줄이 진짜 신호(NaN·스냅샷 실패)를
        목록 아래로 밀어냈다.

        주기를 상수로 적지 않는 이유: 그 값은 코드(`RegimeRuntime`의 구동 Horizon)에 있고,
        복사하면 두 곳이 어긋나는 순간 이 검사가 거짓말을 시작한다. **그날 로그의 인접
        간격 최빈값**을 쓰면 한 번도 상수를 안 읽고 맞출 수 있다
        (`ops/series_coverage.py`가 폴링 카덴스에 쓰는 것과 같은 방법).

        표본이 적으면(3간격 미만) None — 유도할 근거가 없으면 고정 임계로 돌아간다.
        """
        lo = hhmm_to_minutes(self.cfg["gap_scan_window"][0])
        hi = hhmm_to_minutes(self.cfg["gap_scan_window"][1])
        pts = sorted({e["minutes"] for e in self.records if lo <= e["minutes"] <= hi})
        deltas = [b - a for a, b in zip(pts, pts[1:]) if b > a]
        if len(deltas) < 3:
            return None
        return Counter(deltas).most_common(1)[0][0] or None

    def gap_threshold(self):
        """공백 임계 — `max(고정 임계, 최빈간격 × 1.5)`.

        1.5배인 것이 핵심이다: **주기 1회 결손은 반드시 걸린다**(2주기 = 2.0배 > 1.5배).
        주기를 그대로 임계로 쓰면 정상 간격이 매번 걸리고, 2배로 잡으면 1회 결손을 놓친다.
        """
        fixed = self.cfg["gap_threshold_minutes"]
        cadence = self.cadence_minutes()
        if not cadence:
            return fixed, None
        return max(fixed, int(cadence * 1.5)), cadence

    def gaps(self):
        lo = hhmm_to_minutes(self.cfg["gap_scan_window"][0])
        hi = hhmm_to_minutes(self.cfg["gap_scan_window"][1])
        thr, _cadence = self.gap_threshold()
        pts = sorted({e["minutes"] for e in self.records if lo <= e["minutes"] <= hi})
        out = []
        for a, b in zip(pts, pts[1:]):
            if b - a >= thr:
                out.append((a, b, b - a))
        return out

    def anchor_slices(self, anchors, phases):
        w = self.cfg["anchor_window_minutes"]
        out = []
        for a in anchors:
            if a["phase"] not in phases:
                continue
            procs = a.get("procs") or []
            applies = (not procs) or (self.name in procs)
            at = hhmm_to_minutes(a["at"])
            hits = [e for e in self.records if at - w <= e["minutes"] <= at + w]
            out.append((a, hits, applies))
        return out

    def selfcheck_blocks(self):
        """자가점검을 기동 회차별로 쪼갠다 (config 라인이 회차의 시작)."""
        blocks, cur = [], []
        for ln in self.selfcheck:
            if (
                ln.startswith("[OK ] config")
                or ln.startswith("[FAIL] config")
                or ln.startswith("[WARN] config")
            ):
                if cur:
                    blocks.append(cur)
                cur = []
            cur.append(ln)
        if cur:
            blocks.append(cur)
        return blocks


def m2hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


# ---------------------------------------------------------------- JSON 요약
def summarize_json(path: Path, max_chars=2200) -> str:
    raw = read_text(path)
    if raw.startswith("(읽기 실패)"):
        return raw
    try:
        obj = json.loads(raw)
    except Exception:  # noqa: BLE001
        return truncate(raw, max_chars)
    dumped = json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=False)
    if len(dumped) <= max_chars:
        return dumped

    # 너무 크면 1~2단계만 남기고 접는다
    def fold(o, depth=0):
        if depth >= 2:
            if isinstance(o, dict):
                return f"<dict {len(o)}키: {', '.join(list(o)[:8])}…>"
            if isinstance(o, list):
                return f"<list {len(o)}건>"
            return o
        if isinstance(o, dict):
            return {k: fold(v, depth + 1) for k, v in o.items()}
        if isinstance(o, list):
            head = [fold(v, depth + 1) for v in o[:3]]
            return head + ([f"…외 {len(o) - 3}건"] if len(o) > 3 else [])
        return o

    return truncate(json.dumps(fold(obj), ensure_ascii=False, indent=1), max_chars)


# ---------------------------------------------------------------- dev_memory
def devmemory_section(root: Path, day: _date) -> list[str]:
    out = ["", "## 6. dev_memory", ""]
    dm = root / "dev_memory"
    if not dm.is_dir():
        out.append("- dev_memory/ 없음 — SYSTEM.md §7 위반 상태")
        return out
    for fname in ("DECISION_LOG.md", "NEXT_TODO.md"):
        p = dm / fname
        if not p.exists():
            out.append(f"- **{fname}**: 없음")
            continue
        st = p.stat()
        mt = datetime.fromtimestamp(st.st_mtime, KST)
        fresh = "오늘 갱신됨" if mt.date() == day else f"마지막 갱신 {mt:%Y-%m-%d %H:%M}"
        out.append(f"### {fname} — {fmt_bytes(st.st_size)} · {fresh}")
        text = read_text(p)
        heads = [ln.strip() for ln in text.splitlines() if re.match(r"^#{1,3} ", ln)]
        if heads:
            out.append("")
            out.append("최근 항목 헤딩(끝에서 12개):")
            out.append("```")
            out.extend(heads[-12:])
            out.append("```")
        if fname == "NEXT_TODO.md":
            open_items = [
                ln.strip() for ln in text.splitlines() if re.match(r"^\s*[-*]\s*\[ \]", ln)
            ]
            out.append("")
            out.append(f"미완료 체크박스: **{len(open_items)}건** (끝에서 30건)")
            if open_items:
                out.append("```")
                out.extend(truncate(x, 200) for x in open_items[-30:])
                out.append("```")
        tail = text[-2500:]
        out.append("")
        out.append(f"<details><summary>{fname} 꼬리 2.5KB</summary>")
        out.append("")
        out.append("```")
        out.append(tail)
        out.append("```")
        out.append("")
        out.append("</details>")
        out.append("")
    return out


# ---------------------------------------------------------------- 본문 생성
def build(root: Path, day: _date, phase: str, cfg: dict) -> str:
    d = day.strftime("%Y%m%d")
    D = day.strftime("%Y-%m-%d")
    phases = {
        "pre": ["pre"],
        "intra": ["pre", "intra"],
        "post": ["pre", "intra", "post"],
        "all": ["pre", "intra", "post"],
    }[phase]
    now = datetime.now(KST)
    L: list[str] = []
    A = L.append

    A(f"# MESSIAH 증거 다이제스트 — {D} / {phase.upper()}")
    A("")
    A(f"- 생성 {now:%Y-%m-%d %H:%M:%S} KST · 리포 `{root}`")
    A(f"- 점검 범위: {', '.join(phases)} (장전=pre / 장중=intra / 장후=post)")
    A("")

    # ---- 1. 코드 상태 ----
    A("## 1. 코드·커밋 상태")
    A("")
    head = run_git(root, ["rev-parse", "--short", "HEAD"])
    branch = run_git(root, ["rev-parse", "--abbrev-ref", "HEAD"])
    status = run_git(root, ["status", "--porcelain", "--untracked-files=all"])
    dirty = [ln for ln in status.splitlines() if ln.strip()]

    # **개행 잡음과 실제 변경을 갈라 적는다** (2026-08-14 F-7).
    #
    # 리포 파일이 CRLF인데 `core.autocrlf`가 미설정이라, 정규화 설정이 다른 환경에서
    # `git diff`를 돌리면 전 파일이 통째로 "변경됨"으로 잡힌다. 그 숫자("179건")가
    # **4거래일간 재측정 없이 이월되며** paper 승격 차단의 근거로 쓰였다 — 실재하지 않는
    # 부채였다. 한쪽만 적으면 반대 방향 오해가 생기므로 **두 값을 나란히** 적는다.
    raw_diff = run_git(root, ["diff", "--numstat", "HEAD", "--", "src", "scripts"])
    real_diff = run_git(
        root, ["diff", "--numstat", "--ignore-all-space", "HEAD", "--", "src", "scripts"]
    )
    raw_files = len([ln for ln in raw_diff.splitlines() if ln.strip()])
    real_files = len([ln for ln in real_diff.splitlines() if ln.strip()])
    noise = raw_files - real_files

    A(f"- HEAD `{head}` · 브랜치 `{branch}` · 작업트리 미커밋 {len(dirty)}건(untracked 포함)")
    A(
        f"- `src/`+`scripts/` 실제 변경 **{real_files}파일**"
        + (f" · 개행 잡음 {noise}파일(CRLF — 부채 아님)" if noise else " · 개행 잡음 없음")
    )
    if dirty:
        A("```")
        L.extend(dirty[:40])
        if len(dirty) > 40:
            A(f"… 외 {len(dirty) - 40}건")
        A("```")
    nxt = (day + timedelta(days=1)).strftime("%Y-%m-%d")
    todays = run_git(
        root, ["log", "--oneline", "--no-decorate", f"--since={D} 00:00", f"--until={nxt} 00:00"]
    )
    A("")
    A(f"**당일({D}) 커밋**")
    A("```")
    A(todays if todays.strip() else "(당일 커밋 없음)")
    A("```")
    A("")
    A("**직전 커밋 10건**")
    A("```")
    A(run_git(root, ["log", "--oneline", "--no-decorate", "-10"]))
    A("```")
    A("")

    # ---- 2. 프로세스 로그 ----
    A("## 2. 프로세스 세션 경계 · 로그 개황")
    A("")
    digests: dict[str, LogDigest] = {}
    targets = dict(cfg["process_logs"])
    targets.update(cfg["rolling_logs"])
    A("| 프로세스 | 파일 | 크기 | 최종기록 | 라인 | SessionStart | SessionEnd |")
    A("|---|---|---|---|---|---|---|")
    for name, pat in targets.items():
        p = root / pat.format(d=d, D=D)
        dg = LogDigest(name, p, cfg, day).scan()
        digests[name] = dg
        if not dg.exists:
            A(f"| `{name}` | {pat.format(d=d, D=D)} | **없음** | — | — | — | — |")
            continue
        starts, ends = dg.session_markers()
        if not dg.json_lines:
            s_txt = e_txt = "n/a (비-JSON 로그)"
        else:
            s_txt = (
                " / ".join(f"{e['hhmm']}(sha={e['extra'].get('git_sha', '?')})" for e in starts[:3])
                or "—"
            )
            e_txt = (
                " / ".join(f"{e['hhmm']}({truncate(e['msg'], 20)})" for e in ends[:3])
                or "**없음 ⚠**"
            )
        A(
            f"| `{name}` | {p.name} | {fmt_bytes(dg.size)} | {dg.mtime} | {dg.total_lines} | {s_txt} | {e_txt} |"
        )
    A("")
    A("> SessionEnd 가 비어 있으면 비정상 종료를 의심한다 (SYSTEM.md R13 · 금지계명 14).")
    A(
        "> SessionStart 가 2회 이상이면 중복 기동 또는 크래시 후 재기동이다. sha 가 HEAD와 다르면 옛 코드로 돈 것이다."
    )
    A("")

    # 자가점검 라인 — 기동 회차별로, 비-OK는 전량 노출
    for name in ("l1_daily", "g2_daily"):
        dg = digests.get(name)
        if not dg or not dg.exists or not dg.selfcheck:
            continue
        blocks = dg.selfcheck_blocks()
        bad = [
            x
            for x in dg.selfcheck
            if not x.startswith("[OK ]") and not x.startswith("self-check: PASS")
        ]
        A(
            f"### `{name}` 기동 자가점검 — 기동 {len(blocks)}회 · 총 {len(dg.selfcheck)}행 · 비-OK {len(bad)}행"
        )
        A("")
        if bad:
            A("**비-OK 라인 전량**")
            A("```")
            L.extend(bad[:30])
            A("```")
            A("")
        A(f"<details><summary>기동 회차별 전문 ({len(blocks)}회)</summary>")
        A("")
        for i, blk in enumerate(blocks, 1):
            A(f"— 기동 {i}회차")
            A("```")
            L.extend(blk[:16])
            A("```")
        A("")
        A("</details>")
        A("")
    for name, dg in digests.items():
        if dg.exists and dg.raw_preamble and name not in ("l1_daily", "g2_daily"):
            A(f"<details><summary>`{name}` 비-JSON 라인 {len(dg.raw_preamble)}행</summary>")
            A("")
            A("```")
            L.extend(dg.raw_preamble[:40])
            A("```")
            A("")
            A("</details>")
            A("")

    # ---- 3. 레벨/태그 집계 ----
    A("## 3. 로그 레벨·태그 집계")
    A("")
    for name, dg in digests.items():
        if not dg.exists or not dg.json_lines:
            continue
        lv = ", ".join(
            f"{k}={v}"
            for k, v in sorted(
                dg.level_counts.items(),
                key=lambda kv: LEVEL_ORDER.index(kv[0]) if kv[0] in LEVEL_ORDER else 99,
            )
        )
        A(
            f"### `{name}` — JSON {dg.json_lines}행 · {lv}"
            + (f" · 파싱실패 {dg.parse_errors}" if dg.parse_errors else "")
        )
        A("")
        # ERROR 이상은 태그별 전량 요약
        sev = [(k, v) for k, v in dg.by_level_tag.items() if k[0] in ("CRITICAL", "FATAL", "ERROR")]
        if sev:
            A("**ERROR 이상**")
            A("")
            A("| level | tag | 건수 | 최초 | 최종 | 대표 msg |")
            A("|---|---|---|---|---|---|")
            for (level, tag), items in sorted(sev, key=lambda kv: -len(kv[1])):
                A(
                    f"| {level} | `{tag}` | {len(items)} | {items[0]['hhmm']} | {items[-1]['hhmm']} | "
                    f"{truncate(items[0]['msg'], cfg['msg_truncate'])} |"
                )
            A("")
            for (level, tag), items in sorted(sev, key=lambda kv: -len(kv[1]))[:8]:
                n = cfg["max_error_samples_per_tag"]
                A(f"<details><summary>{level}/{tag} 샘플 {min(n, len(items))}건</summary>")
                A("")
                A("```")
                for e in items[:n]:
                    A(f"{e['hhmm']} {e['msg']}")
                    if e["extra"]:
                        A(f"    extra={truncate(json.dumps(e['extra'], ensure_ascii=False), 400)}")
                A("```")
                A("")
                A("</details>")
                A("")
        warn = [(k, v) for k, v in dg.by_level_tag.items() if k[0] in ("WARNING", "WARN")]
        if warn:
            warn.sort(key=lambda kv: -len(kv[1]))
            A(f"**WARNING — 태그 {len(warn)}종 (상위 {min(len(warn), cfg['max_warn_tags'])})**")
            A("")
            A("| tag | 건수 | 최초 | 최종 | 대표 msg |")
            A("|---|---|---|---|---|")
            for (level, tag), items in warn[: cfg["max_warn_tags"]]:
                A(
                    f"| `{tag}` | {len(items)} | {items[0]['hhmm']} | {items[-1]['hhmm']} | "
                    f"{truncate(items[0]['msg'], cfg['msg_truncate'])} |"
                )
            A("")
        info_tags = sorted(((t, c) for t, c in dg.tag_counts.items()), key=lambda kv: -kv[1])[:20]
        A("**전체 태그 상위 20** — " + ", ".join(f"`{t}`×{c}" for t, c in info_tags))
        A("")

    # ---- 4. 반드시 인용할 태그 ----
    A("## 4. 항상 인용하는 태그")
    A("")
    any_q = False
    for name, dg in digests.items():
        if not dg.exists or not dg.quoted:
            continue
        any_q = True
        A(f"### `{name}`")
        A("```")
        for tag, items in dg.quoted.items():
            A(f"--- {tag} ×{len(items)}")
            for e in items[:6]:
                A(f"{e['hhmm']} [{e['level']}] {truncate(e['msg'], 240)}")
                if e["extra"]:
                    A(f"    {truncate(json.dumps(e['extra'], ensure_ascii=False), 300)}")
            if len(items) > 6:
                A(f"    … 외 {len(items) - 6}건")
        A("```")
        A("")
    if not any_q:
        A("(해당 태그 없음)")
        A("")

    # ---- 5. 타임라인 앵커 · 공백 ----
    A("## 5. 타임라인 앵커 · 로그 공백")
    A("")
    for name, dg in digests.items():
        if not dg.exists or not dg.records:
            continue
        A(f"### `{name}` 앵커 (±{cfg['anchor_window_minutes']}분)")
        A("")
        A("| 시각 | 국면 앵커 | 창 내 이벤트 | 대표 |")
        A("|---|---|---|---|")
        for a, hits, applies in dg.anchor_slices(cfg["anchors"], phases):
            rep = "—"
            if hits:
                sev_hit = [
                    h
                    for h in hits
                    if h["level"] in ("ERROR", "CRITICAL", "FATAL", "WARNING", "WARN")
                ]
                pick = sev_hit[0] if sev_hit else hits[0]
                rep = (
                    f"{pick['hhmm']} [{pick['level']}] `{pick['tag']}` {truncate(pick['msg'], 120)}"
                )
            if not applies:
                label = f"_{a['label']} (이 프로세스 범위 밖)_"
            else:
                label = a["label"] + ("" if hits else " ⚠")
            A(f"| {a['at']} | {label} | {len(hits)} | {rep} |")
        A("")
        gaps = dg.gaps() if name in ("l1_daily", "g2_daily") else []
        if name in ("l1_daily", "g2_daily"):
            thr, cadence = dg.gap_threshold()
            # 주기를 밝힌다 — 임계가 왜 그 값인지 사람이 되짚을 수 있어야 한다(2026-08-14 F-13).
            note = f" · 설계 주기 {cadence}분에서 유도" if cadence else " · 고정 임계"
            A(
                f"**{cfg['gap_scan_window'][0]}~{cfg['gap_scan_window'][1]} 구간 "
                f"{thr}분 이상 공백: {len(gaps)}건**{note}"
            )
        if gaps:
            A("")
            A("| 시작 | 재개 | 공백(분) |")
            A("|---|---|---|")
            for a, b, g in gaps[:20]:
                A(f"| {m2hhmm(a)} | {m2hhmm(b)} | {g} |")
        A("")
        first = dg.records[0]
        last = dg.records[-1]
        A(
            f"- 최초 JSON 기록 {first['hhmm']} `{first['tag']}` / 최종 {last['hhmm']} `{last['tag']}`"
        )
        A("")

    # ---- 6. dev_memory ----
    L.extend(devmemory_section(root, day))

    # ---- 7. 산출물 존재 점검 ----
    A("## 7. 산출물 존재 점검")
    A("")
    A("| 국면 | 기대 산출물 | 상태 | 크기 | 최종기록 |")
    A("|---|---|---|---|---|")
    for ph in phases:
        for pat in cfg["expected_artifacts"].get(ph, []):
            rel = pat.format(d=d, D=D)
            p = root / rel
            if p.exists():
                st = p.stat()
                mt = datetime.fromtimestamp(st.st_mtime, KST)
                stale = " ⚠오래됨" if mt.date() != day else ""
                A(f"| {ph} | `{rel}` | 있음{stale} | {fmt_bytes(st.st_size)} | {mt:%m-%d %H:%M} |")
            else:
                A(f"| {ph} | `{rel}` | **없음 ⚠** | — | — |")
    A("")

    # ---- 8. JSON 산출물 요약 ----
    A("## 8. 산출물 JSON 요약")
    A("")
    for pat in cfg["json_artifacts"]:
        rel = pat.format(d=d, D=D)
        p = root / rel
        if not p.exists():
            continue
        mt = datetime.fromtimestamp(p.stat().st_mtime, KST)
        A(f"### `{rel}` — {fmt_bytes(p.stat().st_size)} · {mt:%m-%d %H:%M:%S}")
        A("```json")
        A(summarize_json(p))
        A("```")
        A("")

    # ---- 9. 자동 적신호 ----
    A("## 9. 자동 적신호 (기계가 먼저 잡은 것 — 분석의 출발점이지 결론이 아니다)")
    A("")
    flags: list[str] = []
    snap = root / "logs" / "status_snapshot.json"
    if snap.exists():
        try:
            s = json.loads(read_text(snap))
            cv = s.get("code_version", {})
            if cv.get("stale"):
                flags.append(
                    f"코드 불일치: HEAD `{cv.get('head_git_sha')}` vs 실행 `{cv.get('process_git_sha')}` — {truncate(cv.get('summary', ''), 200)}"
                )
            for cname, c in (s.get("components") or {}).items():
                if str(c.get("state", "")).upper() not in ("OK",):
                    flags.append(
                        f"컴포넌트 `{cname}` 상태 {c.get('state')} — {truncate(c.get('detail', ''), 120)}"
                    )
            cb = s.get("circuit_breaker") or {}
            if cb.get("gateway_halted"):
                flags.append("게이트웨이 정지 상태(gateway_halted=true)")
            il = s.get("irrecoverable_loss") or {}
            if il and not il.get("clean", True):
                flags.append(
                    f"소급 불가 손실 {il.get('lost_items')}건 — {truncate(il.get('summary', ''), 150)}"
                )
            gen = s.get("generated_at_kst", "")
            m = TS_RE.search(gen)
            if m and m.group(1) != D:
                flags.append(f"status_snapshot 이 오늘 것이 아니다 (generated_at={gen})")
        except Exception as e:  # noqa: BLE001
            flags.append(f"status_snapshot.json 해석 실패: {e}")
    else:
        flags.append("status_snapshot.json 없음")

    for name, dg in digests.items():
        if not dg.exists:
            continue
        starts, ends = dg.session_markers()
        if starts and not ends and phase in ("post", "all"):
            flags.append(f"`{name}`: SessionStart 있고 SessionEnd 없음 — 비정상 종료 의심")
        if len(starts) > 1:
            flags.append(
                f"`{name}`: SessionStart {len(starts)}회 ({', '.join(e['hhmm'] for e in starts[:5])}) — 중복 기동/재기동 확인 필요"
            )
        # 거절은 적신호가 아니라 **정상 동작의 기록**이다 — 그래도 버리지 않고 표기한다
        # (거절이 늘면 정시 트리거 시각이 어긋났다는 신호다, 2026-08-10 실측).
        if getattr(dg, "refused_starts", None):
            notes = ", ".join(e["hhmm"] for e in dg.refused_starts[:5])
            flags.append(
                f"`{name}`: 기동 창 거절 {len(dg.refused_starts)}회 ({notes}) — 정상(기동으로 안 셈)"
            )
        shas = {e["extra"].get("git_sha") for e in starts if e["extra"].get("git_sha")}
        odd = {s for s in shas if head and not head.startswith(s) and not s.startswith(head)}
        if odd:
            flags.append(
                f"`{name}`: 기동 sha {', '.join(sorted(odd))} 가 HEAD `{head}` 와 다르다 — 옛 코드로 기동"
            )
        n_err = sum(v for k, v in dg.level_counts.items() if k in ("ERROR", "CRITICAL", "FATAL"))
        if n_err:
            flags.append(f"`{name}`: ERROR 이상 {n_err}건")
        recur = dg.quoted.get("FixVerificationRecurred") or []
        if recur:
            ids = sorted({e["extra"].get("fix_id", "?") for e in recur})
            flags.append(f"`{name}`: 수정 재발 {len(recur)}건 — fix_id={', '.join(ids)}")
        # 임계는 프로세스마다 다르다 — 그쪽 `gap_threshold()`가 이미 주기를 반영했으므로
        # 여기서 다시 2배를 곱하지 않는다. 2026-08-14엔 이 줄이 g2의 정상 30분 격자를
        # 8건이나 적신호로 올려 진짜 신호를 목록 아래로 밀어냈다(F-13).
        for a, b, g in dg.gaps() if name in ("l1_daily", "g2_daily") else []:
            flags.append(f"`{name}`: {m2hhmm(a)}~{m2hhmm(b)} {g}분 로그 공백")

    for ph in phases:
        for pat in cfg["expected_artifacts"].get(ph, []):
            rel = pat.format(d=d, D=D)
            if not (root / rel).exists():
                flags.append(f"산출물 누락({ph}): `{rel}`")

    # **실제 변경이 있을 때만** 올린다 (2026-08-14 F-7). 종전엔 작업트리 엔트리 수를 그대로
    # 적신호로 올려, 개행 잡음과 문서 수정까지 "계명 10 확인 필요"로 매일 찍혔다.
    if real_files:
        flags.append(
            f"`src/`+`scripts/` 미커밋 실제 변경 {real_files}파일 — "
            "금지계명 10(미커밋 수정 실전 반입 금지) 확인 필요"
        )

    if flags:
        for i, f in enumerate(dict.fromkeys(flags), 1):
            A(f"{i}. {f}")
    else:
        A("자동 탐지 적신호 없음. 그래도 §3~§5를 직접 읽고 판단할 것.")
    A("")
    A("---")
    A("")
    A(
        "*이 다이제스트는 원본이 아니라 요약이다. 특정 태그의 전량이 필요하면 "
        '`grep \'"tag": "TAGNAME"\' logs/l1_daily_' + d + ".log` 로 원본을 직접 열 것.*"
    )
    return "\n".join(L)


def load_config(root: Path) -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    over = root / "configs" / "dailycheck_anchors.json"
    if over.exists():
        try:
            user = json.loads(read_text(over))
            cfg.update(user)
            eprint(f"[collect_evidence] 설정 덮어씀: {over}")
        except Exception as e:  # noqa: BLE001
            eprint(f"[collect_evidence] 설정 무시(파싱 실패): {e}")
    return cfg


def main(argv=None):
    ap = argparse.ArgumentParser(description="MESSIAH 일일 점검 증거 수집기")
    ap.add_argument(
        "--phase",
        choices=["pre", "intra", "post", "all"],
        default="post",
        help="장전=pre / 장중=intra / 장후=post (기본 post)",
    )
    ap.add_argument("--date", default=None, help="YYYY-MM-DD 또는 YYYYMMDD (기본 오늘 KST)")
    ap.add_argument("--root", default=None, help="리포 루트 (기본 자동탐지)")
    ap.add_argument("--out", default=None, help="파일로 저장 (기본 stdout)")
    args = ap.parse_args(argv)

    start = Path(args.root) if args.root else Path(__file__).resolve().parent
    root = find_repo_root(start)
    if not (root / "logs").is_dir():
        eprint(f"[collect_evidence] 경고: {root}/logs 가 없다. --root 로 리포 루트를 지정하라.")
    day = parse_date(args.date)
    cfg = load_config(root)
    text = build(root, day, args.phase, cfg)

    if args.out:
        outp = Path(args.out)
        if not outp.is_absolute():
            outp = root / outp
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(text, encoding="utf-8")
        eprint(f"[collect_evidence] 저장: {outp} ({fmt_bytes(len(text.encode('utf-8')))})")
        print(str(outp))
    else:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
