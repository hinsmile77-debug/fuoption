"""계측 축과 결과 축, 그리고 계기가 자기를 채점하는 것 (2026-08-19 장후 F-4·G-4).

2026-08-19 장후에 등록부가 ERROR 4건을 냈다. **네 항목 모두 「계측이 성립하는가」를 묻는
항목이고, 넷 다 그날 정확히 계측에 성공했다.**

    truncation-is-visible       그날 잘림을 정확히 보였다 (11건 · 5계열 · 최솟값 61.2%)
    leg-completeness-measured   12:30 사이클 41/42다리를 정확히 세었다
    ui-restart-observability    180.2분 공백을 정확히 쟀다
    launch-window-refusal-...   위와 **같은 지표**를 공유해 같은 사고를 두 번 셌다

실패한 것은 계측이 아니라 계측 대상인 하루였다. 그리고 같은 날 `no-silent-process-death`는
자기가 못 보는 사고가 일어난 날에 「7거래일 연속 기준 충족」을 선고했다.
"""

from __future__ import annotations

from datetime import date

import pytest

from messiah.ops import fix_verification as fv

_TODAY = date(2026, 8, 19)


def _reports(**by_day: dict) -> dict[date, dict]:
    return {date.fromisoformat(stamp): payload for stamp, payload in by_day.items()}


def _coverage_day(pct: float) -> dict:
    return {"series_coverage": [{"name": "ticks", "coverage_pct": pct, "measured": True}]}


def _item(**kwargs) -> fv.PendingVerification:
    base = dict(
        id="truncation-is-visible",
        summary="**잘림**이 보이는가",
        registered=date(2026, 8, 10),
        metric="series_coverage_pct_min",
        consecutive_days=3,
        min_value=95.0,
    )
    base.update(kwargs)
    return fv.PendingVerification(**base)


# ---------------------------------------------------------------- F-4


def test_a_working_instrument_reporting_a_bad_value_is_not_a_regression():
    """**이 파일의 핵심.** 계기가 설계대로 돌아 나쁜 값을 보고했다 — 그건 이 수정의
    실패가 아니다. 고칠 것이 없는 곳으로 사람을 보내는 신호를 없앤다."""
    reports = _reports(
        **{
            "2026-08-14": _coverage_day(99.4),
            "2026-08-18": _coverage_day(99.1),
            "2026-08-19": _coverage_day(61.2),
        }
    )

    [verdict] = fv.evaluate([_item(axis="instrument")], reports, today=_TODAY)

    assert verdict.status == fv.VerificationStatus.MEASURED_BAD
    assert verdict.last_value == 61.2
    # 사람을 부르는 자리(ERROR)에는 **안 올린다** — 진짜 재발이 그 더미에 묻힌다.
    assert verdict.needs_attention is False


def test_the_same_day_is_still_a_regression_on_the_outcome_axis():
    """축을 안 붙인 항목은 종전 그대로다 — 기존 판정을 소급해 바꾸지 않는다(R18)."""
    reports = _reports(**{"2026-08-18": _coverage_day(99.1), "2026-08-19": _coverage_day(61.2)})

    [verdict] = fv.evaluate([_item()], reports, today=_TODAY)

    assert verdict.status == fv.VerificationStatus.RECURRED
    assert verdict.needs_attention is True


def test_an_instrument_that_stops_producing_a_value_is_still_caught():
    """계측 축이라고 아무거나 통과시키지 않는다 — 값이 안 나오면 그건 진짜 고장이고,
    ②번 분기(`판정 불가 정체`)가 그대로 잡는다."""
    reports = _reports(**{"2026-08-14": _coverage_day(99.4), "2026-08-18": {}, "2026-08-19": {}})

    [verdict] = fv.evaluate([_item(axis="instrument", consecutive_days=2)], reports, today=_TODAY)

    assert verdict.status == fv.VerificationStatus.STALLED


def test_the_scoreboard_gives_it_its_own_bucket():
    """「오늘 위반 4」가 사고 규모가 아니라 계기 개수를 뜻하고 있었다."""
    reports = _reports(**{"2026-08-18": _coverage_day(99.1), "2026-08-19": _coverage_day(61.2)})
    verdicts = fv.evaluate([_item(axis="instrument")], reports, today=_TODAY)

    board = fv.scoreboard(verdicts, today=_TODAY)

    assert board["counts"]["today_violating"] == 0
    assert board["counts"]["measured_bad"] == 1
    assert board["measured_bad"][0]["axis"] == "instrument"
    assert board["measured_bad"][0]["measured_bad"] == ["2026-08-19"]


def test_two_items_may_not_share_one_metric(tmp_path):
    """공백이 한 번 생기면 등록부가 항상 **재발 2건**으로 셌다 — 08-19 재발 4건 중
    2건이 그 중복이었다."""
    path = tmp_path / "registry.yaml"
    path.write_text(
        """
verifications:
  - id: first
    summary: ""
    registered: 2026-08-06
    metric: observation_gap_minutes_max
    max: 5
    consecutive_days: 3
  - id: second
    summary: ""
    registered: 2026-08-07
    metric: observation_gap_minutes_max
    max: 5
    consecutive_days: 3
""",
        encoding="utf-8",
    )

    with pytest.raises(fv.RegistryError, match="함께 쓴다"):
        fv.load_registry(path)


