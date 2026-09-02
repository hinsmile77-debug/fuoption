"""국면별 방향 채점 — "이 국면에서 모델의 방향이 맞기는 하는가" (2026-09-02 신설).

`vol_scorecard.py`가 **피처**의 예측력을 매일 다시 묻는 도구라면, 이쪽은 **판단**의 방향을
국면별로 묻는다. 둘 다 R18("게이트·차단 로직 신설은 섀도 계측 20거래일 후 승격")의 계측
쪽이다 — 이 모듈은 **아무것도 차단하지 않는다.** 세고, 사람이 읽는다.

## 왜 필요했나 (2026-09-02 반사실 측정)

`|S| >= 0.20` 게이트를 낮출지 물었던 분석에서, 임계보다 훨씬 큰 것이 먼저 나왔다.
2026-08-21~09-02의 게이트④ 도달 123 사이클을 "리스크가 전부 승인했다면"으로 되돌려
학습 레이블과 같은 삼중장벽으로 청산했을 때:

    HIGH_VOL     n=34  방향 적중 21%  총 -13,205틱   이항검정 p=0.0004  8일 중 7일 손실
    RANGE        n=60  방향 적중 55%  총  +3,197틱   p=0.82 (무의미)
    TREND_DOWN   n=19  방향 적중 53%  총    +954틱   p=0.68
    TREND_UP     n=10  방향 적중 70%  총  +2,126틱   p=0.95

고변동 국면에서 모델은 34번 중 32번 SHORT를 냈고 그 SHORT의 적중률이 16%였다. 그리고
**|S|가 클수록 더 틀렸다**(|S| 0.10~0.15 구간 6건 적중 0%). 이 손실이 실현되지 않은 유일한
이유는 그 국면의 실효 천장이 0.188이라 게이트 0.20에 못 닿기 때문이다
(`label_geometry.RegimeReachability`) — 즉 **우연이 최악의 국면을 막고 있었다.**

우연은 계측 대상이 아니다. 그래서 이 모듈이 매 거래일 그 숫자를 다시 센다.

## 무엇을 "적중"이라 부르나

`hit = gross_ticks > 0` — 비용 차감 **전** 부호다. 방향이 맞았는지와 그 거래가 남는지는
다른 질문이라 칸을 나눈다(`net_ticks`가 후자). 비용이 배리어 폭의 0.2% 수준이라 두 값이
거의 같지만, 같아 보인다고 한 칸으로 합치면 비용이 커졌을 때 조용히 뜻이 바뀐다.

## 청산 규칙은 **학습 레이블 그대로**다

시스템에 청산 엔진이 아직 없다(`strategy/pipeline.py`의 `_directional_edge()` docstring이
같은 사실을 다른 각도에서 적고 있다 — Net ER은 삼중장벽의 지불을 가정해 계산된다). 그래서
채점의 청산도 모델이 **학습한 목표** 그대로 둔다: ±`width_atr_mult`×ATR(14), 시간배리어
3봉. 다른 규칙을 쓰면 "모델이 틀렸다"와 "청산이 나빴다"가 섞여 어느 쪽도 못 읽는다.

**오버나이트는 넘기지 않는다** — Holding Policy §2.2 A(Type A 오버나이트 자격 없음)가 실제로
거절하는 것이라(2026-09-01 15:30 `risk_reject` 실측), 당일 마지막 봉 종가로 강제 청산한다.
그 결과는 `barrier="eod"`로 따로 표시돼 시간배리어 도달과 구분된다.

## 이 숫자로 무엇을 하면 안 되나

`p < 0.01`이 나와도 이 모듈은 게이트를 만지지 않는다. 표본 8~9거래일은 **단일 국면
에피소드**일 수 있다 — 그 구간이 상승장이었다면 "고변동=하락"이 계속 틀린 것뿐이다.
승격 판단은 20거래일(`MIN_TRADING_DAYS`)과 `MIN_SAMPLES`를 함께 채운 뒤 사람이 한다.
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from messiah.core.messages import BarClosed, Horizon, Regime, bar_confirm_time
from messiah.features.px_core import atr as compute_atr
from messiah.models.labeling import BARRIER_PARAMS, DEFAULT_ATR_WINDOW
from messiah.risk.cost_model import CostModel

# 국면 하나가 "읽을 만하다"고 보는 최소 표본. `vol_scorecard.MIN_SAMPLES`와 같은 값 —
# 같은 질문("이 숫자를 성적으로 읽어도 되나")에 같은 기준을 쓴다.
MIN_SAMPLES = 30

# 승격 논의를 시작할 수 있는 최소 거래일. R18의 섀도 계측 기간과 같다. 표본 수만 채우고
# 하루 이틀에 몰려 있으면 그건 **하루의 성질**이지 모델의 성질이 아니다.
MIN_TRADING_DAYS = 20

# 이 확률보다 낮으면 "동전과 다르다"고 적는다(적기만 한다 — 위 docstring "하면 안 되는 것").
FLAG_P_VALUE = 0.01

STATUS_SCORED = "측정"
STATUS_TOO_FEW = "표본 부족"


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    """`DecisionEmitted` 한 줄에서 채점에 필요한 것만 — 로그 형태와 채점 로직을 갈라 둔다."""

    ts_kst: datetime
    symbol: str
    regime: Regime
    score: float
    gate: str
    score_threshold: float | None = None
    # 국면을 어디서 얻었나 — "self"는 판단 줄이 스스로 말한 것, "joined"는 같은 분의
    # `RegimeClassified`/`MetaGateEvaluated`에서 이어 붙인 것. 세는 이유는 아래
    # `build_regime_lookup()` docstring 참고.
    regime_source: str = "self"

    @property
    def side(self) -> int:
        """+1 매수 / −1 매도 — `meta_decision`의 `score >= threshold`와 같은 부호 규칙."""
        return 1 if self.score >= 0 else -1

    @property
    def gate_passed(self) -> bool:
        return self.gate == "pass"


@dataclass(frozen=True, slots=True)
class DirectionOutcome:
    record: DecisionRecord
    entry_ticks: int
    exit_ticks: int
    barrier: str  # upper / lower / time / eod
    gross_ticks: int  # 방향 반영 손익(비용 차감 전)
    cost_ticks: float
    hold_bars: int
    atr_ticks: float

    @property
    def net_ticks(self) -> float:
        return self.gross_ticks - self.cost_ticks

    @property
    def hit(self) -> bool:
        return self.gross_ticks > 0


@dataclass(frozen=True, slots=True)
class RegimeDirectionCard:
    regime: Regime
    n: int
    n_hit: int
    n_gate_passed: int
    n_regime_joined: int  # 국면을 옛 로그에서 이어 붙인 건수(`build_regime_lookup()`)
    gross_ticks: float
    net_ticks: float
    median_net_ticks: float
    trading_days: int
    net_by_day: dict[str, float]

    @property
    def status(self) -> str:
        return STATUS_SCORED if self.n >= MIN_SAMPLES else STATUS_TOO_FEW

    @property
    def hit_rate(self) -> float:
        return self.n_hit / self.n if self.n else 0.0

    @property
    def p_worse_than_coin(self) -> float:
        """P(적중 <= 관측 | 동전) — 낮을수록 "우연히 이만큼 틀리기는 어렵다"."""
        return _binom_cdf(self.n_hit, self.n)

    @property
    def p_better_than_coin(self) -> float:
        return _binom_cdf(self.n - self.n_hit, self.n)

    @property
    def losing_days(self) -> int:
        return sum(1 for value in self.net_by_day.values() if value < 0)

    @property
    def is_flagged(self) -> bool:
        """표본이 충분하고 동전과 유의하게 다른가 — **한쪽 방향만이 아니라 양쪽 다.**

        지나치게 잘 맞는 것도 계측 대상이다: 이 채점은 반사실이라, 적중률이 비정상적으로
        높으면 모델이 좋은 게 아니라 채점이 미래를 보고 있을 가능성이 먼저다.
        """
        if self.n < MIN_SAMPLES:
            return False
        return min(self.p_worse_than_coin, self.p_better_than_coin) < FLAG_P_VALUE

    @property
    def verdict(self) -> str:
        if self.n == 0:
            return "사이클 없음 — 판정 불가"
        if self.n < MIN_SAMPLES:
            return (
                f"표본 부족({self.n}건 < {MIN_SAMPLES}) — 적중 {self.hit_rate:.0%}는 "
                "아직 성적으로 읽지 않는다"
            )
        base = (
            f"적중 {self.hit_rate:.0%} ({self.n_hit}/{self.n}) · 순손익 "
            f"{self.net_ticks:+.0f}틱 · {self.trading_days}거래일 중 {self.losing_days}일 손실"
        )
        if self.p_worse_than_coin < FLAG_P_VALUE:
            return (
                f"**동전보다 나쁘다** — {base} (이항 p={self.p_worse_than_coin:.4f}). "
                "이 국면의 방향 예측이 계통적으로 틀리고 있다"
            )
        if self.p_better_than_coin < FLAG_P_VALUE:
            return (
                f"**동전보다 좋다** — {base} (이항 p={self.p_better_than_coin:.4f}). "
                "반사실 채점이 미래를 보고 있지 않은지 먼저 의심할 것"
            )
        return f"동전과 구분 안 됨 — {base}"

    def format_lines(self) -> list[str]:
        return [
            f"[{self.regime.value}] {self.status} n={self.n} (게이트 통과 {self.n_gate_passed}건)",
            f"  적중 {self.n_hit}/{self.n} ({self.hit_rate:.0%}) · "
            f"총 {self.net_ticks:+.1f}틱 · 중앙 {self.median_net_ticks:+.1f}틱",
            f"  판정: {self.verdict}",
        ]


def _binom_cdf(k: int, n: int, p: float = 0.5) -> float:
    """P(X <= k) — n이 수백 규모라 정확히 센다(근사하면 경계에서 판정이 흔들린다)."""
    if n <= 0:
        return 1.0
    k = max(0, min(k, n))
    return sum(math.comb(n, i) * p**i * (1.0 - p) ** (n - i) for i in range(k + 1))


_REGIME_TAGS = ("RegimeClassified", "MetaGateEvaluated")


def _minute_key(symbol: str, ts: str) -> tuple[str, datetime] | None:
    try:
        moment = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return symbol, moment.replace(second=0, microsecond=0)


def build_regime_lookup(
    payloads: Iterable[Mapping[str, object]],
) -> dict[tuple[str, datetime], Regime]:
    """옛 로그용 국면 조인표 — `(심볼, 분)` → 국면.

    ## 이 조인은 폴백이고, 폴백은 조용하면 안 된다

    `DecisionEmitted`가 `regime`을 스스로 싣기 시작한 것은 2026-09-02부터다
    (`meta_decision._view_fields()`). 그 이전 로그로 국면별 성적을 보려면 같은 사이클의
    `RegimeClassified`/`MetaGateEvaluated`에서 이어 붙이는 수밖에 없다.

    분 단위로 자르는 이유: 한 사이클 안에서 국면 판정은 판단보다 먼저 나가고 그 간격은
    1초 미만이지만 **초는 다를 수 있다**(실측 09:00:00.87 대 09:00:01.15). 초까지 맞추면
    조인이 통째로 빈다. 30분 격자라 분까지 자르면 충돌은 없다.

    그래도 이건 두 로그를 시각으로 잇는 일이고, 두 로그가 어긋나면 조용히 틀린다. 그래서
    `parse_decisions()`가 **self / joined를 나눠 세고** 산출물에 그대로 남긴다 — 나중에
    "이 성적은 어느 경로로 만든 것인가"를 물을 수 있어야 한다.
    """
    lookup: dict[tuple[str, datetime], Regime] = {}
    for payload in payloads:
        if payload.get("tag") not in _REGIME_TAGS:
            continue
        name, symbol, ts = payload.get("regime"), payload.get("symbol"), payload.get("ts")
        if not isinstance(name, str) or not isinstance(symbol, str) or not isinstance(ts, str):
            continue
        key = _minute_key(symbol, ts)
        if key is None:
            continue
        try:
            lookup[key] = Regime(name)
        except ValueError:
            continue
    return lookup


def parse_decision(
    payload: Mapping[str, object],
    *,
    regime_lookup: Mapping[tuple[str, datetime], Regime] | None = None,
) -> tuple[DecisionRecord | None, str]:
    """구조화 로그 한 줄 → `(레코드 또는 None, 사유)`. 채점 불가한 줄을 0으로 안 채운다."""
    if payload.get("tag") != "DecisionEmitted":
        return None, "not_decision"
    symbol, ts = payload.get("symbol"), payload.get("ts")
    score = payload.get("score")
    if not isinstance(symbol, str) or not isinstance(ts, str):
        return None, "malformed"
    if not isinstance(score, (int, float)):
        return None, "no_score"  # 게이트 ①′·② 이전에 접힌 줄 — 점수 자체가 없다

    source = "self"
    regime: Regime | None = None
    name = payload.get("regime")
    if isinstance(name, str):
        try:
            regime = Regime(name)
        except ValueError:
            regime = None
    if regime is None and regime_lookup is not None:
        key = _minute_key(symbol, ts)
        if key is not None:
            regime = regime_lookup.get(key)
            source = "joined"
    if regime is None:
        return None, "no_regime"

    gate = payload.get("gate")
    threshold = payload.get("score_threshold")
    return (
        DecisionRecord(
            ts_kst=datetime.fromisoformat(ts),
            symbol=symbol,
            regime=regime,
            score=float(score),
            gate=gate if isinstance(gate, str) else "unknown",
            score_threshold=float(threshold) if isinstance(threshold, (int, float)) else None,
            regime_source=source,
        ),
        source,
    )


def parse_decisions(
    payloads: Iterable[Mapping[str, object]],
    *,
    regime_lookup: Mapping[tuple[str, datetime], Regime] | None = None,
) -> tuple[list[DecisionRecord], dict[str, int]]:
    """반환 `(레코드, 사유별 건수)` — `self`/`joined`/버린 사유가 전부 세어져 나온다."""
    counts: dict[str, int] = {"self": 0, "joined": 0, "no_regime": 0, "no_score": 0}
    records: list[DecisionRecord] = []
    for payload in payloads:
        record, reason = parse_decision(payload, regime_lookup=regime_lookup)
        if record is None:
            if reason in counts:
                counts[reason] += 1
            continue
        counts[reason] += 1
        records.append(record)
    records.sort(key=lambda r: r.ts_kst)
    return records, counts


def resolve_outcome(
    record: DecisionRecord,
    bars: Sequence[BarClosed],
    index: int,
    *,
    horizon: Horizon = Horizon.M30,
    atr_window: int = DEFAULT_ATR_WINDOW,
    cost_model: CostModel | None = None,
    intraday_only: bool = True,
) -> DirectionOutcome | None:
    """진입봉 `bars[index]`(그 판단을 촉발한 확정봉)의 종가로 들어가 삼중장벽으로 나온다.

    반환 None: ATR 워밍업 부족, 또는 앞을 볼 봉이 하나도 없음(장 마지막 사이클) —
    **채점하지 않는다**. 결과를 확정할 수 없는 표본을 0으로 채우면 적중률이 희석된다.
    """
    params = BARRIER_PARAMS[horizon]
    atr_ticks = compute_atr(bars[: index + 1], atr_window)
    if atr_ticks is None:
        return None
    entry = bars[index].c_ticks
    width = round(params.width_atr_mult * atr_ticks)
    upper, lower = entry + width, entry - width

    forward = list(bars[index + 1 : index + 1 + params.time_barrier_bars])
    if intraday_only:
        entry_day = bars[index].bar_open_kst.date()
        forward = [b for b in forward if b.bar_open_kst.date() == entry_day]
    if not forward:
        return None

    exit_price: int | None = None
    barrier = ""
    hold = len(forward)
    for step, bar in enumerate(forward, 1):
        # 동일 봉에서 상/하단 동시 터치는 상단 우선 — `labeling._resolve_barrier()`와 같은 규칙
        if bar.h_ticks >= upper:
            exit_price, barrier, hold = upper, "upper", step
            break
        if bar.l_ticks <= lower:
            exit_price, barrier, hold = lower, "lower", step
            break
    if exit_price is None:
        exit_price = forward[-1].c_ticks
        barrier = "time" if len(forward) == params.time_barrier_bars else "eod"

    cost = (cost_model or CostModel()).estimate_round_trip_from_bars(bars[: index + 1], qty=1)
    return DirectionOutcome(
        record=record,
        entry_ticks=entry,
        exit_ticks=exit_price,
        barrier=barrier,
        gross_ticks=record.side * (exit_price - entry),
        cost_ticks=cost.total_ticks,
        hold_bars=hold,
        atr_ticks=atr_ticks,
    )


def resolve_outcomes(
    records: Sequence[DecisionRecord],
    bars_by_symbol: Mapping[str, Sequence[BarClosed]],
    *,
    horizon: Horizon = Horizon.M30,
    atr_window: int = DEFAULT_ATR_WINDOW,
    cost_model: CostModel | None = None,
    intraday_only: bool = True,
) -> tuple[list[DirectionOutcome], dict[str, int]]:
    """반환 `(성사된 채점, 사유별 미채점 건수)` — **못 잰 것도 세서 돌려준다**(고도화 2).

    판단 시각과 봉을 잇는 열쇠는 `bar_confirm_time`(봉 시작 + Horizon 길이)이다. 판단은 봉이
    확정된 직후(±1초)에 나가므로 초 단위를 버리고 맞춘다.
    """
    cost_model = cost_model or CostModel()
    skipped: dict[str, int] = {"no_bars": 0, "no_bar_at_ts": 0, "unresolvable": 0}
    outcomes: list[DirectionOutcome] = []
    index_by_symbol: dict[str, dict[datetime, int]] = {}
    for symbol, bars in bars_by_symbol.items():
        index_by_symbol[symbol] = {
            bar_confirm_time(bar).replace(second=0, microsecond=0): i for i, bar in enumerate(bars)
        }
    for record in records:
        bars = bars_by_symbol.get(record.symbol)
        if not bars:
            skipped["no_bars"] += 1
            continue
        key = record.ts_kst.replace(second=0, microsecond=0)
        index = index_by_symbol[record.symbol].get(key)
        if index is None:
            skipped["no_bar_at_ts"] += 1
            continue
        outcome = resolve_outcome(
            record,
            bars,
            index,
            horizon=horizon,
            atr_window=atr_window,
            cost_model=cost_model,
            intraday_only=intraday_only,
        )
        if outcome is None:
            skipped["unresolvable"] += 1
            continue
        outcomes.append(outcome)
    return outcomes, skipped


def score_regimes(outcomes: Sequence[DirectionOutcome]) -> list[RegimeDirectionCard]:
    """국면별 카드 — 사이클이 하나도 없는 국면은 만들지 않는다(빈 카드는 "0% 적중"으로
    오독된다). 어느 국면이 비었는지는 `summarise()`의 `regimes` 키 목록이 말한다."""
    by_regime: dict[Regime, list[DirectionOutcome]] = {}
    for outcome in outcomes:
        by_regime.setdefault(outcome.record.regime, []).append(outcome)

    cards: list[RegimeDirectionCard] = []
    for regime, group in by_regime.items():
        nets = [o.net_ticks for o in group]
        by_day: dict[str, float] = {}
        for outcome in group:
            day = outcome.record.ts_kst.date().isoformat()
            by_day[day] = by_day.get(day, 0.0) + outcome.net_ticks
        cards.append(
            RegimeDirectionCard(
                regime=regime,
                n=len(group),
                n_hit=sum(1 for o in group if o.hit),
                n_gate_passed=sum(1 for o in group if o.record.gate_passed),
                n_regime_joined=sum(1 for o in group if o.record.regime_source == "joined"),
                gross_ticks=float(sum(o.gross_ticks for o in group)),
                net_ticks=sum(nets),
                median_net_ticks=statistics.median(nets),
                trading_days=len(by_day),
                net_by_day={day: round(value, 1) for day, value in sorted(by_day.items())},
            )
        )
    return sorted(cards, key=lambda c: c.net_ticks)


def format_cards(cards: Sequence[RegimeDirectionCard], skipped: Mapping[str, int]) -> list[str]:
    if not cards:
        return ["국면별 방향 채점: 채점 가능한 사이클 0건 — 로그에 regime 필드가 있는지 확인할 것"]
    lines: list[str] = []
    for card in cards:
        lines.extend(card.format_lines())
    missed = {key: value for key, value in skipped.items() if value}
    if missed:
        lines.append(
            "  미채점: " + " · ".join(f"{key}={value}" for key, value in sorted(missed.items()))
        )
    return lines


def summarise(
    cards: Sequence[RegimeDirectionCard],
    skipped: Mapping[str, int],
    *,
    sources: Mapping[str, int] | None = None,
) -> dict[str, object]:
    total = sum(card.n for card in cards)
    days = sorted({day for card in cards for day in card.net_by_day})
    return {
        "n_scored": total,
        "trading_days": len(days),
        "days": days,
        "meets_shadow_window": len(days) >= MIN_TRADING_DAYS,
        "min_trading_days": MIN_TRADING_DAYS,
        "min_samples": MIN_SAMPLES,
        # 국면을 판단 줄이 스스로 말했나(self), 옛 로그에서 이어 붙였나(joined) — 성적을
        # 어느 경로로 만들었는지가 성적과 함께 남아야 한다.
        "regime_sources": dict(sorted((sources or {}).items())),
        "skipped": {key: value for key, value in sorted(skipped.items())},
        "flagged": [card.regime.value for card in cards if card.is_flagged],
        "regimes": {
            card.regime.value: {
                "status": card.status,
                "n": card.n,
                "n_hit": card.n_hit,
                "hit_rate": round(card.hit_rate, 4),
                "n_gate_passed": card.n_gate_passed,
                "n_regime_joined": card.n_regime_joined,
                "net_ticks": round(card.net_ticks, 1),
                "median_net_ticks": round(card.median_net_ticks, 1),
                "trading_days": card.trading_days,
                "losing_days": card.losing_days,
                "p_worse_than_coin": round(card.p_worse_than_coin, 6),
                "p_better_than_coin": round(card.p_better_than_coin, 6),
                "flagged": card.is_flagged,
                "verdict": card.verdict,
                "net_by_day": card.net_by_day,
            }
            for card in cards
        },
    }


def write_scorecard(
    cards: Sequence[RegimeDirectionCard],
    skipped: Mapping[str, int],
    *,
    symbol: str,
    day: date,
    log_dir: Path,
    reachability: Mapping[str, object] | None = None,
    sources: Mapping[str, int] | None = None,
) -> Path:
    """`logs/regime_direction_YYYYMMDD.json` — 화면 ⑤와 무결성 리포트가 이걸 읽는다.

    `reachability`(`label_geometry.summarise_regime_reachability()`의 출력)를 같은 파일에
    싣는 이유: 화면이 "이 국면은 적중률이 21%다"와 "이 국면은 게이트가 닫혀 있다"를 **한
    화면에서** 함께 보여야, 지금 손실이 안 나는 이유가 판단력이 아니라 산술이라는 것이
    보인다. 두 파일로 갈라 두면 둘을 같이 읽는 사람이 없다.
    """
    path = log_dir / f"regime_direction_{day.strftime('%Y%m%d')}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "date": day.isoformat(),
        "symbol": symbol,
        "direction": summarise(cards, skipped, sources=sources),
    }
    if reachability is not None:
        payload["reachability"] = reachability
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
