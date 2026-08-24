"""Self Evaluation — Ver 2.0 §7 진화 캘린더 "매일 장중 Shadow 병주 → 마감 후 Self
Evaluation: 승률·PF·Sharpe·Regime 정확도 집계, Conformal 교정, 슬리피지 대사" (Ver 2.0 §9
W35~36, Phase 5).

일일 배치 job(장 마감 후 1회 호출) — `run_l1_daily.py`의 `_daily_close()`와 같은 성격이라
`FixedTickScheduler`의 반복 틱이 아니라 하루 운영 스크립트의 마지막 단계로 직접 호출하는
편이 자연스럽다(스케줄러는 "장중 주기적 폴링"을 위한 것이지 "장 마감 후 1회"에는 과함).

## Regime 정확도는 이번 스코프에 없다 (명시적 갭)

Ver 2.0 §7 원문이 요구하는 "Regime 정확도 집계"는 국면 판정의 정답(ground truth)이 있어야
하는데, Regime은애초에 관측 불가능한 잠재 상태라 "정답"의 정의 자체가 없다(사후에 수익률로
근사할 수는 있으나 그 방법론이 이번 스코프 밖) — `SelfEvalReport`에 관련 필드를 넣지 않았다.

## 승격 표본은 **월물 경계에서 끊지 않는다** (2026-08-24 F-27)

`g2_daily_returns.jsonl`의 행을 종전엔 `symbol == 오늘의 심볼`로 걸렀다. 그런데 선물
월물은 한 달에 한 번 바뀌고(2026-08-14 A05608→A05609), 그 순간 **성적표가 0장으로
돌아간다.** Ver 1.1 §8 G2 통과기준이 요구하는 40거래일은 롤 주기(20~22거래일)보다 길므로
**그 시험은 지금 구조로 영원히 끝나지 않는다.** 2026-08-24 실측: 파일 18행, 집계에 쓰인
표본 6개. 다음 롤(2026-09-11)이면 다시 0장이다.

그래서 `champion_sample()`이 롤을 이어 붙인다 — **롤 당일 수익률 한 개만** 뺀다(그날은
두 계약이 섞인 하루라 어느 쪽 성적도 아니다). 그리고 그 절단을 `sample_window`로
리포트에 명시한다. 2026-08-18 결정 *"절단을 조용히 하면 나중에 「왜 40일인데 27행이냐」를
아무도 못 푼다"* 의 이행이다.

**관문 요구일수(40거래일)는 이 모듈이 바꾸지 않는다.** 롤 주기와 40거래일 중 무엇이
정본인지는 사람이 정할 문제이고, 위험 성향이 아니라 승격 속도를 바꾸는 결정이다.
여기서 하는 것은 **재는 것**까지다.

## Conformal 교정 갱신은 이 모듈이 아니라 `models/registry.save_conformal_state()`가 담당

Self Evaluation은 그날의 (예측확률, 실제결과) 이력을 만들어 넘기기만 하고, 그 이력을 번들
디렉터리의 `conformal_state.json`에 쓰는 것은 Registry의 책임(관심사 분리 — Self Evaluation은
"오늘 성적이 어땠는가"만 알고, 번들 파일 레이아웃은 몰라도 된다).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Sequence

from messiah.core import logging as mlog
from messiah.core.messages import Fill, OrderAck, OrderRequest, SelfEvalReport
from messiah.models.metrics import (
    equity_curve_from_returns,
    max_drawdown,
    profit_factor,
    sharpe_ratio,
    win_rate,
)
from messiah.models.wiring_completeness import WiringCompleteness
from messiah.risk.cost_model import CostModel


@dataclass(frozen=True)
class SlippageReconciliation:
    """`realized_ticks`가 **None이면 "잴 수 없었다"**는 뜻이다(체결이 0건이거나 전부 지정가
    매칭에 실패). 0.0은 "슬리피지가 없었다"는 전혀 다른 사실이다 — 2026-08-03까지는 둘 다
    0.0으로 뭉개져서, 주문·체결이 0건인 날의 리포트가 "슬리피지 0틱"이라는 **성과처럼**
    읽혔다(`core/messages.py`의 `n_fills`가 0이 아니라 None인 것과 같은 이유, 마흐디 L18)."""

    predicted_ticks: float
    realized_ticks: float | None
    n_samples: int


def reconcile_slippage(
    orders: Sequence[OrderRequest],
    acks: Sequence[OrderAck],
    fills: Sequence[Fill],
    *,
    cost_model: CostModel | None = None,
) -> SlippageReconciliation:
    """Ver 2.0 §6 "체결 품질 기록: 의도가격 대비 슬리피지를 전 주문에 기록 → Cost Model이
    매주 자기 보정"의 실제 계산부. **지정가 주문만 대상**이다 — 시장가는 `limit_price_ticks`가
    없어 "의도 가격"이라는 기준점 자체가 성립하지 않는다(모듈 docstring).

    `OrderRequest.msg_id` → `OrderAck.request_id` → `OrderAck.broker_order_no` →
    `Fill.broker_order_no` 3단 매칭(Ver 1.1 §4.3 스키마가 정의한 유일한 연결 경로 —
    Position Reconciler 없이도 이 슬리피지 계산 자체는 성립한다는 게 이 함수의 근거)."""
    cost_model = cost_model or CostModel()
    predicted = cost_model.config.expected_spread_ticks
    order_by_msg_id = {o.msg_id: o for o in orders}
    ack_by_broker_order_no = {a.broker_order_no: a for a in acks}
    diffs: list[float] = []
    for fill in fills:
        ack = ack_by_broker_order_no.get(fill.broker_order_no)
        if ack is None:
            continue
        order = order_by_msg_id.get(ack.request_id)
        if order is None or order.limit_price_ticks is None:
            continue
        diffs.append(abs(fill.price_ticks - order.limit_price_ticks))
    realized = statistics.fmean(diffs) if diffs else None  # 표본 0 = 모름(위 docstring)
    return SlippageReconciliation(
        predicted_ticks=predicted, realized_ticks=realized, n_samples=len(diffs)
    )


@dataclass(frozen=True)
class ChampionSample:
    """승격 표본과 **그 표본이 어떻게 잘렸는지** (2026-08-24 F-27)."""

    returns: list[float]
    window: dict


def champion_sample(rows: Sequence[dict]) -> ChampionSample:
    """`g2_daily_returns.jsonl` 전 행에서 승격 표본을 고른다 — 롤을 이어 붙여서.

    입력은 파일에 적힌 순서 그대로의 행들이다(날짜 오름차순 가정). 각 행:
    `{"date": ..., "symbol": ..., "return": ..., "countable": bool | 없음}`.

    ## 빼는 것은 둘뿐이다

    1. **롤 당일 한 개.** 직전 행과 `symbol`이 다른 날은 두 계약이 섞인 하루라 어느 쪽
       성적도 아니다. 첫 행은 롤이 아니다(비교할 앞이 없다).
    2. **`countable`이 명시적으로 `False`인 날.** 키가 **없는** 행은 빼지 않는다 —
       「거래가 없었다」와 「그 시절엔 안 쟀다」는 다른 사실이고, 후자를 `False`로 채우면
       기존 18행이 소급해서 「셀 수 없는 날」이 된다(L18). 그 수는 `sample_window`의
       `legacy_rows_without_countable`로 따로 보인다.

    종목 필터는 **없다.** 그것이 이 함수가 생긴 이유다 — 모듈 docstring 참고.
    """
    counted: list[float] = []
    excluded = {"roll_day": 0, "not_countable": 0}
    legacy = 0
    first_counted: str | None = None
    previous_symbol: str | None = None

    for index, row in enumerate(rows):
        symbol = row.get("symbol")
        is_roll = index > 0 and symbol != previous_symbol
        previous_symbol = symbol
        if is_roll:
            excluded["roll_day"] += 1
            continue
        if "countable" not in row:
            legacy += 1
        elif row.get("countable") is False:
            excluded["not_countable"] += 1
            continue
        value = row.get("return")
        if not isinstance(value, (int, float)):
            # 값이 없는 행은 표본이 아니다. 조용히 0.0으로 읽으면 "본전인 날"이 된다.
            excluded["not_countable"] += 1
            continue
        if first_counted is None:
            first_counted = str(row.get("date", ""))
        counted.append(float(value))

    return ChampionSample(
        returns=counted,
        window={
            "from": first_counted,
            "rows_total": len(rows),
            "rows_counted": len(counted),
            "excluded": excluded,
            # **절단이 아니라 이력의 나이다.** F-27 이전에 적힌 행은 `countable`을 갖고
            # 있지 않다 — 그 수가 0이 되는 날이 이 축이 온전히 도는 첫날이다.
            "legacy_rows_without_countable": legacy,
        },
    )


def run_self_evaluation(
    *,
    date: str,
    symbol: str,
    champion_returns: Sequence[float],
    n_shadow_bundles: int,
    orders: Sequence[OrderRequest] = (),
    acks: Sequence[OrderAck] = (),
    fills: Sequence[Fill] = (),
    cost_model: CostModel | None = None,
    periods_per_year: float = 252.0,
    instance_id: str | None = None,
    wiring: WiringCompleteness | None = None,
    sample_window: dict | None = None,
    promotion_evidence_eligible: bool | None = None,
    promotion_evidence_reason: str | None = None,
) -> SelfEvalReport:
    """하루치 챔피언 실현수익률(`champion_returns`, 비율 단위 — Position Reconciler 부재로
    호출자가 직접 산출해 넘긴다, 모듈 docstring)로 승률·PF·Sharpe·MDD를 집계하고, 그날의
    주문/체결로 슬리피지를 대사한다.

    `instance_id`는 명시적으로 받는다(2026-07-30 수정) — `BusMessage.instance_id`는 보통
    `MessageBus.publish()`가 발행 시점에 채워 넣는데(`core/bus.py`), 이 리포트는 버스를 안
    거치고 `logs/self_eval_{date}.json`으로 곧장 저장돼 기본값 `"unset"`이 그대로 파일에
    남았다(2026-07-29 산출물 실측). 멀티 PC 리포트 병합이 이 필드의 존재 이유라
    (`core/messages.py` 모듈 docstring, Ver 1.1 §7.3) 비어 있으면 나중에 어느 PC 결과인지
    복원할 수 없다.

    `wiring`은 그날 G2가 실제로 어디까지 결선돼 돌았는지다(2026-08-03 추가) — 안 넘기면
    `pnl_measurable=False`로 남는다. 모르는 것을 좋은 쪽으로 가정하지 않는다
    (`models/wiring_completeness.py` 모듈 docstring).
    """
    slippage = reconcile_slippage(orders, acks, fills, cost_model=cost_model)
    # 손익 4지표는 **측정 가능한 날에만** 값을 넣는다 (2026-08-05) — 결선 전에는 이 값들이
    # 전부 0.0으로 나오는데, 그 0은 "본전"이 아니라 "표본이 없음"이다. 5거래일 연속
    # `sharpe=0.0`이 성적처럼 읽힌 것이 이 변경의 직접 계기다
    # (`core/messages.py`의 SelfEvalReport docstring).
    measurable = wiring.pnl_measurable if wiring else False
    report = SelfEvalReport(
        **({"instance_id": instance_id} if instance_id else {}),
        date=date,
        symbol=symbol,
        n_return_samples=len(champion_returns),
        # 체결 건수는 호출자가 그날의 실제 `Fill`을 넘겼을 때만 셀 수 있다 — 안 넘겼으면
        # "0건"이 아니라 "모름"(None)이다(`core/messages.py`의 `SelfEvalReport` docstring).
        n_fills=len(fills) if fills else None,
        win_rate=win_rate(champion_returns) if measurable else None,
        profit_factor=profit_factor(champion_returns) if measurable else None,
        sharpe=(
            sharpe_ratio(champion_returns, periods_per_year=periods_per_year)
            if measurable
            else None
        ),
        max_drawdown=(
            max_drawdown(equity_curve_from_returns(champion_returns)) if measurable else None
        ),
        n_shadow_bundles=n_shadow_bundles,
        slippage_predicted_ticks=slippage.predicted_ticks,
        slippage_realized_ticks=slippage.realized_ticks,
        pnl_measurable=measurable,
        wiring_stage=wiring.stage if wiring else None,
        wiring_summary=wiring.summary() if wiring else None,
        sample_window=sample_window,
        promotion_evidence_eligible=promotion_evidence_eligible,
        promotion_evidence_reason=promotion_evidence_reason,
    )
    mlog.log(
        "SelfEvalReportGenerated",
        "일일 자가평가 리포트",
        date=date,
        symbol=symbol,
        n_return_samples=report.n_return_samples,
        n_fills=report.n_fills,
        sharpe=report.sharpe,
        # 이 필드가 로그에 있어야 나중에 "그날 Sharpe 0이 성적인지 무운영인지"를 되짚을 수 있다.
        pnl_measurable=report.pnl_measurable,
        wiring_stage=report.wiring_stage,
        # **왜 그 표본 수인가**를 로그가 같이 말한다 (F-27). 종전엔 코드를 읽어야 알았다.
        sample_window=report.sample_window,
        promotion_evidence_eligible=report.promotion_evidence_eligible,
    )
    return report
