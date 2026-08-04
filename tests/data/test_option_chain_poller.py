"""옵션체인 REST 폴러 — 시리즈 1개 · ATM±N · get_quote (2026-08-04 재작성).

재작성 전에는 `get_asking_price()`로 근월 체인 **전량**을 돌았다. 실측으로 두 가지가
바뀌었다: (1) 전량은 1,356다리=22.6분이라 성립하지 않고, (2) OP Feature가 필요로 하는
IV/Greeks/OI는 `get_asking_price`가 아니라 `get_quote`에 있다.
"""

from __future__ import annotations

import pytest

from messiah.broker.kis import tr_codes
from messiah.broker.kis.symbol_master import OptionLeg
from messiah.core.messages import OptionQuoteSnapshot
from messiah.core.scheduler import FixedTickScheduler
from messiah.data.option_chain_poller import OptionChainPoller, select_atm_window


class FakeBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, object]] = []

    async def publish(self, topic: str, msg: object) -> None:
        self.published.append((topic, msg))

    async def subscribe(self, patterns, handler) -> None:  # pragma: no cover — 미사용
        raise NotImplementedError


class FakeMaster:
    def __init__(self, chain: list[OptionLeg]) -> None:
        self._chain = chain
        self.calls: list[tuple[str, str]] = []

    def nearest_expiry_chain(self, underlying: str, *, series: str = "regular") -> list[OptionLeg]:
        self.calls.append((underlying, series))
        return self._chain


class FakeRestClient:
    def __init__(self, fail_for: set[str] | None = None) -> None:
        self._fail_for = fail_for or set()
        self.calls: list[tuple[str, str]] = []

    def get_quote(self, symbol: str, market_div_code: str = "O") -> dict:
        self.calls.append((symbol, market_div_code))
        if symbol in self._fail_for:
            raise RuntimeError(f"KIS 500: {symbol}")
        return {"rt_cd": "0", "output1": {"futs_prpr": "1.23"}}


def _chain(strikes, series_prefix="B01608"):
    """행사가마다 콜/풋 한 쌍. 실제 마스터처럼 순서를 섞어 둔다 — 선택기가 정렬에
    의존하지 않는지 보려고."""
    legs = []
    for strike in strikes:
        for opt in ("P", "C"):
            legs.append(
                OptionLeg(
                    option_type=opt,
                    strike=float(strike),
                    symbol=f"{series_prefix}{opt}{int(strike * 10)}",
                    month_label=f"{opt} 202608 {strike}",
                )
            )
    return legs


# ------------------------------------------------------------ ATM 창 선택기


def test_selects_atm_and_n_strikes_each_side():
    chain = _chain([100.0, 102.5, 105.0, 107.5, 110.0, 112.5, 115.0])

    picked = select_atm_window(chain, spot=107.6, strike_window=1)

    assert sorted({leg.strike for leg in picked}) == [105.0, 107.5, 110.0]
    assert len(picked) == 6  # 3행사가 × 콜/풋


def test_window_is_clipped_at_the_edges_of_the_listed_range():
    """창이 상장 범위를 벗어나면 잘린다 — 격자를 생성하지 않고 상장 목록에서 고르기 때문."""
    chain = _chain([100.0, 102.5, 105.0])

    picked = select_atm_window(chain, spot=100.0, strike_window=5)

    assert sorted({leg.strike for leg in picked}) == [100.0, 102.5, 105.0]


def test_atm_is_the_nearest_listed_strike_not_a_generated_grid_point():
    """상장 행사가가 균일 간격이 아니어도 동작해야 한다(마흐디식 round(spot/interval)는
    간격 균일 + 그 행사가가 상장돼 있음을 가정한다)."""
    chain = _chain([100.0, 130.0, 131.0])

    picked = select_atm_window(chain, spot=129.0, strike_window=0)

    assert {leg.strike for leg in picked} == {130.0}


def test_result_is_ordered_by_strike_then_option_type():
    chain = _chain([110.0, 100.0, 105.0])

    picked = select_atm_window(chain, spot=105.0, strike_window=5)

    assert [(leg.strike, leg.option_type) for leg in picked] == [
        (100.0, "C"),
        (100.0, "P"),
        (105.0, "C"),
        (105.0, "P"),
        (110.0, "C"),
        (110.0, "P"),
    ]


def test_empty_chain_selects_nothing():
    assert select_atm_window([], spot=100.0, strike_window=3) == []


# ------------------------------------------------------------ 폴링


async def test_poll_once_queries_only_the_atm_window_with_get_quote():
    chain = _chain([100.0, 102.5, 105.0, 107.5, 110.0])
    master = FakeMaster(chain)
    rest = FakeRestClient()
    bus = FakeBus()
    poller = OptionChainPoller(
        rest, master, bus, series="weekly_mon", reference_price=lambda: 105.0, strike_window=1
    )

    await poller.poll_once()

    assert master.calls == [("KOSPI200", "weekly_mon")]
    assert len(rest.calls) == 6  # 3행사가 × 2 — 전량(10)이 아니다
    assert {code for _, code in rest.calls} == {tr_codes.FID_MRKT_DIV_INDEX_OPTION}
    assert len(bus.published) == 6
    topics = {t for t, _ in bus.published}
    assert topics == {"raw.option_chain.KOSPI200"}


