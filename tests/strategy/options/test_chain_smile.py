"""실옵션 체인 → SmileFit 배선 (2026-09-02 신설).

여기서 지키는 것은 셋이다:
  ① 2026-09-02에 실측으로 확정한 **필드 규약**이 코드에서 조용히 바뀌지 않는다,
  ② 못 만들 때 **왜 못 만들었는지**가 항상 함께 나온다(화면이 사유를 말해야 한다),
  ③ 낡은 체결가가 스마일에 안 들어간다(호가가 없어 이 방어가 유일하다).
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from messiah.core.messages import OptionQuoteSnapshot
from messiah.core.timeutil import now_utc
from messiah.simulator.inprocess_bus import InProcessBus
from messiah.strategy.options.chain_smile import (
    DTE_SETTLEMENT_OFFSET_DAYS,
    MIN_OPEN_INTEREST,
    ChainLeg,
    ChainSmileProvider,
    build_smile,
    is_liquid_leg,
    parity_forward,
    parse_leg,
    smile_points,
    time_to_expiry_years,
    trim_iv_outliers,
)

_UNDERLYING = "KOSPI200"
_FORWARD = 1040.0
_DTE = 10.0


def _leg(
    strike: float,
    option_type: str,
    price: float,
    *,
    oi: float = 500.0,
    volume: float = 100.0,
    dte: float = _DTE,
    age_seconds: float = 0.0,
) -> ChainLeg:
    return ChainLeg(
        symbol=f"{'B' if option_type == 'C' else 'C'}{int(strike)}",
        series="regular",
        option_type=option_type,
        strike=strike,
        price=price,
        dte_days=dte,
        open_interest=oi,
        volume=volume,
        kis_iv=None,
        ts_utc=now_utc() - timedelta(seconds=age_seconds),
    )


def _chain(iv: float = 0.45) -> list[ChainLeg]:
    """Black-76로 **정확히** 만든 체인 — 되돌린 IV가 그 값이어야 한다."""
    from messiah.strategy.options.surface import black76_price

    t = time_to_expiry_years(_DTE)
    assert t is not None
    legs = []
    for strike in (1000.0, 1010.0, 1020.0, 1030.0, 1040.0, 1050.0, 1060.0, 1070.0, 1080.0):
        for option_type in ("C", "P"):
            price = black76_price(
                forward=_FORWARD, strike=strike, r=0.0, sigma=iv, t=t, option_type=option_type
            )
            legs.append(_leg(strike, option_type, price))
    return legs


# --------------------------------------------------------------- 확정 ①: 시간 규약


def test_the_measured_settlement_offset_is_one_day():
    """**이 상수가 바뀌면 IV가 통째로 6% 틀어진다** (2026-09-02 실측).

    `hts_rmnn_dynu`를 그대로 쓰면 우리 IV가 KIS 대비 0.9391, 하루 빼면 1.0011이었다
    (정규 월물 1,402다리 · 위클리 두 시리즈에서도 각각 0.9795/0.9974).
    근거와 재현 방법은 `scripts/verify_option_fields.py`.
    """
    assert DTE_SETTLEMENT_OFFSET_DAYS == 1.0
    assert time_to_expiry_years(11.0) == pytest.approx(10.0 / 365.0)


def test_expiry_day_has_no_smile():
    """잔존 1일(=만기 당일)이면 t<=0 — 근사하지 않고 **판정을 거부한다**."""
    assert time_to_expiry_years(1.0) is None
    assert time_to_expiry_years(0.0) is None

    smile, reason = build_smile([_leg(1040.0, "C", 10.0, dte=1.0)])

    assert smile is None
    assert "만기 당일" in reason


# --------------------------------------------------------------- 파싱


def test_parse_leg_reads_the_mapped_fields_from_a_string_payload():
    """KIS 응답은 숫자도 문자열로 온다 — output1이 리스트로 오는 형태까지 함께 본다."""
    snapshot = OptionQuoteSnapshot(
        underlying=_UNDERLYING,
        series="regular",
        option_type="C",
        strike=1040.0,
        expiry="콜 202609",
        symbol="B01609A14",
        raw={
            "output1": [
                {
                    "futs_prpr": "26.30",
                    "hts_rmnn_dynu": "9",
                    "hts_otst_stpl_qty": "1262",
                    "acml_vol": "137",
                    "acpr": "1040.0",
                    "hts_ints_vltl": "45.94",
                }
            ]
        },
    )

    leg = parse_leg(snapshot)

    assert leg is not None
    assert (leg.price, leg.dte_days, leg.open_interest, leg.volume) == (26.30, 9.0, 1262.0, 137.0)
    assert leg.strike == 1040.0
    assert leg.kis_iv == pytest.approx(0.4594)  # % → 분수 (확정 ②)


def test_parse_leg_refuses_a_priceless_snapshot():
    """가격이 없거나 0이면 IV를 역산할 수 없다 — 0으로 채우지 않고 버린다."""
    snapshot = OptionQuoteSnapshot(
        underlying=_UNDERLYING,
        series="regular",
        option_type="C",
        strike=1040.0,
        expiry="콜 202609",
        symbol="X",
        raw={"output1": {"futs_prpr": "0", "hts_rmnn_dynu": "9"}},
    )

    assert parse_leg(snapshot) is None


# --------------------------------------------------------------- forward·유동성


def test_parity_forward_recovers_the_forward_from_the_chain():
    """선물가를 안 쓰고 **같은 스냅샷 안의 두 다리**로 forward를 뽑는다(시각 어긋남 상쇄)."""
    assert parity_forward(_chain()) == pytest.approx(_FORWARD, abs=0.5)


def test_a_leg_that_did_not_trade_today_is_not_liquid():
    """거래량 0인 다리의 `futs_prpr`는 오늘 가격이 아니라 유물이다 — 스마일에 안 넣는다."""
    assert is_liquid_leg(_leg(1040.0, "C", 20.0, volume=1.0)) is True
    assert is_liquid_leg(_leg(1040.0, "C", 20.0, volume=0.0)) is False
    assert is_liquid_leg(_leg(1040.0, "C", 20.0, oi=MIN_OPEN_INTEREST - 1)) is False


# --------------------------------------------------------------- IV 점·트림


def test_smile_points_take_the_out_of_the_money_side():
    """같은 행사가에 콜·풋이 다 있으면 외가를 쓴다 — 내가는 시간가치 비중이 작아 노이즈가 크다."""
    t = time_to_expiry_years(_DTE)
    points = smile_points(_chain(), _FORWARD, t)

    strikes = [k for k, _ in points]
    assert strikes == sorted(strikes)
    assert len(points) == 9  # 행사가당 정확히 하나
    for _, iv in points:
        assert iv == pytest.approx(0.45, abs=0.01)  # 만든 IV를 그대로 되돌린다


def test_a_stale_print_is_trimmed_away():
    """이웃이 전부 45%인데 혼자 16%인 점 — 2026-09-02 실측에 실제로 있던 형태."""
    points = [(1020.0, 0.45), (1030.0, 0.44), (1040.0, 0.45), (1050.0, 0.46), (1060.0, 0.16)]

    kept = trim_iv_outliers(points)

    assert (1060.0, 0.16) not in kept
    assert len(kept) == 4


def test_trimming_does_not_fire_on_a_thin_chain():
    """점이 5개 미만이면 MAD가 한두 점에 끌려다닌다 — 정상 점을 자를 위험이 더 크다."""
    points = [(1030.0, 0.45), (1040.0, 0.44), (1050.0, 0.16)]

    assert trim_iv_outliers(points) == points


# --------------------------------------------------------------- build_smile 사유


def test_build_smile_says_why_it_could_not():
    """`NO_OPTION`이 화면에 뜰 때 **무엇을 고쳐야 하는지**가 사유로 갈려 있어야 한다."""
    assert build_smile([])[1] == "체인 미수신"

    only_calls = [leg for leg in _chain() if leg.option_type == "C"]
    assert "forward" in build_smile(only_calls)[1]

    # 콜·풋은 다 있는데 **오늘 아무것도 안 거래된** 체인 — forward는 서지만 점이 안 선다.
    untraded = [
        _leg(strike, option_type, 10.0, volume=0.0)
        for strike in (1030.0, 1040.0, 1050.0)
        for option_type in ("C", "P")
    ]
    smile, reason = build_smile(untraded)
    assert smile is None and "유동성" in reason


def test_build_smile_fits_a_real_shaped_chain():
    smile, reason = build_smile(_chain(iv=0.45))

    assert smile is not None
    assert smile.dte == int(_DTE - DTE_SETTLEMENT_OFFSET_DAYS)
    assert smile.forward == pytest.approx(_FORWARD, abs=0.5)
    assert smile.iv_at(_FORWARD) == pytest.approx(0.45, abs=0.01)
    assert smile.is_reliable
    assert "점" in reason


# --------------------------------------------------------------- 제공자(구독 → 콜백)


@pytest.mark.asyncio
async def test_provider_builds_a_smile_from_published_snapshots():
    bus = InProcessBus()
    provider = ChainSmileProvider(_UNDERLYING, bus)

    for leg in _chain():
        await provider.handle_snapshot(
            OptionQuoteSnapshot(
                underlying=_UNDERLYING,
                series="regular",
                option_type=leg.option_type,
                strike=leg.strike,
                expiry="콜 202609",
                symbol=leg.symbol,
                raw={
                    "output1": {
                        "futs_prpr": str(leg.price),
                        "hts_rmnn_dynu": str(int(_DTE)),
                        "hts_otst_stpl_qty": "500",
                        "acml_vol": "100",
                        "acpr": str(leg.strike),
                    }
                },
            )
        )

    smile = provider()

    assert smile is not None
    assert smile.iv_at(_FORWARD) == pytest.approx(0.45, abs=0.01)
    assert provider.stats["fits"] == 1


@pytest.mark.asyncio
async def test_provider_ignores_other_underlyings_and_series():
    """한 버스에 세 시리즈가 함께 흐른다 — 섞이면 만기가 다른 다리로 스마일을 만든다."""
    bus = InProcessBus()
    provider = ChainSmileProvider(_UNDERLYING, bus, series="regular")
    payload = {"output1": {"futs_prpr": "10", "hts_rmnn_dynu": "9", "acml_vol": "5"}}

    await provider.handle_snapshot(
        OptionQuoteSnapshot(
            underlying=_UNDERLYING,
            series="weekly_mon",
            option_type="C",
            strike=1040.0,
            expiry="x",
            symbol="W",
            raw=payload,
        )
    )
    await provider.handle_snapshot(
        OptionQuoteSnapshot(
            underlying="OTHER",
            series="regular",
            option_type="C",
            strike=1040.0,
            expiry="x",
            symbol="O",
            raw=payload,
        )
    )

    assert provider.stats["legs"] == 0
    assert provider() is None
    assert provider.last_reason == "체인 미수신"


@pytest.mark.asyncio
async def test_stale_legs_drop_out_of_the_smile():
    """체인이 끊기면 마지막 값이 영원히 스마일로 남으면 안 된다(L18 — 조용한 낡은 값)."""
    bus = InProcessBus()
    provider = ChainSmileProvider(_UNDERLYING, bus, max_age_seconds=60.0)
    for leg in _chain():
        provider._legs[leg.symbol] = ChainLeg(  # noqa: SLF001 — 나이만 바꿔 주입
            symbol=leg.symbol,
            series=leg.series,
            option_type=leg.option_type,
            strike=leg.strike,
            price=leg.price,
            dte_days=leg.dte_days,
            open_interest=leg.open_interest,
            volume=leg.volume,
            kis_iv=None,
            ts_utc=leg.ts_utc - timedelta(seconds=600),
        )

    assert provider() is None
    assert provider.last_reason == "체인 미수신"


# ------------------------------------------------- 배선: 살아 있는 버스에 실제로 물리는가
#
# 2026-09-03 P0(F-89). 위의 테스트는 전부 `handle_snapshot()`을 **직접** 불렀다 — 그래서
# `run_forever()`가 버스를 어떻게 부르는지는 하나도 보지 않았고, 그 자리에
# `async for msg in self._bus.subscribe(topic)`라는 계약 위반이 남은 채 병합됐다.
# 첫 실전 기동(08:25:38)에서 `TypeError`로 G2 세션 전체가 내려앉았다.
#
# 그러니 여기서는 **핸들러를 직접 부르지 않는다.** 버스에 발행만 하고, 다리가 들어왔는지로
# 구독이 실제로 걸렸는지를 본다.


async def test_run_forever_subscribes_through_the_real_bus_contract():
    """`run_forever()` 후 토픽에 발행하면 다리가 쌓인다 — 구독 배선이 실제로 걸렸다는 뜻."""
    bus = InProcessBus()
    provider = ChainSmileProvider(_UNDERLYING, bus, series="regular")

    await provider.run_forever()  # InProcessBus는 등록만 하고 즉시 반환한다

    await bus.publish(
        f"raw.option_chain.{_UNDERLYING}",
        OptionQuoteSnapshot(
            underlying=_UNDERLYING,
            series="regular",
            option_type="C",
            strike=1040.0,
            expiry="콜 202609",
            symbol="B01609A14",
            raw={
                "output1": {
                    "futs_prpr": "26.30",
                    "hts_rmnn_dynu": "9",
                    "hts_otst_stpl_qty": "1262",
                    "acml_vol": "137",
                }
            },
        ),
    )

    assert (
        provider.stats["legs"] == 1
    ), "발행이 핸들러에 닿지 않았다 — `run_forever()`의 구독 배선이 깨졌다(2026-09-03 F-89)"


async def test_run_forever_uses_the_callback_subscribe_signature():
    """구독 호출의 **형태**를 고정한다 — patterns는 리스트, handler는 바운드 메서드.

    위 테스트는 결과(다리가 쌓였는가)를 보고, 이 테스트는 계약(어떻게 불렀는가)을 본다.
    문자열 1개만 넘기던 종전 코드는 이 단언에서 걸린다.
    """
    calls: list[tuple] = []

    class RecordingBus:
        async def publish(self, topic, msg):  # pragma: no cover — 이 테스트는 발행 안 함
            pass

        async def subscribe(self, patterns, handler, *, on_kill=None):
            calls.append((patterns, handler, on_kill))

    provider = ChainSmileProvider(_UNDERLYING, RecordingBus(), series="regular")
    await provider.run_forever()

    assert len(calls) == 1
    patterns, handler, on_kill = calls[0]
    assert patterns == [f"raw.option_chain.{_UNDERLYING}"]
    assert handler == provider.handle_snapshot
    assert on_kill is None, "봉만 보는 순수 구독자다 — kill은 원한 구독자에게만(2026-08-07 P0-1)"
