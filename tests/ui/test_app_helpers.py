"""app.py의 순수(에 가까운) 헬퍼 함수 검증 — Streamlit 세션 없이 직접 호출 가능한 것만.

렌더 함수(`render_*`)는 Streamlit 런타임이 필요해 `test_app_smoke.py`의 `AppTest`가 담당한다
— 이 파일은 그 아래 계층(Parquet 읽기·설정 조회)만 다룬다."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import polars as pl
import pytest
from messiah.core.messages import BarClosed, Horizon
from messiah.data.archiver import ParquetArchiver
from messiah.ui.app import (
    _available_dates,
    _candlestick_figure,
    _default_redis_url,
    _load_bars,
    _load_bars_with_status,
)

_KST = timezone(timedelta(hours=9))  # core/timeutil.py의 KST와 동일 정의(고정 오프셋)


def _write_bar_parquet(path: Path, *, open_hour_kst: int) -> None:
    """실제 `data/archiver.py`와 동일하게 tz-aware(KST) datetime을 그대로 저장 — Polars가
    Parquet에 쓸 때 내부적으로 `time_zone='UTC'`로 정규화하는 실제 왕복 경로를 재현한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pl.DataFrame(
        {
            "symbol": ["A05608"],
            "horizon": ["1m"],
            "bar_open_kst": [datetime(2026, 7, 29, open_hour_kst, 0, 0, tzinfo=_KST)],
            "o_ticks": [48500],
            "h_ticks": [48510],
            "l_ticks": [48490],
            "c_ticks": [48505],
            "volume": [12],
            "quality_ok": [True],
        }
    )
    df.write_parquet(path)


def test_load_bars_returns_kst_wall_clock_not_utc(tmp_path):
    # 실측으로 확인된 버그(2026-07-29): Parquet 왕복 후 그대로 읽으면 09:00 KST 개장 봉이
    # 00:00으로 보인다(UTC로 정규화된 물리값을 그대로 노출) — 09시로 되돌아와야 한다.
    bar_dir = tmp_path / "bars"
    _write_bar_parquet(bar_dir / "A05608" / "1m" / "2026-07-29.parquet", open_hour_kst=9)

    bars = _load_bars("A05608", "1m", date(2026, 7, 29), bar_dir)

    assert bars is not None
    ts = bars["bar_open_kst"][0]
    assert ts.hour == 9
    assert bars.schema["bar_open_kst"].time_zone == "Asia/Seoul"


def test_load_bars_missing_file_returns_none(tmp_path):
    bar_dir = tmp_path / "bars"
    assert _load_bars("A05608", "1m", date(2026, 7, 29), bar_dir) is None


def test_candlestick_figure_x_axis_matches_kst_wall_clock(tmp_path):
    bar_dir = tmp_path / "bars"
    _write_bar_parquet(bar_dir / "A05608" / "1m" / "2026-07-29.parquet", open_hour_kst=9)
    bars = _load_bars("A05608", "1m", date(2026, 7, 29), bar_dir)

    fig = _candlestick_figure(bars, tick_size=0.02)

    assert str(fig.data[0].x[0]) == "2026-07-29T09:00:00.000000"


def test_default_redis_url_reads_from_instance_config(monkeypatch):
    class _FakeCfg:
        redis_url = "redis://localhost:6380/0"

    monkeypatch.setattr("messiah.ui.app.load_instance", lambda: _FakeCfg())
    assert _default_redis_url() == "redis://localhost:6380/0"


def test_default_redis_url_falls_back_when_config_load_fails(monkeypatch):
    def _raise():
        raise RuntimeError("configs/instance.yaml not found")

    monkeypatch.setattr("messiah.ui.app.load_instance", _raise)
    # 화면을 죽이는 대신 프로젝트가 실제로 쓰는 기본값(6380)으로 폴백한다 — 예전의 잘못된
    # 하드코딩(6379)으로 되돌아가지 않는다.
    assert _default_redis_url() == "redis://localhost:6380/0"


@pytest.mark.parametrize("bad_url", ["redis://localhost:6379/0"])
def test_default_redis_url_is_never_the_old_wrong_default(monkeypatch, bad_url):
    # 회귀 방지 — 예전 하드코딩 값이 실수로 다시 기본값이 되는 걸 명시적으로 막는다.
    assert _default_redis_url() != bad_url


# ---------------------------------------------------------------- 봉 읽기 방어층 (2026-07-30)


def test_load_bars_with_status_returns_no_warning_on_healthy_read(tmp_path):
    bar_dir = tmp_path / "bars"
    _write_bar_parquet(bar_dir / "A05608" / "1m" / "2026-07-29.parquet", open_hour_kst=9)

    bars, warning = _load_bars_with_status("A05608", "1m", date(2026, 7, 29), bar_dir)

    assert bars is not None
    assert warning is None


