"""장중 재기동이 249분짜리 기동 지연으로 적힌 날 (2026-08-19 장후 F-2·F-6).

2026-08-19 12:29에 사람이 죽은 프로세스를 되살렸다. 그 프로세스는 이전 세션이 무엇을 봤는지
모르는 채로 「정시 트리거(08:20)로부터 249분」을 계산했고, 화면은 오후 내내 이렇게 말했다:

    "오늘 영구 소실 — 기동 지연 249분 · option_chain/regular 1건"

그날 08:20~09:50은 **정상 수집됐다.** 실제 손실은 09:50~12:29의 158.9분이었다. 같은 하루에
대해 세 개의 숫자가 남았고(249.4 · 0.5 · 158.9~180.2) 어느 것도 159가 아니었다.
"""

from __future__ import annotations

import json
from datetime import date, datetime

from messiah.core.timeutil import KST
from messiah.ops import loss_ledger, observation_gaps, session_guard

_DAY = date(2026, 8, 19)


def _log(path, *stamps: str, refused: tuple[str, ...] = (), day: str = "2026-08-19") -> None:
    records = [{"ts": f"{day}T{s}+09:00", "tag": "SessionStart"} for s in stamps]
    records += [{"ts": f"{day}T{s}+09:00", "tag": "LaunchWindowRefused"} for s in refused]
    records.sort(key=lambda r: r["ts"])
    path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")


def _at(clock: str) -> datetime:
    hour, minute, second = (int(p) for p in clock.split(":"))
    return datetime(2026, 8, 19, hour, minute, second, tzinfo=KST)


# ---------------------------------------------------------------- 재기동 판정


def test_the_second_launch_of_the_day_knows_it_is_one(tmp_path):
    """**이 파일의 핵심.** 재기동한 프로세스는 이전 세션을 모르지만, 「내가 첫 기동이
    아니다」는 로그 파일 하나로 알 수 있다."""
    path = tmp_path / "l1_daily_20260819.log"
    _log(path, "08:20:29", "12:29:23")

    assert session_guard.prior_sessions_today(path, now=_at("12:29:25")) == 1


def test_the_first_launch_does_not_count_itself(tmp_path):
    """`mlog.setup()`이 이미 이번 세션의 `SessionStart`를 같은 파일에 찍었다."""
    path = tmp_path / "l1_daily_20260819.log"
    _log(path, "08:20:29")

    assert session_guard.prior_sessions_today(path, now=_at("08:20:31")) == 0


def test_a_missing_log_is_not_a_restart(tmp_path):
    """판정 불가를 이유로 거짓 재기동을 만들지 않는다."""
    assert session_guard.prior_sessions_today(tmp_path / "없는파일.log", now=_at("08:20:31")) == 0


def test_a_refused_boot_trigger_is_not_a_prior_session(tmp_path):
    """**초판이 빠뜨려 다음 날 아침 바로 드러난 자리** (2026-08-20 장전 1-3).

    PC 부팅 트리거는 매일 06:42에 `SessionStart` 한 줄을 남기고 곧바로 기동 창 가드에
    거절된다. 그 줄을 세면 08:20 정시 기동이 **매일** 자기를 장중 재기동이라 판정하고,
    지키려던 `start_lag_minutes`가 영원히 None으로 침묵한다 — 고치려던 것보다 나쁘다.

    2026-08-20 실측: 06:42:31.327 SessionStart → 06:42:31.334 LaunchWindowRefused →
    08:20:16.902 SessionStart(정상). 그날 09:03 스냅샷이 개장 전인데
    `restarted_mid_day: true` · `start_lag_minutes: null` 이었다.
    """
    path = tmp_path / "l1_daily_20260820.log"
    _log(path, "06:42:31", "08:20:16", refused=("06:42:31",), day="2026-08-20")

    now = datetime(2026, 8, 20, 8, 20, 18, tzinfo=KST)
    assert session_guard.prior_sessions_today(path, now=now) == 0


def test_a_real_restart_still_counts_when_a_refusal_also_happened(tmp_path):
    """**양방향으로 본다.** 거절을 빼는 변경이 진짜 재기동까지 지우면 계기를 죽인 것이다.

    2026-08-19 실측: 05:55:35 거절 → 08:20:29 정시 → 12:29:23 재기동(진짜).
    """
    path = tmp_path / "l1_daily_20260819.log"
    _log(path, "05:55:35", "08:20:29", "12:29:23", refused=("05:55:35",))

    assert session_guard.prior_sessions_today(path, now=_at("12:29:25")) == 1
    assert session_guard.prior_sessions_today(path, now=_at("08:20:31")) == 0


def test_the_refusal_pairing_lives_in_one_place():
    """같은 사실("기동이 몇 번이었나")을 두 곳이 따로 구현하면 어긋난다 — 2026-08-20이 그 증거다."""
    from messiah.ops import integrity_report

    pairs = (["07:23:31", "08:35:34"], ["07:23:31"])
    assert integrity_report._drop_refused_starts(*pairs) == ["08:35:34"]
    assert session_guard.drop_refused_starts(*pairs) == ["08:35:34"]


