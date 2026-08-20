"""피처 퇴화 판정을 **하루에 가두지 않는다** — 다일 누적 (2026-08-14 G-9).

## 왜 필요한가

30m는 하루 **15봉**이 물리적 상한이다(2026-08-14 실측: `1m=410 → 30m=15`). 퇴화 판정
하한은 30표본이므로 **어떤 날에도 못 넘는다.** 임계를 낮추면 오탐이 늘고, 그대로 두면
영원히 판정 불가다 — 둘 다 답이 아니다.

그 결과가 2026-08-14 리포트의 *"30m 피처 퇴화 0건(14표본)"* 이었다. 가장 위험한 Horizon에
대한 가장 안심되는 문장이 **매일** 나오고 있었다. F-C가 그 문장을 "판정 보류"로 바꿔
거짓말을 멈췄고, 이 모듈은 그다음 질문에 답한다 — **그럼 언제 판정하나.**

## 답: 축을 하루에서 N거래일로 옮긴다

30m도 3거래일이면 45봉이라 하한 30을 넘는다. 이 발상은 이미 이 저장소에 있다 —
`ops/fix_verification`의 *"N거래일 연속 기준 충족"* 이 정확히 같은 구조다. 새 개념이
아니라 **같은 패턴을 퇴화 검사에도 적용**하는 것이다.

## 퇴화는 **교집합**으로 센다

N일 중 하루만 상수였던 피처는 조용한 장의 흔적일 수 있다. "세션 내내 죽어 있었다"를
N세션으로 늘리면 **모든 날에 죽어 있었다**가 되어야 한다 — 합집합으로 세면 창을 넓힐수록
퇴화가 늘어나는 이상한 축이 된다.

## 롤 경계가 창 안에 있으면 그 사실을 싣는다

심볼이 바뀌면 피처의 성질도 바뀔 수 있다(유동성·만기까지 잔존기간). 판정을 막지는 않되
`symbols`에 남겨 사람이 그 창의 판정을 어떻게 읽을지 정할 수 있게 한다 — 조용히 섞지
않는다(R10).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from messiah.ops import incomplete_days

DEFAULT_PATH = Path("logs") / "feature_health_rolling.json"

# 몇 거래일을 합산하는가. 3일이면 30m이 45봉으로 하한 30을 넘는다 — **가장 느린 Horizon이
# 기준이다.** 더 늘리면 판정이 그만큼 옛 사실을 반영하게 되고, 줄이면 30m이 다시 판정
# 불가로 돌아간다.
DEFAULT_WINDOW_DAYS = 3


@dataclass(frozen=True)
class RollingVerdict:
    """한 Horizon의 다일 누적 판정."""

    horizon: str
    days: tuple[str, ...]
    samples: int
    judged: bool
    always_nan: tuple[str, ...]
    constant: tuple[str, ...]
    symbols: tuple[str, ...] = field(default_factory=tuple)
    # **창에서 빼낸 날** (2026-08-19 F-3). 뺐다는 사실을 안 남기면 `days`가 짧아진 이유를
    # 아무도 모른다 — 표본이 준 것과 그날 수집이 없던 것이 구분되지 않는다(L18).
    excluded_days: tuple[str, ...] = field(default_factory=tuple)

    @property
    def degenerate_count(self) -> int:
        return len(self.always_nan) + len(self.constant)

    @property
    def spans_rollover(self) -> bool:
        """창 안에서 심볼이 바뀌었는가 — 판정을 막지는 않되 드러낸다."""
        return len(self.symbols) > 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "horizon": self.horizon,
            "days": list(self.days),
            "samples": self.samples,
            "judged": self.judged,
            "always_nan": list(self.always_nan),
            "constant": list(self.constant),
            "symbols": list(self.symbols),
            "spans_rollover": self.spans_rollover,
            "excluded_days": list(self.excluded_days),
        }


def _load(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def record_day(
    healths: Iterable[Any],
    *,
    symbol: str,
    day: date,
    path: Path = DEFAULT_PATH,
    keep_days: int = 40,
) -> Path | None:
    """그날의 Horizon별 표본·퇴화 목록을 누적 파일에 **덮어쓴다**(그날 것만).

    같은 날 두 번 불려도 마지막 값이 남는다 — 장중 재기동이 실제로 그 경우다.
    실패해도 예외를 올리지 않는다: 관측 보조가 종료 절차를 죽이면 본말전도다.

    ## `keep_days` 는 이 저장소의 유일한 자동 삭제다 (2026-08-19 안전장치)

    산출물 보관정책을 훑다가 알았다 — 점검 산출물 전체에서 **파일을 스스로 버리는 코드는
    여기 한 곳뿐**이다. 그런데 안전장치가 없었다. 셋을 넣는다:

    1. **`keep_days < 1` 이면 아무것도 안 버린다.** 0을 「전부 지워라」로 읽지 않는다.
       현행 `days[:-keep_days]` 는 `keep_days=0` 일 때 `[:-0]` == `[:0]` == `[]` 이라
       우연히 안전했지만, **음수면 앞에서부터 버린다**(`keep_days=-1` → 가장 오래된 1일
       삭제). 우연에 기대지 않는다.
    2. **버린 날짜를 인쇄한다.** 조용한 정리는 사고가 나도 아무도 모른다 — 이 파일이
       30m 퇴화 판정의 **유일한** 누적 상태이고, 잘리면 그 Horizon이 며칠간 판정 불가로
       돌아간다(2026-08-18에 누적 1일이라 15m·30m이 `unmeasured` 2건을 만든 그 일).
    3. **mtime이 아니라 날짜 키로 자른다.** `days`의 키가 `YYYY-MM-DD` 라 이미 그렇다 —
       파일을 복사하거나 백업에서 되돌려도 「어느 날 관측인가」는 안 바뀐다. 명시해 둔다.

    40일인 이유는 `window_days=3`(판정 창)보다 훨씬 길게 둬서 **창을 넓히는 실험이
    데이터 부족으로 막히지 않게** 하기 위해서다. 읽는 비용은 40일치가 수십 KB다.
    """
    stamp = day.isoformat()
    payload = _load(path)
    days: dict[str, Any] = payload.get("days") if isinstance(payload.get("days"), dict) else {}
    days[stamp] = {
        "symbol": symbol,
        "horizons": {
            health.horizon: {
                "samples": int(getattr(health, "samples", 0) or 0),
                "always_nan": list(getattr(health, "always_nan", []) or []),
                "constant": list(getattr(health, "constant", []) or []),
            }
            for health in healths
        },
    }
    # 오래된 날은 버린다 — 이 파일이 무한정 커지면 매일 읽는 비용이 된다.
    # 자르는 기준은 **날짜 키**(`YYYY-MM-DD`)지 mtime이 아니다. 백업에서 되돌린 파일도
    # 어느 날 관측인지는 안 바뀐다.
    dropped: list[str] = []
    if keep_days >= 1:
        dropped = sorted(days)[:-keep_days]
        for old in dropped:
            days.pop(old, None)
    # keep_days < 1 은 「전부 지워라」가 아니라 「지우지 마라」다. 0을 파괴로 읽지 않는다.

    # 폐기 흔적은 **이 파일 안에** 남긴다. 이 모듈은 로거를 안 쓴다(종료 절차를 죽이지
    # 않으려고 예외도 안 올리는 설계다). 로그에 적으면 로테이션이 지우지만 이 파일은
    # 2026-08-19부터 git 추적이라 흔적이 커밋 이력에 남는다 — 더 나은 자리다.
    history: list[Any] = payload.get("pruned") if isinstance(payload.get("pruned"), list) else []
    if dropped:
        history.append({"at": stamp, "keep_days": keep_days, "dropped": dropped})
    history = history[-20:]  # 흔적이 본문보다 커지지 않게
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        body: dict[str, Any] = {"days": days}
        if history:
            body["pruned"] = history
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
    except OSError:
        return None


def judge(
    *,
    day: date,
    path: Path = DEFAULT_PATH,
    window_days: int = DEFAULT_WINDOW_DAYS,
    min_samples: int,
    incomplete_known: Mapping[date, bool] | None = None,
    log_dir: Path | None = None,
) -> list[RollingVerdict]:
    """`day` 이하의 최근 `window_days`거래일을 합산해 Horizon별로 판정한다.

    창은 **파일에 실제로 있는 날**로 센다 — 달력상 거래일이 아니라. 수집이 안 돈 날을
    창에 넣으면 그 자리가 0표본으로 비어 판정이 늦어지기만 한다.

    ## 불완전일은 창에 안 넣는다 (2026-08-19 F-3)

    2026-08-19에 두 프로세스가 09:50~12:29 죽어 커버리지가 61%였다. 그런데 10m·15m의 롤링
    창은 **2일**이고 그중 하루가 그날이었는데 `judged: true`로 확정 판정이 났다 — 창의
    절반이 반나절짜리인 채로. 표본 수(`samples`)만 보면 하한을 넘으니 통과이고, 그 표본이
    **어느 하루에서 왔는가**를 묻는 자리가 없었다.

    제외하면 표본이 줄어 `judged: false`가 늘어난다. **그 자체는 정직해지는 것이다** —
    임계를 낮추지 않고 창을 넓혀 답하는 이 모듈의 원칙(docstring 상단)과 같은 방향이다.

    `incomplete_known`은 아직 파일에 안 쓰인 판정(주로 **오늘**)을 끼워 넣는 자리다 —
    `ops/integrity_report.build_report()`가 오늘 리포트를 만드는 중에 이 함수를 부른다.
    """
    days: dict[str, Any] = _load(path).get("days") or {}
    stamp = day.isoformat()
    # 창을 **자르기 전에** 불완전일을 뺀다. 자른 뒤에 빼면 창이 그만큼 짧아지기만 하고,
    # 그러면 사고가 난 주에 판정이 연쇄로 멈춘다 — 원하는 것은 "덜 보는 것"이 아니라
    # "온전한 날을 그만큼 더 거슬러 보는 것"이다.
    candidates = sorted(d for d in days if d <= stamp)
    parsed = [(d, date.fromisoformat(d)) for d in candidates]
    keep, dropped = incomplete_days.usable_days(
        [value for _stamp, value in parsed],
        # **누적 파일이 있는 곳에서 무결성 리포트도 찾는다** (2026-08-20). 모듈 기본값
        # (`logs/`)으로 접으면, tmp 디렉터리로 누적 파일을 만든 테스트가 **저장소의 진짜
        # `logs/`를 집어 든다** — 실제로 2026-08-20에 그 일이 났다: J-3b로 옛 리포트도
        # 판정되기 시작하자 tmp 픽스처를 쓰던 롤 경계 테스트가 진짜 08-14(33분 소실일)를
        # 제외당해 깨졌다. 운영에서는 `DEFAULT_PATH.parent == logs` 라 동작이 같다.
        # (`ops/integrity_report`가 `bar_dir`의 부모에서 계열 경로를 파생하는 것과 같은 규율.)
        log_dir=log_dir or path.parent,
        known=incomplete_known,
    )
    kept = {value.isoformat() for value in keep}
    usable = [d for d in candidates if d in kept][-window_days:]
    excluded = tuple(
        value.isoformat() for value in dropped if usable and value.isoformat() >= usable[0]
    )
    if not usable:
        return []

    horizons: list[str] = []
    for d in usable:
        for horizon in days[d].get("horizons") or {}:
            if horizon not in horizons:
                horizons.append(horizon)

    out: list[RollingVerdict] = []
    for horizon in sorted(horizons):
        entries = [
            (d, days[d]["horizons"][horizon])
            for d in usable
            if horizon in (days[d].get("horizons") or {})
        ]
        if not entries:
            continue
        samples = sum(int(e.get("samples", 0) or 0) for _d, e in entries)
        judged = samples >= min_samples
        out.append(
            RollingVerdict(
                horizon=horizon,
                days=tuple(d for d, _e in entries),
                samples=samples,
                judged=judged,
                # **교집합** — 모든 날에 죽어 있어야 퇴화다(모듈 docstring).
                always_nan=_common(e.get("always_nan") for _d, e in entries),
                constant=_common(e.get("constant") for _d, e in entries),
                symbols=tuple(
                    dict.fromkeys(
                        days[d].get("symbol") for d, _e in entries if days[d].get("symbol")
                    )
                ),
                excluded_days=excluded,
            )
        )
    return out


def _common(groups: Iterable[Sequence[str] | None]) -> tuple[str, ...]:
    sets = [set(g or ()) for g in groups]
    if not sets:
        return ()
    common = set.intersection(*sets) if len(sets) > 1 else sets[0]
    return tuple(sorted(common))
