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


class FlakyRestClient(FakeRestClient):
    """지정한 심볼이 **첫 호출에서만** 실패한다 — 2026-08-05 실측의 일시적 500/끊김 재현."""

    def __init__(self, fail_once_for: set[str]) -> None:
        super().__init__()
        self._pending = set(fail_once_for)

    def get_quote(self, symbol: str, market_div_code: str = "O") -> dict:
        if symbol in self._pending:
            self._pending.discard(symbol)
            self.calls.append((symbol, market_div_code))
            raise RuntimeError(f"KIS 500(일시): {symbol}")
        return super().get_quote(symbol, market_div_code)


async def _no_sleep(_seconds: float) -> None:
    """재시도 지연을 실제로 자지 않는다 — 대기 여부는 `sleep` 주입으로 따로 검증한다."""


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
        rest,
        master,
        bus,
        series="regular",
        reference_price=lambda: 100.0,
        strike_window=1,
        sleep=_no_sleep,
    )

    await poller.poll_once()

    # 실패한 다리는 재시도까지 2번, 성공한 다리는 1번 (2026-08-05 재시도 도입)
    assert len(rest.calls) == 3
    assert len(bus.published) == 1  # 성공한 것만 발행
    assert "OptionChainPollError" in logged


# ------------------------------------------- 다리 재시도 (2026-08-05 장중 실측 대응)
#
# 그날 5건이 실패해(500 ×3 · disconnect ×2) 먼쓰리 3사이클·목위클리 1사이클이 41다리로 남았다.
# 유량 점유가 33%(내성 3.03배)였으므로 재시도할 여유가 없어서가 아니라 경로가 없어서였다.


async def test_transient_failure_is_recovered_by_the_retry(monkeypatch):
    """첫 호출만 실패하는 다리는 **같은 사이클 안에서** 살아나야 한다."""
    logged: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "messiah.data.option_chain_poller.mlog.log",
        lambda tag, msg, **f: logged.append((tag, f)),
    )
    chain = _chain([100.0])
    flaky = chain[0].symbol
    rest = FlakyRestClient(fail_once_for={flaky})
    bus = FakeBus()
    poller = OptionChainPoller(
        rest,
        FakeMaster(chain),
        bus,
        series="regular",
        reference_price=lambda: 100.0,
        strike_window=1,
        sleep=_no_sleep,
    )

    await poller.poll_once()

    assert len(bus.published) == 2, "재시도로 살아난 다리가 발행되지 않았다"
    tags = [tag for tag, _ in logged]
    assert "OptionChainPollRetried" in tags
    # **결손이 아니므로** WARNING 태그는 안 나와야 한다 — 안 그러면 리포트의 WARNING 수가
    # 더 이상 "잃은 다리 수"를 뜻하지 않는다.
    assert "OptionChainPollError" not in tags


async def test_retry_waits_before_trying_again():
    """즉시 재시도는 대개 또 500이다 — 최소한 설정된 지연만큼은 쉬어야 한다."""
    slept: list[float] = []

    async def _record(seconds: float) -> None:
        slept.append(seconds)

    chain = _chain([100.0])
    rest = FlakyRestClient(fail_once_for={chain[0].symbol})
    poller = OptionChainPoller(
        rest,
        FakeMaster(chain),
        FakeBus(),
        series="regular",
        reference_price=lambda: 100.0,
        strike_window=1,
        retry_delay_seconds=0.5,
        sleep=_record,
    )

    await poller.poll_once()

    assert slept == [0.5]  # 실패한 다리 1개에 대해 한 번만


async def test_persistent_failure_still_gives_up_and_reports_the_attempts(monkeypatch):
    """계속 실패하는 다리는 결국 포기하되, **몇 번 시도했는지**가 로그에 남아야 한다 —
    다음 점검이 "서버가 잠깐 흔들린 것"과 "그 종목이 계속 안 된 것"을 구분할 수 있게."""
    logged: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "messiah.data.option_chain_poller.mlog.log",
        lambda tag, msg, **f: logged.append((tag, f)),
    )
    chain = _chain([100.0])
    dead = chain[0].symbol
    rest = FakeRestClient(fail_for={dead})
    poller = OptionChainPoller(
        rest,
        FakeMaster(chain),
        FakeBus(),
        series="regular",
        reference_price=lambda: 100.0,
        strike_window=1,
        sleep=_no_sleep,
    )

    await poller.poll_once()

    errors = [f for tag, f in logged if tag == "OptionChainPollError"]
    assert len(errors) == 1, "포기한 다리는 정확히 한 번만 WARNING이어야 한다"
    assert errors[0]["attempts"] == 2
    assert errors[0]["symbol"] == dead


async def test_retry_can_be_disabled():
    """`retry_attempts=0`이면 2026-08-05 이전과 완전히 같은 동작 — 유량이 빠듯한 인스턴스가
    끌 수 있어야 한다."""
    chain = _chain([100.0])
    rest = FakeRestClient(fail_for={chain[0].symbol})
    poller = OptionChainPoller(
        rest,
        FakeMaster(chain),
        FakeBus(),
        series="regular",
        reference_price=lambda: 100.0,
        strike_window=1,
        retry_attempts=0,
        sleep=_no_sleep,
    )

    await poller.poll_once()

    assert len(rest.calls) == 2  # 다리 2개 × 1회씩


