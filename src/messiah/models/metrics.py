"""성과·교정 지표 — Ver 1.2 §8.3 Registry 관문, Ver 1.6 §8 Validator 추가검사 (Ver 2.0 §9 W14~16).

전부 순수 함수(부작용 없음, 외부 상태 의존 없음)로 둔다 — `models/validator.py`가 관문
판정에 쓰고, Self Evaluation(Phase 5) 등 다른 미래 소비자가 생겨도 재사용 가능하게
결합도를 낮췄다(cv.py가 labeling.py에 의존하지 않는 것과 같은 설계 원칙).

**Deflated Sharpe(Ver 1.2 §8.3 "Deflated Sharpe p-value < 0.05")는 이번 스코프에 없다** —
시행 횟수 보정이 핵심인데, 그 시행 횟수를 세는 Optuna 탐색 기반 정식 Trainer가 아직 없다
(W17~19 스코프, capability_matrix.md 알려진 갭). 여기 `sharpe_ratio()`는 그 위에 다중시행
보정을 씌울 수 있는 원료(raw Sharpe)까지만 제공한다.
"""

from __future__ import annotations

import statistics
from typing import Sequence


def sharpe_ratio(returns: Sequence[float], *, periods_per_year: float) -> float:
    """
    계산: 연율화 Sharpe = mean(returns)/stdev(returns) × sqrt(periods_per_year).
    입력: `returns`는 기간별(예: 일별) 수익률(비율) — 이미 비용 차감된 값이어야 한다
         (Ver 1.2 §8.3 "비용 차감 후 Sharpe").
    실패 조건: 표본이 2개 미만이거나 표준편차가 0(수익률 전부 동일)이면 계산 불능 — 예외
         대신 0.0을 반환한다(Validator가 관문 판정 시 자연히 "미달"로 처리되게 함).
    """
    if len(returns) < 2:
        return 0.0
    stdev = statistics.pstdev(returns)
    if stdev == 0:
        return 0.0
    return statistics.fmean(returns) / stdev * (periods_per_year**0.5)


def max_drawdown(equity_curve: Sequence[float]) -> float:
    """
    계산: 누적 자산곡선에서 그 시점까지의 최고점 대비 최대 낙폭 비율(0~1, 양수로 반환 —
         예: 0.15 = 15% 낙폭). `equity_curve[0]`이 시작 자본.
    """
    if len(equity_curve) < 2:
        return 0.0
    peak = equity_curve[0]
    worst = 0.0
    for value in equity_curve:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak)
    return worst


def negative_window_ratio(window_returns: Sequence[float]) -> float:
    """Ver 1.2 §8.3 "검증 창별 성과 일관성: 음(−) 성과 창 비율 < 40%" — 창별 총수익 중
    음수 비율(0~1)."""
    if not window_returns:
        return 0.0
    negative = sum(1 for r in window_returns if r < 0)
    return negative / len(window_returns)


def win_rate(returns: Sequence[float]) -> float:
    """승률 — 0이 아닌 수익률(체결) 중 양수 비율. 정확히 0(비용까지 정확히 상쇄)인 거래는
    승/패 어느 쪽도 아니라 분모에서 제외한다. 표본이 전부 0이거나 비어 있으면 0.0(Sharpe와
    같은 "계산 불능은 0" 관례, Validator가 자연히 미달로 처리)."""
    wins = sum(1 for r in returns if r > 0)
    losses = sum(1 for r in returns if r < 0)
    total = wins + losses
    return wins / total if total else 0.0


def profit_factor(returns: Sequence[float]) -> float:
    """Profit Factor = 총이익 / |총손실|. 손실이 전혀 없으면(이익이 있는 경우) 무한대,
    거래 자체가 없으면 0.0."""
    gains = sum(r for r in returns if r > 0)
    losses = -sum(r for r in returns if r < 0)
    if losses == 0:
        return float("inf") if gains > 0 else 0.0
    return gains / losses


def equity_curve_from_returns(returns: Sequence[float], starting: float = 1.0) -> list[float]:
    """수익률(비율) 시퀀스 → 누적 자산곡선. `max_drawdown()`의 입력을 만드는 공용 헬퍼 —
    `models/shadow_manager.py`·`models/self_evaluation.py`가 공유(중복 방지)."""
    curve = [starting]
    for r in returns:
        curve.append(curve[-1] * (1 + r))
    return curve


def multiclass_brier_score(
    probs: Sequence[Sequence[float]], true_class_idx: Sequence[int]
) -> float:
    """
    계산: 다중클래스 Brier Score = mean_i[ Σ_c (y_ic − p_ic)² ], y는 원-핫(정답 클래스만
         1). 0에 가까울수록 교정이 좋다(확정적으로 맞으면 0, 확정적으로 완전히 틀리면
         최대 2). Ver 1.6 §8 "교정 품질" 검사의 정량 지표.
    입력: `probs[i]`는 표본 i의 클래스별 확률 벡터(합이 1), `true_class_idx[i]`는 그
         표본의 정답 클래스 인덱스.
    """
    if not probs:
        raise ValueError("probs가 비어 있음")
    if len(probs) != len(true_class_idx):
        raise ValueError("probs와 true_class_idx의 길이가 다르다")
    total = 0.0
    for row, true_idx in zip(probs, true_class_idx):
        total += sum((float(c == true_idx) - p) ** 2 for c, p in enumerate(row))
    return total / len(probs)
