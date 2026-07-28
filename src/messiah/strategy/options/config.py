"""Options AI 임계값 — `RiskEngineConfig`/`SizerConfig`(risk/risk_engine.py, risk/sizer.py)와
동일 스타일의 하드코딩 기본값 dataclass (Ver 2.0 §9 W27~29).

Ver 1.3 §10은 `configs/options.yaml`을 제안하지만, 이 저장소가 실제로 정착한 관습은
`core/config.py`에 확인된 대로 dataclass 기본값이다 — `configs/`엔 `instance.yaml`·
`krx_holidays.yaml` 둘뿐, 모듈별 YAML은 없다. 그 아직-실현 안 된 문서상의 계획보다
저장소가 실제로 쓰는 관습을 따른다."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OptionsConfig:
    # IV Rank 상태 경계 (Ver 1.3 §4.1 매트릭스 열 경계)
    iv_rank_low: float = 30.0
    iv_rank_high: float = 70.0
    # 방향 중립 판정 임계 — MetaDecisionEngine.SCORE_THRESHOLD(Ver 2.0 §3.1 ④⑤)와 같은 0.20
    # 값을 쓰지만, Options AI는 방향을 다시 만들지 않는다(Ver 1.3 §1 "방향의 단일 출처")는
    # 원칙상 의도적으로 그 상수를 import하지 않고 독립적으로 보유한다 — 우연히 같은 값일 뿐,
    # Meta Decision이 임계를 바꿔도 이 설정까지 따라 바뀔 이유는 없다.
    direction_score_threshold: float = 0.20
    # 델타 기준 행사가 선택 (Ver 1.3 §4.2 "매도 다리는 15~30Δ, 매수 다리는 30~50Δ")
    short_leg_delta_low: float = 0.15
    short_leg_delta_high: float = 0.30
    long_leg_delta_low: float = 0.30
    long_leg_delta_high: float = 0.50
    # DTE 범위 (Ver 1.3 §4.2 "매도 전략은 DTE 15~45, 매수 전략은 DTE 20 이상")
    short_structure_dte_low: int = 15
    short_structure_dte_high: int = 45
    long_structure_dte_min: int = 20
    # 후보 생성 상한 (Ver 1.3 §4.2 "진입 후보 최대 10개 내외")
    max_candidates: int = 10
    # Skew 절대값이 이 값을 넘으면 풋매도 계열(신용) 후보 자동 제외 (Ver 1.3 §4.2) — 초기값,
    # Walk-Forward 재추정 대상(Ver 1.3 §9 "매트릭스 임계는 학습이 아니라 주기적 캘리브레이션").
    skew_extreme_threshold: float = 0.10
    # §6-2 "IV Rank < 50에서 credit 전략 금지" 임계 — iv_rank_high(매트릭스 열 경계, 70)와는
    # 별개 숫자다(안전규칙이 매트릭스보다 보수적이어야 한다는 §6 원칙 그대로 다른 상수로 분리).
    credit_iv_rank_floor: float = 50.0
