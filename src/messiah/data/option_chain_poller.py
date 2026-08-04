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
"""

from __future__ import annotations

import asyncio
from typing import Callable, Sequence

from messiah.broker.kis import tr_codes
from messiah.broker.kis.rest_client import KISRestClient
from messiah.broker.kis.symbol_master import IndexDerivativesMaster, OptionLeg
from messiah.core import logging as mlog
from messiah.core.bus import TOPIC_RAW, BusLike
from messiah.core.messages import OptionQuoteSnapshot

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
    ) -> None:
        """
        입력: `series`는 `core/universe.OPTION_SERIES_BY_TOKEN`의 값 하나
             ("regular"/"weekly_mon"/"weekly_thu"). `reference_price`는 ATM을 정할 기준가를
             주는 무인자 콜러블 — 없으면(None 반환) 그 사이클은 건너뛴다.
        실패 조건: `strike_window < 1`이면 ValueError(0이면 ATM 1개만 보는 셈인데, 그건
             `GAMMA_FLIP_MIN_LEGS=6`에도 못 미쳐 GEX가 성립하지 않는다).

        `reference_price`를 **주입받는 이유**: 폴러가 버스를 직접 구독하면 테스트마다 버스를
        띄워야 하고, 기준가 출처를 바꿀 때(선물 틱 → KOSPI200 현물) 폴러를 고쳐야 한다.
        지금 출처는 미니선물 현재가다 — 옵션은 KOSPI200 현물 기준이라 베이시스만큼 어긋나지만,
        행사가 간격 2.5pt 대비 기준창이 한 칸 밀리는 정도라 ±10에서는 무해하다. (역설적이게도
        `get_quote` 응답의 `output3`이 KOSPI200 현물을 실어 나르므로, 한 사이클 돈 뒤에는 더
        정확한 기준가를 쓸 수 있다 — 다만 그 전환은 실제 응답이 쌓인 뒤의 별도 판단이다.)
        """
        if strike_window < 1:
            raise ValueError("strike_window는 1 이상이어야 한다 — 0이면 GEX가 성립하지 않는다")
        self._rest_client = rest_client
        self._master = master
        self._bus = bus
        self._series = series
        self._reference_price = reference_price
        self._underlying = underlying
        self._strike_window = strike_window

    @property
    def series(self) -> str:
        return self._series

    @property
    def legs_per_cycle(self) -> int:
        """이 폴러가 한 사이클에 쓸 REST 호출 수의 상한 — 기동 시 유량 예산을 찍는 데 쓴다.
        실제 상장 행사가가 창보다 적으면 이보다 적게 나간다."""
        return (2 * self._strike_window + 1) * 2

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
        if not chain:
            mlog.log(
                "OptionChainPollEmpty",
                "근월물 체인이 비어 있음 — 마스터파일 갱신 필요할 수 있음",
                underlying=self._underlying,
                series=self._series,
            )
            return

        for leg in select_atm_window(chain, spot, self._strike_window):
            await self._poll_one(leg)

    async def _poll_one(self, leg: OptionLeg) -> None:
        try:
            raw = await asyncio.to_thread(
                self._rest_client.get_quote, leg.symbol, tr_codes.FID_MRKT_DIV_INDEX_OPTION
            )
        except Exception as exc:  # noqa: BLE001 — REST 실패로 폴링 루프가 죽으면 안 됨
            mlog.log(
                "OptionChainPollError",
                f"조회 실패: {exc}",
                underlying=self._underlying,
                series=self._series,
                symbol=leg.symbol,
            )
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
