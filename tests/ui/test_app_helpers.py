"""app.py의 순수(에 가까운) 헬퍼 함수 검증 — Streamlit 세션 없이 직접 호출 가능한 것만.

렌더 함수(`render_*`)는 Streamlit 런타임이 필요해 `test_app_smoke.py`의 `AppTest`가 담당한다
— 이 파일은 그 아래 계층(Parquet 읽기·설정 조회)만 다룬다."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import polars as pl
import pytest

from messiah.core.health import health_cache_key
from messiah.core.messages import BarClosed, Health, HealthLevel, Horizon
from messiah.data.archiver import ParquetArchiver
from messiah.ui import app as app_module
from messiah.ui.app import (
    _absence_reason,
    _available_dates,
    _candlestick_figure,
    _component_versions,
    _default_redis_url,
    _live_date_notice,
    _load_bars,
    _load_bars_with_status,
)
from messiah.ui.bar_reader import BarExportError
from messiah.ui.bar_series import BarSeries
from messiah.ui.data_source import DataSourceMode, FreshnessBadge, TopicSnapshot

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
    ts = bars.x_kst[0]
    assert ts.hour == 9
    # 2026-08-03부터 스냅샷의 시각은 naive KST 벽시계다(`ui/bar_series.py`) — 예전엔
    # tz-aware(Asia/Seoul) 컬럼이었고 변환은 plotly에 넘기기 직전에 했다. "09:00 개장봉이
    # 09:00으로 보인다"는 검증 의도는 그대로, 못박는 지점만 앞당겨졌다.
    assert ts.tzinfo is None


def test_load_bars_missing_file_returns_none(tmp_path):
    bar_dir = tmp_path / "bars"
    assert _load_bars("A05608", "1m", date(2026, 7, 29), bar_dir) is None


def test_candlestick_figure_x_axis_matches_kst_wall_clock(tmp_path):
    bar_dir = tmp_path / "bars"
    _write_bar_parquet(bar_dir / "A05608" / "1m" / "2026-07-29.parquet", open_hour_kst=9)
    bars = _load_bars("A05608", "1m", date(2026, 7, 29), bar_dir)

    fig = _candlestick_figure(bars, tick_size=0.02)

    # 2026-07-31부터 x는 naive KST 벽시계 datetime이다(`_candlestick_figure` 주석 참고) —
    # 표현 형식이 바뀌었을 뿐 "09:00 개장 봉이 09:00으로 보인다"는 원래 검증 의도는 같다.
    assert fig.data[0].x[0] == datetime(2026, 7, 29, 9, 0)  # noqa: DTZ001 — naive가 검증 대상
    assert fig.data[0].x[0].tzinfo is None


def test_candlestick_figure_passes_plain_python_values_to_plotly(tmp_path):
    """2026-07-31 크래시 대응 — polars 객체를 plotly 검증기에 그대로 넘기면 네이티브 변환
    경로를 탄다(`_candlestick_figure` docstring). 순수 파이썬 값만 넘어가는지 못박는다."""
    bar_dir = tmp_path / "bars"
    _write_bar_parquet(bar_dir / "A05608" / "1m" / "2026-07-29.parquet", open_hour_kst=9)
    bars = _load_bars("A05608", "1m", date(2026, 7, 29), bar_dir)

    fig = _candlestick_figure(bars, tick_size=0.02)

    trace = fig.data[0]
    for values in (trace.x, trace.open, trace.high, trace.low, trace.close):
        assert not isinstance(values, pl.Series)
        assert all(isinstance(v, (int, float, datetime)) for v in values)
    assert trace.close[0] == pytest.approx(48505 * 0.02)


def test_cache_never_hands_out_polars_objects(tmp_path):
    """2026-08-03 P0-2의 핵심 계약 — 캐시가 내보내는 값에는 polars 타입이 **하나도** 없어야
    한다.

    07-31에 넣은 직렬화 락이 실패한 이유가 정확히 이것이었다: 락은 파싱만 감쌌는데 `load()`가
    공유 `pl.DataFrame`을 그대로 돌려줘서, 소비(`is_empty()`·`to_list()`)는 전부 락 밖에서
    같은 네이티브 객체를 만졌다. "락을 잘 걸었나"를 사람이 지키는 대신 여기서 타입으로
    못박는다 — 이 단언이 깨지면 그 구멍이 되살아난 것이다.
    """
    bar_dir = tmp_path / "bars"
    _write_bar_parquet(bar_dir / "A05608" / "1m" / "2026-07-29.parquet", open_hour_kst=9)

    bars = _load_bars("A05608", "1m", date(2026, 7, 29), bar_dir)

    assert isinstance(bars, BarSeries)
    for column in (bars.x_kst, bars.o_ticks, bars.h_ticks, bars.l_ticks, bars.c_ticks):
        assert isinstance(column, tuple)  # 불변 — 소비자가 실수로 못 바꾼다
        assert not isinstance(column, pl.Series)
        assert all(isinstance(v, (int, float, datetime)) for v in column)


def test_cache_hit_returns_the_same_immutable_snapshot(tmp_path):
    """파일이 안 바뀌면 재변환도 없어야 한다 — 5초 재렌더마다 `to_list()`를 5번씩 다시 돌던
    예전 경로가 되살아나지 않게 동일 객체 반환을 못박는다."""
    bar_dir = tmp_path / "bars"
    _write_bar_parquet(bar_dir / "A05608" / "1m" / "2026-07-29.parquet", open_hour_kst=9)

    first = _load_bars("A05608", "1m", date(2026, 7, 29), bar_dir)
    second = _load_bars("A05608", "1m", date(2026, 7, 29), bar_dir)

    assert first is second


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
    assert bars.x_kst[0].hour == 9  # 직전 성공본 그대로
    assert warning is not None and "직전 값" in warning


def test_child_crash_keeps_the_screen_alive_on_the_last_good_snapshot(tmp_path, monkeypatch):
    """P0-1(b)의 최종 계약 — 봉 파싱 프로세스가 **네이티브 크래시로 죽어도** 화면은 산다.

    2026-07-29~08-03에 UI를 5거래일 연속 즉사시킨 게 바로 이 크래시다. 이제 그건 자식에서
    나고, 부모는 직전 성공본을 계속 그리면서 그 사실을 화면에 표시한다 — 죽지도(구조),
    숨기지도(L18) 않는다.
    """
    bar_dir = tmp_path / "bars"
    _write_bar_parquet(bar_dir / "A05608" / "1m" / "2026-07-29.parquet", open_hour_kst=9)

    good, warning = _load_bars_with_status("A05608", "1m", date(2026, 7, 29), bar_dir)
    assert good is not None and warning is None

    def _crashing(*_args, **_kwargs):
        raise BarExportError("봉 익스포터 실패 — 네이티브 크래시(access violation)")

    monkeypatch.setattr(app_module, "read_day_series", _crashing)
    _write_bar_parquet(bar_dir / "A05608" / "1m" / "2026-07-29.parquet", open_hour_kst=10)

    bars, warning = _load_bars_with_status("A05608", "1m", date(2026, 7, 29), bar_dir)

    assert bars is good  # 직전 성공본 그대로 — 화면이 비지 않는다
    assert warning is not None and "네이티브 크래시" in warning


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
    assert bars is not None and len(bars) == 2


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


def test_load_bars_does_not_respawn_child_for_unchanged_file(tmp_path, monkeypatch):
    """LIVE는 5초마다 이 경로를 탄다 — 봉은 봉 주기에 한 번만 바뀌므로 대부분 캐시 히트여야
    한다.

    2026-08-03부터 읽기는 **자식 프로세스**에서 일어난다(`ui/bar_reader.py`) — 기동에 약
    0.8초가 드므로 이 캐시가 그 비용을 감당 가능하게 만드는 전제 그 자체다. 예전엔 부모의
    `ParquetArchiver.read_day` 호출 수를 셌는데, 이제 그 호출은 부모에 존재하지 않으므로
    **자식을 몇 번 띄웠나**를 센다. 검증 의도는 그대로다.
    """
    bar_dir = tmp_path / "bars"
    _write_bar_parquet(bar_dir / "A05608" / "1m" / "2026-07-29.parquet", open_hour_kst=9)

    calls = {"n": 0}
    real = app_module.read_day_series

    def _counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(app_module, "read_day_series", _counting)

    for _ in range(5):
        _load_bars_with_status("A05608", "1m", date(2026, 7, 29), bar_dir)

    assert calls["n"] == 1


def test_load_bars_rereads_after_file_changes(tmp_path):
    bar_dir = tmp_path / "bars"
    path = bar_dir / "A05608" / "1m" / "2026-07-29.parquet"
    _write_bar_parquet(path, open_hour_kst=9)
    first, _ = _load_bars_with_status("A05608", "1m", date(2026, 7, 29), bar_dir)
    assert first is not None and first.x_kst[0].hour == 9

    _write_bar_parquet(path, open_hour_kst=10)
    second, warning = _load_bars_with_status("A05608", "1m", date(2026, 7, 29), bar_dir)

    assert warning is None
    assert second is not None and second.x_kst[0].hour == 10


# ---------------------------------------------------------------- NO_DATA 세 갈래 (P1-2)


class _FakeSource:
    """`DataSource` Protocol의 최소 구현 — 신선도 배지를 **직접 지정**할 수 있어야 한다.

    실제 `LiveDataSource`는 벽시계로 age를 재므로 STALE 상태를 테스트에서 만들려면 시간을
    조작해야 한다. 여기서 재려는 것은 "배지가 어떻게 계산되나"가 아니라 "주어진 배지에서
    어떤 사유를 말하나"라, 배지를 입력으로 받는 편이 검증 의도에 맞는다.
    """

    def __init__(self, mode=DataSourceMode.LIVE, snapshots=None) -> None:
        self.mode = mode
        self._snapshots = dict(snapshots or {})

    def snapshot(self, key: str) -> TopicSnapshot:
        return self._snapshots.get(key) or TopicSnapshot(None, FreshnessBadge.NO_DATA, None)


def _health_snapshot(level=HealthLevel.OK, *, badge=FreshnessBadge.LIVE, age=3.0, git_sha=""):
    message = Health(component="g2.pipeline", level=level, git_sha=git_sha)
    return TopicSnapshot(message=message, badge=badge, age_seconds=age)


_G2 = health_cache_key("g2.pipeline")


def test_unwired_publisher_is_not_reported_as_an_outage():
    """`intel.futures`가 안 오는 건 사고가 아니라 구조다 — live 번들이 0개라 발행 자체가 없다.

    끊김과 같은 회색 NO_DATA로 보이면 Redis 단절과 "원래 그런 것"이 구분되지 않는다(L18).
    """
    source = _FakeSource(snapshots={_G2: _health_snapshot()})

    reason = _absence_reason(source, "FuturesView")

    assert reason is not None
    assert "미배선" in reason
    assert "끊김" not in reason


def test_waiting_is_distinguished_from_unwired():
    source = _FakeSource(snapshots={_G2: _health_snapshot()})

    assert "대기" in (_absence_reason(source, "DecisionIntent") or "")
    assert "미배선" in (_absence_reason(source, "OptionsView") or "")


def test_dead_publisher_turns_the_same_topic_into_an_outage():
    """**선언이 관측을 이기지 않는다** — 발행자가 죽으면 "미배선"이라고 우기지 않는다."""
    source = _FakeSource(snapshots={_G2: _health_snapshot(badge=FreshnessBadge.STALE, age=95.0)})

    reason = _absence_reason(source, "FuturesView")

    assert reason is not None
    assert "끊김" in reason
    assert "95초" in reason


def test_publisher_that_never_started_is_an_outage_too():
    source = _FakeSource()  # g2.pipeline heartbeat 자체가 없음

    reason = _absence_reason(source, "FuturesView")

    assert reason is not None
    assert "끊김" in reason and "미기동" in reason


def test_connection_failure_overrides_every_per_topic_reason():
    """연결이 끊기면 모든 토픽이 같은 이유로 비어 있다 — 개별 사유가 그걸 가리면 안 된다."""
    source = _FakeSource(
        snapshots={
            "LiveConnectionError": TopicSnapshot(
                Health(component="command-center-ui", level=HealthLevel.CRITICAL),
                FreshnessBadge.LIVE,
                1.0,
            ),
            _G2: _health_snapshot(),
        }
    )

    assert _absence_reason(source, "FuturesView") == "끊김 — LIVE 연결 실패"


def test_replay_mode_never_claims_an_outage():
    """재생 화면의 NO_DATA는 "이 스냅샷에 그 값이 없다"지 발행자의 생사와 무관하다."""
    source = _FakeSource(mode=DataSourceMode.REPLAY)

    assert _absence_reason(source, "FuturesView") is None


def test_every_top_bar_topic_has_a_declared_reason():
    """사유를 못 대는 토픽이 화면에 남으면 다시 "회색 하나로 뭉뚱그리기"로 되돌아간다."""
    for key in ("FuturesView", "OptionsView", "DecisionIntent", "Fill", "RegimeState"):
        assert key in app_module._ABSENCE_REASON
        assert key in app_module._PUBLISHER_OF


# ---------------------------------------------------------------- 코드 버전 (P0-1)


def test_component_versions_only_include_components_that_reported():
    """안 보낸 컴포넌트는 이미 신호등이 드러낸다 — 같은 사실을 두 곳에서 다르게 말하지 않는다."""
    source = _FakeSource(snapshots={_G2: _health_snapshot(git_sha="8810867")})

    versions = _component_versions(source)

    assert versions == {"g2.pipeline": "8810867"}
    assert "l1.collector" not in versions


def test_component_versions_keep_the_empty_sha_of_an_old_process():
    """빈 SHA는 "이 필드가 생기기 전 코드"라는 뜻이라 **버려지면 안 된다**(P0-1의 실제 상태)."""
    source = _FakeSource(snapshots={_G2: _health_snapshot(git_sha="")})

    assert _component_versions(source) == {"g2.pipeline": ""}


# ---------------------------------------------------------------- LIVE 차트 날짜 (P1-3)


def test_live_chart_labels_today_as_today():
    is_today, notice = _live_date_notice(date(2026, 8, 5), today=date(2026, 8, 5))

    assert is_today is True
    assert "오늘(2026-08-05)" in notice


def test_live_chart_never_calls_a_past_day_today():
    """장 개시 전·적재 정지 구간에 전일 차트가 "오늘"로 찍히던 경로의 회귀 방지."""
    is_today, notice = _live_date_notice(date(2026, 8, 4), today=date(2026, 8, 5))

    assert is_today is False
    assert "2026-08-04" in notice
    assert "오늘(2026-08-05) 봉이 아직 없어" in notice
