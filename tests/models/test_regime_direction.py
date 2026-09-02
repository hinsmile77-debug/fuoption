"""국면별 방향 채점 — 2026-09-02 신설.

이 채점이 답하는 것은 "고변동 국면에서 모델의 방향이 맞기는 하는가"이고, 그 답이 게이트를
만지는 근거가 되므로 **채점 자체가 틀리면 안 되는** 자리다. 그래서 여기서 검사하는 것은
숫자의 예쁨이 아니라 세 가지다: 방향 부호가 뒤집히지 않는가, 못 잰 것을 0으로 채우지
않는가, 폴백(국면 조인)이 조용하지 않은가.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from messiah.core.messages import BarClosed, Horizon, Regime
from messiah.models import regime_direction as rd

KST = timezone(timedelta(hours=9))
_SYMBOL = "A05609"


def _bars(closes: list[int], *, start_hour: int = 9, high_pad: int = 5, low_pad: int = 5):
    """30m 봉 시퀀스 — 종가만 주면 고저는 그 언저리로 지어 준다(배리어를 안 건드리게)."""
    out = []
    for i, close in enumerate(closes):
        open_kst = datetime(2026, 9, 2, start_hour, 0, tzinfo=KST) + timedelta(minutes=30 * i)
        out.append(
            BarClosed(
                symbol=_SYMBOL,
                horizon=Horizon.M30,
                bar_open_kst=open_kst,
                o_ticks=close,
                h_ticks=close + high_pad,
                l_ticks=close - low_pad,
                c_ticks=close,
                volume=1000,
            )
        )
    return out


def _record(ts: datetime, score: float, regime: Regime = Regime.TREND_UP) -> rd.DecisionRecord:
    return rd.DecisionRecord(
        ts_kst=ts, symbol=_SYMBOL, regime=regime, score=score, gate="pass", score_threshold=0.2
    )


def test_short_on_a_falling_market_is_a_hit() -> None:
    """**부호 규약** — S<0이면 SHORT이고, 값이 내려가면 적중이다.

    이 한 줄이 뒤집히면 "고변동에서 계통적으로 틀린다"는 판정이 통째로 반대가 된다.
    """
    closes = [1000] * 16 + [1000, 990, 985, 980]
    bars = _bars(closes)
    entry_index = 16
    record = _record(bars[entry_index].bar_open_kst + timedelta(minutes=30), score=-0.3)

    outcome = rd.resolve_outcome(record, bars, entry_index)

    assert outcome is not None
    assert outcome.hit is True
    assert outcome.gross_ticks > 0
    assert outcome.net_ticks < outcome.gross_ticks  # 비용은 반드시 차감된다


def test_long_on_the_same_falling_market_is_a_miss() -> None:
    closes = [1000] * 16 + [1000, 990, 985, 980]
    bars = _bars(closes)
    outcome = rd.resolve_outcome(
        _record(bars[16].bar_open_kst + timedelta(minutes=30), score=+0.3), bars, 16
    )

    assert outcome is not None
    assert outcome.hit is False
    assert outcome.gross_ticks < 0


def test_the_last_cycle_of_the_day_is_not_scored() -> None:
    """앞을 볼 봉이 없으면 **채점하지 않는다** — 0으로 채우면 적중률이 희석된다.

    오버나이트를 넘기지 않는 것은 Holding Policy §2.2 A가 실제로 거절하기 때문이다
    (2026-09-01 15:30 `risk_reject` 실측).
    """
    bars = _bars([1000] * 20)
    last = len(bars) - 1

    assert rd.resolve_outcome(_record(bars[last].bar_open_kst, score=0.3), bars, last) is None


def test_overnight_is_not_carried() -> None:
    """다음 날 봉이 있어도 당일 마지막 봉 종가로 끊고 `eod`로 표시한다."""
    bars = _bars([1000] * 18)
    tomorrow = bars[-1].bar_open_kst + timedelta(days=1)
    bars.append(
        BarClosed(
            symbol=_SYMBOL,
            horizon=Horizon.M30,
            bar_open_kst=tomorrow,
            o_ticks=2000,
            h_ticks=2000,
            l_ticks=2000,
            c_ticks=2000,
            volume=10,
        )
    )
    outcome = rd.resolve_outcome(_record(bars[-2].bar_open_kst, score=0.3), bars, len(bars) - 2)

    assert outcome is None  # 진입봉이 그날 마지막이면 앞이 없다 → 미채점

    outcome = rd.resolve_outcome(_record(bars[-3].bar_open_kst, score=0.3), bars, len(bars) - 3)
    assert outcome is not None
    assert outcome.barrier == "eod"
    assert outcome.exit_ticks == 1000  # 다음 날 2000을 쓰지 않았다


def test_missing_regime_is_joined_and_counted() -> None:
    """옛 로그 폴백은 **조용하지 않다** — self/joined가 나뉘어 세어진다."""
    payloads = [
        {
            "tag": "RegimeClassified",
            "ts": "2026-09-01T09:00:00.871556+09:00",
            "symbol": _SYMBOL,
            "regime": "HIGH_VOL",
        },
        {  # 옛 판단 줄 — 국면 없음
            "tag": "DecisionEmitted",
            "ts": "2026-09-01T09:00:01.394474+09:00",
            "symbol": _SYMBOL,
            "score": -0.049,
            "gate": "score",
        },
        {  # 새 판단 줄 — 스스로 말한다
            "tag": "DecisionEmitted",
            "ts": "2026-09-02T09:00:01.000000+09:00",
            "symbol": _SYMBOL,
            "score": 0.31,
            "gate": "pass",
            "regime": "TREND_UP",
            "score_threshold": 0.2,
        },
    ]
    lookup = rd.build_regime_lookup(payloads)
    records, counts = rd.parse_decisions(payloads, regime_lookup=lookup)

    assert [r.regime for r in records] == [Regime.HIGH_VOL, Regime.TREND_UP]
    assert [r.regime_source for r in records] == ["joined", "self"]
    assert counts == {"self": 1, "joined": 1, "no_regime": 0, "no_score": 0}


def test_a_decision_without_a_score_is_counted_not_dropped() -> None:
    """게이트 ①′·② 이전에 접힌 줄은 점수가 없다 — 버리되 **센다**."""
    records, counts = rd.parse_decisions(
        [{"tag": "DecisionEmitted", "ts": "2026-09-02T09:00:00+09:00", "symbol": _SYMBOL}]
    )

    assert records == []
    assert counts["no_score"] == 1


def test_seconds_do_not_have_to_match_for_the_join() -> None:
    """국면 판정(09:00:00.87)과 판단(09:00:01.15)은 **초가 다르다** — 분으로 자른다."""
    lookup = rd.build_regime_lookup(
        [
            {
                "tag": "MetaGateEvaluated",
                "ts": "2026-09-01T13:30:00.867588+09:00",
                "symbol": _SYMBOL,
                "regime": "RANGE",
            }
        ]
    )
    records, _ = rd.parse_decisions(
        [
            {
                "tag": "DecisionEmitted",
                "ts": "2026-09-01T13:30:01.568087+09:00",
                "symbol": _SYMBOL,
                "score": 0.02,
                "gate": "score",
            }
        ],
        regime_lookup=lookup,
    )

    assert [r.regime for r in records] == [Regime.RANGE]


def test_a_regime_that_always_misses_is_flagged_but_only_with_enough_samples() -> None:
    """2026-09-02 고변동의 형태 — 적중률이 낮고 표본이 차면 깃발이 선다."""
    few = _card(n=10, n_hit=2)
    many = _card(n=40, n_hit=8)

    assert few.is_flagged is False  # MIN_SAMPLES 미만이면 성적으로 읽지 않는다
    assert "표본 부족" in few.verdict
    assert many.is_flagged is True
    assert "동전보다 나쁘다" in many.verdict


def test_being_too_right_is_also_flagged() -> None:
    """반사실 채점이 미래를 보고 있으면 적중률이 비정상적으로 높게 나온다 — 그것도 잡는다."""
    card = _card(n=40, n_hit=34)

    assert card.is_flagged is True
    assert "동전보다 좋다" in card.verdict


def _card(*, n: int, n_hit: int) -> rd.RegimeDirectionCard:
    return rd.RegimeDirectionCard(
        regime=Regime.HIGH_VOL,
        n=n,
        n_hit=n_hit,
        n_gate_passed=0,
        n_regime_joined=n,
        gross_ticks=0.0,
        net_ticks=-100.0,
        median_net_ticks=-10.0,
        trading_days=8,
        net_by_day={"2026-09-01": -100.0},
    )


def test_scorecard_file_carries_both_axes(tmp_path: Path) -> None:
    """화면이 **한 화면에서** 읽어야 하므로 방향과 천장이 한 파일에 있어야 한다."""
    path = rd.write_scorecard(
        [_card(n=40, n_hit=8)],
        {"no_bars": 2},
        symbol=_SYMBOL,
        day=date(2026, 9, 2),
        log_dir=tmp_path,
        reachability={"HIGH_VOL": {"closed": True, "ceiling_solo": 0.188}},
        sources={"self": 0, "joined": 40},
    )
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["direction"]["regimes"]["HIGH_VOL"]["flagged"] is True
    assert payload["direction"]["skipped"] == {"no_bars": 2}
    assert payload["direction"]["regime_sources"] == {"joined": 40, "self": 0}
    assert payload["reachability"]["HIGH_VOL"]["closed"] is True
