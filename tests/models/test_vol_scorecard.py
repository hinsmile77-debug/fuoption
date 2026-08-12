"""변동성 축 일일 채점 — 고도화 4 (2026-08-05 신설).

이 모듈의 존재 이유는 2026-08-04 관문 결과(8개월 단일 국면 in-sample)가 **새 데이터에서도
유지되는가**를 매일 자동으로 묻는 것이다. 그래서 테스트도 "IC를 정확히 계산하는가"보다
**"거짓 양성을 안 내는가"**에 무게를 둔다 — 첫 구현이 정확히 그 함정 둘에 빠졌다.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from messiah.core.messages import BarClosed, FeatureVector, Horizon
from messiah.core.timeutil import KST
from messiah.models import vol_scorecard

_START = datetime(2026, 8, 3, 9, 0, tzinfo=KST)


def _bars(n: int, *, seed: int = 1) -> list[BarClosed]:
    """변동성이 군집하는 합성 봉 — 실제 시계열의 가장 강건한 정형화된 사실을 흉내낸다."""
    rng = 12345 + seed
    price = 10_000
    out: list[BarClosed] = []
    for i in range(n):
        rng = (1103515245 * rng + 12345) % (2**31)
        # 변동성 군집: 100봉 주기로 진폭이 커졌다 작아진다
        amplitude = 4 + int(20 * (0.5 + 0.5 * math.sin(i / 30)))
        step = (rng % (2 * amplitude + 1)) - amplitude
        price = max(100, price + step)
        out.append(
            BarClosed(
                symbol="T",
                horizon=Horizon.M5,
                bar_open_kst=_START + timedelta(minutes=5 * i),
                o_ticks=price,
                h_ticks=price + amplitude,
                l_ticks=max(1, price - amplitude),
                c_ticks=price,
                volume=100,
            )
        )
    return out


def _vectors(bars: list[BarClosed], values_by_name) -> list[FeatureVector]:  # noqa: ANN001
    return [
        FeatureVector(
            symbol="T",
            horizon=Horizon.M5,
            bar_open_kst=bar.bar_open_kst,
            feature_set="test",
            values={name: fn(i, bar) for name, fn in values_by_name.items()},
        )
        for i, bar in enumerate(bars)
    ]


def test_absent_feature_is_distinguished_from_too_few_samples():
    """ "피처셋에 없음"과 "표본 부족"은 완전히 다른 사건이다 — 전자는 사람이 조치할 대상
    (재학습 후 feature_set 승격)이고 후자는 그냥 기다리면 된다.

    2026-08-05 첫 실행에서 다섯 개가 "미측정"으로 나왔는데, 원인은 창 접미사 누락
    (`px_kurt_r` vs `px_kurt_r_5`)이었다. 이유가 안 적혀 있어 한참 헤맸다.
    """
    bars = _bars(200)
    vectors = _vectors(bars, {"present": lambda i, b: float(i % 7)})

    card = vol_scorecard.score_horizon(
        bars, vectors, horizon=Horizon.M5, watchlist=("present", "없는피처"), baseline_features=()
    )

    by_name = {f.name: f for f in card.features}
    assert by_name["없는피처"].status == vol_scorecard.STATUS_ABSENT
    assert by_name["present"].status == vol_scorecard.STATUS_SCORED


def test_too_few_samples_is_unmeasured_not_a_verdict():
    """표본이 모자란 날은 실패가 아니라 **미측정**이다 — 0으로 우기지 않는다(L18)."""
    bars = _bars(10)
    vectors = _vectors(bars, {"f": lambda i, b: float(i)})

    card = vol_scorecard.score_horizon(bars, vectors, horizon=Horizon.M5, watchlist=("f",))

    assert card.measurable is False
    assert card.baseline_ic is None
    assert "판정하지 않음" in card.note


def test_a_pure_noise_feature_does_not_survive():
    """**거짓 양성을 안 내는가** — 이 모듈에서 가장 중요한 성질.

    첫 구현은 통과 기준이 "부분 IC ≠ 0"이었다. 거의 모든 실수는 0이 아니므로 잡음 피처도
    전부 통과했고, 7개 중 5개가 무조건 "기준선 초과"로 찍혔다. 그건 판정이 아니라
    **판정하는 척**이었다.
    """
    bars = _bars(400)
    rng = 999

    def _noise(i: int, _bar: BarClosed) -> float:
        nonlocal rng
        rng = (1103515245 * rng + 12345) % (2**31)
        return float(rng % 1000)

    vectors = _vectors(bars, {"noise": _noise})

    card = vol_scorecard.score_horizon(
        bars, vectors, horizon=Horizon.M5, watchlist=("noise",), baseline_features=()
    )

    assert card.measurable is True
    assert card.beats_baseline == []


def test_a_feature_that_is_the_label_survives():
    """반대 방향의 확인 — 진짜 예측력이 있으면 통과해야 한다(기준이 너무 빡빡하지 않은가).

    다음 구간의 실현변동성을 그대로 아는 피처를 넣는다. 이건 미래 정보라 현실엔 없지만,
    관문 기준이 **통과 가능한 값인지**를 확인하는 상한 대조군이다.
    """
    bars = _bars(400)
    horizon_bars = vol_scorecard.BARRIER_PARAMS[Horizon.M5].time_barrier_bars
    forward = vol_scorecard.forward_realized_volatility(bars, horizon_bars=horizon_bars)
    vectors = _vectors(
        bars, {"oracle": lambda i, b: forward[i] if forward[i] is not None else float("nan")}
    )

    card = vol_scorecard.score_horizon(
        bars, vectors, horizon=Horizon.M5, watchlist=("oracle",), baseline_features=()
    )

    assert card.beats_baseline == ["oracle"]


def test_baseline_features_are_controlled_not_scored():
    """기준선 자신을 기준선으로 통제하면 잔차가 0이라 판정이 무의미하다.

    이 갈래가 없으면 `vl_gk_5`가 매일 "기준선 초과"로 찍힌다 — 2026-08-04 관문이 이미
    "기준선의 프록시"라고 판정한 바로 그 계열인데도.
    """
    bars = _bars(300)
    vectors = _vectors(bars, {"vl_gk_5": lambda i, b: float(b.h_ticks - b.l_ticks)})

    card = vol_scorecard.score_horizon(
        bars,
        vectors,
        horizon=Horizon.M5,
        watchlist=("vl_gk_5",),
        baseline_features=("vl_gk_5",),
    )

    score = card.features[0]
    assert score.status == vol_scorecard.STATUS_BASELINE
    assert score.partial_ic is None
    assert score.name not in card.beats_baseline
    assert card.baseline_used == ["직전RV", "vl_gk_5"]


def test_controlling_for_gk_removes_the_volatility_estimator_false_positive():
    """2026-08-05 실측 회귀 — 이 통제가 없으면 매일 거짓 양성이 난다.

    직전 RV만 통제했을 때 `vl_atr_rel_5`가 5m에서 t +9.0으로 통과했다. 종가 기반 RV는
    **비효율적 추정량**이라 그것만 통제하면 "OHLC로 현재 변동성을 더 잘 잰다"가 증분처럼
    보인다 — 새 정보가 아니라 추정 효율이다. 레인지 기반 GK까지 통제하면 사라진다.
    """
    bars = _bars(500)
    values = {
        "vl_gk_5": lambda i, b: float(b.h_ticks - b.l_ticks),
        # GK와 거의 같은 정보를 담은 다른 추정량(레인지의 단조 변환).
        "vl_atr_rel_5": lambda i, b: float(b.h_ticks - b.l_ticks) * 1.7 + 0.5,
    }
    vectors = _vectors(bars, values)

    rv_only = vol_scorecard.score_horizon(
        bars, vectors, horizon=Horizon.M5, watchlist=("vl_atr_rel_5",), baseline_features=()
    )
    rv_and_gk = vol_scorecard.score_horizon(
        bars,
        vectors,
        horizon=Horizon.M5,
        watchlist=("vl_atr_rel_5",),
        baseline_features=("vl_gk_5",),
    )

    assert rv_only.beats_baseline == ["vl_atr_rel_5"]  # 통제 부족 시 거짓 양성
    assert rv_and_gk.beats_baseline == []  # GK까지 통제하면 사라진다


def test_mismatched_lengths_raise_rather_than_silently_look_ahead():
    """봉과 벡터가 어긋나면 채점이 조용히 미래를 본다 — 실패하는 편이 낫다."""
    bars = _bars(50)
    vectors = _vectors(bars, {"f": lambda i, b: float(i)})[:-1]

    try:
        vol_scorecard.score_horizon(bars, vectors, horizon=Horizon.M5)
    except ValueError as exc:
        assert "길이 불일치" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("길이 불일치를 통과시켰다")


def test_write_scorecards_produces_the_file_the_report_reads(tmp_path):
    """리포트가 읽는 정본은 **로그가 아니라 파일**이다.

    이 스크립트는 장후에 사람이 따로 돌리므로 그 stdout이 `logs/l1_daily_*.log`에 안 들어간다.
    구조화 로그만 남기면 리포트가 영원히 "미측정"으로 찍는다(2026-08-05에 실제로 그랬다).
    """
    from datetime import date

    bars = _bars(300)
    vectors = _vectors(bars, {"f": lambda i, b: float(i % 11)})
    card = vol_scorecard.score_horizon(
        bars, vectors, horizon=Horizon.M5, watchlist=("f",), baseline_features=()
    )

    path = vol_scorecard.write_scorecards(
        [card], symbol="A05608", day=date(2026, 8, 4), log_dir=tmp_path
    )

    assert path.name == "vol_scorecard_20260804.json"
    import json

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["symbol"] == "A05608"
    assert "5m" in payload["horizons"]
    assert payload["horizons"]["5m"]["measurable"] is True


# ------------------------------ 통제후 정의불가를 산출물이 말한다 (2026-08-12 G-4)


def _card_with(scores: list[vol_scorecard.FeatureScore]) -> vol_scorecard.VolScorecard:
    return vol_scorecard.VolScorecard(
        horizon="5m",
        samples=100,
        baseline_ic=0.5,
        window_days=20,
        baseline_used=["vl_gk_5"],
        features=scores,
    )


def test_the_control_variable_itself_is_reported_as_undefined():
    """2026-08-12 실측 재현 — `vl_gk_5`가 3개 Horizon 전부 「통제후 정의불가」였다.

    통제 변수 자신이 통제군에 있으니 잔차가 0인 것은 구조적 결과다. 문제는 그 사실이
    `vol_scorecard_*.json`에 **아무 형태로도 안 남았다**는 것이다 — `beats_baseline`에도
    `absent_features`에도 없어서, 「7개 중 1개 초과」의 분모가 실제로는 6개라는 것을
    **콘솔을 본 사람만** 알았다.
    """
    card = _card_with(
        [
            # 통제군 자신 — 값은 있는데 통제 후 잔차가 사라졌다.
            vol_scorecard.FeatureScore(name="vl_gk_5", ic=0.515, partial_ic=None),
            vol_scorecard.FeatureScore(name="px_kurt_r_5", ic=0.1, partial_ic=0.05, partial_t=3.0),
            # 애초에 피처셋에 없는 것 — 이쪽은 다른 상태다(`absent_features`).
            vol_scorecard.FeatureScore(
                name="ev_tod_cos", ic=None, partial_ic=None, status=vol_scorecard.STATUS_ABSENT
            ),
        ]
    )

    assert card.undefined_after_control == ["vl_gk_5"]

    summary = vol_scorecard.summarise([card])["5m"]
    assert summary["undefined_after_control"] == ["vl_gk_5"]
    assert summary["absent_features"] == ["ev_tod_cos"], "미탑재와 정의불가는 다른 상태다"
    assert summary["beats_baseline"] == ["px_kurt_r_5"]


def test_the_console_line_reports_the_true_denominator():
    """분모가 정직해야 한다 — 판정 자체가 불가능했던 피처는 "초과 못 함"이 아니다."""
    card = _card_with(
        [
            vol_scorecard.FeatureScore(name="vl_gk_5", ic=0.515, partial_ic=None),
            vol_scorecard.FeatureScore(name="px_kurt_r_5", ic=0.1, partial_ic=0.05, partial_t=3.0),
        ]
    )

    line = vol_scorecard.format_scorecards([card])[0]

    assert "기준선 초과 1/1개" in line, "분모는 2가 아니라 채점 가능했던 1이다"
    assert "통제후 정의불가 1개(vl_gk_5)" in line


def test_a_clean_scorecard_says_nothing_extra():
    """정의불가가 없으면 그 문구도 없다 — 매일 붙는 꼬리표는 곧 안 읽힌다."""
    card = _card_with(
        [vol_scorecard.FeatureScore(name="px_kurt_r_5", ic=0.1, partial_ic=0.05, partial_t=3.0)]
    )

    line = vol_scorecard.format_scorecards([card])[0]

    assert "정의불가" not in line
    assert "기준선 초과 1/1개" in line
