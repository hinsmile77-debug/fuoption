"""S-2 — 국면 연동 섀도 임계가 **실제로 무엇을 거르는가** (2026-08-31).

섀도 임계는 국면 연동인데(`RANGE +0.05` · `HIGH_VOL +0.10`) 그 축의 종일 집계가 어디에도
없었다. 2026-08-31 장중은 「섀도가 6/8=75%에서 false」를 **로그를 손으로 세어** 냈고,
장후는 15:30 회차 한 줄밖에 확인하지 못했다. 승격(R18) 판단은 20거래일 분포로 내리는데,
그 분포를 매일 손으로 세면 20일을 못 간다.

⚠ 이 축은 **어떤 차단에도 쓰이지 않는다.** 계측 선행이고, 실판정(`passed`)과 이름으로
갈라 둔다 — 섞으면 R18의 20거래일 관측이 무의미해진다.
"""

from __future__ import annotations

import json


def _line(prob, *, passed, shadow, regime="RANGE"):
    return json.dumps(
        {
            "ts": "2026-08-31T09:00:00+09:00",
            "level": "INFO",
            "tag": "MetaGateEvaluated",
            "probability": prob,
            "threshold": 0.0,
            "passed": passed,
            "passed_shadow": shadow,
            "regime": regime,
        },
        ensure_ascii=False,
    )


def _analyze(tmp_path, lines):
    from messiah.ops.integrity_report import analyze_logs

    log = tmp_path / "g2_daily_20260831.log"
    log.write_text("\n".join(lines), encoding="utf-8")
    return analyze_logs([log])["meta_gate"]


def test_shadow_passes_and_blocks_are_counted_for_the_whole_day(tmp_path):
    """장중이 손으로 센 6/8 를 계기가 대신 센다."""
    lines = [_line(0.5, passed=True, shadow=True) for _ in range(2)]
    lines += [_line(0.02, passed=True, shadow=False) for _ in range(6)]

    result = _analyze(tmp_path, lines)

    assert result["evaluations"] == 8
    assert result["shadow_measured"] == 8
    assert result["shadow_passes"] == 2
    assert result["shadow_blocks"] == 6


def test_shadow_blocks_are_bucketed_by_regime(tmp_path):
    """「무엇을 거르나」의 답은 국면별 내역에 있다 — 총계만으로는 임계를 못 만진다."""
    lines = [
        _line(0.02, passed=True, shadow=False, regime="HIGH_VOL"),
        _line(0.02, passed=True, shadow=False, regime="HIGH_VOL"),
        _line(0.02, passed=True, shadow=False, regime="RANGE"),
        _line(0.9, passed=True, shadow=True, regime="TREND_UP"),
    ]

    result = _analyze(tmp_path, lines)

    assert result["blocked_by_regime"] == {"HIGH_VOL": 2, "RANGE": 1}
    assert "TREND_UP" not in result["blocked_by_regime"], "통과한 회차는 차단 내역이 아니다"


def test_a_regimeless_block_is_named_not_dropped(tmp_path):
    """국면이 안 실린 회차를 버리면 합이 안 맞는다 — 이름을 주고 남긴다."""
    line = json.dumps(
        {
            "ts": "2026-08-31T09:00:00+09:00",
            "level": "INFO",
            "tag": "MetaGateEvaluated",
            "probability": 0.02,
            "threshold": 0.0,
            "passed": True,
            "passed_shadow": False,
        }
    )

    result = _analyze(tmp_path, [line])

    assert result["blocked_by_regime"] == {"미상": 1}
    assert result["shadow_blocks"] == 1


def test_logs_from_before_the_shadow_axis_are_unmeasured_not_zero(tmp_path):
    """`passed_shadow` 는 2026-08-25 F-41 이후의 필드다 — 그 이전은 **미측정**이다(L18)."""
    line = json.dumps(
        {
            "ts": "2026-08-20T09:00:00+09:00",
            "level": "INFO",
            "tag": "MetaGateEvaluated",
            "probability": 0.3,
            "threshold": 0.7,
            "passed": False,
        }
    )

    result = _analyze(tmp_path, [line])

    assert result["evaluations"] == 1, "확률 분포는 그대로 나온다"
    assert result["shadow_measured"] is None
    assert result["shadow_passes"] is None
    assert result["shadow_blocks"] is None
    assert result["blocked_by_regime"] is None


def test_the_shadow_axis_does_not_touch_the_real_verdict(tmp_path):
    """**판정 불변** — 실판정 축의 값이 섀도 유무와 무관하게 같아야 한다."""
    probs = [0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.65, 0.68, 0.72, 0.75]
    bare = [
        json.dumps(
            {
                "ts": "2026-08-31T09:00:00+09:00",
                "level": "INFO",
                "tag": "MetaGateEvaluated",
                "probability": p,
                "threshold": 0.7,
                "passed": p >= 0.7,
            }
        )
        for p in probs
    ]
    withshadow = [
        json.dumps(
            {
                "ts": "2026-08-31T09:00:00+09:00",
                "level": "INFO",
                "tag": "MetaGateEvaluated",
                "probability": p,
                "threshold": 0.7,
                "passed": p >= 0.7,
                "passed_shadow": p >= 0.75,
                "regime": "RANGE",
            }
        )
        for p in probs
    ]

    a = _analyze(tmp_path / "a", bare) if False else None
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    a = _analyze(tmp_path / "a", bare)
    b = _analyze(tmp_path / "b", withshadow)

    for key in ("evaluations", "passes", "threshold", "p50", "p90", "max", "frozen_run"):
        assert a[key] == b[key], key
    # 그리고 섀도는 실판정과 **다른 답**을 낸다 — 같으면 축을 나눌 이유가 없다.
    assert b["passes"] == 2 and b["shadow_passes"] == 1
