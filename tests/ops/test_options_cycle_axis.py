"""무결성 리포트의 관측 축 두 건 (2026-09-17 G-9 · G-10).

- **G-9 `options_cycles`** — 5분 보조 판단이 하루에 몇 번 돌았고 어디가 비었나.
  서비스 쪽 `OptionsSubLoopStalled`는 「끊겼다 **이어짐**」만 잡는다(다음 사이클이 와야
  간격이 재진다). 이 축이 나머지 절반 — **끊긴 채 끝남** — 을 맡는다.
- **G-10 `session_boundary_inflation`** — 야간 갭이 봉 한 칸으로 들어간 정도. 태그는
  2026-08-20부터 있었는데 리포트에는 건수 하나뿐이라 `ratio`가 어디에도 안 실렸다.
"""

from __future__ import annotations

import json

from messiah.ops.integrity_report import analyze_logs


def _line(tag: str, at: str, **fields) -> str:
    return json.dumps(
        {"ts": f"2026-09-17T{at}+09:00", "level": "INFO", "tag": tag, **fields},
        ensure_ascii=False,
    )


def _analyze(tmp_path, lines, key):
    log = tmp_path / "g2_daily_20260917.log"
    log.write_text("\n".join(lines), encoding="utf-8")
    return analyze_logs([log])[key]


# ---------------------------------------------------------------- G-9


def test_the_real_2026_09_17_afternoon_is_reconstructed(tmp_path):
    """그날 실측 그대로 — 14:30·14:40·14:50에 무결정이 찍히고 14:35는 **후보가 나와서**
    `OptionsViewPublished`가 찍혔다면 공백은 없다. 결정 사이클을 세지 않으면 이 검사가
    14:30→14:40을 600초 공백으로 잘못 읽는다(일곱 번 보고된 그 오진)."""
    axis = _analyze(
        tmp_path,
        [
            _line("OptionsNoCandidate", "14:30:01", reason="IV Surface 미준비"),
            _line("OptionsViewPublished", "14:35:01", n_candidates=2),
            _line("OptionsNoCandidate", "14:40:03", reason="IV Surface 미준비"),
            _line("OptionsViewPublished", "14:45:02", n_candidates=1),
            _line("OptionsNoCandidate", "14:50:00", reason="IV Surface 미준비"),
        ],
        "options_cycles",
    )

    assert axis["n_cycles"] == 5
    assert axis["n_published"] == 2
    assert axis["n_no_candidate"] == 3
    assert axis["gaps"] is None, "결정 사이클을 세면 이 오후에 공백은 없다"
    assert axis["max_gap_seconds"] == 0


def test_a_cycle_that_never_came_back_is_caught_here_and_only_here(tmp_path):
    """서비스 쪽 태그는 **다음 사이클이 와야** 간격을 잰다 — 루프가 끊긴 채 하루가 끝나면
    그 태그는 안 뜬다. 여기서는 전수로 다시 재므로 끊긴 구간이 그대로 남는다."""
    axis = _analyze(
        tmp_path,
        [
            _line("OptionsNoCandidate", "14:25:00", reason="매트릭스 셀 후보 없음(관망)"),
            _line("OptionsNoCandidate", "15:25:00", reason="후보 생성 실패"),
        ],
        "options_cycles",
    )

    assert axis["gaps"] == [{"from_kst": "14:25:00", "to_kst": "15:25:00", "seconds": 3600}]
    assert axis["max_gap_seconds"] == 3600
    assert (
        axis["stalls_detected_live"] == 0
    ), "실시간 태그는 0인데 사후 계측은 잡았다 — 그 차이가 신호다"


def test_live_stall_tags_are_carried_alongside_the_recomputed_gaps(tmp_path):
    """둘이 **겹치는 것이 의도다** — 실시간 수와 사후 수가 다르면 그 자체가 신호다."""
    axis = _analyze(
        tmp_path,
        [
            _line("OptionsNoCandidate", "14:30:01", reason="IV Surface 미준비"),
            _line("OptionsNoCandidate", "14:40:03", reason="IV Surface 미준비"),
            _line("OptionsSubLoopStalled", "14:40:03", gap_seconds=602.0, cadence_seconds=300.0),
        ],
        "options_cycles",
    )

    assert axis["stalls_detected_live"] == 1
    assert axis["stalls_live"][0]["gap_seconds"] == 602.0
    assert axis["gaps"][0]["seconds"] == 602


