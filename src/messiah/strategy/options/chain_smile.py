"""실옵션 체인 → `SmileFit` — `OptionsAIService.smile_provider`의 실데이터 구현체
(2026-09-02 신설, Ver 1.3 §3.1).

`strategy/options/service.py` 모듈 docstring이 예고한 자리다: *"`OptionQuoteSnapshot`이 아직
필드 미해석 상태라 이 서비스가 `raw.option_chain.*`를 직접 구독해 실시간으로 `SmileFit`을
만들 수 없다 — 그 배선은 필드 매핑이 확정된 뒤의 별도 작업이다(알려진 갭)."* 이 파일이 그
"확정된 뒤"다. 아래 §1이 확정의 근거고, §2가 그 결과 쓰는 값이다.

# §1. 필드 의미는 이렇게 확정했다 (2026-09-02, 실계좌 수집분 20거래일)

문서를 읽어서가 아니라 **KIS가 준 값을 우리 Black-76으로 재현해서** 확정했다. 재현되면
규약이 같은 것이고, 안 되면 다른 것이다 — 어느 쪽이든 추측이 아니다.
검증기는 `scripts/verify_option_fields.py`, 표본은 `data/option_chain/{시리즈}/*.parquet`
(2026-08-05~09-02, 정규 20거래일·위클리 각 13~18거래일, ATM 근처 2,300여 다리).

**forward는 시장에서 직접 뽑는다** — 풋-콜 패리티 `F = K + C − P`의 ATM 근처 중앙값. 선물가를
쓰면 폴링 시각(옵션)과 봉 확정 시각(선물)이 어긋나 그 차이가 IV 오차로 새어 든다.

## 확정 ① 잔존만기는 `hts_rmnn_dynu − 1`이다 (핵심)

`hts_rmnn_dynu`를 그대로 365로 나누면 우리 IV가 KIS IV보다 **6% 낮게** 나왔고, 그 편차가
잔존만기에 따라 체계적으로 변했다(잔존 2일 0.58 → 8일 0.87 → 15일 0.92 → 24일 0.96).
차이를 상수 뺄셈으로 보면 전 구간 **1.0~1.2일**로 일정하다. 실제로 1을 빼자:

    t = hts_rmnn_dynu / 365       → 우리IV / KIS IV = 0.9391  (계통 편차)
    t = (hts_rmnn_dynu − 1) / 365 → 우리IV / KIS IV = 1.0011  ← 정규 월물 1,402다리
                                                       0.9795  ← 월위클리
                                                       0.9974  ← 목위클리

즉 `hts_rmnn_dynu`는 **만기일 당일을 포함해 센 달력일수**이고, 실제 잔존은 그보다 하루 적다.
세 시리즈에서 독립적으로 같은 결론이 나왔다.

## 확정 ② IV(`hts_ints_vltl`)는 %다 — /100이 맞다

위 비율이 1.00이라는 것이 곧 그 증명이다(마흐디의 raw/100 결론과 일치, 이번엔 우리 데이터로).

## 확정 ③ 베가는 우리 규약과 같고, **감마·세타는 다르다 — 그것도 시리즈마다 다르게**

같은 IV·같은 forward·같은 t로 우리가 계산한 값 대비 KIS 값의 비(잔존 5일 이상만):

    시리즈        n     IV비    베가비    감마비     세타비    KIS 세타 중앙
    정규 월물    937   1.0010    1.00     0.72       1.38        -2.35
    월위클리      99   1.0305  114.66     0.98     286.31      -668.29
    목위클리      18   0.9932  112.43     0.96     315.60      -702.75

**같은 API·같은 응답 스키마인데 시리즈마다 그릭스 스케일이 다르다.** 정규는 베가가 우리
규약(IV 1%p당 pt)과 일치하는데 위클리는 약 100배(= 1.0 sigma당으로 보인다), 세타는 정규
1.38배·위클리 약 290배다. 어느 쪽도 하나의 단위 규약으로 설명되지 않는다.

그래서 **KIS 그릭스는 쓰지 않는다.** `core/messages.GreeksProfile` 계약대로 `surface.py`가
직접 계산한다 — `OptionQuoteSnapshot` docstring이 예고한 선택지 중 이쪽이고, 이번 측정이
그 예고를 근거로 바꿨다. 미결제약정(OI)만은 계산으로 못 만들어 API가 유일 출처다(그 필드는
정수 카운트라 단위 모호성이 없다).

## 확정 ④ 호가(bid/ask)는 응답에 **없다** — 유동성 축을 갈아야 했다

`get_quote(O)`는 호가를 주지 않는다(`OptionQuoteSnapshot` docstring의 2026-08-04 실측:
호가는 `get_asking_price` 쪽이고, 다리당 2회 호출은 유량 예산을 두 배로 먹는다). 그래서
`surface.is_liquid_quote(bid, ask)`는 **이 경로에서 호출할 수 없다** — 이 파일은 그 함수를
쓰지 않고 OI·거래량 기반 `is_liquid_leg()`를 따로 둔다. 같은 이름으로 덮어쓰지 않는 이유는
둘이 **다른 것을 재기 때문**이다: 스프레드는 집행 비용을, OI/거래량은 그 행사가에 시장이
있는지를 잰다. 호가가 필요해지는 것은 집행 품질 단계이고 그때 `get_asking_price`를 켠다.

OI 임계를 0→100으로 올려도 |우리IV − KIS IV| 중앙이 0.0417→0.0462로 **줄지 않았다** —
잔여 차이는 유동성이 아니라 시각 어긋남(스냅샷 다리별 최대 수십 초)에서 온다.

# §2. 그래서 이 모듈이 하는 일

`raw.option_chain.{underlying}` 구독 → 다리별 최신 스냅샷 캐시 → 요청 시 그 캐시로
`(행사가, IV)` 점들을 만들어 `surface.fit_smile()`에 넘긴다. 캐시가 오래됐거나 점이 3개
미만이면 **None을 돌려준다** — 그러면 `OptionsAIService`가 `NO_OPTION`을 사유와 함께 낸다
(침묵이 아니라 판단, Ver 1.3 §5.2).
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping, Sequence

from messiah.core import logging as mlog
from messiah.core.bus import TOPIC_RAW, BusLike
from messiah.core.messages import BusMessage, OptionQuoteSnapshot
from messiah.core.timeutil import now_utc
from messiah.strategy.options.surface import SmileFit, fit_smile, implied_vol

# §1 확정 ①. `hts_rmnn_dynu`는 만기일 당일을 포함해 센 달력일수다.
DTE_SETTLEMENT_OFFSET_DAYS = 1.0
DAYS_PER_YEAR = 365.0

# KIS `get_quote(O)` output1 필드 이름 — 여기가 이 프로젝트의 **정본 매핑**이다.
FIELD_PRICE = "futs_prpr"  # 현재가(지수 포인트)
FIELD_DTE = "hts_rmnn_dynu"  # 잔존일수(만기일 포함 — 확정 ①)
FIELD_OPEN_INTEREST = "hts_otst_stpl_qty"  # 미결제약정 — 계산으로 못 만든다
FIELD_VOLUME = "acml_vol"  # 누적거래량
FIELD_STRIKE = "acpr"  # 행사가(스냅샷의 strike와 100% 일치 실측)
FIELD_KIS_IV = "hts_ints_vltl"  # KIS 내재변동성(%) — 대조용으로만 싣는다

# 유동성 하한. 호가가 없어 스프레드를 못 재는 대신 쓰는 축(확정 ④).
#
# 값의 근거: 2026-08-05~09-02 정규 월물 OI 사분위가 62 / 137 / 304다. 하위 사분위를 그대로
# 자르면 스마일 점이 3개 미만으로 떨어지는 날이 생겨(피팅 자체가 불가) 그보다 아래로 둔다.
# **R18대로 이 값은 아무것도 차단하지 않는 상태로 먼저 관측한다** — 지금 이 경로가 만드는
# 것은 `intel.options`(화면 표시)뿐이고 주문 경로는 없다.
MIN_OPEN_INTEREST = 10

# **거래량 0인 다리는 가격이 아니라 유물이다** (2026-09-02 실측). `futs_prpr`는 최종체결가라
# 그날 체결이 없으면 전일(또는 그 전) 값이 그대로 남는다 — 그 다리의 IV는 오늘 시장이 아니다.
# 20거래일 1,006 사이클에서 이 한 줄이 스마일 RMS 잔차 중앙을 0.0486 → 0.0249로 떨어뜨렸다
# (`SURFACE_RESIDUAL_WARN_THRESHOLD` 0.05 기준 신뢰 비율 52% → 89%).
MIN_VOLUME = 1.0

# 그러고도 남는 낡은 체결가를 자르는 로버스트 트림 — 중앙값에서 MAD의 이 배수를 넘는 IV 점은
# 버린다. 거래량 필터와 **함께** 쓸 때 잔차 중앙 0.0168 · 신뢰 95%(같은 표본).
#
# 왜 밴드(|log(K/F)| 제한)가 아니라 트림인가: 폴링 창이 이미 ATM ±10행사가라 전 점이
# |log(K/F)| <= 0.08 안에 있다 — 밴드를 어떻게 잡아도 한 점도 안 잘렸다(실측). 잔차의 원인은
# 외가 곡률이 아니라 **낡은 체결가**였다.
IV_OUTLIER_MAD_MULTIPLE = 3.0

# 이 초를 넘긴 스냅샷은 스마일에 안 쓴다. 옵션 체인 폴링 주기는 시리즈당 60~180초라
# (`scripts/run_l1_daily.py` 폴링 계획) 그 두 배를 넘으면 한 사이클을 통째로 건너뛴 것이다.
DEFAULT_MAX_AGE_SECONDS = 360.0

MIN_SMILE_POINTS = 3  # `surface.fit_smile()`의 하한과 같은 값(2차 다항)


@dataclass(frozen=True, slots=True)
class ChainLeg:
    """스냅샷 1개에서 **해석된** 값 — raw는 여기서 끝나고 아래로는 안 흐른다."""

    symbol: str
    series: str
    option_type: str
    strike: float
    price: float
    dte_days: float
    open_interest: float
    volume: float
    kis_iv: float | None  # 대조용(§1 확정 ③) — 계산에는 안 쓴다
    ts_utc: datetime

    @property
    def time_to_expiry(self) -> float | None:
        """연 단위 잔존만기 — 만기 당일(또는 그 이후)이면 None."""
        return time_to_expiry_years(self.dte_days)


def time_to_expiry_years(dte_days: float) -> float | None:
    t = (dte_days - DTE_SETTLEMENT_OFFSET_DAYS) / DAYS_PER_YEAR
    return t if t > 0 else None


def _number(raw: Mapping[str, object], field: str) -> float | None:
    value = raw.get(field)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def parse_leg(snapshot: OptionQuoteSnapshot) -> ChainLeg | None:
    """`OptionQuoteSnapshot` → `ChainLeg`. 필수 필드가 없거나 값이 이상하면 None.

    `raw`는 `get_quote(O)` 응답 전체(output1/2/3)다 — output1이 리스트로 오는 경우가 있어
    `data/option_chain_archiver._flatten_quote()`와 같은 방식으로 첫 원소를 편다. 그쪽은
    적재용으로 **전 필드를 해석 없이** 펴고, 이쪽은 계산에 쓸 몇 개만 **의미를 확정해서**
    읽는다 — 같은 바이트를 두 목적이 각자 읽는 것이지 정본이 둘인 것은 아니다.
    """
    block = snapshot.raw.get("output1", snapshot.raw)
    if isinstance(block, list):
        block = block[0] if block and isinstance(block[0], dict) else {}
    if not isinstance(block, Mapping):
        return None

    price = _number(block, FIELD_PRICE)
    dte = _number(block, FIELD_DTE)
    if price is None or price <= 0 or dte is None:
        return None
    strike = _number(block, FIELD_STRIKE)
    if strike is None or strike <= 0:
        strike = float(snapshot.strike)
    return ChainLeg(
        symbol=snapshot.symbol,
        series=snapshot.series,
        option_type=snapshot.option_type,
        strike=strike,
        price=price,
        dte_days=dte,
        open_interest=_number(block, FIELD_OPEN_INTEREST) or 0.0,
        volume=_number(block, FIELD_VOLUME) or 0.0,
        kis_iv=(lambda v: v / 100.0 if v and v > 0 else None)(_number(block, FIELD_KIS_IV)),
        ts_utc=snapshot.ts_utc,
    )


def parity_forward(legs: Sequence[ChainLeg], *, near_pairs: int = 6) -> float | None:
    """풋-콜 패리티로 시장이 말하는 forward를 뽑는다 — `F = K + C − P`의 ATM 근처 중앙값.

    왜 선물가를 안 쓰나: 옵션 스냅샷은 다리마다 폴링 시각이 다르고(한 사이클 안에서 수십 초
    분산) 선물 봉은 30분 격자다. 그 어긋남이 그대로 IV 편차로 들어온다. 패리티는 **같은
    스냅샷 안의 두 다리**만 쓰므로 시각 어긋남이 상쇄된다.

    ATM 근처만 쓰는 이유: 깊은 외가는 한쪽 다리가 호가 단위(0.01)에 눌려 패리티가 깨진다.
    """
    calls = {leg.strike: leg for leg in legs if leg.option_type == "C"}
    puts = {leg.strike: leg for leg in legs if leg.option_type == "P"}
    pairs = [(k, k + calls[k].price - puts[k].price) for k in sorted(set(calls) & set(puts))]
    if not pairs:
        return None
    rough = statistics.median(f for _, f in pairs)
    near = sorted(pairs, key=lambda item: abs(item[0] - rough))[:near_pairs]
    return statistics.median(f for _, f in near)


def is_liquid_leg(
    leg: ChainLeg, *, min_open_interest: float = MIN_OPEN_INTEREST, min_volume: float = MIN_VOLUME
) -> bool:
    """호가가 없어 스프레드를 못 재는 자리의 대체 축(§1 확정 ④).

    `surface.is_liquid_quote()`를 **대체하지 않는다** — 그쪽은 집행 비용(스프레드), 이쪽은
    그 행사가에 시장이 있는지를 잰다. 호가를 구독하게 되면 둘을 함께 쓴다.
    """
    return leg.open_interest >= min_open_interest and leg.volume >= min_volume


def smile_points(
    legs: Sequence[ChainLeg], forward: float, t: float, *, r: float = 0.0
) -> list[tuple[float, float]]:
    """`(행사가, IV)` 점들 — 같은 행사가에 콜·풋이 다 있으면 **외가 쪽**을 쓴다.

    내가(ITM)는 내재가치가 커서 가격의 시간가치 비중이 작고, 그래서 같은 호가 단위 오차가
    IV로 증폭된다. 표준 관행대로 forward 기준 외가만 취한다.
    """
    best: dict[float, ChainLeg] = {}
    for leg in legs:
        otm = (leg.option_type == "C" and leg.strike >= forward) or (
            leg.option_type == "P" and leg.strike <= forward
        )
        if not otm:
            continue
        best[leg.strike] = leg

    points: list[tuple[float, float]] = []
    for strike, leg in sorted(best.items()):
        iv = implied_vol(
            price=leg.price, forward=forward, strike=strike, r=r, t=t, option_type=leg.option_type
        )
        if iv is not None:
            points.append((strike, iv))
    return trim_iv_outliers(points)


def trim_iv_outliers(
    points: Sequence[tuple[float, float]], *, mad_multiple: float = IV_OUTLIER_MAD_MULTIPLE
) -> list[tuple[float, float]]:
    """중앙값에서 MAD의 `mad_multiple`배를 벗어난 IV 점을 버린다 — 낡은 체결가 방어.

    점이 5개 미만이면 트림하지 않는다: 그 개수에서는 MAD 자체가 한두 점에 끌려다녀서
    **정상 점을 자를 위험이 이상치를 남길 위험보다 크다**(피팅 하한이 3점이라 여유도 없다).
    """
    if len(points) < 5:
        return list(points)
    ivs = [iv for _, iv in points]
    median_iv = statistics.median(ivs)
    mad = statistics.median(abs(iv - median_iv) for iv in ivs)
    # **퍼짐이 사실상 0이면 트림하지 않는다.** 스마일이 거의 평평한 체인에서는 MAD가 부동소수
    # 잡음 수준(1e-14)까지 내려가는데, 그러면 그 잡음의 몇 배라는 문턱이 **정상 점을 자른다**
    # (합성 체인 테스트에서 9점 중 2점이 그렇게 잘렸다). 이상치 방어가 정상값을 깎으면
    # 방어가 아니라 손상이다.
    if mad <= max(1e-9, abs(median_iv) * 1e-6):
        return list(points)
    limit = mad_multiple * 1.4826 * mad  # 1.4826 = 정규분포에서 MAD→표준편차 환산
    return [(k, iv) for k, iv in points if abs(iv - median_iv) <= limit]


def build_smile(
    legs: Iterable[ChainLeg],
    *,
    r: float = 0.0,
    min_open_interest: float = MIN_OPEN_INTEREST,
    min_volume: float = MIN_VOLUME,
) -> tuple[SmileFit | None, str]:
    """반환 `(SmileFit 또는 None, 사유)` — **못 만든 이유가 항상 함께 나온다.**

    사유를 돌려주는 이유: 이 함수가 None을 내면 화면에 `NO_OPTION`이 뜨는데, 그때 "옵션
    로직이 안 도는 것"과 "체인이 아직 안 온 것"과 "유동성이 없어 점이 모자란 것"이 같은
    문구로 보이면 사람이 고칠 것을 못 고른다(`meta_decision`의 게이트 이름과 같은 규율).
    """
    legs = list(legs)
    if not legs:
        return None, "체인 미수신"

    by_expiry: dict[float, list[ChainLeg]] = {}
    for leg in legs:
        by_expiry.setdefault(leg.dte_days, []).append(leg)
    dte_days = min(by_expiry)  # 근월(가장 가까운 만기)
    group = by_expiry[dte_days]

    t = time_to_expiry_years(dte_days)
    if t is None:
        return None, f"만기 당일 이후(잔존 {dte_days:.0f}일) — 스마일 대상 아님"

    forward = parity_forward(group)
    if forward is None:
        return None, "풋-콜 쌍이 없어 forward를 못 구했다"

    liquid = [
        leg
        for leg in group
        if is_liquid_leg(leg, min_open_interest=min_open_interest, min_volume=min_volume)
    ]
    if not liquid:
        return None, f"유동성 하한(OI>={min_open_interest:.0f})을 넘는 다리 0개"

    points = smile_points(liquid, forward, t, r=r)
    if len(points) < MIN_SMILE_POINTS:
        return None, (
            f"스마일 점 {len(points)}개 < {MIN_SMILE_POINTS} "
            f"(유동 다리 {len(liquid)}개 · IV 수렴 실패 {len(liquid) - len(points)}개)"
        )

    smile = fit_smile(forward, int(round(dte_days - DTE_SETTLEMENT_OFFSET_DAYS)), points)
    if smile is None:
        return None, "피팅 실패"
    return smile, f"점 {len(points)}개 · forward {forward:.2f} · 잔존 {dte_days:.0f}일"


class ChainSmileProvider:
    """`raw.option_chain.{underlying}` 구독 → `SmileFit` 콜백 (`SmileProvider` 계약).

    `OptionsAIService`는 이 객체를 **호출 가능한 것**으로만 안다 — 그래서 백테스트·테스트가
    합성 스마일을 주입하는 경로가 그대로 남는다(그 서비스 docstring의 설계 의도).
    """

    def __init__(
        self,
        underlying: str,
        bus: BusLike,
        *,
        series: str = "regular",
        max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
        min_open_interest: float = MIN_OPEN_INTEREST,
        min_volume: float = MIN_VOLUME,
        r: float = 0.0,
    ) -> None:
        self._underlying = underlying
        self._bus = bus
        self._series = series
        self._max_age = max_age_seconds
        self._min_oi = min_open_interest
        self._min_volume = min_volume
        self._r = r
        self._legs: dict[str, ChainLeg] = {}
        self._last_reason = "체인 미수신"
        self._fits = 0
        self._misses = 0

    @property
    def last_reason(self) -> str:
        """마지막 호출이 왜 그 결과였나 — 화면·로그가 사유를 물을 자리."""
        return self._last_reason

    @property
    def stats(self) -> dict[str, int]:
        return {"fits": self._fits, "misses": self._misses, "legs": len(self._legs)}

    async def handle_snapshot(self, msg: BusMessage) -> None:
        if not isinstance(msg, OptionQuoteSnapshot):
            return
        if msg.underlying != self._underlying or msg.series != self._series:
            return
        leg = parse_leg(msg)
        if leg is None:
            return
        self._legs[leg.symbol] = leg

    def __call__(self) -> SmileFit | None:
        fresh = self._fresh_legs()
        smile, reason = build_smile(
            fresh, r=self._r, min_open_interest=self._min_oi, min_volume=self._min_volume
        )
        self._last_reason = reason
        if smile is None:
            self._misses += 1
            return None
        self._fits += 1
        # **신뢰불가 피팅은 조용히 통과시키지 않는다** — `SmileFit.is_reliable`이 이미 잔차로
        # 판정하고 있는데 그 판정이 아무 데도 안 나가면 없는 것과 같다(R18의 계측 쪽).
        if not smile.is_reliable:
            mlog.log(
                "OptionSmileResidualHigh",
                f"스마일 잔차 {smile.rms_residual:.4f} > 임계 — 피팅 신뢰불가",
                underlying=self._underlying,
                series=self._series,
                dte=smile.dte,
                n_points=smile.n_points,
                rms_residual=smile.rms_residual,
            )
        return smile

    def _fresh_legs(self) -> list[ChainLeg]:
        now = now_utc()
        return [
            leg
            for leg in self._legs.values()
            if (now - leg.ts_utc).total_seconds() <= self._max_age
        ]

    async def run_forever(self) -> None:
        topic = f"{TOPIC_RAW}.option_chain.{self._underlying}"
        mlog.log(
            "OptionSmileProviderStarted",
            f"{topic} 구독 — 시리즈 {self._series} · 유동성 하한 OI>={self._min_oi:.0f}",
            underlying=self._underlying,
            series=self._series,
            topic=topic,
        )
        # **버스 구독은 콜백 등록이지 async 제너레이터가 아니다** (2026-09-03 P0, F-89).
        #
        # 여기에 종전엔 `async for msg in self._bus.subscribe(topic)`라고 적혀 있었다. 그
        # 호출은 `MessageBus.subscribe(patterns, handler)`의 계약(`core/bus.py:173`)과 인자
        # 개수부터 맞지 않아 **첫 실행에서 즉시 `TypeError`**였고, 그 예외가
        # `_run_regular_session()`의 `asyncio.gather()`를 타고 올라가 2026-09-03 08:25:38에
        # G2 세션 전체(선물 판단·Risk·Sizer·OrderGateway·국면·하트비트)를 기동 2초 만에
        # 내렸다 — 그날 하루 판단 공백. 같은 토픽을 듣는
        # `data/option_chain_archiver.py:256`과 형제 서비스
        # `strategy/options/service.py:234`는 처음부터 이 형태였다.
        #
        # `on_kill`을 주지 않는 것은 의도다: 이 제공자는 봉만 보는 순수 구독자라
        # `KillSignal`을 받을 이유가 없고, 받으면 `handle_snapshot`이 그것을 견뎌야 한다는
        # 계약이 새로 생긴다(`core/bus.py`의 2026-08-07 P0-1 주석 — kill은 원한 구독자에게만).
        await self._bus.subscribe([topic], self.handle_snapshot)


def moneyness(strike: float, forward: float) -> float:
    """log(K/F) — `SmileFit.iv_at()`이 쓰는 축과 같은 정의(진단·로그용)."""
    return math.log(strike / forward)
