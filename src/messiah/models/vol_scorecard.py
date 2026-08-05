"""변동성 축 일일 채점 — 고도화 4 (2026-08-05).

## 왜 이 모듈이 생겼나

2026-08-04 피처 관문이 측정으로 확정한 것이 있다:

    Horizon   방향 축 통과      변동성 축 통과      RV+GK 통제 후
    5m         7 / 137          78 / 137           59
    15m       13 / 137          69 / 137           56
    30m       19 / 137          66 / 137           47

부호 있는 방향 피처 39개는 3 Horizon 117 판정에서 **단 1건**만 통과했고 그마저 무부호
강도(`px_adx`)였다. 같은 39개가 변동성 축에서는 22개씩 통과한다. 지속성(직전 RV·GK)을
통제한 뒤에도 `ev_tod_cos`(30m 0.442)·`ev_close_remain`은 살아남는다.

**그런데 그건 8개월 단일 국면의 in-sample 측정이다.** 그 위에 곧바로 모델을 얹는 것은 이
프로젝트가 여러 번 당한 실패 형태다(SYSTEM.md R18 "게이트·차단 로직 신설은 섀도 계측
20거래일 후 승격", 그리고 마흐디의 감마플립 넉 달). 먼저 물어야 할 것은 **"그 예측력이
새 데이터에서도 존재하는가"**이고, 그 질문은 매 거래일 자동으로 답해질 수 있다.

이 모듈이 그 자리다. 모델도, 학습도, 배포도 필요 없다 — 그날 아카이브와 로컬 계산뿐이다.

## 왜 손익이 아니라 예측 품질인가

`ShadowLedger`(`models/shadow_manager.py`)는 방향 예측을 **가상 체결**로 환산해 손익을
낸다. 변동성 예측을 거기 태우면 무의미한 숫자가 나온다 — "변동성이 커진다"는 매수도
매도도 아니기 때문이다. 그리고 이 프로젝트엔 아직 **변동성을 파는 수단이 없다**(옵션
스프레드·레인지 매매 미구현, 2026-08-04 DECISION_LOG에 기록).

그래서 손익 의미론을 흉내내지 않고 **IC(순위상관)** 로만 잰다. 팔 수단이 생기기 전까지
정직하게 말할 수 있는 것은 "맞히는가"뿐이다.

## 무엇이 기준선인가

변동성 군집은 금융 시계열에서 가장 강건한 정형화된 사실이다. 그래서 **직전 RV 자신의
IC**를 항상 같이 낸다 — 피처가 그걸 못 넘으면 통과 개수가 아무리 많아도 의미가 없다.
2026-08-04 실측으로 기준선 IC는 5m +0.576이었다.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np

from messiah.core import logging as mlog
from messiah.core.messages import BarClosed, FeatureVector, Horizon
from messiah.features import gate
from messiah.models.labeling import (
    BARRIER_PARAMS,
    forward_realized_volatility,
    trailing_realized_volatility,
)

# 하루치로 채점할 수 있는 최소 표본. 5m 기준 정규장은 하루 ~78봉인데, 전방 RV 레이블이
# 뒤쪽 `horizon_bars`개를 잘라먹으므로 실제로는 그보다 적다. 30봉 미만이면 IC의 표준오차가
# 상관계수 자체만큼 커져 숫자에 의미가 없다 — 그런 날은 **판정하지 않는다**(L18).
MIN_SAMPLES = 30

# 2026-08-04 관문이 RV+GK 통제 후에도 상위에 남긴 피처들. 이 목록은 **가설**이지 결론이
# 아니다 — 이 모듈의 존재 이유가 "그 가설이 새 데이터에서 유지되는가"를 매일 재는 것이다.
# 목록을 바꿀 때는 그 근거를 dev_memory에 남길 것.
#
# **창 접미사가 붙어 있어야 한다**(`px_kurt_r`가 아니라 `px_kurt_r_5`) — 창 있는 피처는
# `features/spec.py`가 `{이름}_{창}`으로 펼친다. 2026-08-05 첫 실행에서 접미사 없는 이름
# 다섯 개가 전부 "미측정"으로 나와 발견했다.
#
# `ev_*` 둘은 **현재 프로덕션 feature_set(v2026.07)에 없다** — 2026-08-04에 만들었지만
# 아직 켜지지 않았다(켜려면 재학습이 필요하고 그건 R11상 장후 별도 세션이다). 일부러
# 목록에 남겨 둔다: 매일 "피처셋에 없음"으로 찍히는 것이, 관문이 가장 값어치 있다고 지목한
# 카테고리가 아직 운영에 안 붙어 있다는 사실을 계속 드러내기 때문이다.
DEFAULT_WATCHLIST: tuple[str, ...] = (
    "ev_tod_cos",
    "ev_close_remain",
    "px_kurt_r_5",
    "px_high_dist_5",
    "px_ema_dev_5",
    "vl_gk_5",
    "vl_atr_rel_5",
)

# 피처 1개의 판정 상태 — "못 쟀다"의 이유를 갈라 적는다. 표본이 모자란 것과 그 피처가
# 애초에 프로덕션 feature_set에 없는 것은 **완전히 다른 사건**이고, 후자는 사람이 조치할
# 대상이다(재학습 후 feature_set 승격).
STATUS_SCORED = "측정"
STATUS_ABSENT = "피처셋에 없음"
STATUS_TOO_FEW = "표본 부족"
STATUS_BASELINE = "기준선 자신"

# 통제변수로 쓸 **피처** — 직전 RV에 더해서 넣는다.
#
# 이게 없으면 이 모듈이 매일 거짓 양성을 낸다. 2026-08-05 첫 실행에서 직전 RV만 통제했더니
# `vl_gk_5`(t +8.8)·`vl_atr_rel_5`(t +9.0)가 5m·15m에서 통과했는데, **그건 2026-08-04
# 관문이 이미 "기준선의 프록시"라고 판정한 바로 그 계열이다.** 관문의 통제 3단계가
# 그 사실을 이렇게 보여줬다:
#
#     통제 없음 → 직전RV 통제 → RV+GK 통제
#     78/137        63           59        (5m 통과 수)
#     변동성 추정량 계열 |IC| 중앙: 0.469 → 0.194 → 0.048
#
# 종가 기반 RV는 **비효율적 추정량**이라(Parkinson 1980 이래 알려진 사실) 그것만 통제하면
# "OHLC로 현재 변동성을 더 잘 잰다"가 증분처럼 보인다. 그건 새 정보가 아니라 추정 효율이다.
# 레인지 기반 GK까지 통제해야 관문의 엄격 설정과 같아진다.
DEFAULT_BASELINE_FEATURES: tuple[str, ...] = ("vl_gk_5",)


@dataclass
class FeatureScore:
    name: str
    ic: float | None  # 주변 IC — None은 정의 불가(상수이거나 표본 부족)
    partial_ic: float | None  # 직전 RV를 통제한 부분 IC
    status: str = STATUS_SCORED
    # 겹침 보정 t값(부분 IC 기준). 겹친 표본을 독립으로 세면 t가 √overlap배 부풀어
    # 잡음이 유의해 보인다 — `features/gate.ic_t_stat()`와 같은 보정을 쓴다.
    partial_t: float | None = None

    @property
    def survives(self) -> bool:
        """관문(`features/gate.py`)과 **같은 기준**을 넘었는가 — 효과크기 + 유의성 둘 다."""
        if self.partial_ic is None or self.partial_t is None:
            return False
        return abs(self.partial_ic) >= gate.DEFAULT_MIN_ABS_IC and (
            abs(self.partial_t) >= gate.DEFAULT_MIN_ABS_T
        )


@dataclass
class VolScorecard:
    """한 Horizon의 변동성 축 채점 결과 (최근 `window_days` 거래일 구간).

    ## 왜 하루가 아니라 구간인가 (2026-08-05 첫 실행에서 확정)

    하루치만 채점해 보니 표본이 이랬다: **5m 76 · 15m 20 · 30m 7**. 30m은 정규장에서
    하루 15봉밖에 안 나오고 전방 RV 레이블이 뒤쪽을 잘라먹는다. 15m·30m은 매일 "표본 부족"이
    되어 **관문이 실제로 검증하고 싶었던 두 Horizon이 영원히 판정 불가**가 된다.

    그래서 대상일로 **끝나는** 최근 N거래일을 채점한다. 매일 창이 하루씩 밀리므로 값의
    날짜별 변화가 곧 예측력의 드리프트다 — 하루짜리 잡음보다 이쪽이 R18("섀도 계측
    20거래일")이 묻는 질문에도 더 가깝다.
    """

    horizon: str
    samples: int
    baseline_ic: float | None  # 직전 RV 자신의 IC — 피처가 넘어야 할 선
    window_days: int = 1  # 이 채점이 덮은 거래일 수
    # 실제로 통제에 쓴 기준선 피처 — 설정에 있어도 feature_set에 없으면 빠지므로,
    # "무엇을 통제한 결과인가"가 산출물에 남아야 나중에 값을 비교할 수 있다.
    baseline_used: list[str] = field(default_factory=list)
    features: list[FeatureScore] = field(default_factory=list)
    note: str = ""

    @property
    def measurable(self) -> bool:
        return self.samples >= MIN_SAMPLES and self.baseline_ic is not None

    @property
    def beats_baseline(self) -> list[str]:
        """기준선 너머의 증분이 **관문과 같은 기준으로** 살아남은 피처.

        판정에 쓰는 것은 주변 IC가 아니라 **부분 IC**다. 변동성 군집 때문에 아무 변동성
        추정량이나 주변 IC는 높게 나오고(2026-08-04: 통제 전 상위가 전부 변동성 추정량),
        통제하면 |IC| 중앙값이 0.469 → 0.048로 무너졌다.

        기준은 `features/gate.py`의 관문과 **같은 값**을 쓴다 — 효과크기(|IC| ≥ 0.02)와
        겹침 보정 t값(|t| ≥ 2.0) 둘 다. 초기에 "부분 IC ≠ 0"으로 뒀다가 7개 중 5개가
        무조건 통과하는 것을 보고 고쳤다(2026-08-05): 거의 모든 실수는 0이 아니므로 그건
        판정이 아니라 **판정하는 척**이었다. 두 곳이 다른 기준을 쓰면 연구 경로와 운영
        경로가 서로 다른 말을 하게 된다.
        """
        return [f.name for f in self.features if f.survives]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _column(vectors: Sequence[FeatureVector], name: str) -> np.ndarray:
    """피처 1개의 값 열. 없는 이름이면 전부 NaN — 예외를 던지지 않는다.

    관심 목록에 오타가 있거나 그 피처가 현재 `feature_set`에 없을 수 있는데, 그때 채점
    전체를 죽이면 안 된다. 대신 `ic=None`으로 나가고, 그게 곧 "이 피처는 측정되지 않았다"다.
    """
    out = np.full(len(vectors), np.nan, dtype=float)
    for i, vector in enumerate(vectors):
        value = vector.values.get(name)
        if value is not None:
            out[i] = value
    return out


def score_horizon(
    bars: Sequence[BarClosed],
    vectors: Sequence[FeatureVector],
    *,
    horizon: Horizon,
    watchlist: Sequence[str] = DEFAULT_WATCHLIST,
    baseline_features: Sequence[str] = DEFAULT_BASELINE_FEATURES,
    window_days: int = 1,
) -> VolScorecard:
    """그 Horizon 하루치를 채점한다 — 순수 함수(네트워크·파일 접근 없음).

    입력: `bars`와 `vectors`는 **같은 길이·같은 순서**여야 한다(i번째 벡터가 i번째 봉의
         확정 시점에 산출된 것). 어긋나면 채점이 조용히 미래를 보게 되므로 ValueError.
    계산: 전방 RV를 레이블로, 직전 RV를 통제변수로 삼아 관심 피처의 주변 IC와 부분 IC를 낸다.
         예측 구간(`horizon_bars`)은 방향 레이블의 시간배리어와 **같은 봉 수**를 쓴다 —
         두 축의 IC를 견주려면 예측 구간이 같아야 한다(`scripts/run_feature_gate.py`와 동일).
    실패 조건: 표본 부족은 실패가 아니라 **미측정**이다(`measurable=False`, note에 사유).
    """
    if len(bars) != len(vectors):
        raise ValueError(f"봉과 피처 벡터의 길이 불일치: {len(bars)} vs {len(vectors)}")

    horizon_bars = BARRIER_PARAMS[horizon].time_barrier_bars
    forward = forward_realized_volatility(bars, horizon_bars=horizon_bars)
    trailing = trailing_realized_volatility(bars, horizon_bars=horizon_bars)

    keep = [
        i
        for i, (f, t) in enumerate(zip(forward, trailing))
        if f is not None and t is not None and np.isfinite(f) and np.isfinite(t)
    ]
    if len(keep) < MIN_SAMPLES:
        return VolScorecard(
            horizon=horizon.value,
            samples=len(keep),
            baseline_ic=None,
            window_days=window_days,
            note=f"표본 {len(keep)} < 최소 {MIN_SAMPLES} — 판정하지 않음",
        )

    y = np.array([forward[i] for i in keep], dtype=float)
    trailing_rv = np.array([trailing[i] for i in keep], dtype=float)
    baseline_ic = gate.spearman(trailing_rv, y)

    known = set(vectors[0].values) if vectors else set()
    # 통제 행렬 = [직전 RV] + 설정된 기준선 피처들. 없는 피처는 조용히 빠진다(그 사실은
    # 아래 `baseline_used`로 리포트에 남으므로 침묵이 아니다).
    baseline_used = [n for n in baseline_features if n in known]
    columns = [trailing_rv] + [_column(vectors, n)[keep] for n in baseline_used]
    baseline_matrix = np.column_stack(columns)

    scores: list[FeatureScore] = []
    for name in watchlist:
        # 그 피처가 현재 feature_set에 아예 없는 것과 표본이 모자란 것은 다른 사건이다.
        if name not in known:
            scores.append(FeatureScore(name=name, ic=None, partial_ic=None, status=STATUS_ABSENT))
            continue
        # 기준선 자신을 기준선으로 통제하면 잔차가 0이라 판정이 무의미하다 — 관심 목록에
        # 남겨 두되 그 사실을 상태로 밝힌다(주변 IC는 여전히 정보이므로 계산한다).
        if name in baseline_used:
            column = _column(vectors, name)[keep]
            mask = np.isfinite(column)
            scores.append(
                FeatureScore(
                    name=name,
                    ic=gate.spearman(column[mask], y[mask]) if mask.sum() >= MIN_SAMPLES else None,
                    partial_ic=None,
                    status=STATUS_BASELINE,
                )
            )
            continue
        column = _column(vectors, name)[keep]
        mask = np.isfinite(column)
        # 유한한 표본이 부족하면 그 피처만 미측정으로 남긴다 — 다른 피처는 계속 잰다(L22).
        if int(mask.sum()) < MIN_SAMPLES:
            scores.append(FeatureScore(name=name, ic=None, partial_ic=None, status=STATUS_TOO_FEW))
            continue
        partial_ic = gate.partial_spearman(column[mask], y[mask], baseline_matrix[mask])
        scores.append(
            FeatureScore(
                name=name,
                ic=gate.spearman(column[mask], y[mask]),
                partial_ic=partial_ic,
                # 겹침은 레이블 구간 그 자체다 — 전방 RV가 `horizon_bars`봉을 덮으므로
                # 인접 표본이 그만큼 겹친다(`scripts/run_feature_gate.py`와 같은 처리).
                partial_t=(
                    None
                    if partial_ic is None
                    else gate.ic_t_stat(partial_ic, int(mask.sum()), overlap=horizon_bars)
                ),
            )
        )

    return VolScorecard(
        horizon=horizon.value,
        samples=len(keep),
        baseline_ic=baseline_ic,
        window_days=window_days,
        baseline_used=["직전RV", *baseline_used],
        features=scores,
    )


def format_scorecards(cards: Sequence[VolScorecard]) -> list[str]:
    """사람이 장 마감 후 훑는 요약 — 기준선을 **먼저** 적는다.

    피처 IC를 먼저 보여주면 사람은 그걸 성적으로 읽는다. 넘어야 할 선이 얼마나 높은지가
    앞에 있어야 한다(`models/wiring_completeness.py`가 결선 상태를 손익보다 먼저 찍는 것과
    같은 이유).
    """
    lines: list[str] = []
    for card in cards:
        if not card.measurable:
            lines.append(f"  {card.horizon}: 미측정 — {card.note}")
            continue
        beats = card.beats_baseline
        lines.append(
            f"  {card.horizon}: 최근 {card.window_days}거래일 · 표본 {card.samples} · "
            f"기준선 IC {card.baseline_ic:+.3f} · 통제 {'+'.join(card.baseline_used)} · "
            f"기준선 초과 {len(beats)}/{len(card.features)}개"
        )
        for score in card.features:
            if score.ic is None:
                lines.append(f"      {score.name:<18} 미측정 — {score.status}")
                continue
            partial = "정의불가" if score.partial_ic is None else f"{score.partial_ic:+.3f}"
            t_text = "" if score.partial_t is None else f" (t {score.partial_t:+.1f})"
            mark = " ✓" if score.survives else ""
            lines.append(
                f"      {score.name:<18} IC {score.ic:+.3f} · 통제후 {partial}{t_text}{mark}"
            )
    return lines


def write_scorecards(
    cards: Sequence[VolScorecard], *, symbol: str, day: date, log_dir: Path
) -> Path:
    """채점 결과를 `logs/vol_scorecard_YYYYMMDD.json`으로 — 무결성 리포트가 이걸 읽는다.

    **왜 로그가 아니라 파일인가**: 이 스크립트는 장후에 사람이 따로 돌리므로 그 stdout이
    `logs/l1_daily_*.log`에 안 들어간다. 구조화 로그만 남기면 리포트가 영원히 못 본다
    (2026-08-05에 실제로 그렇게 만들었다가 리포트가 "미측정"으로 찍는 것을 보고 고쳤다).
    `verify_archive_volume.py`의 산출물과 같은 방식이다.

    측정 못 한 Horizon도 남긴다. "오늘 몇 개를 쟀고 몇 개가 미측정이었나"가 매일 기록돼야
    나중에 "그 예측력이 유지됐는가"를 이력으로 물을 수 있다.
    """
    path = log_dir / f"vol_scorecard_{day.strftime('%Y%m%d')}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"date": day.isoformat(), "symbol": symbol, "horizons": summarise(cards)},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def log_scorecards(cards: Sequence[VolScorecard], *, symbol: str, date: str) -> None:
    """채점 결과를 구조화 로그로도 — 장중 세션이 직접 부를 때(있다면)를 위한 경로.

    리포트가 읽는 정본은 `write_scorecards()`가 쓰는 파일이다.
    """
    for card in cards:
        mlog.log(
            "VolAxisScorecard",
            (
                f"{card.horizon} 변동성 축 — 기준선 IC {card.baseline_ic:+.3f} · "
                f"기준선 초과 {len(card.beats_baseline)}개"
                if card.measurable
                else f"{card.horizon} 변동성 축 미측정 — {card.note}"
            ),
            symbol=symbol,
            date=date,
            horizon=card.horizon,
            samples=card.samples,
            window_days=card.window_days,
            baseline_ic=card.baseline_ic,
            baseline_used=card.baseline_used,
            beats_baseline=card.beats_baseline,
            scores={s.name: s.partial_ic for s in card.features},
        )


def summarise(cards: Mapping[str, VolScorecard] | Sequence[VolScorecard]) -> dict[str, object]:
    """무결성 리포트에 실을 축약형 — Horizon → {기준선 IC, 초과 피처 목록, 미탑재 피처}.

    `absent_features`가 있는 이유 (2026-08-05 2차, 고도화 5): 관심 목록의 `ev_tod_cos`·
    `ev_close_remain`은 2026-08-04 관문이 상위로 지목했는데 **프로덕션 `feature_set`에 없어
    측정조차 안 된다.** 재학습 후 `configs/instance.yaml`의 `feature_set`을 `v2026.08-ev`로
    승격하면 사라져야 할 목록이고, 그 승격이 실제로 먹혔는지를 확인할 유일한 수단이다.

    종전 축약형은 이 정보를 버렸다 — `STATUS_ABSENT`(피처셋에 없음)와 `STATUS_TOO_FEW`
    (표본 부족)를 애써 갈라 놓고도 리포트까지 오면 둘 다 그냥 "beats_baseline에 없음"이었다.
    이 프로젝트가 반복한 실패 형태다: **결선했다고 믿는데 조용히 안 붙어 있는 상태.**
    """
    items = cards.values() if isinstance(cards, Mapping) else cards
    return {
        card.horizon: {
            "samples": card.samples,
            "window_days": card.window_days,
            "baseline_ic": card.baseline_ic,
            "baseline_used": card.baseline_used,
            "beats_baseline": card.beats_baseline,
            "measurable": card.measurable,
            "absent_features": [f.name for f in card.features if f.status == STATUS_ABSENT],
        }
        for card in items
    }