def test_the_shipped_registry_has_no_shared_metric():
    """이 저장소의 실제 등록부가 그 상태로 돌아가지 않게 한다."""
    fv.load_registry()  # 공유가 있으면 RegistryError


# ---------------------------------------------------------------- G-4


def _death_day(*, gaps_minutes: float, abnormal: int) -> dict:
    return {
        "observation_gaps": [{"process": "l1_daily", "minutes": gaps_minutes}],
        "abnormal_exits": [{"process": "l1_daily"}] * abnormal,
    }


def _watched(**kwargs) -> fv.PendingVerification:
    base = dict(
        id="no-silent-process-death",
        summary="프로세스가 죽고 안 돌아온 날을 리포트가 말하는가",
        registered=date(2026, 8, 7),
        metric="abnormal_exits",
        consecutive_days=3,
        max_value=0.0,
        control_metric="observation_gap_minutes_max",
        control_min=5.0,
        control_summary="관측 공백이 5분을 넘은 날에 비정상 종료가 0이면 이 축이 눈이 먼 것이다",
    )
    base.update(kwargs)
    return fv.PendingVerification(**base)


def test_an_instrument_that_slept_through_the_accident_does_not_graduate():
    """**G-4의 핵심.** 계기의 출력을 그 계기의 합격 기준으로 쓰면, 계기가 눈이 멀수록
    성적이 좋아진다. 2026-08-19가 정확히 그 상태였다."""
    reports = _reports(
        **{
            "2026-08-13": _death_day(gaps_minutes=0.0, abnormal=0),
            "2026-08-14": _death_day(gaps_minutes=0.0, abnormal=0),
            "2026-08-18": _death_day(gaps_minutes=0.0, abnormal=0),
            "2026-08-19": _death_day(gaps_minutes=180.2, abnormal=0),
        }
    )

    [verdict] = fv.evaluate([_watched()], reports, today=_TODAY)

    assert verdict.status == fv.VerificationStatus.INSTRUMENT_BLIND
    assert verdict.needs_attention is True
    assert "180.2" in verdict.detail


def test_the_fixed_instrument_graduates_normally():
    """F-1 적용 후에는 같은 날 같은 입력으로 실명 판정이 안 난다 — 축이 실제로 봤기 때문."""
    reports = _reports(
        **{
            "2026-08-18": _death_day(gaps_minutes=0.0, abnormal=0),
            "2026-08-19": _death_day(gaps_minutes=180.2, abnormal=2),
        }
    )

    [verdict] = fv.evaluate([_watched()], reports, today=_TODAY)

    assert verdict.status == fv.VerificationStatus.RECURRED  # 값이 나쁜 것은 결과 축의 일이다
    assert verdict.last_value == 2.0


def test_a_quiet_day_is_not_blindness():
    """사건이 없던 날에 0을 내는 것은 정상이다 — 대조가 조용하면 이 판정도 조용하다."""
    reports = _reports(
        **{
            "2026-08-13": _death_day(gaps_minutes=0.0, abnormal=0),
            "2026-08-14": _death_day(gaps_minutes=0.0, abnormal=0),
            "2026-08-18": _death_day(gaps_minutes=0.0, abnormal=0),
            "2026-08-19": _death_day(gaps_minutes=0.0, abnormal=0),
        }
    )

    [verdict] = fv.evaluate([_watched()], reports, today=_TODAY)

    assert verdict.status == fv.VerificationStatus.VERIFIED


def test_a_control_metric_may_not_be_the_item_itself(tmp_path):
    """계기가 자기를 채점하는 것을 막으려는 자리인데 그 자체가 되면 안 된다."""
    path = tmp_path / "registry.yaml"
    path.write_text(
        """
verifications:
  - id: self-watching
    summary: ""
    registered: 2026-08-07
    metric: abnormal_exits
    max: 0
    consecutive_days: 3
    negative_control:
      metric: abnormal_exits
      min: 0
""",
        encoding="utf-8",
    )

    with pytest.raises(fv.RegistryError, match="자기 자신"):
        fv.load_registry(path)


# ---------------------------------------------------------------- F-3 연쇄 가드


def test_an_incomplete_day_does_not_look_like_a_broken_instrument():
    """F-3이 반쪽짜리 하루를 창에서 빼면서 못 잰 날이 늘어난다 — 그걸 계측 고장으로
    세면 사고가 난 주에 「계측이 고장 났을 수 있다」가 연쇄로 나온다."""
    reports = _reports(
        **{
            "2026-08-14": _coverage_day(99.4),
            "2026-08-18": {"incomplete_day": True},
            "2026-08-19": {"incomplete_day": True},
        }
    )

    [verdict] = fv.evaluate([_item(consecutive_days=2)], reports, today=_TODAY)

    assert verdict.status != fv.VerificationStatus.STALLED