def test_normal_jitter_is_not_a_gap(tmp_path):
    """09-17 실측 간격은 597·600·602초였다 — 임계가 주기의 2.0배면 한 마크 결손이 정확히
    경계에 걸려 그날그날 다르게 판정된다. 1.5배(450초)는 정상 지터를 안 잡는다."""
    axis = _analyze(
        tmp_path,
        [
            _line("OptionsNoCandidate", "09:00:00", reason="x"),
            _line("OptionsNoCandidate", "09:05:04", reason="x"),
            _line("OptionsNoCandidate", "09:09:57", reason="x"),
        ],
        "options_cycles",
    )

    assert axis["gaps"] is None
    assert axis["threshold_seconds"] == 450.0


def test_no_tag_at_all_is_unmeasured_not_zero(tmp_path):
    """0 사이클과 「이 계측 이전 로그」를 섞지 않는다(L18) — 옵션 서비스가 아예 안 붙은
    프로세스의 로그에서도 이 축이 `0건 정상`으로 읽히면 안 된다."""
    axis = _analyze(tmp_path, [_line("SessionStart", "08:25:00")], "options_cycles")

    assert axis is None


# ---------------------------------------------------------------- G-10


def test_the_inflation_ratio_finally_reaches_the_report(tmp_path):
    """2026-08-20부터 태그는 있었는데 리포트에는 `tag_counts`의 숫자 하나뿐이었다 —
    판정에 쓰이는 값(`ratio`)이 안 실리면 이 태그는 사실상 미측정이다."""
    axis = _analyze(
        tmp_path,
        [
            _line(
                "SessionBoundaryInflation",
                "15:46:00",
                horizon="30m",
                ratio=1.0,
                boundary_pairs=1,
                window_bars=60,
                with_boundary=0.004,
                same_session=0.004,
                constant_features=["px_ret_60"],
            )
        ],
        "session_boundary_inflation",
    )

    assert axis["30m"]["ratio"] == 1.0
    assert axis["30m"]["boundary_pairs"] == 1
    assert axis["30m"]["window_bars"] == 60
    assert axis["30m"]["at_kst"] == "15:46:00"


def test_the_worst_of_the_day_wins_not_the_last(tmp_path):
    """같은 Horizon에서 여러 번 뜨면 평균도 마지막도 아닌 **최대**가 판정 기준이다
    (`clock_skew_seconds`가 절댓값 최대를 쓰는 것과 같은 규율)."""
    axis = _analyze(
        tmp_path,
        [
            _line("SessionBoundaryInflation", "15:46:00", horizon="30m", ratio=3.2),
            _line("SessionBoundaryInflation", "15:47:00", horizon="30m", ratio=1.1),
        ],
        "session_boundary_inflation",
    )

    assert axis["30m"]["ratio"] == 3.2


def test_each_horizon_keeps_its_own_row(tmp_path):
    """Horizon마다 창 길이가 달라 경계가 창 안에 갇히는 조건이 다르다 — 하나로 접으면
    "어느 Horizon이 문제인가"가 사라진다."""
    axis = _analyze(
        tmp_path,
        [
            _line("SessionBoundaryInflation", "15:46:00", horizon="30m", ratio=3.2),
            _line("SessionBoundaryInflation", "15:46:00", horizon="15m", ratio=1.4),
        ],
        "session_boundary_inflation",
    )

    assert sorted(axis) == ["15m", "30m"]


def test_an_absent_tag_is_unmeasured_not_healthy(tmp_path):
    """이 태그는 **퇴화 피처가 있을 때만** 뜬다 — 0건을 "정상"으로 읽으면 안 된다."""
    axis = _analyze(tmp_path, [_line("SessionStart", "08:25:00")], "session_boundary_inflation")

    assert axis is None
