"""아카이브 거래량 대조 판정 — 2026-08-05 신설 (`scripts/verify_archive_volume.py`).

순수 판정 함수만 검증한다. REST 호출·인증은 이 도구의 관심사가 아니라 `KISRestClient`의
몫이고, 여기서 흉내내면 "네트워크를 안 타는 척하는 네트워크 테스트"가 된다.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from verify_archive_volume import WARN_RATIO, compare_day  # noqa: E402


def test_half_lost_volume_is_flagged():
    """2026-07-28~30 실측 상태 — WS 프레임의 첫 체결만 파싱해 거래량 절반이 사라졌다.
    비율 0.49~0.52로 나오던 그 사고를 이 판정이 잡아야 한다."""
    archived = {"09:00": 250, "09:01": 240, "09:02": 260}
    official = {"09:00": 500, "09:01": 496, "09:02": 505}

    ratio, common, mine, theirs = compare_day(archived, official)

    assert ratio is not None and ratio < WARN_RATIO
    assert round(ratio, 2) == 0.50
    assert (common, mine, theirs) == (3, 750, 1501)


def test_matching_volume_passes():
    archived = {"09:00": 500, "09:01": 496}
    official = {"09:00": 500, "09:01": 496}

    ratio, common, _, _ = compare_day(archived, official)

    assert ratio == 1.0 >= WARN_RATIO
    assert common == 2


def test_only_common_minutes_are_compared():
    """거래소 분봉과 수집 분봉은 장전 구간 포함 여부가 다를 수 있다 — 그 차이를 거래량
    결함으로 오인하면 매일 오탐이 난다."""
    archived = {"08:45": 400, "09:00": 500}  # 장전 봉을 우리만 갖고 있다
    official = {"09:00": 500, "15:40": 300}  # 종가단일가를 저쪽만 갖고 있다

    ratio, common, mine, theirs = compare_day(archived, official)

    assert ratio == 1.0
    assert (common, mine, theirs) == (1, 500, 500)


def test_no_overlap_is_undecidable_not_zero():
    """겹치는 분이 없으면 **판정 불가**다 — 0.0("전부 잃었다")으로 우기지 않는다(L18)."""
    ratio, common, _, _ = compare_day({"08:45": 10}, {"09:00": 10})

    assert ratio is None
    assert common == 0


def test_empty_archive_is_undecidable():
    assert compare_day({}, {"09:00": 10})[0] is None
