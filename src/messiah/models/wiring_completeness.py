"""G2 결선 완성도 — 고도화 C (2026-08-03).

## 왜 만들었나

2026-08-03 일일점검 시점에 `logs/self_eval_*.json`은 4거래일치가 쌓여 있었고, 전부 이랬다:

    n_return_samples=4  win_rate=0.0  profit_factor=0.0  sharpe=0.0  max_drawdown=0.0
    g2_daily_returns.jsonl: return 0.0 × 4일

이 숫자들은 "성적이 나빴다"가 아니라 **"애초에 거래가 없었다"**는 뜻이다. 같은 날 G2 로그
첫 줄이 `live 번들 결선: []`이었고 판단·주문·체결 태그가 하루 종일 0건이었다 — 모델이 하나도
결선되지 않은 채로 파이프라인만 돈 것이다.

그런데 리포트는 그 사실을 말하지 않고 `Sharpe 0.00`을 출력했다. Sharpe 0은 "수익도 손실도
없었다"는 **측정 결과처럼** 읽힌다. 이 프로젝트는 같은 실패를 이미 두 번 겪었다:

    2026-07-31  `n_trades=3`이 체결 0건인 날에 "3건 거래"로 읽힘   → 이름을 갈랐다
    2026-08-03  `slippage_realized_ticks=0.0`이 "슬리피지 0틱"으로 읽힘 → None으로 갈랐다

세 번째가 손익 지표 전체다. 이 단계에서 재야 할 것은 손익이 아니라 **결선 완성도**다 —
"모델이 붙었나 → 판단이 나왔나 → 주문이 나갔나 → 체결을 셀 수 있나". 그게 다 채워지기
전까지 손익 지표는 측정값이 아니라 자리표시자다.

## 왜 단계(stage)로 표현하나

개별 항목만 나열하면 사람이 매번 "그래서 지금 어디까지 온 거지"를 재조합해야 한다. 앞
단계가 비면 뒤 단계는 볼 필요조차 없다는 **순서**가 이 도메인의 사실이므로, 그 순서를 그대로
단계로 굳힌다 — 첫 번째로 비어 있는 칸이 곧 지금 해야 할 일이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 순서가 의미다 — 앞이 비면 뒤는 볼 필요가 없다(모듈 docstring "왜 단계로 표현하나").
STAGE_NO_BUNDLE = "번들 미결선"
STAGE_NO_DECISION = "판단 미발생"
STAGE_NO_ORDER = "주문 미발생"
STAGE_NO_FILL_ACCOUNTING = "체결 집계 불가"
STAGE_MEASURABLE = "손익 측정 가능"


@dataclass(frozen=True)
class WiringCompleteness:
    """그날 G2가 실제로 어디까지 결선돼 돌았는지.

    `pnl_measurable`이 False면 같은 리포트의 승률·PF·Sharpe·MDD는 **측정값이 아니다**.
    """

    live_bundles: list[str] = field(default_factory=list)
    shadow_bundles: int = 0
    n_decisions: int = 0
    n_orders: int = 0
    fills_countable: bool = False

    @property
    def stage(self) -> str:
        """지금 막혀 있는 첫 지점 — 이게 곧 다음에 할 일이다."""
        if not self.live_bundles:
            return STAGE_NO_BUNDLE
        if self.n_decisions == 0:
            return STAGE_NO_DECISION
        if self.n_orders == 0:
            return STAGE_NO_ORDER
        if not self.fills_countable:
            return STAGE_NO_FILL_ACCOUNTING
        return STAGE_MEASURABLE

    @property
    def pnl_measurable(self) -> bool:
        return self.stage == STAGE_MEASURABLE

    def summary(self) -> str:
        """사람이 읽는 한 줄 — 손익 지표를 믿어도 되는지가 먼저 나와야 한다."""
        head = (
            "손익 측정 가능"
            if self.pnl_measurable
            else f"손익 측정 단계 아님({self.stage}) — 승률·PF·Sharpe·MDD는 자리표시자"
        )
        bundles = ",".join(self.live_bundles) if self.live_bundles else "없음"
        return (
            f"{head} · live 번들 {bundles} · shadow {self.shadow_bundles}개 · "
            f"판단 {self.n_decisions}건 · 주문 {self.n_orders}건 · "
            f"체결집계 {'가능' if self.fills_countable else '불가'}"
        )
