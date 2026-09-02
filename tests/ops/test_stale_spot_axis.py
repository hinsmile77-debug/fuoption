"""ATM 기준가 스테일 구간을 무결성 리포트가 센다 — 2026-09-02 F-73④.

K-5 판정(2026-08-31)이 확정한 공백을 메운다: 08-28 23분·11사이클·462다리, 08-31 22분·
10사이클·420다리가 전 거래일 종가로 정한 창(+3.12% 어긋남)에서 나갔는데 무결성 리포트의
`series_findings`·`horizon_findings`가 전부 비어 있었고 커버리지는 100%였다. **커버리지는
「쌓였는가」를 재고, 이 축은 「맞는 창에서 쌓였는가」를 잰다.**
"""

from __future__ import annotations

import json

from messiah.ops.integrity_report import analyze_logs


def _opened(series="regular", age=61200.0):
    return json.dumps(
        {
            "ts": "2026-09-02T08:22:00+09:00",
            "level": "WARNING",
            "tag": "OptionChainStaleSpot",
            "series": series,
            "spot_as_of": "2026-09-01T15:34:00+09:00",
            "spot_age_seconds": age,
            "threshold_seconds": 60.0,
        },
        ensure_ascii=False,
    )


def _resolved(series="regular", cycles=10, max_age=62400.0):
    return json.dumps(
        {
            "ts": "2026-09-02T08:45:00+09:00",
            "level": "INFO",
            "tag": "OptionChainStaleSpotResolved",
            "series": series,
            "first_spot_as_of": "2026-09-01T15:34:00+09:00",
            "resolved_at_kst": "2026-09-02T08:45:00+09:00",
            "cycles": cycles,
            "max_age_seconds": max_age,
        },
        ensure_ascii=False,
    )


def _axis(tmp_path, lines):
    log = tmp_path / "l1_daily_20260902.log"
    log.write_text("\n".join(lines), encoding="utf-8")
    return analyze_logs([log])["option_chain_stale_spot"]


def test_a_day_without_the_tag_is_none_not_zero(tmp_path):
    """F-73 이전 로그에는 태그가 아예 없다 — 0으로 적으면 과거 전부가 거짓 통과다(L18)."""
    assert _axis(tmp_path, [_line_polled()]) is None


def _line_polled():
    return json.dumps(
        {"ts": "2026-09-02T09:00:00+09:00", "level": "DEBUG", "tag": "OptionChainPolled"},
        ensure_ascii=False,
    )


def test_a_resolved_episode_reports_its_cycles(tmp_path):
    """08-31 실측 모양 — 장전 한 구간, 10사이클, 최악 17시간."""
    axis = _axis(tmp_path, [_opened(), _resolved()])

    assert axis["episodes"] == 1
    assert axis["resolved_episodes"] == 1
    assert axis["unresolved_episodes"] == 0
    assert axis["stale_spot_cycles"] == 10
    assert axis["max_age_seconds"] == 62400.0
    assert axis["cycles_by_series"] == {"regular": 10}


def test_series_are_counted_apart(tmp_path):
    """세 폴러가 서로 다른 격자로 돈다 — 한쪽이 스테일인 동안 다른 쪽은 신선할 수 있다."""
    axis = _axis(
        tmp_path,
        [
            _opened("regular"),
            _opened("weekly_mon"),
            _resolved("regular", cycles=10),
            _resolved("weekly_mon", cycles=5),
        ],
    )

    assert axis["cycles_by_series"] == {"regular": 10, "weekly_mon": 5}
    assert axis["stale_spot_cycles"] == 15


def test_an_episode_that_never_resolved_is_not_folded_into_zero(tmp_path):
    """해소 없이 세션이 끝나면 **사이클 수를 모른다** — 0으로 적으면 「없었다」가 된다.

    「22분 어긋났다」와 「언제 회복했는지 모른다」는 다른 사실이고, 후자가 더 나쁘다.
    """
    axis = _axis(tmp_path, [_opened()])

    assert axis["episodes"] == 1
    assert axis["unresolved_episodes"] == 1
    assert axis["stale_spot_cycles"] == 0
    assert axis["episodes_detail"][0]["resolved"] is False
    # 사이클을 몰라도 **얼마나 오래된 값이었는지**는 시작 로그가 알고 있다.
    assert axis["max_age_seconds"] == 61200.0