async def test_published_snapshot_carries_series_and_raw_response():
    master = FakeMaster(_chain([100.0]))
    bus = FakeBus()
    poller = OptionChainPoller(
        FakeRestClient(),
        master,
        bus,
        series="weekly_thu",
        reference_price=lambda: 100.0,
        strike_window=1,
    )

    await poller.poll_once()

    snap = bus.published[0][1]
    assert isinstance(snap, OptionQuoteSnapshot)
    assert snap.series == "weekly_thu"
    assert snap.strike == 100.0
    assert snap.raw == {"rt_cd": "0", "output1": {"futs_prpr": "1.23"}}


async def test_missing_reference_price_skips_the_cycle_without_falling_back(monkeypatch):
    """**전량 폴백 금지** — 전량은 1,356다리(22.6분)라 폴백이 곧 폭주다."""
    logged: list[str] = []
    monkeypatch.setattr(
        "messiah.data.option_chain_poller.mlog.log", lambda tag, msg, **f: logged.append(tag)
    )
    master = FakeMaster(_chain([100.0, 102.5, 105.0]))
    rest = FakeRestClient()
    poller = OptionChainPoller(
        rest, master, FakeBus(), series="regular", reference_price=lambda: None
    )

    await poller.poll_once()

    assert rest.calls == []
    assert master.calls == []  # 체인 조회조차 안 한다
    assert "OptionChainSkipped" in logged


async def test_non_positive_reference_price_also_skips(monkeypatch):
    monkeypatch.setattr("messiah.data.option_chain_poller.mlog.log", lambda tag, msg, **f: None)
    rest = FakeRestClient()
    poller = OptionChainPoller(
        rest, FakeMaster(_chain([100.0])), FakeBus(), series="regular", reference_price=lambda: 0.0
    )

    await poller.poll_once()

    assert rest.calls == []


async def test_empty_chain_logs_and_stops(monkeypatch):
    logged: list[str] = []
    monkeypatch.setattr(
        "messiah.data.option_chain_poller.mlog.log", lambda tag, msg, **f: logged.append(tag)
    )
    rest = FakeRestClient()
    poller = OptionChainPoller(
        rest, FakeMaster([]), FakeBus(), series="weekly_mon", reference_price=lambda: 100.0
    )

    await poller.poll_once()

    assert rest.calls == []
    assert "OptionChainPollEmpty" in logged


async def test_one_leg_failure_does_not_stop_the_rest(monkeypatch):
    logged: list[str] = []
    monkeypatch.setattr(
        "messiah.data.option_chain_poller.mlog.log", lambda tag, msg, **f: logged.append(tag)
    )
    chain = _chain([100.0])
    failing = chain[0].symbol  # 풋
    master = FakeMaster(chain)
    rest = FakeRestClient(fail_for={failing})
    bus = FakeBus()
    poller = OptionChainPoller(
        rest, master, bus, series="regular", reference_price=lambda: 100.0, strike_window=1
    )

    await poller.poll_once()

    assert len(rest.calls) == 2  # 둘 다 시도
    assert len(bus.published) == 1  # 성공한 것만 발행
    assert "OptionChainPollError" in logged


async def test_publish_failure_is_logged_but_not_raised(monkeypatch):
    logged: list[str] = []
    monkeypatch.setattr(
        "messiah.data.option_chain_poller.mlog.log", lambda tag, msg, **f: logged.append(tag)
    )

    class FailingBus(FakeBus):
        async def publish(self, topic: str, msg: object) -> None:
            raise ConnectionError("Redis 다운")

    poller = OptionChainPoller(
        FakeRestClient(),
        FakeMaster(_chain([100.0])),
        FailingBus(),
        series="regular",
        reference_price=lambda: 100.0,
        strike_window=1,
    )

    await poller.poll_once()  # 예외 전파 없이 로깅만

    assert "OptionChainPollError" in logged


# ------------------------------------------------------------ 예산 · 구성


def test_legs_per_cycle_reports_the_rate_budget_input():
    """기동 로그가 유량 예산을 찍으려면 폴러가 자기 비용을 말할 수 있어야 한다."""
    poller = OptionChainPoller(
        FakeRestClient(),
        FakeMaster([]),
        FakeBus(),
        series="regular",
        reference_price=lambda: 100.0,
        strike_window=10,
    )

    assert poller.legs_per_cycle == 42  # (2*10+1)*2 — 계획서의 예산 계산과 같은 값


def test_zero_strike_window_is_rejected():
    """ATM 1행사가(2다리)는 GAMMA_FLIP_MIN_LEGS=6에도 못 미친다."""
    with pytest.raises(ValueError):
        OptionChainPoller(
            FakeRestClient(),
            FakeMaster([]),
            FakeBus(),
            series="regular",
            reference_price=lambda: 100.0,
            strike_window=0,
        )


async def test_scheduler_drives_poll_once_repeatedly():
    master = FakeMaster(_chain([100.0]))
    rest = FakeRestClient()
    bus = FakeBus()
    poller = OptionChainPoller(
        rest, master, bus, series="regular", reference_price=lambda: 100.0, strike_window=1
    )

    await FixedTickScheduler(tick_seconds=0.05).run_forever(poller.poll_once, max_iterations=3)

    assert len(rest.calls) == 6  # 2다리 × 3회
    assert len(bus.published) == 6
