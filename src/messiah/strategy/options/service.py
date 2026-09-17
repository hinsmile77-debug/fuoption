"""OptionsAIService — bar.5m·intel.futures 구독 → surface→matrix→evaluator→safety →
intel.options 발행 진입점 (Ver 1.3 §10, §2 갱신주기표, Ver 2.0 §9 W30~31).

`FuturesAIService`(strategy/futures/service.py)와 같은 배선 스타일: 구독 → 캐시 갱신 →
계산 → 발행. 다른 점은 이 서비스가 방향을 스스로 만들지 않고 `intel.futures`를 그대로
재사용한다는 것(Ver 1.3 §1 "방향의 단일 출처") — `handle_futures_view()`는 점수만 캐싱한다.

## `smile_provider`로 Vol Engine 데이터 출처를 분리한다 (핵심 설계 결정)

`OptionQuoteSnapshot`(core/messages.py)이 아직 필드 미해석 상태라(L16 처방) 이 서비스가
`raw.option_chain.*`를 직접 구독해 실시간으로 `SmileFit`을 만들 수 없다 — 그 배선(원시
호가→중간가→`surface.fit_smile()`)은 필드 매핑이 확정된 뒤의 별도 작업이다(알려진 갭).
그래서 이 서비스는 "지금 쓸 수 있는 스마일이 뭔지" 자체를 묻지 않고, `smile_provider:
Callable[[], SmileFit | None]`라는 콜백에 위임한다 — 실데이터 배선이 생기면 그 콜백만
갈아끼우면 되고, 지금은 백테스트·시뮬레이터·단위테스트가 합성 스마일을 주입해 이 서비스의
나머지 로직(매트릭스→평가→안전규칙→발행)을 전부 검증할 수 있다(`broker/base.BrokerAdapter`가
KIS/시뮬레이터를 갈아끼우는 것과 같은 "동일 인터페이스" 철학, Ver 1.0.1 §2.1).

## `intel.futures` 미수신 상태에서는 항상 NO_OPTION

방향 뷰가 한 번도 안 왔으면 `_latest_score` 기본값(0.0)이 우연히 NEUTRAL로 분류돼 버릴 수
있다 — "아직 모른다"를 "중립 확인됨"으로 낙관 해석하면 안 된다(`strategy/futures/service.py`
의 `_UNSEEN_REGIME`과 동일 철학). `_has_futures_view` 플래그로 이 상태를 명시적으로 막는다.

## 안전규칙(safety.py) 통과 여부만 필터링 — 매도 전략 유예 정책 없음

`evaluate_candidate_safety()`가 거부한 후보는 조용히 버려진다(로그만 남김). 후보가 전부
기각되면 `NO_OPTION`으로 그 사실을 명시한다(Ver 1.3 §5.2 "NO_OPTION도 명시적 출력이다").
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from messiah.core import logging as mlog
from messiah.core.bus import TOPIC_BAR, TOPIC_FUTURES, TOPIC_OPTIONS, BusLike
from messiah.core.event_calendar import EventCalendar
from messiah.core.messages import (
    HORIZON_SECONDS,
    BarClosed,
    BusMessage,
    FuturesView,
    Horizon,
    OptionsView,
)
from messiah.core.timeutil import now_utc
from messiah.strategy.options.config import OptionsConfig
from messiah.strategy.options.evaluator import (
    EvaluatorConfig,
    buildable_structures,
    evaluate_candidate,
    rank_candidates,
)
from messiah.strategy.options.matrix import candidate_specs
from messiah.strategy.options.matrix_coverage import unbuildable_reason
from messiah.strategy.options.safety import evaluate_candidate_safety
from messiah.strategy.options.surface import SmileFit
from messiah.strategy.options.vol_metrics import IVHistory

SmileProvider = Callable[[], SmileFit | None]

#: 침묵 감시 임계 배수 (2026-09-17 G-9) — `_mark_cycle()` docstring "임계는 …".
_STALL_CADENCE_MULTIPLE = 1.5


class OptionsAIService:
    def __init__(
        self,
        symbol: str,
        underlying: str,
        smile_provider: SmileProvider,
        bus: BusLike,
        *,
        iv_history: IVHistory | None = None,
        options_config: OptionsConfig = OptionsConfig(),
        evaluator_config: EvaluatorConfig = EvaluatorConfig(),
        r: float = 0.03,
        event_calendar: EventCalendar | None = None,
        top_n: int = 3,
    ) -> None:
        self._symbol = symbol
        self._underlying = underlying
        self._smile_provider = smile_provider
        self._bus = bus
        self._iv_history = iv_history or IVHistory()
        self._options_config = options_config
        self._evaluator_config = evaluator_config
        self._r = r
        self._event_calendar = event_calendar
        self._top_n = top_n
        # 평가기가 만들 수 있는 구조 — 기동 시 한 번 코드에 물어본다(2026-09-02).
        # 매 사이클 다시 계산할 값이 아니고, 설정이 바뀌면 서비스가 새로 서므로 안전하다.
        self._buildable_structures = buildable_structures(options_config)
        self._latest_score: float = 0.0
        self._has_futures_view = False
        # 마지막 `FuturesView`가 말한 구동 주기 (2026-08-20 F-A′). 이 뷰는 `FuturesView` 도착과
        # M5 완성봉 **양쪽**에 트리거되므로 실제 갱신 간격은 둘 중 짧은 쪽이고, 그것을
        # `cadence_seconds`로 실어 화면이 추측하지 않게 한다.
        self._futures_cadence_seconds: float | None = None
        # **사이클이 돌았다는 사실 자체**를 센다 (2026-09-17 F-111). 이 둘이 없어서
        # 2026-09-08~09-17에 「오후에 5분 보조판단이 조용히 멈춘다」가 일곱 번 보고됐다 —
        # 실제로는 안 멈췄고, `_publish_view()`의 **성공 경로에만 로그가 없었다**(아래
        # `_publish_view` docstring). 무결정에만 로그를 단 F-93의 비대칭이 원인이다.
        self._cycles = 0
        self._last_cycle_at: datetime | None = None

    async def handle_futures_view(self, msg: BusMessage) -> bool:
        """`intel.futures`를 받아 점수를 캐싱하고 뷰를 다시 낸다.

        **처리했는가를 돌려준다** (2026-09-09 1-4) — 걸러낸 것과 처리한 것을 `_dispatch`가
        가려 로그로 남기기 위해서다. 그 이유는 `_dispatch` docstring에 있다.
        """
        if not isinstance(msg, FuturesView) or msg.symbol != self._symbol:
            return False
        self._latest_score = msg.score
        self._has_futures_view = True
        self._futures_cadence_seconds = msg.cadence_seconds
        await self._publish_view(as_of=msg.valid_until or msg.ts_utc)
        return True

    async def handle_bar(self, msg: BusMessage) -> bool:
        """M5 완성봉을 받아 뷰를 다시 낸다. 반환값의 뜻은 `handle_futures_view`와 같다."""
        if not isinstance(msg, BarClosed) or msg.symbol != self._symbol:
            return False
        if msg.horizon != Horizon.M5:
            return False
        await self._publish_view(as_of=msg.bar_open_kst)
        return True

    async def _publish_view(self, *, as_of: datetime) -> None:
        """이 사이클의 판단을 만들어 `intel.options`로 낸다.

        ## 성공 경로에 로그가 없어서 「침묵」으로 읽혔다 (2026-09-17 F-111)

        2026-09-08부터 09-17까지 일일점검이 **일곱 번** *"오후에 5분 보조 판단 절차가 조용히
        멈춘다"* 를 보고했다. 계측(F-105: `OptionsHandleBarFailed`·`OptionsDispatchIgnored`·
        `SubscriberHandlerFailed`)은 그 열흘 내내 전량 0건이라 원인을 못 골랐다.

        **멈춘 적이 없다.** 09-17 실측:

            14:30:01  bar.5m 발행(receivers=5) → OptionsNoCandidate "IV Surface 미준비"
            14:35:01  bar.5m 발행(receivers=5) → 로그 없음      ← "침묵"
            14:40:03  bar.5m 발행(receivers=5) → OptionsNoCandidate "IV Surface 미준비"

        `receivers`는 Redis `PUBLISH`의 반환값, 즉 **실제로 배달된 구독자 수**다(`core/bus.
        publish`). 봉은 왔고 핸들러는 돌았다. 그 사이클에 스마일이 살아 있어 후보가
        만들어졌고 안전규칙을 통과했으며, 그래서 **아래 마지막 블록(발행)** 으로 갔다 —
        그리고 이 함수에서 로그를 안 남기는 경로는 그 하나뿐이었다.

        09-16은 더 분명하다. 13:30~14:25는 매 마크가 `매트릭스 셀 후보 없음(관망)`이었고
        14:30~15:20 열한 마크가 통째로 무로그, 15:25에 다시 `후보 생성 실패`가 찍혔다 —
        그 한 시간은 정지가 아니라 **후보가 실제로 나온 구간**이다. 같은 날 첫 실거래
        (진입 2·청산 2)가 있었다.

        2026-09-07 F-93이 무결정 4갈래에 로그를 달면서 **결정 경로에는 안 달았다.** 그
        비대칭이 "판단이 나온 사이클"을 로그상 "아무 일도 없던 사이클"과 같은 모양으로
        만들었다. `intel.options`는 pub/sub이라 이력이 남지 않고 구독자는 화면 하나뿐이라
        (다음 발행이 덮는다), 그 사이클의 후보는 **어디에도 남지 않은 채 사라졌다.**
        """
        self._mark_cycle()
        if not self._has_futures_view:
            await self._publish_no_option("Futures AI 방향 뷰 미수신")
            return

        smile = self._smile_provider()
        if smile is None:
            await self._publish_no_option("IV Surface 미준비")
            return

        atm_iv = smile.iv_at(smile.forward)
        self._iv_history.add(atm_iv)
        iv_rank = self._iv_history.rank(atm_iv)

        specs = candidate_specs(self._latest_score, iv_rank, self._options_config)
        if not specs:
            reason = "IV Rank 이력 부족" if iv_rank is None else "매트릭스 셀 후보 없음(관망)"
            await self._publish_no_option(reason)
            return
        assert iv_rank is not None  # specs가 비지 않았다는 것 자체가 iv_rank 판정됨의 증거

        is_expiry_day = (
            self._event_calendar.is_expiry_day(as_of.date()) if self._event_calendar else False
        )

        candidates = []
        unbuildable: list[str] = []
        for spec in specs:
            rationale = {
                "structure": spec.structure,
                "iv_rank": iv_rank,
                "score": self._latest_score,
            }
            candidate = evaluate_candidate(
                spec,
                smile,
                r=self._r,
                score=self._latest_score,
                config=self._evaluator_config,
                rationale=rationale,
            )
            if candidate is None:
                # **여기서 조용히 넘어가면 사유가 뒤에서 거짓말이 된다** (2026-09-02).
                # 평가 실패는 두 가지가 섞여 있다: ㉠ 평가기가 그 구조 자체를 못 만든다
                # (매트릭스 셀과 평가기 불일치 — `matrix_coverage.py`), ㉡ 스마일이 목표
                # 델타에 안 닿아 행사가를 못 찾았다. ㉠은 코드 결함, ㉡은 그날 시장이다.
                unbuildable.append(spec.structure)
                mlog.log(
                    "OptionsCandidateUnbuildable",
                    f"{spec.structure} 다리를 만들지 못했다 — 구조 미지원이거나 "
                    f"스마일이 목표 델타에 닿지 않는다",
                    symbol=self._symbol,
                    structure=spec.structure,
                    supported=spec.structure in self._buildable_structures,
                    iv_rank=iv_rank,
                    score=self._latest_score,
                )
                continue
            verdict = evaluate_candidate_safety(
                candidate,
                iv_rank=iv_rank,
                config=self._options_config,
                is_expiry_day=is_expiry_day,
            )
            if not verdict.allowed:
                mlog.log(
                    "OptionsCandidateRejected",
                    "; ".join(verdict.violations),
                    symbol=self._symbol,
                    structure=spec.structure,
                )
                continue
            candidates.append(candidate)

        if not candidates:
            # 사유를 갈라 말한다 — "안전규칙이 걸렀다"와 "만들지도 못했다"는 고칠 곳이 다르다.
            structural = unbuildable_reason(
                [spec.structure for spec in specs], self._buildable_structures
            )
            await self._publish_no_option(
                structural
                or (
                    "생성된 후보가 전부 안전규칙에서 기각됨"
                    if not unbuildable
                    else f"후보 생성 실패({', '.join(unbuildable)}) 또는 안전규칙 기각"
                )
            )
            return

        ranked = rank_candidates(candidates, top_n=self._top_n)
        view = OptionsView(
            symbol=self._symbol,
            underlying=self._underlying,
            candidates=ranked,
            cadence_seconds=self._cadence_seconds(),
        )
        # **결정도 로그로 남긴다** (2026-09-17 F-111) — 무결정만 남기던 F-93의 비대칭을
        # 여기서 닫는다. 이 한 줄이 없어서 판단이 나온 사이클이 "침묵"으로 일곱 번 보고됐다.
        # `intel.options`는 pub/sub이라 이력이 없고 구독자는 화면뿐이므로(다음 발행이 덮는다),
        # **후보가 실제로 뭐였는지 남는 곳은 이 로그가 유일하다.**
        mlog.log(
            "OptionsViewPublished",
            f"후보 {len(ranked)}건 발행 — {', '.join(c.structure for c in ranked)}",
            symbol=self._symbol,
            n_candidates=len(ranked),
            structures=[c.structure for c in ranked],
            iv_rank=iv_rank,
            score=self._latest_score,
            is_expiry_day=is_expiry_day,
        )
        await self._bus.publish(TOPIC_OPTIONS, view)

    async def _publish_no_option(self, reason: str) -> None:
        # **무결정도 로그로 남긴다** (2026-09-07 F-93). 이 함수로 모이는 경로가 넷인데
        # (① Futures 방향 뷰 미수신 ② IV Surface 미준비 ③ IV Rank 이력 부족/매트릭스 셀
        # 없음 ④ 후보를 만들었지만 안전규칙에서 전부 기각) 종전엔 버스 발행만 하고 로그를
        # 안 남겼다. 09-07 장중 46사이클 중 사유가 로그로 재구성되는 것이 최대 1건이었고,
        # 그래서 "왜 안 샀나"를 사후에 못 짚었다 — 발행된 뷰는 다음 발행이 덮는다.
        mlog.log(
            "OptionsNoCandidate",
            reason,
            symbol=self._symbol,
            reason=reason,
        )
        view = OptionsView(
            symbol=self._symbol,
            underlying=self._underlying,
            no_option_reason=reason,
            cadence_seconds=self._cadence_seconds(),
        )
        await self._bus.publish(TOPIC_OPTIONS, view)

    def _mark_cycle(self) -> None:
        """사이클 1회를 세고, **직전 사이클과의 간격이 비정상이면 그것도 남긴다** (G-9).

        임계는 구동 주기의 1.5배다 — 5분 격자에서 한 마크를 통째로 건너뛰면(=600초) 걸리고,
        정상 지터(±수 초)에는 안 걸린다. 임계를 2.0배로 두면 한 마크 결손이 정확히 경계에
        걸려 그날그날 다르게 판정된다(09-17 실측 간격 597·600·602초).

        ## 한계를 여기 적어둔다

        이 측정은 **다음 사이클이 와야** 성립한다 — 루프가 영영 죽으면 이 태그는 안 뜬다.
        그 경우는 장 마감 뒤 `ops/integrity_report.py`의 `options_cycles` 축이 하루치
        `OptionsCycle*` 태그 간격을 훑어 잡는다. 두 장치가 각각 「끊겼다 이어짐」과
        「끊긴 채 끝남」을 맡는다.

        **판정은 하지 않는다**(R18) — 게이트도 차단도 아니고 세기만 한다. WARNING 승격은
        라이브 20거래일 분포를 본 뒤 사람이 정한다(`OptionSmileResidualHigh`와 같은 규율).
        """
        now = now_utc()
        previous = self._last_cycle_at
        self._cycles += 1
        self._last_cycle_at = now
        if previous is None:
            return
        gap = (now - previous).total_seconds()
        threshold = self._cadence_seconds() * _STALL_CADENCE_MULTIPLE
        if gap <= threshold:
            return
        mlog.log(
            "OptionsSubLoopStalled",
            f"직전 판단 사이클과 {gap:.0f}초 — 구동 주기 "
            f"{self._cadence_seconds():.0f}초의 {gap / self._cadence_seconds():.1f}배",
            symbol=self._symbol,
            gap_seconds=round(gap, 1),
            cadence_seconds=self._cadence_seconds(),
            threshold_seconds=round(threshold, 1),
            cycle_index=self._cycles,
        )

    @property
    def cycles(self) -> int:
        """기동 이후 이 서비스가 판단을 만든 횟수 — 결정·무결정을 가리지 않는다."""
        return self._cycles

    def _cadence_seconds(self) -> float:
        """이 뷰의 실제 갱신 간격 — 두 트리거 중 **짧은 쪽** (2026-08-20 F-A′).

        M5 완성봉은 `FuturesView` 유무와 무관하게 300초마다 반드시 온다(`handle_bar`).
        `FuturesView`가 더 빠른 격자로 구동되면 그쪽이 실질 주기다. 긴 쪽을 쓰면 임계가
        헐거워져 진짜 정지를 늦게 잡는다 — `derived_stale_after`의 `max(fallback, ...)`와 같은
        방향의 보수성이다.
        """
        bar_cadence = float(HORIZON_SECONDS[Horizon.M5])
        futures = self._futures_cadence_seconds
        return bar_cadence if futures is None else min(bar_cadence, futures)

    async def run_forever(self) -> None:
        patterns = [f"{TOPIC_BAR}.{Horizon.M5.value}.{self._symbol}", TOPIC_FUTURES]
        await self._bus.subscribe(patterns, self._dispatch)

    async def _dispatch(self, msg: BusMessage) -> None:
        """구독 메시지를 두 핸들러로 갈라 보내고, **아무도 처리하지 않은 것을 남긴다**.

        ## 왜 계측을 붙였나 (2026-09-09 이상점 1-4)

        09-09 장중에 5분 그리드 중 09:15·09:25 두 마크만 `OptionsNoCandidate`가 없었다.
        그날 저녁까지 원인을 못 가린 이유는 **세 가능성이 전부 무로그**였기 때문이다:

            ㉠ 메시지가 아예 안 왔다        (수집·버스 쪽)
            ㉡ 왔는데 필터가 걸러냈다        (심볼·호라이즌 불일치)
            ㉢ 왔는데 처리 중 예외가 났다    (이 서비스 쪽)

        ㉢은 버스가 이미 `SubscriberHandlerFailed`로 잡는다 — 다만 **로그를 조절한다**
        (1·10·20…100번째만 찍는다, `core/bus._log_subscriber_failure`). 그래서 2~9번째
        실패는 원리적으로 안 보인다. 09-09는 그 태그가 하루 0건이었으므로 ㉢은 아니었지만,
        「0건이니 아니다」를 말할 수 있는 것은 첫 건이 반드시 찍히는 덕이고 그 이상은 못 센다.

        여기서 ㉡을 이름 붙이면 세 갈래가 로그로 갈린다 — 마크가 없는데 이 태그도 없으면
        ㉠(메시지 미도달)이고, 있으면 ㉡이다. 다음 재발 때 고칠 곳이 바로 정해진다.

        ## 예외를 다시 던지는 이유

        여기서 삼키면 버스의 실패 카운터가 안 오르고 루프 보호 로그도 사라진다 — 판정과
        동작이 바뀐다. 이 서비스는 **맥락만 얹고**(어느 핸들러·어느 심볼) 그대로 올려보낸다.
        조절되지 않는 한 줄이 남으므로 2~9번째 실패도 이제는 보인다.
        """
        handled = False
        try:
            if isinstance(msg, BarClosed):
                handled = await self.handle_bar(msg)
            elif isinstance(msg, FuturesView):
                handled = await self.handle_futures_view(msg)
        except Exception as exc:
            mlog.log(
                "OptionsHandleBarFailed",
                f"{type(msg).__name__} 처리 중 예외 — 이 사이클의 판단이 비었다: {exc}",
                symbol=self._symbol,
                message_type=type(msg).__name__,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        if handled:
            return
        # 구독 패턴이 M5·`intel.futures`뿐이므로 정상 운영에서 이 로그는 0건이다 —
        # **한 건이라도 뜨면 그것이 1-4의 답이다.** 그래서 조절하지 않는다.
        mlog.log(
            "OptionsDispatchIgnored",
            f"{type(msg).__name__}을 아무 핸들러도 처리하지 않았다 — "
            f"심볼·호라이즌 불일치이거나 구독 패턴이 넓다",
            symbol=self._symbol,
            message_type=type(msg).__name__,
            message_symbol=getattr(msg, "symbol", None),
            horizon=getattr(getattr(msg, "horizon", None), "value", None),
        )
