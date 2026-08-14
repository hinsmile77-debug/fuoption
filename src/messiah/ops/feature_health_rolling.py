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
from typing import Any, Iterable, Sequence

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
    for old in sorted(days)[:-keep_days]:
        days.pop(old, None)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"days": days}, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
    except OSError:
        return None


def judge(
    *,
    day: date,
    path: Path = DEFAULT_PATH,
    window_days: int = DEFAULT_WINDOW_DAYS,
    min_samples: int,
) -> list[RollingVerdict]:
    """`day` 이하의 최근 `window_days`거래일을 합산해 Horizon별로 판정한다.

    창은 **파일에 실제로 있는 날**로 센다 — 달력상 거래일이 아니라. 수집이 안 돈 날을
    창에 넣으면 그 자리가 0표본으로 비어 판정이 늦어지기만 한다.
    """
    days: dict[str, Any] = _load(path).get("days") or {}
    stamp = day.isoformat()
    usable = sorted(d for d in days if d <= stamp)[-window_days:]
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
            )
        )
    return out


def _common(groups: Iterable[Sequence[str] | None]) -> tuple[str, ...]:
    sets = [set(g or ()) for g in groups]
    if not sets:
        return ()
    common = set.intersection(*sets) if len(sets) > 1 else sets[0]
    return tuple(sorted(common))
