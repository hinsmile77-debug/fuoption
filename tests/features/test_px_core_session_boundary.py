"""야간 갭이 10분봉 한 칸으로 들어가고 있었다 — 2026-08-20 F-G (W-11 종결).

## 무슨 일이 있었나

`px_max_ret_60`이 10m Horizon에서 세션 내내(41봉) 단 하나의 값에 고정됐고, 등록부가
`no-degenerate-features` **3번째 재발**(최초 2026-08-13)로 판정했다. 아카이브에서 직접
재현한 값이 원인을 말한다:

    +0.032462  2026-08-19 15:30 KST -> 2026-08-20 08:40 KST   ← 창의 argmax (17시간 간격)
    +0.019337  2026-08-14 15:30 KST -> 2026-08-17 08:40 KST   ← 주말 갭 (3일 간격)
    +0.017546  2026-08-20 09:00     -> 2026-08-20 09:10       ← 그날 장중 최대

창 60은 **60분이 아니라 60봉**이다. 10m에서 60봉 = 600분 ≈ 1.46 거래일이고 한 세션은
410분 ≈ 41봉이므로 **창이 세션보다 길다**. argmax가 세션 중에 창을 벗어날 수 없고, 그
argmax가 하필 전일 종가 → 당일 시가를 잇는 세션 경계 봉이다. 야간 갭 +3.25%는 그날 장중
최대(+1.75%)의 **1.85배** — 어떤 장중 봉도 이걸 못 넘는다.

`dev_memory` W-11은 *"창 정의 60분과 10m 40표본(창 6봉)의 관계"* 를 가설로 적어 뒀다.
**그 가설은 틀렸다** — 창은 60봉이고, 상수의 원인은 표본 수가 아니라 세션 경계다.

## 이 파일이 메우는 것 — SYSTEM.md R16

R16은 *"Feature 계산은 known-value 회귀 테스트 필수"* 다. **세션 경계를 포함한 다중일
버퍼에 대한 known-value 케이스가 하나도 없었고**, 그 결손이 이 사고의 직접 원인이다.
여기 값들은 `data/bars/A05609/10m/*.parquet` 4일치 153봉에서 실측한 것이다.

## 값을 바꿨다 — 재학습과 같은 커밋으로 (2026-08-20 F-G 2단계)

라이브 번들 `real-20260811-1604-30m`이 오염된 값 위에서 학습돼 있었다. 정의만 바꾸면
학습분포와 추론분포가 어긋나므로 **전환과 재학습을 한 커밋에** 넣었다.

전환 방식은 「경계 쌍 제외」가 아니라 **「경계를 넘지 않는 인접쌍을 필요한 수만큼 더 걷기」**다.
그냥 빼면 반환 길이가 창에 못 미쳐 `len(...) < window` 가드에 걸리는데, 10m의 `*_60`은 창
(60봉)이 세션(약 41봉)보다 길어 **매일** 그렇게 된다 — 오염을 NaN으로 바꿀 뿐이라 더 나쁘다.

실측 효과(`data/bars/A05609/10m/` 4일치 153봉, 창이 찬 구간의 distinct value):

    전환 전  0.008827 · 0.019337(주말 갭) · 0.032462(야간 갭)   ← 3개 중 2개가 인공물
    전환 후  0.008743 · 0.008827 · 0.017546                     ← 전부 실제 장중 값
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from messiah.core.messages import BarClosed, BarSession, Horizon
from messiah.core.timeutil import KST
from messiah.features import px_core

# 실측 상수 (`data/bars/A05609/10m/`, 2026-08-17~20)
OVERNIGHT_GAP = 0.032462281826249884  # 08-19 15:30 -> 08-20 08:40
INTRADAY_MAX = 0.017546  # 08-20 09:00 -> 09:10
INFLATION_RATIO = 1.85


def _bar(when: datetime, close: int) -> BarClosed:
    return BarClosed(
        symbol="A05609",
        horizon=Horizon.M10,
        bar_open_kst=when,
        o_ticks=close,
        h_ticks=close,
        l_ticks=close,
        c_ticks=close,
        volume=100,
        session=BarSession.REGULAR,
    )


def _two_day_bars() -> list[BarClosed]:
    """08-19 오후 + 08-20 오전 — 실측 비율을 그대로 재현하는 최소 버퍼.

    종가는 틱 정수라 비율이 정확히 재현되도록 역산해서 고른 값이다.
    """
    bars: list[BarClosed] = []
    # 전일 오후: 완만한 하락 (장중 최대 수익률이 야간 갭보다 작아야 한다)
    base = datetime(2026, 8, 19, 14, 0, tzinfo=KST)
    close = 52_000
    for step in range(9):  # 14:00 ~ 15:20
        bars.append(_bar(base + timedelta(minutes=10 * step), close))
        close -= 20
    prev_close = close + 20  # 전일 마지막 종가
    # 당일: 08:40 시가가 전일 종가 대비 +3.2462%
    today_open = round(prev_close * (1 + OVERNIGHT_GAP))
    day = datetime(2026, 8, 20, 8, 40, tzinfo=KST)
    bars.append(_bar(day, today_open))
    # 09:00 -> 09:10 이 그날 장중 최대 +1.7546%
    bars.append(_bar(datetime(2026, 8, 20, 9, 0, tzinfo=KST), today_open))
    bars.append(
        _bar(
            datetime(2026, 8, 20, 9, 10, tzinfo=KST),
            round(today_open * (1 + INTRADAY_MAX)),
        )
    )
    return bars


# ------------------------------------------------------ 경계 식별


def test_boundary_is_found_between_trading_days() -> None:
    bars = _two_day_bars()
    boundaries = px_core.session_boundary_indices(bars)
    assert len(boundaries) == 1
    left, right = bars[boundaries[0]], bars[boundaries[0] + 1]
    assert left.bar_open_kst.date() != right.bar_open_kst.date()


def test_single_day_buffer_has_no_boundary() -> None:
    day = datetime(2026, 8, 20, 9, 0, tzinfo=KST)
    bars = [_bar(day + timedelta(minutes=10 * i), 52_000 + i) for i in range(10)]
    assert px_core.session_boundary_indices(bars) == []


def test_trading_day_is_read_in_kst_not_utc() -> None:
    """**정확히 반대로 틀리는 실수를 막는다.**

    `bar_open_kst`는 이름과 달리 UTC로 저장돼 오는 경로가 있다(Parquet 재생). UTC 날짜로
    가르면 08:40 KST 봉이 전날(UTC 23:40)로 떨어져 **09:00과의 사이가 경계로 오인**되고,
    정작 진짜 경계(전일 15:30 → 당일 08:40)는 UTC상 같은 날이라 놓친다.
    """
    from datetime import timezone

    pre = datetime(2026, 8, 20, 8, 40, tzinfo=KST).astimezone(timezone.utc)
    post = datetime(2026, 8, 20, 9, 0, tzinfo=KST).astimezone(timezone.utc)
    assert pre.date() != post.date(), "전제 확인 — UTC로 보면 이 둘은 다른 날이다"
    assert px_core.session_boundary_indices([_bar(pre, 52_000), _bar(post, 52_100)]) == []


# ------------------------------------------------------ 계량


def test_inflation_reproduces_the_measured_ratio() -> None:
    """실측 1.85배 — 야간 갭이 그날 장중 최대의 1.85배였다."""
    bars = _two_day_bars()
    result = px_core.session_boundary_inflation(bars, window=len(bars) - 1)
    assert result is not None
    assert result["boundary_pairs"] == 1
    assert result["with_boundary"] == pytest.approx(OVERNIGHT_GAP, rel=1e-3)
    assert result["same_session"] == pytest.approx(INTRADAY_MAX, rel=1e-3)
    assert result["ratio"] == pytest.approx(INFLATION_RATIO, abs=0.02)


def test_inflation_is_none_when_there_is_nothing_to_measure() -> None:
    """창이 안 차면 0이 아니라 **판정 불가**다 — 0은 "오염 없음"으로 읽힌다 (L18)."""
    day = datetime(2026, 8, 20, 9, 0, tzinfo=KST)
    bars = [_bar(day + timedelta(minutes=10 * i), 52_000) for i in range(3)]
    assert px_core.session_boundary_inflation(bars, window=60) is None


def test_same_session_returns_is_shorter_than_the_pair_count() -> None:
    """반환 길이가 `len(bars)-1`이 **아니다.** 호출측이 길이를 가정하면 안 된다 —

    그 가정이 정확히 이 버그를 만든 형태다(`zip(closes, closes[1:])`는 두 봉이 10분
    떨어졌는지 17시간 떨어졌는지 구분할 수단이 없다).
    """
    bars = _two_day_bars()
    assert len(px_core.same_session_returns(bars)) == len(bars) - 1 - 1


# ------------------------------------------------ 전환 후 계약


def test_px_max_ret_excludes_the_overnight_gap() -> None:
    """**전환의 본체.** 야간 갭이 더 이상 argmax를 차지하지 않는다.

    2026-08-20 실측에서 이 값은 0.032462(야간 갭)였고, 이제 0.017546(그날 장중 최대)이다.
    """
    bars = _two_day_bars()
    value = px_core.px_max_ret(bars, window=len(bars) - 2)
    assert value == pytest.approx(INTRADAY_MAX, rel=1e-3)
    assert value < OVERNIGHT_GAP


def test_sample_count_is_preserved_across_the_boundary() -> None:
    """**경계 쌍을 빼되 표본 수는 유지한다.**

    그냥 빼면 반환 길이가 창에 못 미쳐 호출측 가드에 걸리고, 10m의 `*_60`은 창이 세션보다
    길어 **매일** 그렇게 된다 — 오염을 NaN으로 바꿀 뿐이라 더 나쁘다.
    """
    bars = _two_day_bars()
    count = len(bars) - 2  # 경계 1곳이 있으므로 인접쌍 총수보다 하나 적게 요구
    pairs = px_core.same_session_pairs(bars, count)
    assert pairs is not None
    assert len(pairs) == count
    days = {b.bar_open_kst.astimezone(KST).date() for b in bars}
    assert len(days) == 2, "이 픽스처는 이틀을 걸친다 — 전제 확인"


def test_pairs_never_cross_a_boundary() -> None:
    """걷어 온 쌍 중 어느 하나도 거래일을 넘지 않는다."""
    bars = _two_day_bars()
    boundary = px_core.session_boundary_indices(bars)[0]
    crossing = (
        float(bars[boundary].c_ticks),
        float(bars[boundary + 1].c_ticks),
    )
    pairs = px_core.same_session_pairs(bars, len(bars) - 2)
    assert pairs is not None
    assert crossing not in pairs


def test_warmup_shortage_is_none_not_a_short_list() -> None:
    """모자라면 짧은 목록이 아니라 `None`이다 — 호출측이 길이를 가정하지 않아도 되게."""
    day = datetime(2026, 8, 20, 9, 0, tzinfo=KST)
    bars = [_bar(day + timedelta(minutes=10 * i), 52_000 + i) for i in range(5)]
    assert px_core.same_session_pairs(bars, 4) is not None
    assert px_core.same_session_pairs(bars, 5) is None


def test_span_features_are_deliberately_untouched() -> None:
    """`px_ret`·`px_mom`은 인접쌍이 아니라 **두 점 사이의 구간 수익률**이다.

    그 구간이 밤을 넘으면 야간 이동이 값에 들어가는데, 그것은 오염이 아니라 **그 피처가 재는
    것 자체**다. 야간 갭을 따로 보고 싶으면 `px_gap_open`이 전용으로 들고 있고, 같은 정보를
    두 경로로 넣으면 다중공선성이 생긴다. 여기 손대면 이 테스트가 그 판단을 상기시킨다.
    """
    bars = _two_day_bars()
    span = px_core.px_ret(bars, window=len(bars) - 1)
    assert span is not None
    assert span > 0.02, "이틀을 걸친 구간 수익률은 야간 갭을 포함해야 한다"


def test_log_returns_helper_is_gone() -> None:
    """종가만 받는 헬퍼를 다시 만들면 같은 결함이 조용히 돌아온다.

    `_log_returns(closes)`는 두 봉이 10분 떨어졌는지 17시간 떨어졌는지 알 방법이 없었고,
    그것이 정확히 야간 갭 오염의 형태였다.
    """
    assert not hasattr(px_core, "_log_returns")
