"""옵션체인(OP) REST 폴링 — 시리즈 하나를 ATM±N 창으로 훑는다.

`FixedTickScheduler.run_forever(poller.poll_once)`로 구동된다. 매 호출마다
`IndexDerivativesMaster.nearest_expiry_chain()`으로 그 시리즈의 근월물 체인을 얻고, **기준가
근방 ATM±N 행사가만** 골라 `KISRestClient.get_quote()`로 순차 조회해
`raw.option_chain.{underlying}`에 `OptionQuoteSnapshot`을 발행한다.

## 인스턴스 하나 = 시리즈 하나

확정 유니버스는 3종(먼쓰리·월위클리·목위클리, `core/universe.py`)인데 **셋을 한 폴러가 돌지
않는다.** 시리즈마다 주기와 위상이 달라야 하기 때문이다(아래 유량 예산). `FixedTickScheduler`
가 `phase_offset_seconds`를 이미 갖고 있고 그 격자가 epoch 기준이라 재시작해도 위상이
유지되므로, 폴러를 3개 만들어 서로 다른 스케줄러에 태우는 것이 가장 단순하다.

## 유량 예산 — 이게 이 모듈의 설계 제약 전부다

마흐디(선행 프로젝트)가 같은 것을 만들다 두 번 크게 잃었다:

- 2026-07-08: 폴러마다 페이서를 따로 뒀더니 앱키 초당 한도를 넘겨 500 폭주 — **정규장 405분
  중 203분치 옵션체인이 통째로 유실**. 이후 "폴러별 페이서 분리"는 봉인.
- 2026-07-30: 3북을 균등하게 60초로 돌려 총수요 **0.663건/초(용량의 66%)**. 적응형 백오프가
  평균 1.29배·최대 2.61배까지 벌어지자 **옵션체인 25사이클(5.1%)이 통째로 유실**. 총수요가
  한계선에 붙어 있으면 호출 시각을 아무리 재배치해도 해결되지 않는다.
- 2026-08-03: 위클리 2북을 **같은 분에 몰아** 짝수분 30레그/홀수분 10레그로 3:1 쏠림 —
  밀림 39건의 100%가 짝수 버킷이었고, 짝수 사이클이 격자를 넘기면 다음 홀수분이 통째로
  스킵돼 결손 41분 중 39분이 홀수분이었다. **총량이 아니라 분산의 문제.**

그래서 MESSIAH는 처음부터 **시리즈별 주기 차등 + 위상 분리**로 간다(`scripts/run_l1_daily.py`
의 `_OPTION_CHAIN_PLAN`). ATM±10 · 먼쓰리 300초 · 위클리 각 600초 기준 총수요는

    먼쓰리 42/300 + 위클리월 42/600 + 위클리목 42/600 + 수급 3/60 = 0.330건/초

로 용량(1건/초, `rest_client.DEFAULT_MIN_REQUEST_INTERVAL_SECONDS`)의 33%다 — 백오프가
**3.03배**까지 벌어져도 버틴다(마흐디가 실제로 관측한 최대치가 2.61배였다). 3종을 균등하게
300초로 돌면 0.470건/초·내성 2.13배로 **그날 똑같이 잠겼을** 값이 된다.

## 전량 폴링은 구조적으로 금지한다

근월 체인 전량은 먼쓰리 780 · 월위클리 242 · 목위클리 334 = **1,356다리**다(2026-08-04
마스터파일 실측). 1건/초면 **1회 폴링에 22.6분**이라 애초에 성립하지 않는다. 그래서
기준가를 못 구하면 **전량으로 폴백하지 않고 그 사이클을 건너뛴다** — 폴백이 곧 폭주다.

## 왜 get_quote인가

`OptionQuoteSnapshot` docstring 참고 — 2026-08-04 실측으로 `get_asking_price`는 호가만,
`get_quote`는 IV·Greeks·미결제약정·KOSPI200 현물까지 준다. 현 스코프의 OP Feature는 전부
후자 쪽이라 다리당 1회만 부른다.

## 빈 체인의 이유를 가른다 (2026-08-07 P0-2)

2026-08-07에 `weekly_thu` 체인이 하루 종일 비었다. 폴러는 `OptionChainPollEmpty`(WARNING,
"마스터파일 갱신 필요할 수 있음")를 **22회** 찍었고, 그 문구를 믿고 마스터파일을 두 번
갱신했지만 결과는 같았다. 실제 원인은 **KRX 규정상 미상장**이었다 — 8월 둘째 목요일(8/13)은
코스피200옵션 월물 최종거래일이라 그날 만기 목위클리는 상장되지 않는다.

그 규칙은 이미 `core/event_calendar.py`에 있었다(`thursday_weekly_listed()`, 2026-07-10
마흐디 실측 이식). 폴러가 안 물어봤을 뿐이다. 이제 묻는다:

    캘린더=상장 · 체인 있음   → 정상 폴링 (조용)
    캘린더=상장 · 체인 없음   → OptionChainSeriesMissing        (ERROR, 3사이클 연속에서 1회)
    캘린더=미상장 · 체인 없음 → OptionChainSeriesNotListed      (DEBUG, 첫 사이클 1회)
    캘린더=미상장 · 체인 있음 → OptionChainCalendarViolation    (ERROR) + **그래도 수집한다**

넷째 줄이 이 설계의 핵심이다. **억제가 아니라 양방향 단언이다.** 미상장이라고 판정한 날에도
체인 조회는 계속하고, 받으면 운다. 그 비용은 사이클당 조회 0건(체인이 비면 다리 순회 자체가
없다)이고, 그 값으로 규정 지식을 매일 재검증한다. 억제만 하면 규정이 바뀌었을 때 만기 하루
짜리 체인을 조용히 받아 모델에 먹이게 되고, 그건 빈 파일보다 나쁘다.

**한계**: 마스터파일은 기동 시 한 번 적재된다. 장중에 신규 상장돼도 이 프로세스는 다음 날
기동 전까지 못 본다 — 넷째 줄은 "우리가 아는 마스터파일 기준"의 단언이다.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Sequence

from messiah.broker.kis import tr_codes
from messiah.broker.kis.rest_client import KISRestClient
from messiah.broker.kis.symbol_master import IndexDerivativesMaster, OptionLeg
from messiah.core import logging as mlog
from messiah.core.bus import TOPIC_RAW, BusLike
from messiah.core.messages import OptionQuoteSnapshot
from messiah.data import poll_retry

# 재시도 계층의 정본은 `data/poll_retry.py`다 (2026-08-10 A-4). 여기 있던 상수와
# `_fetch_with_retry()`를 그리로 옮겼다 — 같은 KIS 500을 받는 `InvestorFlowPoller`가
# 재시도 없이 그대로 잃고 있었고, 사유화된 메서드라 그 사실이 안 보였다.
# 이름을 여기에도 남기는 이유는 호출자·테스트가 이미 이 이름으로 부르고 있어서다.
RETRY_ATTEMPTS = poll_retry.RETRY_ATTEMPTS
RETRY_DELAY_SECONDS = poll_retry.RETRY_DELAY_SECONDS

_MISSING_STREAK_ALERT = 3
"""상장돼 있어야 할 체인이 몇 사이클 연속 비면 ERROR로 올리는가 (2026-08-07 P0-2).

