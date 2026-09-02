"""후보의 다리를 **실제 거래 가능한 계약**으로 확정한다 — 주문 경로 4a-1 (2026-09-02 신설).

## 왜 필요한가 — 후보의 다리에는 종목코드가 없다

`evaluator.build_legs()`는 목표 델타로부터 `surface.find_strike_for_delta()`가 **보간해서**
낸 연속 행사가를 그대로 `StrategyLeg.strike`에 담는다(예: 1043.7). 그런데 시장에 상장된
행사가는 격자 위에만 있고(2.5 간격), `symbol_master.option_symbol()`은 **정확히 일치하는**
행사가만 찾는다 — 1043.7로는 항상 `None`이다. `StrategyLeg.symbol`이 그동안 계속 `None`이었던
이유이고, 그 필드 주석이 *"실제 체인 종목 매핑 전이면 None"*이라고 이미 적고 있었다.

## 스냅한 뒤 **반드시 재평가한다**

행사가를 1043.7 → 1045.0으로 옮기면 그 다리의 IV·프리미엄·그릭스가 전부 달라진다. 스냅 전
평가값을 그대로 들고 주문하면 **존재하지 않는 계약의 성적**을 주장하는 것이다. 그래서 이
모듈은 스냅된 다리로 `evaluate_candidate(legs=...)`를 다시 부른다 — 그 함수에 `legs` 인자를
연 이유가 이것이다.

재평가는 **NetER 부호까지 바꿀 수 있다.** 스냅 폭이 격자의 절반(1.25pt)까지 벌어지므로,
ATM 근처에서는 그 이동이 델타 0.05 수준의 차이가 된다. 재평가 결과가 안전규칙을 못 넘으면
그 후보는 **버린다** — 스냅이 후보를 죽이는 것은 정상 동작이다.

## 유동성은 여기서 한 번 더 본다

스마일은 이미 `chain_smile.is_liquid_leg()`로 걸러진 점들로 만들었지만, 그 필터는 **스마일
피팅용**이고 주문은 다른 질문이다: 그 행사가에 오늘 거래가 있었나, OI가 있나. 스냅 결과가
비유동 행사가로 떨어지면 이웃 격자를 본다(`max_snap_steps`).

## 이 모듈은 주문을 내지 않는다

`ResolvedCandidate`까지만 만든다. 주문 요청 생성은 `option_order.py`, 실제 제출은
`OrderGateway`다 — Ver 1.3 §0 "Options AI는 출력만 한다"의 경계를 그대로 지킨다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from messiah.core.messages import StrategyCandidate, StrategyLeg
from messiah.strategy.options.chain_smile import ChainLeg, is_liquid_leg
from messiah.strategy.options.evaluator import EvaluatorConfig, evaluate_candidate
from messiah.strategy.options.matrix import CandidateSpec
from messiah.strategy.options.surface import SmileFit

# 스냅 시 이웃 격자를 몇 칸까지 넓혀 볼 것인가. 0이면 가장 가까운 행사가만 본다.
# 2칸(±5.0pt)을 넘어가면 그건 "가까운 행사가로 옮긴 것"이 아니라 다른 후보다.
DEFAULT_MAX_SNAP_STEPS = 2

# **스냅 거리 상한 — 격자 몇 칸까지 허용하나** (2026-09-02 실측이 요구한 가드).
#
# 아카이브 재생에서 다리가 **60~206pt** 옮겨졌다. 상장 격자가 2.5pt이니 24~82칸이다. 원인은
# `surface.find_strike_for_delta()`가 스마일 다항식을 **폴링 창 밖으로 외삽**하기 때문이다 —
# 체인은 ATM ±10행사가(±25pt)만 수집하는데 날개 델타(0.15~0.30)가 그 밖을 가리킨다.
#
# 그 상태로 "가장 가까운 상장 행사가"를 고르면 **창 가장자리**가 뽑히고, 그건 목표와 아무
# 관계 없는 계약이다. 재평가 NetER이 +7.60 → +0.00으로 무너진 것이 그 증거다.
# 목표가 상장 범위 밖이면 **후보를 버린다** — 가장자리로 대충 옮기지 않는다.
DEFAULT_MAX_SNAP_GRID_STEPS = 1.5


@dataclass(frozen=True, slots=True)
class ResolvedLeg:
    """상장 계약으로 확정된 다리 — `symbol`이 항상 있다(없으면 만들지 않는다)."""

    leg: StrategyLeg  # 스냅된 행사가 + 종목코드가 채워진 것
    chain: ChainLeg  # 그 계약의 현재 시세(가격·OI·거래량)
    requested_strike: float  # 스냅 전 목표 행사가 — 얼마나 옮겼는지가 남아야 한다

    @property
    def snap_distance(self) -> float:
        return abs(self.leg.strike - self.requested_strike)


@dataclass(frozen=True, slots=True)
class ResolvedCandidate:
    """주문으로 옮길 수 있는 후보 — 스냅 후 **재평가된** 값을 담는다."""

    candidate: StrategyCandidate  # 재평가 결과(스냅된 행사가 기준)
    legs: tuple[ResolvedLeg, ...]
    before_snap: StrategyCandidate  # 스냅 전 — 비교용(로그·화면이 둘을 나란히 보여준다)

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(leg.leg.symbol or "" for leg in self.legs)

    @property
    def max_snap_distance(self) -> float:
        return max((leg.snap_distance for leg in self.legs), default=0.0)

    @property
    def net_er_shift(self) -> float:
        """재평가로 NetER이 얼마나 움직였나 — 스냅의 대가를 숫자로 남긴다."""
        return float(self.candidate.net_expected_return - self.before_snap.net_expected_return)


def listed_strikes(chain: Sequence[ChainLeg], option_type: str) -> list[float]:
    """그 옵션 종류의 **상장된** 행사가 목록(오름차순)."""
    return sorted({leg.strike for leg in chain if leg.option_type == option_type})


def grid_step(strikes: Sequence[float]) -> float | None:
    """상장 격자 폭 — 인접 행사가 차이의 **최빈값**(중앙값이 아니다).

    중앙값을 쓰면 창 가장자리의 큰 간격이 섞여 격자를 과대평가한다. 최빈값은 격자가
    균일한 구간이 압도적으로 많다는 사실을 그대로 쓴다.
    """
    if len(strikes) < 2:
        return None
    ordered = sorted(strikes)
    gaps = [round(b - a, 6) for a, b in zip(ordered, ordered[1:]) if b > a]
    if not gaps:
        return None
    return max(set(gaps), key=gaps.count)


def snap_strike(
    target: float,
    strikes: Sequence[float],
    *,
    step: int = 0,
) -> float | None:
    """목표 행사가에서 `step`번째로 가까운 상장 행사가 — 없으면 None.

    `step=0`이 가장 가까운 것, 1이 그다음. 동률이면 **낮은 행사가**를 먼저 본다(결정론적
    타이브레이크 — `labeling._resolve_barrier()`의 상단 우선과 같은 규율).
    """
    if not strikes:
        return None
    ordered = sorted(strikes, key=lambda k: (abs(k - target), k))
    return ordered[step] if step < len(ordered) else None


def resolve_candidate(
    candidate: StrategyCandidate,
    spec: CandidateSpec,
    smile: SmileFit,
    chain: Sequence[ChainLeg],
    *,
    r: float = 0.0,
    score: float = 0.0,
    config: EvaluatorConfig = EvaluatorConfig(),
    max_snap_steps: int = DEFAULT_MAX_SNAP_STEPS,
    max_snap_grid_steps: float = DEFAULT_MAX_SNAP_GRID_STEPS,
    min_open_interest: float | None = None,
) -> tuple[ResolvedCandidate | None, str]:
    """반환 `(확정된 후보 또는 None, 사유)` — **못 옮긴 이유가 항상 함께 나온다.**

    사유를 돌려주는 이유는 `chain_smile.build_smile()`과 같다: "상장 행사가가 없다"와
    "그 행사가가 비유동이다"와 "재평가에서 후보가 사라졌다"는 고칠 곳이 다르다.
    """
    by_symbol = {(leg.option_type, leg.strike): leg for leg in chain}
    resolved: list[ResolvedLeg] = []

    for leg in candidate.legs:
        strikes = listed_strikes(chain, leg.option_type)
        if not strikes:
            return None, f"{leg.option_type} 행사가가 체인에 없다"

        picked: ChainLeg | None = None
        snapped_strike: float | None = None
        for step in range(max_snap_steps + 1):
            strike = snap_strike(leg.strike, strikes, step=step)
            if strike is None:
                break
            chain_leg = by_symbol.get((leg.option_type, strike))
            if chain_leg is None:
                continue
            liquid = (
                is_liquid_leg(chain_leg)
                if min_open_interest is None
                else is_liquid_leg(chain_leg, min_open_interest=min_open_interest)
            )
            if liquid:
                picked, snapped_strike = chain_leg, strike
                break

        if picked is None or snapped_strike is None:
            return None, (
                f"{leg.option_type} {leg.strike:.1f} 근처 {max_snap_steps + 1}개 격자가 "
                f"전부 비유동이거나 미상장"
            )

        # **목표가 상장 범위 밖이면 버린다** — 가장자리로 대충 옮기지 않는다(상수 주석).
        step = grid_step(strikes)
        if step is not None:
            limit = step * max_snap_grid_steps
            distance = abs(snapped_strike - leg.strike)
            if distance > limit:
                return None, (
                    f"{leg.option_type} 목표 {leg.strike:.1f}이 상장 범위 밖 — 가장 가까운 "
                    f"상장 행사가 {snapped_strike:.1f}까지 {distance:.1f}pt로 격자({step:.1f}pt)의 "
                    f"{distance / step:.1f}배다. 스마일 외삽이 폴링 창(ATM±10) 밖을 가리킨 것이라 "
                    f"그 계약은 목표와 무관하다"
                )
        resolved.append(
            ResolvedLeg(
                leg=StrategyLeg(
                    option_type=leg.option_type,
                    strike=snapped_strike,
                    dte=leg.dte,
                    is_short=leg.is_short,
                    delta=leg.delta,
                    symbol=picked.symbol,
                ),
                chain=picked,
                requested_strike=leg.strike,
            )
        )

    # **다리 둘이 같은 계약이면 그 구조가 아니다** (2026-09-02 실측).
    #
    # 아카이브 재생에서 Iron Condor의 네 다리가 `B01608A04,B01608A04`처럼 겹쳤다. 겹친
    # 구조는 최대손실 계산도 그릭스 합산도 전부 거짓이 되고, 주문으로 나가면 **의도한 적
    # 없는 포지션**이 된다. 스냅이 만든 축퇴는 조용히 통과시키지 않는다.
    symbols = [item.leg.symbol for item in resolved]
    if len(set(symbols)) != len(symbols):
        duplicated = sorted({sym for sym in symbols if symbols.count(sym) > 1})
        return None, (
            f"스냅 결과 다리가 겹쳤다({', '.join(duplicated)}) — {candidate.structure} 구조가 "
            f"성립하지 않는다"
        )

    # **재평가** — 스냅된 행사가로 다시 계산한다(이 모듈의 존재 이유).
    reevaluated = evaluate_candidate(
        spec,
        smile,
        r=r,
        score=score,
        config=config,
        rationale=dict(candidate.rationale),
        legs=[item.leg for item in resolved],
    )
    if reevaluated is None:
        return None, "스냅 후 재평가에서 후보가 성립하지 않는다"

    worst_snap = max((item.snap_distance for item in resolved), default=0.0)
    return (
        ResolvedCandidate(candidate=reevaluated, legs=tuple(resolved), before_snap=candidate),
        f"{len(resolved)}다리 확정 · 최대 스냅 {worst_snap:.2f}pt",
    )


def resolve_symbols_only(
    candidate: StrategyCandidate, chain: Sequence[ChainLeg]
) -> Mapping[str, str]:
    """진단용 — 재평가 없이 "이 후보의 다리가 어느 종목코드로 떨어지나"만 본다."""
    out: dict[str, str] = {}
    for leg in candidate.legs:
        strikes = listed_strikes(chain, leg.option_type)
        strike = snap_strike(leg.strike, strikes)
        if strike is None:
            continue
        for chain_leg in chain:
            if chain_leg.option_type == leg.option_type and chain_leg.strike == strike:
                out[f"{leg.option_type}{leg.strike:.1f}"] = chain_leg.symbol
                break
    return out
