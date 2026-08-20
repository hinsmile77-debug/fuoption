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


def _derive_from_stored(report: Mapping[str, Any], min_coverage_pct: float) -> bool | None:
    """저장된 리포트의 **입력**으로 불완전일을 계산한다 — 불리언이 없을 때 (2026-08-20 J-3b).

    ## 왜 필요한가 — 오염을 막으려 만든 축이 정작 그 오염을 못 막았다

    `incomplete_day` 필드는 2026-08-20부터 쓰인다. 그런데 이 축을 만든 이유인 **2026-08-19**
    리포트는 그 전에 쓰여 필드가 없다. 그래서 `load()`가 None을 돌려주고, `usable_days()`는
    None을 「제외 대상 아님」으로 다루므로(그 판단 자체는 옳다) 08-19가 창에 그대로 남았다:

        2026-08-20 실전 산출물
          feature_health_rolling  days=[08-18, 08-19, 08-20]  excluded_days=[]
          vol_scorecard           window_days=20              excluded_days=[]

    08-19 장후가 *"이 오염은 되돌릴 수 없다 — 소급해서 「그날은 반쪽이었다」고 말해 줄 필드가
    없기 때문이다"* 라고 적었을 때, 그 문장은 **불리언 필드**에 관한 것이었다. 판정의 **입력**
    (`series_coverage`)은 그날 리포트에 처음부터 다 있었다 — 08-19는 5계열 최솟값 61.2%다.

    ## 이것은 과거 판정을 뒤집는 것이 아니다 (R18)

    뒤집는 것은 「그날 그 축이 내린 판정」을 바꾸는 일이다. 여기엔 그런 판정이 **없었다** —
    축 자체가 없었다. 하는 일은 원래 있던 데이터에 오늘의 정의를 적용해 **롤링 창에 넣을지
    말지**를 정하는 것뿐이고, 저장된 파일은 한 바이트도 안 건드린다(그날의 채점 기록은
    나중에 덮지 않는다는 방침 유지).

    ## 못 재는 것과 재서 괜찮은 것을 가른다

    `series_coverage`조차 없는 옛 리포트(2026-08-06 이전)는 여전히 **None**이다 — 계산할
    입력이 없는 것과 계산해 보니 온전한 것은 다르다(L18). None은 제외되지 않으므로 그
    시절 날들이 창에서 통째로 사라지지 않는다.

    **장중 사망(`abnormal_exits`의 `mid_session`)은 여기서 안 본다.** 그 필드도 2026-08-20
    이후에만 채워지고, 그 전 리포트의 `abnormal_exits`는 세션 단위 판정 이전이라 빈 배열이
    많다 — 없는 것을 근거로 「온전했다」고 말하게 된다. 커버리지 하나로도 08-19는 잡힌다.
    """
    coverages = report.get("series_coverage")
    if not isinstance(coverages, list):
        return None
    measured = [
        float(item["coverage_pct"])
        for item in coverages
        if isinstance(item, dict)
        and item.get("measured")
        and item.get("expected", True)
        and isinstance(item.get("coverage_pct"), (int, float))
    ]
    if not measured:
        return None
    return min(measured) < min_coverage_pct


def load(
    log_dir: Path = DEFAULT_LOG_DIR, *, min_coverage_pct: float = MIN_COVERAGE_PCT
) -> dict[date, bool | None]:
    """`logs/daily_integrity_*.json` → {날짜: 불완전일인가}.

    저장된 `incomplete_day` 불리언이 있으면 그것이 정본이다. 없으면 그 리포트의 **입력**
    (`series_coverage`)으로 계산한다(`_derive_from_stored`) — 축이 생기기 전의 날도
    롤링 창에서 가려낼 수 있어야 하기 때문이다(2026-08-20 J-3b).

    계산할 입력조차 없으면 None(**판정 불가**)이다 — False가 아니다(L18). 그 시절 날들을
    「완전했다」로 세면 오염된 창이 조용히 통과하고, 반대로 전부 버리면 30m처럼 창이 좁은
    축이 영영 판정 불가가 된다. 그래서 None은 제외하지 않는다(`usable_days()` 참고).

    `ops/loss_budget.load_daily_losses()`와 같은 파일을 읽지만 필요한 것만 꺼낸다.
    """
    out: dict[date, bool | None] = {}
    for path in sorted(log_dir.glob("daily_integrity_*.json")):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            day = date.fromisoformat(report["date"])
        except (OSError, ValueError, KeyError):
            continue  # 깨진 파일 하나가 나머지 집계를 막지 않는다
        value = report.get("incomplete_day")
        out[day] = (
            bool(value)
            if isinstance(value, bool)
            else _derive_from_stored(report, min_coverage_pct)
        )
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
    확인된 날」에만 한다 — 저장된 불리언이든 저장된 커버리지에서 계산한 것이든.
    """
    flags = dict(load(log_dir))
    if known:
        flags.update(known)
    usable: list[date] = []
    excluded: list[date] = []
    for day in days:
        (excluded if flags.get(day) is True else usable).append(day)
    return usable, excluded