3사이클이면 위클리(600초 격자) 기준 30분, 먼쓰리(300초) 기준 15분이다. 재시도 계층
(`OptionChainPollRetried`)이 사는 범위를 확실히 넘어서고, 사람이 조치할 시간도 남는다.

1사이클에서 울지 않는 이유: 마스터파일이 갱신되는 순간이나 기동 직후 한 사이클이 비는
것은 정상 범위다. 22사이클을 다 울지 않는 이유: 2026-08-07에 그렇게 해서 22줄이 나왔고,
그 22줄이 하나같이 틀린 처방("마스터파일 갱신 필요")을 가리켰다."""

DEFAULT_STRIKE_WINDOW = 10
"""ATM 편측 행사가 개수 — 창 크기는 (2N+1)×2다리.

마흐디는 ATM±2였지만 그건 **WS 구독 슬롯 한도(41)** 때문이지 피처 요구가 아니었다
(`main.py:82-85` — 3북×(2·2+1)×2+1 = 31/41, ±3이면 43으로 초과). MESSIAH의 이 경로는
REST라 그 제약이 없다.

문헌(마흐디 `RESEARCH_EXPIRY_SELECTION_v1.md`)의 "ATM±1~2 집중"도 **진입 대상** 규칙이지
관측 규칙이 아니다 — 같은 문서가 "깊은 OTM은 진입 금지, **관측만**"이라고 분리한다.

