"""아카이브 거래량 대조 판정 — 2026-08-05 신설 (`scripts/verify_archive_volume.py`).

순수 판정 함수만 검증한다. REST 호출·인증은 이 도구의 관심사가 아니라 `KISRestClient`의
몫이고, 여기서 흉내내면 "네트워크를 안 타는 척하는 네트워크 테스트"가 된다.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from verify_archive_volume import (  # noqa: E402
    HEAD_MISSING_MINUTES_LIMIT,
    WARN_RATIO,
    compare_day,
)


def test_half_lost_volume_is_flagged():
    """2026-07-28~30 실측 상태 — WS 프레임의 첫 체결만 파싱해 거래량 절반이 사라졌다.
    비율 0.49~0.52로 나오던 그 사고를 이 판정이 잡아야 한다."""
    archived = {"09:00": 250, "09:01": 240, "09:02": 260}
    official = {"09:00": 500, "09:01": 496, "09:02": 505}

    result = compare_day(archived, official)

    assert result.ratio is not None and result.ratio < WARN_RATIO
    assert round(result.ratio, 2) == 0.50
    assert (result.common_minutes, result.archived_volume, result.official_volume) == (3, 750, 1501)
    assert not result.ok


def test_matching_volume_passes():
    archived = {"09:00": 500, "09:01": 496}
    official = {"09:00": 500, "09:01": 496}

    result = compare_day(archived, official)

    assert result.ratio == 1.0 >= WARN_RATIO
    assert result.common_minutes == 2
    assert result.ok


def test_only_common_minutes_are_compared():
    """거래소 분봉과 수집 분봉은 장전 구간 포함 여부가 다를 수 있다 — 그 차이를 거래량
    결함으로 오인하면 매일 오탐이 난다."""
    archived = {"08:45": 400, "09:00": 500}  # 장전 봉을 우리만 갖고 있다
    official = {"09:00": 500, "15:40": 300}  # 종가단일가를 저쪽만 갖고 있다

    result = compare_day(archived, official)

    assert result.ratio == 1.0
    assert (result.common_minutes, result.archived_volume, result.official_volume) == (1, 500, 500)


def test_no_overlap_is_undecidable_not_zero():
    """겹치는 분이 없으면 **판정 불가**다 — 0.0("전부 잃었다")으로 우기지 않는다(L18)."""
    result = compare_day({"08:45": 10}, {"09:00": 10})

    assert result.ratio is None
    assert result.common_minutes == 0
    assert not result.ok


def test_empty_archive_is_undecidable():
    assert compare_day({}, {"09:00": 10}).ratio is None


# ---------------------------------------------- B-1(2026-08-10): 미수집 분을 어디가 빈지로


def test_a_late_start_shows_up_as_head_missing_minutes():
    """2026-08-10의 그 자리 — 미수집 13분이 **전부 아침**이었는데 임계 20분 아래라 조용했다.

    그날 이 축이 잘림을 본 **유일한 축**이었다(계열 커버리지 100% · 봉 결손 0 · 관측 공백
    없음). 숫자 하나로는 "장중에 13분 빠진 날"과 구분되지 않는다.
    """
    official = {f"08:{m:02d}": 10 for m in range(45, 58)} | {f"09:{m:02d}": 10 for m in range(60)}
    archived = {f"09:{m:02d}": 10 for m in range(60)}

    result = compare_day(archived, official)

    assert result.missing_minutes == 13
    assert (result.head_missing_minutes, result.middle_missing_minutes) == (13, 0)
    assert result.tail_missing_minutes == 0
    assert not result.ok, "머리 미수집은 한 분도 봐주지 않는다"


def test_a_mid_session_hole_is_not_called_a_late_start():
    """장중 구멍은 회선을, 머리 구멍은 스케줄러를 의심하게 한다 — 처방이 다르다."""
    official = {f"09:{m:02d}": 10 for m in range(60)}
    archived = {f"09:{m:02d}": 10 for m in range(60) if not 20 <= m < 25}

    result = compare_day(archived, official)

    assert (result.head_missing_minutes, result.middle_missing_minutes) == (0, 5)
    assert result.ok, "중간 5분은 임계 20분 안이라 정상이다"


def test_a_truncated_afternoon_lands_in_the_tail():
    """2026-08-07의 모양 — 13:41에 죽고 안 돌아왔다. 머리가 아니라 꼬리다."""
    official = {f"13:{m:02d}": 10 for m in range(60)}
    archived = {f"13:{m:02d}": 10 for m in range(41)}

    result = compare_day(archived, official)

    assert (result.head_missing_minutes, result.tail_missing_minutes) == (0, 19)
    assert result.middle_missing_minutes == 0


def test_a_normal_day_has_no_missing_minutes_at_all():
    """정상일(2026-08-04·08-07 실측)은 공통 410분 = 공식 410분이었다 — 머리 임계 0의 근거."""
    official = {f"{h:02d}:{m:02d}": 10 for h in (9, 10) for m in range(60)}

    result = compare_day(dict(official), official)

    assert result.missing_minutes == 0
    assert result.head_missing_minutes == HEAD_MISSING_MINUTES_LIMIT == 0
    assert result.ok


def test_without_any_common_minute_the_split_is_not_guessed():
    """머리/꼬리를 가를 기준선(아카이브의 첫·마지막 분)이 없으면 구간 판정을 안 한다."""
    result = compare_day({"08:45": 10}, {"09:00": 10, "09:01": 10})

    assert result.missing_minutes == 2
    assert (result.head_missing_minutes, result.middle_missing_minutes) == (0, 0)
    assert result.tail_missing_minutes == 0