def test_load_bars_with_status_missing_file_is_not_a_warning(tmp_path):
    """파일이 아직 없는 건 장애가 아니라 '데이터 없음'이다 — 경고로 올리면 매일 아침
    첫 봉 전까지 헛경고가 뜬다."""
    bars, warning = _load_bars_with_status("A05608", "1m", date(2026, 7, 29), tmp_path / "bars")

    assert bars is None
    assert warning is None


def test_load_bars_falls_back_to_last_good_frame_when_file_is_torn(tmp_path):
    """2026-07-30 08:57 UI 즉사 지점의 회귀 방지 — 수집기가 파일을 갈아끼우는 순간을 읽어도
    화면은 직전 값으로 살아남고, 그 사실을 조용히 숨기지 않는다(L18)."""
    bar_dir = tmp_path / "bars"
    path = bar_dir / "A05608" / "1m" / "2026-07-29.parquet"
    _write_bar_parquet(path, open_hour_kst=9)

    good, _ = _load_bars_with_status("A05608", "1m", date(2026, 7, 29), bar_dir)
    assert good is not None

    path.write_bytes(b"")  # 교체 도중의 truncate 상태를 그대로 재현

    bars, warning = _load_bars_with_status("A05608", "1m", date(2026, 7, 29), bar_dir)

    assert bars is not None
    assert bars["bar_open_kst"][0].hour == 9  # 직전 성공본 그대로
    assert warning is not None and "직전 값" in warning


def test_load_bars_reports_failure_when_there_is_no_last_good_frame(tmp_path):
    bar_dir = tmp_path / "bars"
    path = bar_dir / "A05608" / "1m" / "2026-07-29.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a parquet file")

    bars, warning = _load_bars_with_status("A05608", "1m", date(2026, 7, 29), bar_dir)

    assert bars is None
    assert warning is not None and "읽지 못했" in warning


def test_load_bars_reads_intraday_shards_before_compaction(tmp_path):
    """장중에는 하루치가 시간대 조각으로 흩어져 있다 — 화면이 그걸 못 읽으면 당일 차트가
    통째로 빈다(`data/archiver.py` "조각 쓰기")."""
    bar_dir = tmp_path / "bars"
    archiver = ParquetArchiver(bar_dir)
    for hour in (9, 10):
        archiver.append_bar(
            BarClosed(
                symbol="A05608",
                horizon=Horizon.M1,
                bar_open_kst=datetime(2026, 7, 29, hour, 0, tzinfo=_KST),
                o_ticks=1,
                h_ticks=1,
                l_ticks=1,
                c_ticks=1,
                volume=1,
                quality_ok=True,
            )
        )

    bars, warning = _load_bars_with_status("A05608", "1m", date(2026, 7, 29), bar_dir)

    assert warning is None
    assert bars is not None and bars.height == 2


def test_available_dates_sees_intraday_shard_directories(tmp_path):
    bar_dir = tmp_path / "bars"
    ParquetArchiver(bar_dir).append_bar(
        BarClosed(
            symbol="A05608",
            horizon=Horizon.M1,
            bar_open_kst=datetime(2026, 7, 29, 9, 0, tzinfo=_KST),
            o_ticks=1,
            h_ticks=1,
            l_ticks=1,
            c_ticks=1,
            volume=1,
            quality_ok=True,
        )
    )

    assert _available_dates("A05608", "1m", bar_dir) == [date(2026, 7, 29)]


def test_load_bars_does_not_reparse_unchanged_file(tmp_path, monkeypatch):
    """LIVE는 5초마다 이 경로를 탄다 — 봉은 분당 한 번만 바뀌므로 대부분 캐시 히트여야 한다."""
    bar_dir = tmp_path / "bars"
    _write_bar_parquet(bar_dir / "A05608" / "1m" / "2026-07-29.parquet", open_hour_kst=9)

    calls = {"n": 0}
    real = ParquetArchiver.read_day

    def _counting(self, *args, **kwargs):
        calls["n"] += 1
        return real(self, *args, **kwargs)

    monkeypatch.setattr(ParquetArchiver, "read_day", _counting)

    for _ in range(5):
        _load_bars_with_status("A05608", "1m", date(2026, 7, 29), bar_dir)

    assert calls["n"] == 1


def test_load_bars_rereads_after_file_changes(tmp_path):
    bar_dir = tmp_path / "bars"
    path = bar_dir / "A05608" / "1m" / "2026-07-29.parquet"
    _write_bar_parquet(path, open_hour_kst=9)
    first, _ = _load_bars_with_status("A05608", "1m", date(2026, 7, 29), bar_dir)
    assert first is not None and first["bar_open_kst"][0].hour == 9

    _write_bar_parquet(path, open_hour_kst=10)
    second, warning = _load_bars_with_status("A05608", "1m", date(2026, 7, 29), bar_dir)

    assert warning is None
    assert second is not None and second["bar_open_kst"][0].hour == 10
