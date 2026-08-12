"""RegimeAI — 하이브리드 오케스트레이터 (Ver 1.1 §3-1, Ver 1.6 §3.1, Ver 2.0 §9 W20~21).

통계층(HMM, `hmm_model.py`) → 규칙층(오버라이드, `rules.py`) → 명명층(HMM 결과일 때만,
`naming.py`)을 하나로 묶어 `RegimeState`(`intel.regime`)를 낸다. 규칙층이 발동하면
확신도 1.0으로 통계층 결과를 완전히 덮어쓴다(Ver 1.6 §3.1 "[규칙층] 오버라이드 —
통계보다 우선").

**실패 시 UNKNOWN**(Ver 1.1 §3-1 "판단 불가 → UNKNOWN 발행, 하위 AI들은 보수 모드로
전환"): 워밍업 부족으로 관측치를 못 만들면 예외가 아니라 UNKNOWN을 발행한다 — 이건
정상 운영 경로다.

**지속시간(state_duration_bars)은 인스턴스 상태로 추적**한다 — `classify()`를 호출할
때마다 "봉 하나가 새로 도착했다"고 보고, 직전 호출과 같은 Regime이면 누적, 다르면 1로
리셋한다. 호출 순서가 실제 시간 순서와 같아야 의미가 있다(재생/실시간 양쪽 다 그렇게
호출되는 게 전제).

**가중치 매트릭스(Ver 1.2 §7.1) 결선은 없다** — Aggregator(Futures AI)가 아직 없어
(W24~26 스코프) `intel.regime`을 실제로 소비하는 곳이 없다. 이번 주는 발행 가능한
상태까지만 만든다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

import numpy as np

from messiah.core.messages import BarClosed, Regime, RegimeState, bar_confirm_time
from messiah.strategy.regime.hmm_model import (
    DEFAULT_N_STATES_CANDIDATES,
    OBSERVATION_WINDOW,
    RegimeHMM,
    build_observations,
)
from messiah.strategy.regime.naming import label_states
from messiah.strategy.regime.rules import DEFAULT_RULE_CHAIN, Rule, RuleContext, apply_rules

# `classify()`가 사후분포를 낼 때 forward로 훑는 최근 관측 수 (2026-08-11).
#
# 클수록 startprob의 영향이 줄지만 그만큼 봉 이력이 필요하다(관측 하나에 봉 하나).
# 60은 30분봉 기준 약 4.6거래일 — `RegimeRuntime`의 이력 버퍼(200봉) 안에 넉넉히 들어가고,
# 끈적한 전이행렬에서도 초기분포의 영향이 사실상 사라지는 길이다. 실측 근거는
# `classify()` docstring: 60으로 홀드아웃 국면 분포가 한 상태 100%에서 다섯 상태로 갈라졌다.
_FILTER_OBSERVATIONS = 60


class RegimeAI:
    def __init__(
        self,
        model: RegimeHMM,
        labels: dict[int, Regime],
        *,
        window: int = OBSERVATION_WINDOW,
        rule_chain: Sequence[Rule] = DEFAULT_RULE_CHAIN,
    ) -> None:
        self._model = model
        self._labels = labels
        self._window = window
        self._rule_chain = rule_chain
        self._last_regime: Regime | None = None
        self._state_duration = 0

    @property
    def n_states(self) -> int:
        return self._model.n_states

    @property
    def min_bars_for_classify(self) -> int:
        """`classify()`가 UNKNOWN을 즉시 반환하지 않으려면 필요한 최소 봉 수.

        `classify()` 본문이 쓰는 것과 **같은 값**이다(아래 `min_length`) — 호출측이
        "몇 봉을 채워야 하나"를 알아야 하는데, 그 값을 웜스타트 코드에 다시 하드코딩하면
        window를 바꾼 날 한쪽만 바뀐다. 2026-08-12에 이 값(22)과 하루가 만드는 30m 봉
        수(15)의 대소가 곧 그날 판단 전량이 NO_TRADE였던 이유였고, 그 산술을 호출측이
        스스로 확인할 수 있어야 한다(`features/engine.py`의 `history_capacity`와 같은 취지).
        """
        return self._window + 2

    @property
    def recommended_history_bars(self) -> int:
        """전이행렬이 startprob을 충분히 씻어내는 이력 길이 — 웜스타트 충전량의 하한.

        `classify()`가 `bars[-(min_length + _FILTER_OBSERVATIONS):]`를 잘라 쓰므로 그보다
        짧으면 사후분포에 초기분포의 영향이 남는다(그 docstring "짧으면 있는 만큼만 쓰고,
        그 경우 startprob의 영향이 남는다")."""
        return self.min_bars_for_classify + _FILTER_OBSERVATIONS

    @property
    def labels(self) -> dict[int, Regime]:
        """상태 인덱스 -> Regime 명명 결과(사본) — 사람 검수·스모크 출력용."""
        return dict(self._labels)

    @property
    def hmm_model(self) -> RegimeHMM:
        """통계층 원본 — 상태 시퀀스 재계산 등 명명층 도구(naming.describe_labels)에
        직접 넘길 때 필요."""
        return self._model

    @classmethod
    def fit(
        cls,
        bars: Sequence[BarClosed],
        *,
        window: int = OBSERVATION_WINDOW,
        n_states_candidates: Sequence[int] = DEFAULT_N_STATES_CANDIDATES,
        random_state: int = 0,
        rule_chain: Sequence[Rule] = DEFAULT_RULE_CHAIN,
    ) -> RegimeAI:
        """`bars`(구동 Horizon 완성봉, 보통 30m)로 통계층+명명층을 함께 학습한다."""
        observations, indices = build_observations(bars, window)
        if observations.shape[0] == 0:
            raise ValueError(f"학습 가능한 관측치가 0건 — 데이터 부족(bars={len(bars)}건)")

        model = RegimeHMM.fit(
            observations, n_states_candidates=n_states_candidates, random_state=random_state
        )
        states = model.predict_states(observations)
        labels = label_states(observations, indices, bars, states)
        return cls(model, labels, window=window, rule_chain=rule_chain)

    def classify(
        self, bars: Sequence[BarClosed], *, rule_context: RuleContext | None = None
    ) -> RegimeState:
        """
        입력: `bars`는 지금까지의 전체 이력(마지막 봉이 판정 대상) — 관측 계산에는 최근
             일부만 있으면 되므로 그 구간만 잘라 쓴다. 필요한 최소 길이는 `window`+2다
             (px_core.px_autocorr가 셋 중 가장 엄격해 `window`+2를 요구 — px_trend_r2/
             vl_vol_ratio는 `window`+1이면 충분하지만 가장 엄격한 쪽에 맞춘다. `window`+1로
             잘랐다가 px_autocorr만 워밍업 미달로 None이 되어 매번 UNKNOWN이 나오던 버그를
             테스트로 잡음).
        계산: 통계층(HMM 상태·확신도·명명) → 규칙층(오버라이드 검사) 순. `rule_context`를
             생략하면 vol_ratio만 자동으로 채우고 economic/expiry는 미구현이라 None으로
             둔다(rules.py 모듈 docstring).

        ## 관측 **하나**만 넘기면 전이행렬이 죽는다 (2026-08-11 실측 수정)

        종전엔 `bars[-(window+2):]`로 잘라 관측 1~3개를 만든 뒤 `predict_proba(obs[-1:])`,
        즉 **길이 1짜리 시퀀스**를 HMM에 넘겼다. 길이 1의 사후분포는
        `startprob × emission`뿐이라 전이행렬도 이력도 전혀 안 쓰인다.

        2026-08-11 실데이터 학습에서 그 결과가 드러났다. 학습된 `startprob_`이
        `[0, 0, 0, 0, 1.0]`(원-핫)이었고 — 단일 시퀀스로 적합하면 흔하다, 첫 관측이 어느
        상태였는지를 그대로 배운다 — 그래서 다른 상태의 사후확률이 **항상 0**이 됐다.
        홀드아웃 437봉이 전부 같은 국면(`TREND_DOWN`)으로 나왔다. 같은 관측을 전 구간
        Viterbi로 풀면 5개 상태가 73~100개씩 골고루 나온다: **모델이 아니라 추론이 틀렸다.**

        이제 최근 `_FILTER_OBSERVATIONS`개 관측을 **시퀀스로** 넘기고 마지막 시점의 사후분포를
        쓴다(forward filtering). 전이행렬을 타고 오면서 startprob의 지배력이 지수적으로
        사라진다. 미래 참조는 없다 — 전부 판정 시점까지의 과거 관측이다.
        """
        symbol = bars[-1].symbol if bars else "UNKNOWN"
        min_length = self.min_bars_for_classify
        if len(bars) < min_length:
            return self._emit(
                symbol,
                Regime.UNKNOWN,
                confidence=0.0,
                transition_prob={},
                rule_override=None,
                bars=bars,
            )

        # 필터 길이만큼 **더** 가져온다 — 관측 하나당 봉 하나가 더 필요하다.
        # `bars`가 짧으면 있는 만큼만 쓰고, 그 경우 startprob의 영향이 남는다(줄어들 뿐이다).
        tail = bars[-(min_length + _FILTER_OBSERVATIONS) :]
        observations, _ = build_observations(tail, self._window)
        if observations.shape[0] == 0:
            return self._emit(
                symbol,
                Regime.UNKNOWN,
                confidence=0.0,
                transition_prob={},
                rule_override=None,
                bars=bars,
            )

        # **시퀀스 전체**를 넘기고 마지막 시점을 읽는다(위 docstring "관측 하나만 넘기면").
        proba = self._model.predict_proba(observations)[-1]
        state = int(np.argmax(proba))
        confidence = float(proba[state])
        statistical_regime = self._labels.get(state, Regime.UNKNOWN)
        transition_prob = self._transition_prob_from_state(state)

        context = rule_context or RuleContext(vol_ratio=float(observations[-1][2]))
        override = apply_rules(context, self._rule_chain)

        if override is not None:
            return self._emit(
                symbol,
                override.regime,
                confidence=1.0,
                transition_prob=transition_prob,
                rule_override=override.reason,
                bars=bars,
            )
        return self._emit(
            symbol,
            statistical_regime,
            confidence=confidence,
            transition_prob=transition_prob,
            rule_override=None,
            bars=bars,
        )

    def _emit(
        self,
        symbol: str,
        regime: Regime,
        *,
        confidence: float,
        transition_prob: dict[str, float],
        rule_override: str | None,
        bars: Sequence[BarClosed],
    ) -> RegimeState:
        if regime == self._last_regime:
            self._state_duration += 1
        else:
            self._state_duration = 1
            self._last_regime = regime
        valid_until = bar_confirm_time(bars[-1]) if bars else None
        return RegimeState(
            symbol=symbol,
            regime=regime,
            confidence=confidence,
            state_duration_bars=self._state_duration,
            transition_prob=transition_prob,
            rule_override=rule_override,
            valid_until=valid_until,
        )

    def _transition_prob_from_state(self, state: int) -> dict[str, float]:
        """다음 상태 전이확률을 Regime 이름으로 합산 — 여러 HMM 상태가 같은 Regime으로
        묶였을 수 있어(명명층, 상태 수 4~6 중 4개 원형) 확률을 더한다."""
        row = self._model.transition_matrix[state]
        result: dict[str, float] = {}
        for next_state, prob in enumerate(row):
            regime = self._labels.get(next_state)
            if regime is None:
                continue
            result[regime.value] = result.get(regime.value, 0.0) + float(prob)
        return result

    # ---------------------------------------------------------------- 영속화

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._model.save(_hmm_path(path))
        metadata = {
            "window": self._window,
            "labels": {str(state): regime.value for state, regime in self._labels.items()},
        }
        path.with_suffix(".json").write_text(
            json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
        )

    @classmethod
    def load(cls, path: Path, *, rule_chain: Sequence[Rule] = DEFAULT_RULE_CHAIN) -> RegimeAI:
        """`rule_chain`은 함수 참조라 직렬화하지 않는다 — 커스텀 체인을 썼다면 로드 시
        다시 넘겨야 한다(생략 시 기본 체인)."""
        path = Path(path)
        model = RegimeHMM.load(_hmm_path(path))
        metadata = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        labels = {int(state): Regime(value) for state, value in metadata["labels"].items()}
        return cls(model, labels, window=metadata["window"], rule_chain=rule_chain)


def _hmm_path(base: Path) -> Path:
    return base.with_name(f"{base.stem}_hmm.pkl")
