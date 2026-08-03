"""봉 파싱의 프로세스 분리 검증 (2026-08-03 P0-1(b)).

이 계층의 존재 이유는 단 하나다 — **자식이 죽어도 부모(화면)는 산다**. 그래서 검증도
거기에 집중한다: ① 부모에 polars가 안 올라오는가 ② 자식의 죽음이 예외로 바뀌는가
③ 실패와 "데이터 없음"이 절대 안 뭉개지는가.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import polars as pl
import pytest
from messiah.core.messages import Horizon
from messiah.ui.bar_reader import (
    BarExportError,
    _Completed,
    read_day_series,
)
from messiah.ui.bar_series import BarSeries

_KST = timezone(timedelta(hours=9))

# Windows에서 access violation으로 죽은 자식의 종료 코드 — 5거래일 연속 UI를 죽인 바로 그
# 크래시가 자식에서 났을 때 부모가 받는 값이다(`ui/bar_reader.py`).
_ACCESS_VIOLATION = -1073741819


def _write_day(bar_dir: Path, *, hours: tuple[int, ...]) -> None:
    path = bar_dir / "A05608" / "1m" / "2026-07-29.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "symbol": ["A05608"] * len(hours),
            "horizon": ["1m"] * len(hours),
            "bar_open_kst": [datetime(2026, 7, 29, h, 0, tzinfo=_KST) for h in hours],
            "o_ticks": [48500 + h for h in hours],
            "h_ticks": [48510 + h for h in hours],
            "l_ticks": [48490 + h for h in hours],
            "c_ticks": [48505 + h for h in hours],
            "volume": [12] * len(hours),
            "quality_ok": [True] * len(hours),
        }
    ).write_parquet(path)


def _runner(returncode: int, stdout: str = "", stderr: str = ""):
    def _run(_command, _timeout):
        return _Completed(returncode, stdout, stderr)

    return _run


# ---------------------------------------------------------------- ① 부모에 polars가 없다


def test_ui_process_never_loads_polars():
    """이 프로젝트에서 가장 비싸게 배운 계약 — UI 프로세스에 polars 런타임이 올라오면
    `_polars_runtime.pyd`의 access violation이 **부모에서** 나고 화면이 즉사한다.
    5거래일 연속 그렇게 죽었다(07-29·07-30·07-31·08-03, 전부 동일 fault offset).

    자식 프로세스로 파싱을 미뤄놓고도 부모가 어딘가에서 polars를 끌어오면 그 분리는
    무의미하다 — 그래서 임포트 그래프 자체를 여기서 못박는다. 깨끗한 인터프리터에서
    `messiah.ui.app`을 임포트하고 `sys.modules`를 확인한다.
    """
    probe = (
        "import sys, messiah.ui.app;"
        "print('polars' in sys.modules, 'messiah.data.archiver' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=True,
    )

    assert result.stdout.strip().endswith("False False")


# ---------------------------------------------------------------- ② 실제 자식 왕복


def test_reads_real_day_through_a_child_process(tmp_path):
    _write_day(tmp_path, hours=(9, 10))

    series = read_day_series(tmp_path, "A05608", Horizon.M1, date(2026, 7, 29))

    assert isinstance(series, BarSeries)
    assert len(series) == 2
    # 저장은 UTC로 정규화되지만 화면엔 KST 벽시계여야 한다(2026-07-29 실측 버그).
    assert [ts.hour for ts in series.x_kst] == [9, 10]
    assert all(ts.tzinfo is None for ts in series.x_kst)
    assert series.c_ticks == (48505 + 9, 48505 + 10)


def test_missing_day_is_none_not_an_error(tmp_path):
    """그날 데이터가 없는 건 장애가 아니다 — 매일 아침 첫 봉 전까지가 정확히 이 상태다."""
    assert read_day_series(tmp_path, "A05608", Horizon.M1, date(2026, 7, 29)) is None


# ---------------------------------------------------------------- ③ 자식의 죽음 = 예외


def test_child_access_violation_becomes_an_exception_not_empty_data(tmp_path):
    """이 테스트가 P0-1(b) 전체의 요점이다.

    자식이 5거래일 연속 UI를 죽인 그 크래시로 죽었을 때, 부모는 ⓐ 같이 죽지 않고
    ⓑ 빈 시계열로 뭉개지도 않고 ⓒ **그게 네이티브 크래시였다는 사실**까지 담은 예외를
    올려야 한다. ⓑ가 특히 중요하다 — 조용히 빈 값을 돌려주면 사고가 "데이터 없는 날"로
    위장된다(L18).
    """
    with pytest.raises(BarExportError) as excinfo:
        read_day_series(
            tmp_path,
            "A05608",
            Horizon.M1,
            date(2026, 7, 29),
            runner=_runner(_ACCESS_VIOLATION, stderr="Windows fatal exception: access violation"),
        )

    assert "네이티브 크래시" in str(excinfo.value)


def test_child_timeout_becomes_an_exception(tmp_path):
    """자식이 멈추면 부모의 렌더 스레드가 같이 멈춘다 — 무한정 기다리지 않는다."""

    def _hanging(_command, timeout):
        raise subprocess.TimeoutExpired(cmd="bar_export", timeout=timeout)

    with pytest.raises(BarExportError, match="응답 없음"):
        read_day_series(
            tmp_path, "A05608", Horizon.M1, date(2026, 7, 29), runner=_hanging, timeout_seconds=3
        )


def test_garbage_on_stdout_becomes_an_exception(tmp_path):
    with pytest.raises(BarExportError, match="표식"):
        read_day_series(
            tmp_path,
            "A05608",
            Horizon.M1,
            date(2026, 7, 29),
            runner=_runner(0, stdout="ImportWarning: 뭔가 시끄러운 라이브러리\n"),
        )


def test_library_noise_before_the_marker_does_not_corrupt_payload(tmp_path):
    """자식 stdout에 라이브러리 경고가 섞여도 payload는 온전해야 한다 — 표식 뒤만 읽는
    설계가 실제로 그 일을 하는지 본다."""
    from messiah.data.bar_export import PAYLOAD_MARKER

    noisy = (
        "UserWarning: 어떤 라이브러리가 stdout에 흘린 줄\n"
        f"{PAYLOAD_MARKER}\n"
        '{"x_kst":["2026-07-29T09:00:00"],"o_ticks":[1],"h_ticks":[2],'
        '"l_ticks":[0],"c_ticks":[1]}\n'
    )

    series = read_day_series(
        tmp_path, "A05608", Horizon.M1, date(2026, 7, 29), runner=_runner(0, stdout=noisy)
    )

    assert series is not None and len(series) == 1
    assert series.x_kst[0] == datetime(2026, 7, 29, 9, 0)  # noqa: DTZ001 — naive가 계약


def test_payload_shape_mismatch_becomes_an_exception(tmp_path):
    from messiah.data.bar_export import PAYLOAD_MARKER

    with pytest.raises(BarExportError, match="형태가 계약과 다름"):
        read_day_series(
            tmp_path,
            "A05608",
            Horizon.M1,
            date(2026, 7, 29),
            runner=_runner(0, stdout=f'{PAYLOAD_MARKER}\n{{"x_kst":["2026-07-29T09:00:00"]}}\n'),
        )