오히려 좁으면 위험하다는 실증이 있다: 마흐디 `options_intel.py`의 `GAMMA_FLIP_MIN_LEGS = 6`
("행사가 3개×콜풋 미만이면 GEX(S) 곡선이 몇 점으로만 결정돼 부호 전환 위치가 사실상
임의값")인데 ATM±2 = 10다리로 하한 바로 위였고, **감마플립 산출 실패가 조용해서 버그가
넉 달간 안 보였다**. ±10이면 42다리로 하한 대비 7배 여유다.
"""


class OptionChainPoller:
    """옵션 시리즈 **하나**의 ATM±N 체인을 주기 폴링한다.

    `FixedTickScheduler.run_forever(poller.poll_once)`로 구동 — 콜백은 무인자 코루틴이어야
    하는 스케줄러 계약(scheduler.py) 그대로 `poll_once()`가 그 시그니처.
    """

    def __init__(
        self,
        rest_client: KISRestClient,
        master: IndexDerivativesMaster,
        bus: BusLike,
        *,
        series: str,
        reference_price: Callable[[], float | None],
        underlying: str = "KOSPI200",
        strike_window: int = DEFAULT_STRIKE_WINDOW,
        retry_attempts: int = RETRY_ATTEMPTS,
        retry_delay_seconds: float = RETRY_DELAY_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        listed: Callable[[], bool] | None = None,
    ) -> None:
        """
        입력: `series`는 `core/universe.OPTION_SERIES_BY_TOKEN`의 값 하나
             ("regular"/"weekly_mon"/"weekly_thu"). `reference_price`는 ATM을 정할 기준가를
             주는 무인자 콜러블 — 없으면(None 반환) 그 사이클은 건너뛴다.
        실패 조건: `strike_window < 1`이면 ValueError(0이면 ATM 1개만 보는 셈인데, 그건
             `GAMMA_FLIP_MIN_LEGS=6`에도 못 미쳐 GEX가 성립하지 않는다).

        `listed`는 "오늘 이 시리즈가 상장돼 있는가"를 주는 무인자 콜러블이다(모듈 docstring
        "빈 체인의 이유를 가른다"). 안 주면 **항상 상장**으로 본다 — 캘린더를 모르는 호출자가
        조용히 면제받는 일이 없어야 한다. `reference_price`와 같은 이유로 주입받는다: 폴러가
        `EventCalendar`를 직접 열면 테스트마다 휴장일 파일이 필요해진다.

        `reference_price`를 **주입받는 이유**: 폴러가 버스를 직접 구독하면 테스트마다 버스를
        띄워야 하고, 기준가 출처를 바꿀 때(선물 틱 → KOSPI200 현물) 폴러를 고쳐야 한다.
        지금 출처는 미니선물 현재가다 — 옵션은 KOSPI200 현물 기준이라 베이시스만큼 어긋나지만,
        행사가 간격 2.5pt 대비 기준창이 한 칸 밀리는 정도라 ±10에서는 무해하다. (역설적이게도
        `get_quote` 응답의 `output3`이 KOSPI200 현물을 실어 나르므로, 한 사이클 돈 뒤에는 더
        정확한 기준가를 쓸 수 있다 — 다만 그 전환은 실제 응답이 쌓인 뒤의 별도 판단이다.)
        """
        if strike_window < 1:
            raise ValueError("strike_window는 1 이상이어야 한다 — 0이면 GEX가 성립하지 않는다")
        if retry_attempts < 0:
            raise ValueError("retry_attempts는 0 이상이어야 한다")
        self._rest_client = rest_client
        self._master = master
        self._bus = bus
        self._series = series
        self._reference_price = reference_price
        self._underlying = underlying
        self._strike_window = strike_window
        self._retry_attempts = retry_attempts
        self._retry_delay_seconds = retry_delay_seconds
        self._sleep = sleep
        self._listed = listed
        # 빈 체인이 연속으로 몇 사이클째인가 (2026-08-07 P0-2). 사이클마다 울면 22줄이
        # 되고 그건 아무도 안 읽는다 — `_MISSING_STREAK_ALERT`에서 딱 한 번 크게 운다.
        self._empty_streak = 0
        # 미상장 안내를 이미 찍었는가 — 하루에 한 번이면 충분하다.
        self._not_listed_announced = False

    @property
    def series(self) -> str:
        return self._series

    @property
    def legs_per_cycle(self) -> int:
        """이 폴러가 한 사이클에 쓸 REST 호출 수의 상한 — 기동 시 유량 예산을 찍는 데 쓴다.
        실제 상장 행사가가 창보다 적으면 이보다 적게 나간다."""
        return (2 * self._strike_window + 1) * 2

    @property
    def expected_legs_per_cycle(self) -> int:
        """오늘 **실제로** 나갈 호출 수 — 미상장 시리즈는 0이다 (2026-08-07 P1-1).

        `legs_per_cycle`은 선언(창 크기)이고 이쪽은 관측 전제다. 유량 예산이 선언을 세면
        2026-08-07처럼 3계열 기준 0.330건/초를 찍는데 실수요는 2계열분이었다. 그날은 여유
        방향이라 무해했지만, **예산이 실제와 무관하다는 사실 자체가 결함이다** — 반대
        방향으로 어긋나면 그게 곧 유량 초과이고, 마흐디가 두 번 그렇게 잃었다.
        """
        if self._listed is not None and not self._listed():
            return 0
        return self.legs_per_cycle

    async def poll_once(self) -> None:
        """ATM±N 다리를 순차 조회해 발행한다.

        다리 하나의 실패가 나머지를 막지 않는다(L22 — 항목 하나의 실패가 루프 전체를 죽이면
        안 됨). 기준가가 없거나 체인이 비면 **전량 폴백 없이** 건너뛴다(모듈 docstring).
        """
        spot = self._reference_price()
        if spot is None or spot <= 0:
            mlog.log(
                "OptionChainSkipped",
                "기준가 없음 — 이 사이클을 건너뛴다(전량 폴링으로 폴백하지 않음)",
                underlying=self._underlying,
                series=self._series,
            )
            return

        chain = self._master.nearest_expiry_chain(self._underlying, series=self._series)
        listed = self._listed() if self._listed is not None else True

        if not chain:
            self._report_empty_chain(listed)
            return

        self._empty_streak = 0
        if not listed:
            # 미상장이라고 판정했는데 체인이 있다 — **규정 이해가 틀렸다.** 데이터는 그대로
            # 받는다(모듈 docstring "양방향 단언"): 버리면 나중에 무엇을 받았는지 못 본다.
            mlog.log(
                "OptionChainCalendarViolation",
                "캘린더는 미상장이라 했는데 체인이 있다 — 규정 판정이 틀렸거나 "
                "시리즈 매핑이 어긋났다. 수집은 계속한다",
                underlying=self._underlying,
                series=self._series,
                legs=len(chain),
                nearest=chain[0].month_label,
            )

        for leg in select_atm_window(chain, spot, self._strike_window):
            await self._poll_one(leg)

    def _report_empty_chain(self, listed: bool) -> None:
        """빈 체인의 이유를 가려서 딱 필요한 만큼만 운다 — 모듈 docstring의 표 그대로."""
        if not listed:
            self._empty_streak = 0
            if not self._not_listed_announced:
                self._not_listed_announced = True
                mlog.log(
                    "OptionChainSeriesNotListed",
                    "규정상 미상장 — 오늘 이 시리즈는 수집하지 않는다(정상)",
                    underlying=self._underlying,
                    series=self._series,
                )
            return

        self._empty_streak += 1
        # 사이클마다 빵부스러기는 남긴다 — DEBUG라 경보가 아니지만, 나중에 "몇 시부터
        # 비었나"를 로그에서 바로 찾을 수 있어야 한다. 2026-08-07에 이 줄이 WARNING이라
        # 22번 울었고, 그래서 이 태그는 **강등**됐지 사라진 게 아니다.
        mlog.log(
            "OptionChainPollEmpty",
            f"근월물 체인이 비어 있음 ({self._empty_streak}사이클째)",
            underlying=self._underlying,
            series=self._series,
            cycles=self._empty_streak,
        )
        if self._empty_streak == _MISSING_STREAK_ALERT:
            # 정확히 한 번만 운다. 넘어선 뒤엔 조용한데, 그 조용함은 "복구됐다"가 아니라
            # "이미 말했다"이다 — 복구 여부는 장후 커버리지 축이 판정한다.
            mlog.log(
                "OptionChainSeriesMissing",
                f"상장돼 있어야 할 시리즈의 체인이 {self._empty_streak}사이클 연속 비어 있다 "
                f"— 마스터파일 또는 시리즈 매핑 확인 필요",
                underlying=self._underlying,
                series=self._series,
                cycles=self._empty_streak,
            )

    async def _poll_one(self, leg: OptionLeg) -> None:
        raw = await self._fetch_with_retry(leg)
        if raw is None:
            return

        snapshot = OptionQuoteSnapshot(
            underlying=self._underlying,
            series=self._series,
            option_type=leg.option_type,
            strike=leg.strike,
            expiry=leg.month_label,
            symbol=leg.symbol,
            raw=raw,
        )
        try:
            await self._bus.publish(f"{TOPIC_RAW}.option_chain.{self._underlying}", snapshot)
        except Exception as exc:  # noqa: BLE001
            mlog.log(
                "OptionChainPollError",
                f"발행 실패: {exc}",
                underlying=self._underlying,
                series=self._series,
                symbol=leg.symbol,
            )

    async def _fetch_with_retry(self, leg: OptionLeg) -> dict | None:
        """다리 1개를 조회한다 — 재시도 계층의 정본은 `data/poll_retry.py`다 (2026-08-10 A-4).

        태그를 세 갈래로 가르는 규율(`OptionChainPollRetried` vs `OptionChainPollError`)은
        그쪽 모듈 docstring에 있다. 여기서 하는 일은 **호출을 만들고 어휘를 싣는 것**뿐이다.
        """
        return await poll_retry.fetch_with_retry(
            lambda: asyncio.to_thread(
                self._rest_client.get_quote, leg.symbol, tr_codes.FID_MRKT_DIV_INDEX_OPTION
            ),
            retried_tag="OptionChainPollRetried",
            error_tag="OptionChainPollError",
            # 계열 이름은 아카이브·커버리지 축과 **같은 어휘**를 쓴다 — 장중 장부와 장후
            # 커버리지를 나란히 놓고 검산하려면 이름이 같아야 한다.
            loss_series=f"option_chain/{self._series}",
            retry_attempts=self._retry_attempts,
            retry_delay_seconds=self._retry_delay_seconds,
            sleep=self._sleep,
            underlying=self._underlying,
            series=self._series,
            symbol=leg.symbol,
        )


def select_atm_window(
    chain: Sequence[OptionLeg], spot: float, strike_window: int
) -> list[OptionLeg]:
    """
    입력: 한 시리즈의 근월 체인 전체, 기준가, 편측 행사가 개수.
    계산: **실제 상장 행사가**를 오름차순으로 세워 기준가에 가장 가까운 것을 ATM으로 잡고,
         그 앞뒤 `strike_window`개씩을 취한다. 마흐디처럼 `round(spot/interval)*interval`로
         격자를 **생성**하지 않는 이유: 그 방식은 간격이 균일하고 그 행사가가 반드시 상장돼
         있다고 가정한다(마흐디도 `symbol_formatter`가 None을 주면 조용히 건너뛰는 보정이
         필요했다). 상장 목록에서 고르면 그 가정 자체가 필요 없고, 창 가장자리가 상장 범위를
         벗어나도 자동으로 잘린다.
    반환: 행사가 오름차순 → 같은 행사가 안에서 C, P 순. 호출 순서를 결정론적으로 두는 것은
         로그·아카이브 대조 때 "어디까지 돌다 끊겼나"를 읽기 위해서다.
    """
    if not chain:
        return []
    strikes = sorted({leg.strike for leg in chain})
    atm_idx = min(range(len(strikes)), key=lambda i: (abs(strikes[i] - spot), i))
    lo = max(0, atm_idx - strike_window)
    hi = min(len(strikes), atm_idx + strike_window + 1)
    wanted = set(strikes[lo:hi])
    return sorted(
        (leg for leg in chain if leg.strike in wanted),
        key=lambda leg: (leg.strike, leg.option_type),
    )