def test_negative_retry_attempts_is_rejected():
    with pytest.raises(ValueError, match="retry_attempts"):
        OptionChainPoller(
            FakeRestClient(),
            FakeMaster([]),
            FakeBus(),
            series="regular",
            reference_price=lambda: 100.0,
            retry_attempts=-1,
        )


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


# ---------------------------------------------------------------- 캘린더 게이트 (2026-08-07 P0-2)
#
# 2026-08-07 실측: `weekly_thu` 체인이 하루 종일 비었고 폴러는 `OptionChainPollEmpty`
# (당시 WARNING)를 22회 찍었다. 원인은 KRX 규정상 미상장이었고, 그 22줄이 가리킨 처방
# ("마스터파일 갱신 필요")은 전부 틀렸다. 아래 넷이 그날의 네 가지 경우다.


async def test_not_listed_series_announces_once_and_stays_quiet(monkeypatch):
    """캘린더=미상장 · 체인 없음 → 하루 한 번만 안내하고 조용하다."""
    logged: list[str] = []
    monkeypatch.setattr(
        "messiah.data.option_chain_poller.mlog.log", lambda tag, msg, **f: logged.append(tag)
    )
    poller = OptionChainPoller(
        FakeRestClient(),
        FakeMaster([]),
        FakeBus(),
        series="weekly_thu",
        reference_price=lambda: 100.0,
        listed=lambda: False,
    )

    for _ in range(22):  # 그날 실제로 돈 사이클 수
        await poller.poll_once()

    assert logged == ["OptionChainSeriesNotListed"]
    assert "OptionChainPollEmpty" not in logged
    assert "OptionChainSeriesMissing" not in logged


async def test_listed_but_empty_escalates_once_at_the_streak_threshold(monkeypatch):
    """캘린더=상장 · 체인 없음 → 진짜 사고. 3사이클째에 **딱 한 번** ERROR."""
    logged: list[str] = []
    monkeypatch.setattr(
        "messiah.data.option_chain_poller.mlog.log", lambda tag, msg, **f: logged.append(tag)
    )
    poller = OptionChainPoller(
        FakeRestClient(),
        FakeMaster([]),
        FakeBus(),
        series="regular",
        reference_price=lambda: 100.0,
        listed=lambda: True,
    )

    for _ in range(10):
        await poller.poll_once()

    assert logged.count("OptionChainSeriesMissing") == 1
    # 빵부스러기는 매 사이클 남는다(DEBUG) — "몇 시부터 비었나"를 찾을 수 있어야 한다.
    assert logged.count("OptionChainPollEmpty") == 10


async def test_calendar_violation_still_collects(monkeypatch):
    """캘린더=미상장 · 체인 **있음** → 운다. 그리고 **그래도 수집한다**.

    양방향 단언의 핵심. 억제만 하면 규정이 바뀐 날 만기 하루짜리 체인을 조용히 받아
    모델에 먹인다 — 빈 파일보다 나쁘다.
    """
    logged: list[str] = []
    monkeypatch.setattr(
        "messiah.data.option_chain_poller.mlog.log", lambda tag, msg, **f: logged.append(tag)
    )
    rest = FakeRestClient()
    poller = OptionChainPoller(
        rest,
        FakeMaster(_chain([100.0])),
        FakeBus(),
        series="weekly_thu",
        reference_price=lambda: 100.0,
        listed=lambda: False,
    )

    await poller.poll_once()

    assert "OptionChainCalendarViolation" in logged
    assert rest.calls, "미상장 판정이 수집을 막으면 안 된다 — 받은 것은 버리지 않는다"


async def test_recovery_resets_the_streak(monkeypatch):
    """비었다가 돌아오면 streak이 풀린다 — 다음에 또 3사이클 비면 다시 운다."""
    logged: list[str] = []
    monkeypatch.setattr(
        "messiah.data.option_chain_poller.mlog.log", lambda tag, msg, **f: logged.append(tag)
    )
    master = FakeMaster([])
    poller = OptionChainPoller(
        FakeRestClient(),
        master,
        FakeBus(),
        series="regular",
        reference_price=lambda: 100.0,
        listed=lambda: True,
    )

    for _ in range(3):
        await poller.poll_once()
    assert logged.count("OptionChainSeriesMissing") == 1

    master._chain = _chain([100.0])  # 복구
    await poller.poll_once()

    master._chain = []  # 다시 끊김
    for _ in range(3):
        await poller.poll_once()
    assert logged.count("OptionChainSeriesMissing") == 2


async def test_expected_legs_is_zero_for_unlisted_series():
    """유량 예산이 **선언이 아니라 오늘 실제 수요**를 세게 하는 값 (P1-1)."""
    listed = OptionChainPoller(
        FakeRestClient(),
        FakeMaster([]),
        FakeBus(),
        series="regular",
        reference_price=lambda: 100.0,
        listed=lambda: True,
    )
    unlisted = OptionChainPoller(
        FakeRestClient(),
        FakeMaster([]),
        FakeBus(),
        series="weekly_thu",
        reference_price=lambda: 100.0,
        listed=lambda: False,
    )
    unknown = OptionChainPoller(
        FakeRestClient(),
        FakeMaster([]),
        FakeBus(),
        series="regular",
        reference_price=lambda: 100.0,
    )

    assert listed.expected_legs_per_cycle == listed.legs_per_cycle
    assert unlisted.expected_legs_per_cycle == 0
    # 캘린더를 안 준 호출자는 **면제받지 않는다**.
    assert unknown.expected_legs_per_cycle == unknown.legs_per_cycle