# ---------------------------------------------------------------- 장부


def test_a_restart_does_not_invent_a_249_minute_start_lag():
    """이 프로세스는 08:20~09:50에 무엇이 수집됐는지 모른다 — 모른다고 말한다."""
    loss_ledger.reset()
    loss_ledger.record_start_lag(249.4, restarted_mid_day=True)

    ledger = loss_ledger.current().to_dict()

    assert ledger["start_lag_minutes"] is None  # 「기동 지연」이 아니다
    assert ledger["minutes_since_trigger"] == 249.4  # 잰 값 자체는 안 버린다
    assert ledger["restarted_mid_day"] is True
    assert "기동 지연 249분" not in ledger["summary"]
    assert "장중 재기동" in ledger["summary"]
    loss_ledger.reset()


def test_a_normal_late_launch_still_says_so():
    """08-10의 38분처럼 **진짜** 기동 지연은 종전 그대로 말한다 — 회귀 방지."""
    loss_ledger.reset()
    loss_ledger.record_start_lag(38.0)

    ledger = loss_ledger.current().to_dict()

    assert ledger["start_lag_minutes"] == 38.0
    assert ledger["restarted_mid_day"] is False
    assert "기동 지연 38분" in ledger["summary"]
    loss_ledger.reset()


# ---------------------------------------------------------------- F-6


def _gap(cause: str) -> observation_gaps.ObservationGap:
    return observation_gaps.ObservationGap(
        process="l1_daily",
        from_kst="09:50:29",
        to_kst="12:29:23",
        minutes=158.9,
        exact=False,
        cause=cause,
    )


def _causes_file(tmp_path, *, from_kst: str = "09:50:29"):
    path = tmp_path / "incident_causes.yaml"
    path.write_text(
        "incidents:\n"
        "  - date: 2026-08-19\n"
        "    process: l1_daily\n"
        f'    from_kst: "{from_kst}"\n'
        "    cause: Windows Update 재시작\n"
        "    evidence: logs/dailycheck/2026-08-19_incident_0950_deepdive.md\n",
        encoding="utf-8",
    )
    return path


def test_a_human_confirmed_cause_reaches_the_artifact(tmp_path):
    """사람은 12:14에 원인을 확정했는데 15:45 리포트는 「원인 불명」으로 봉인했다."""
    [gap] = observation_gaps.apply_known_causes(
        _DAY, [_gap("원인 불명 — 호스트 종료 이벤트 없음")], path=_causes_file(tmp_path)
    )

    assert gap.cause == "Windows Update 재시작"
    assert gap.cause_source == "human"
    assert gap.evidence.endswith("2026-08-19_incident_0950_deepdive.md")
    assert "사람 확정" in gap.describe()


def test_the_machine_wins_when_it_actually_saw_something(tmp_path):
    """호스트 이벤트로 특정된 원인은 안 덮는다 — 기계가 본 증거가 더 강하고, 이 통로가
    자동 판정을 가리기 시작하면 자동이 틀렸을 때 아무도 모른다."""
    [gap] = observation_gaps.apply_known_causes(
        _DAY, [_gap("호스트 OS 종료 (기타(계획되지 않음))")], path=_causes_file(tmp_path)
    )

    assert gap.cause.startswith("호스트 OS 종료")
    assert gap.cause_source == "auto"


def test_a_gap_nobody_explained_says_so(tmp_path):
    """적어 둔 것이 없으면 「미해결」이라고 말한다 — 침묵과 구분된다."""
    [gap] = observation_gaps.apply_known_causes(
        _DAY,
        [_gap("원인 불명 — 호스트 종료 이벤트 없음")],
        path=tmp_path / "없는파일.yaml",
    )

    assert gap.cause_source == "unresolved"


def test_a_few_minutes_of_drift_still_matches(tmp_path):
    """로그가 갱신되면 추정 시각이 몇 분 움직인다 — 그때마다 짝이 끊기면 아무도 파일을
    안 고친다."""
    [gap] = observation_gaps.apply_known_causes(
        _DAY,
        [_gap("원인 불명 — 호스트 종료 이벤트 없음")],
        path=_causes_file(tmp_path, from_kst="09:48:00"),
    )

    assert gap.cause_source == "human"


def test_a_far_away_entry_does_not_match(tmp_path):
    """허용 오차 밖은 다른 사건이다 — 엉뚱한 원인이 붙으면 없느니만 못하다."""
    [gap] = observation_gaps.apply_known_causes(
        _DAY,
        [_gap("원인 불명 — 호스트 종료 이벤트 없음")],
        path=_causes_file(tmp_path, from_kst="13:30:00"),
    )

    assert gap.cause_source == "unresolved"
