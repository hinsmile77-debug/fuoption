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
# 2026-09-17 F-114로 새로 생긴 칸. **체결을 세게 됐는데도 손익 4지표를 못 내는 구간이
# 실재한다** — `PositionReconciler`가 실현손익을 내지만 단위가 **틱**이고, 승률·PF·Sharpe·MDD가
# 먹는 `champion_returns`는 **자본 대비 비율**이다. 틱을 비율로 바꾸려면 계약 승수(원/지수
# 포인트)가 있어야 하는데 그 값이 이 저장소 어디에도 없다(`configs/instance.yaml`에
# `futures_tick_size`는 있어도 승수는 없다).
#
# 이 칸이 없으면 F-114 결선 직후 `fills_countable=True`가 곧장 `pnl_measurable=True`로
# 이어지고, 그 순간 **36행 전부 0.0인 `g2_daily_returns.jsonl`로 계산한 `sharpe=0.0`이
# 「측정값」 도장을 받는다** — 이 모듈이 2026-08-03에 막으려고 만들어진 바로 그 형태다.
STAGE_NO_PNL_UNIT = "수익률 환산 불가(계약 승수 미정)"
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
    # 틱 손익을 **자본 대비 비율**로 바꿀 수 있는가 — 계약 승수가 설정에 있어야 True다.
    # 기본값 False가 의도다: 모르는 것을 좋은 쪽으로 가정하지 않는다(모듈 docstring).
    returns_convertible: bool = False
    # 로컬 장부와 브로커 포지션 대사 결과. `None` = **대사를 안 돌렸다**(False = 돌렸는데
    # 틀렸다). 셋을 구분해야 "체결 수를 믿어도 되는가"가 한 줄로 읽힌다.
    positions_reconciled: bool | None = None

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
        if not self.returns_convertible:
            return STAGE_NO_PNL_UNIT
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
        # 대사는 **세 값**이다 — 안 함/일치/불일치. "불일치"를 "안 함"과 같은 말로
        # 쓰면 유령 포지션이 침묵으로 읽힌다(L12).
        reconciled = {None: "미실시", True: "일치", False: "**불일치**"}[self.positions_reconciled]
        return (
            f"{head} · live 번들 {bundles} · shadow {self.shadow_bundles}개 · "
            f"판단 {self.n_decisions}건 · 주문 {self.n_orders}건 · "
            f"체결집계 {'가능' if self.fills_countable else '불가'} · "
            f"포지션대사 {reconciled} · "
            f"수익률환산 {'가능' if self.returns_convertible else '불가(계약 승수 미정)'}"
        )
