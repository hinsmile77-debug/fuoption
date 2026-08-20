"""**반쪽짜리 하루**를 하루로 세지 않는다 (2026-08-19 장후 P1-3 / F-3·G-3).

## 왜 이 모듈이 생겼나

2026-08-19에 두 수집 프로세스가 09:50에 죽어 12:29에 되살아났다. 그날 장후 배치는 반나절이
비어 있는 입력으로 6단계를 완주했고, 산출물은 그 사실을 표시하는 필드 **없이** 정상
확정본으로 저장됐다:

    daily_integrity_20260819.json
      provisional      false                    ← 옳다. 그쪽은 "아직 안 만들어진 산출물"의 축이다
      series_coverage  ticks 61.2% · K2I 63.2% · regular 63.0% · weekly 70.8%
      feature_health_rolling  10m {days: [08-18, 08-19], judged: **true**}
      vol_scorecard           5m samples 208 (window_days 20)

**불완전일을 표시할 필드 자체가 없었다.** 그래서 10m·15m 퇴화 판정의 2일 창 중 하루가
반나절짜리인데 `judged: true`로 확정 판정이 났고, 20거래일 IC에도 그날 표본이 정상 가중으로
들어갔다. `dev_memory/DECISION_LOG.md` 2026-08-19 장중 ③이 예고한 바로 그 형태다 —
*"위험한 것은 이 피처가 그대로 아카이브되어 훗날 학습·백테스트 입력이 되는 것 — 그때 이
구간은 '정상 데이터'로 보인다."*

**이 오염은 되돌릴 수 없다.** 소급해서 「그날은 반쪽이었다」고 말해 줄 필드가 없기 때문이다.
이 모듈이 그 필드를 만들고, 롤링 창을 구성하는 소비처가 **전부 여기를 통해서만** 날짜
목록을 얻게 한다.

## 왜 소비처를 한 자리로 모으나 (G-3)

`series_coverage`는 오늘 5계열의 61~71%를 정확히 기록했다. 그런데 그 값을 읽는 코드는
등록부(`truncation-is-visible`) **하나뿐**이었고, 그 하나는 값을 읽어 자기 자신을
「실패」로 채점하는 데 썼다. 정작 오염을 막아야 할 롤링 소비자들은 커버리지를 조회조차
하지 않았다.

이 저장소가 네 번 반복한 실패 형태다(`ops/canonical_consumers.py`가 존재하는 이유):
**기록은 되는데 아무도 안 읽는 축.** 그래서 판정 로직을 소비처마다 복제하지 않고 여기
하나에 둔다.

## 임계가 95%인 이유

등록부 `truncation-is-visible`의 기준(`series_coverage_pct_min ≥ 95`)과 **같은 값**이다.
두 축이 다른 임계를 쓰면 「잘림은 보이는데 불완전일은 아닌 날」이 생기고, 그런 날은 사람이
어느 축을 믿어야 하는지 알 수 없다.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

DEFAULT_LOG_DIR = Path("logs")

# 이 아래로 떨어지면 그날은 불완전일이다 — 등록부 `truncation-is-visible`과 같은 값
# (모듈 docstring "임계가 95%인 이유").
MIN_COVERAGE_PCT = 95.0


def judge(
    *,
    coverages: Sequence[Any] = (),
    abnormal_exits: Sequence[Mapping[str, Any]] = (),
    min_coverage_pct: float = MIN_COVERAGE_PCT,
) -> tuple[bool, list[str], float | None]:
    """오늘이 불완전일인가 — (판정, 사유들, 계열 커버리지 최솟값).

    두 갈래 중 **하나라도** 걸리면 불완전일이다:

        ① 판정된 계열의 커버리지 최솟값 < `min_coverage_pct`
        ② `abnormal_exits`에 `mid_session` 건이 있다 (장중에 죽었다 돌아온 날)

    ②를 따로 두는 이유: 죽어 있던 구간이 짧아 커버리지는 95%를 넘는데 그 사이의 판단·
    주문 경로는 통째로 없는 날이 있다. 커버리지는 **적재**를 보고 이쪽은 **관측**을 본다.

    커버리지를 한 계열도 못 잰 날은 세 번째 값이 None이다 — 0.0이 아니다(L18). 그때
    ②만으로 판정하며, 그 사실은 사유 문장에 남는다.
    """
    measured = [
        float(item.coverage_pct)
        for item in coverages
        if getattr(item, "measured", False) and getattr(item, "expected", False)
    ]
    worst = round(min(measured), 1) if measured else None

    reasons: list[str] = []
    if worst is not None and worst < min_coverage_pct:
        thin = sorted(
            (
                (round(float(item.coverage_pct), 1), str(item.name))
                for item in coverages
                if getattr(item, "measured", False)
                and getattr(item, "expected", False)
                and float(item.coverage_pct) < min_coverage_pct
            )
        )
        detail = " · ".join(f"{name} {pct:g}%" for pct, name in thin[:5])
        reasons.append(f"계열 커버리지 {worst:g}% < 기준 {min_coverage_pct:g}% ({detail})")

    for item in abnormal_exits:
        if not item.get("mid_session"):
            continue
        reasons.append(
            f"{item.get('process', '?')} 장중 사망 {item.get('minutes_lost', 0)}분 "
            f"({item.get('died_at_kst', '?')}~{item.get('recovered_at_kst', '?')})"
        )

    return bool(reasons), reasons, worst


def load(log_dir: Path = DEFAULT_LOG_DIR) -> dict[date, bool | None]:
    """`logs/daily_integrity_*.json` → {날짜: 불완전일인가}. 축이 없는 옛 리포트는 None.

    None은 **판정 불가**다 — False가 아니다(L18). 이 축이 생기기 전(2026-08-19 이전)의
    날들을 「완전했다」로 세면 오염된 창이 조용히 통과한다. 다만 소비처는 그 None을
    「제외 대상 아님」으로 다룬다(`usable_days()` 참고) — 옛 날짜를 소급해 전부 버리면
    30m처럼 창이 좁은 축이 영영 판정 불가가 된다.

    `ops/loss_budget.load_daily_losses()`와 같은 파일을 읽지만 필요한 한 필드만 꺼낸다.
    """
    out: dict[date, bool | None] = {}
    for path in sorted(log_dir.glob("daily_integrity_*.json")):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            day = date.fromisoformat(report["date"])
        except (OSError, ValueError, KeyError):
            continue  # 깨진 파일 하나가 나머지 집계를 막지 않는다
        value = report.get("incomplete_day")
        out[day] = bool(value) if isinstance(value, bool) else None
    return out


def usable_days(
    days: Iterable[date],
    *,
    log_dir: Path = DEFAULT_LOG_DIR,
    known: Mapping[date, bool | None] | None = None,
) -> tuple[list[date], list[date]]:
    """롤링 창에 쓸 날짜를 가른다 — (쓸 수 있는 날, 제외한 날).

    **이 함수가 G-3의 자리다.** 롤링 창을 구성하는 소비처는 전부 여기를 통해서만 날짜
    목록을 얻는다: `ops/feature_health_rolling.judge()`(3거래일) ·
    `scripts/run_vol_scorecard.py`(20거래일) · `ops/fix_verification`의 판정 불가 누적.

    `known`으로 아직 파일에 안 쓰인 오늘 판정을 끼워 넣을 수 있다 — 오늘 리포트를
    **만드는 중**에 이 함수를 부르는 경로(`build_report()`)가 있기 때문이다.

    판정 불가(None)는 **제외하지 않는다**(위 `load()` docstring). 제외는 「불완전하다고
    확인된 날」에만 한다.
    """
    flags = dict(load(log_dir))
    if known:
        flags.update(known)
    usable: list[date] = []
    excluded: list[date] = []
    for day in days:
        (excluded if flags.get(day) is True else usable).append(day)
    return usable, excluded
