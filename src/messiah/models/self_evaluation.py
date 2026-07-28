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
from messiah.risk.cost_model import CostModel


@dataclass(frozen=True)
class SlippageReconciliation:
    predicted_ticks: float
    realized_ticks: float
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
    realized = statistics.fmean(diffs) if diffs else 0.0
    return SlippageReconciliation(
        predicted_ticks=predicted, realized_ticks=realized, n_samples=len(diffs)
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
) -> SelfEvalReport:
    """하루치 챔피언 실현수익률(`champion_returns`, 비율 단위 — Position Reconciler 부재로
    호출자가 직접 산출해 넘긴다, 모듈 docstring)로 승률·PF·Sharpe·MDD를 집계하고, 그날의
    주문/체결로 슬리피지를 대사한다."""
    slippage = reconcile_slippage(orders, acks, fills, cost_model=cost_model)
    report = SelfEvalReport(
        date=date,
        symbol=symbol,
        n_trades=len(champion_returns),
        win_rate=win_rate(champion_returns),
        profit_factor=profit_factor(champion_returns),
        sharpe=sharpe_ratio(champion_returns, periods_per_year=periods_per_year),
        max_drawdown=max_drawdown(equity_curve_from_returns(champion_returns)),
        n_shadow_bundles=n_shadow_bundles,
        slippage_predicted_ticks=slippage.predicted_ticks,
        slippage_realized_ticks=slippage.realized_ticks,
    )
    mlog.log(
        "SelfEvalReportGenerated",
        "일일 자가평가 리포트",
        date=date,
        symbol=symbol,
        n_trades=report.n_trades,
        sharpe=report.sharpe,
    )
    return report
