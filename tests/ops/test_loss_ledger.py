"""오늘 영구히 잃은 것의 실시간 장부 (2026-08-10 B-2).

2026-08-10에 08:20 트리거가 기동 창에 막혀 08:58에야 수집이 떴다. 그 38분의 체결틱·수급·
옵션체인은 소급 경로가 없어 영원히 없고, **그 사실이 사람 눈에 닿은 것은 15:45 장후
리포트**였다. 화면은 종일 초록이었다 — 화면이 틀린 게 아니라 아무 자리도
*"오늘 이미 잃은 것이 있나"*를 묻지 않았다.
"""

from __future__ import annotations

import asyncio

import pytest

from messiah.data import poll_retry
from messiah.ops import loss_ledger


@pytest.fixture(autouse=True)
def _clean_ledger():
    """전역 상태를 쓰는 모듈이라 테스트끼리 새면 안 된다."""
    loss_ledger.reset()
    yield
    loss_ledger.reset()


def test_a_quiet_day_says_so_instead_of_saying_nothing():
    """ "봤는데 없다"와 "이 자리가 아무것도 안 본다"가 구분돼야 한다 — 후자가 08-10의 상태였다."""
    ledger = loss_ledger.current()

    assert ledger.clean
    assert ledger.describe() == "오늘 소급 불가 손실 없음"
    assert ledger.to_dict()["lost_items"] == 0


def test_the_morning_lag_is_the_first_thing_on_the_ledger():
    """기동 지연은 프로세스가 뜨는 **순간 이미 확정된** 손실이고, 대개 그날 가장 큰 덩어리다."""
    loss_ledger.record_start_lag(38.5)

    ledger = loss_ledger.current()

    assert not ledger.clean
    assert "기동 지연 38분" in ledger.describe()
    assert "영구 소실" in ledger.describe()


def test_a_small_lag_is_not_called_a_loss():
    """정시 기동도 트리거보다 20~30초 늦다(self-check + Docker) — 매일 울면 안 읽힌다."""
    loss_ledger.record_start_lag(0.4)

    assert loss_ledger.current().clean


def test_an_unmeasurable_lag_is_not_zero():
    """등록 정본을 못 읽은 것과 제때 뜬 것은 다르다(L18)."""
    loss_ledger.record_start_lag(None)

    ledger = loss_ledger.current()

    assert ledger.start_lag_minutes is None
    assert ledger.clean, "판정 불가를 손실로 세면 매일 빨간불이 된다"


def test_lost_items_accumulate_per_series():
    """2026-08-10 실측 — 옵션 다리 1개(14:30) + 수급 3행(10:46·15:19·15:31)."""
    loss_ledger.record_lost("option_chain/regular")
    for _ in range(3):
        loss_ledger.record_lost("flow_intraday/K2I")

    ledger = loss_ledger.current()

    assert ledger.lost_by_series == {"option_chain/regular": 1, "flow_intraday/K2I": 3}
    assert ledger.lost_items == 4
    assert not ledger.clean
    assert "flow_intraday/K2I 3건" in ledger.describe()


def test_the_ledger_is_a_copy_so_callers_cannot_corrupt_it():
    """스냅샷을 쓰는 쪽이 dict를 들고 있다가 고치면 장부가 조용히 틀어진다."""
    loss_ledger.record_lost("ticks")

    snapshot = loss_ledger.current()
    snapshot.lost_by_series["ticks"] = 999

    assert loss_ledger.current().lost_by_series == {"ticks": 1}


# ---------------------------------------------------------------- 폴러와의 결선


class _Boom:
    def __init__(self, fails: int) -> None:
        self.left = fails
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        if self.left > 0:
            self.left -= 1
            raise RuntimeError("KIS 500")
        return {"ok": True}


async def _no_sleep(_seconds: float) -> None:
    pass


def test_only_a_final_failure_lands_on_the_ledger(monkeypatch):
    """**재시도로 살아난 것은 손실이 아니다.**

    둘을 같이 세면 이 숫자가 "오늘 잃은 것"을 더 이상 뜻하지 않는다 — 2026-08-10에
    옵션체인은 53건 실패 중 52건이 살아났고 실제 손실은 1다리였다.
    """
    monkeypatch.setattr(poll_retry.mlog, "log", lambda *a, **k: None)

    recovered = _Boom(fails=1)
    asyncio.run(
        poll_retry.fetch_with_retry(
            recovered,
            retried_tag="R",
            error_tag="E",
            sleep=_no_sleep,
            loss_series="option_chain/regular",
        )
    )

    assert loss_ledger.current().lost_items == 0, "살아난 것은 안 센다"

    lost = _Boom(fails=99)
    asyncio.run(
        poll_retry.fetch_with_retry(
            lost,
            retried_tag="R",
            error_tag="E",
            sleep=_no_sleep,
            loss_series="option_chain/regular",
        )
    )

    assert loss_ledger.current().lost_by_series == {"option_chain/regular": 1}


def test_a_caller_without_a_loss_series_does_not_touch_the_ledger(monkeypatch):
    """소급 가능한 계열까지 이 장부에 오르면 "영원히 없는 것"이라는 뜻이 흐려진다."""
    monkeypatch.setattr(poll_retry.mlog, "log", lambda *a, **k: None)

    asyncio.run(
        poll_retry.fetch_with_retry(
            _Boom(fails=99), retried_tag="R", error_tag="E", sleep=_no_sleep
        )
    )

    assert loss_ledger.current().lost_items == 0
