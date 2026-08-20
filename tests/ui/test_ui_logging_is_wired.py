"""계기를 만들어 두고 배선을 안 했다 — 2026-08-20 F-B (D-2 마감).

2026-08-18 G-0818I-4가 `_log_first_render_freshness()`를 넣어 `UISnapshotFreshness`를 남기게
했다. 그런데 이 프로세스는 `mlog.setup()`을 **한 번도 부른 적이 없어서** `_logger`에 핸들러가
없었고, 그 줄들은 사흘간 전부 허공으로 갔다. UI 관련 이상점을 화면 캡처로만 쫓아야 했던 이유다.

## 왜 `NestedSessionStart`가 아닌가

2026-08-20 장중 계획은 이 변경이 `starts_by_process`를 오염시킬 수 있다며 `NESTED_SESSION_ENV`
배선을 선행 조건으로 걸었다. 확인해 보니 그 경로는 성립하지 않는다:

    ops/integrity_report.log_paths_for()  →  l1_daily · g2_paper 만 돌려준다
    ops/observation_gaps.parse_ui_starts()→  "Uvicorn server started" 줄을 센다

`analyze_logs()`는 UI 로그를 아예 읽지 않고, UI 기동 수는 `SessionStart`가 아니라 Uvicorn 줄로
센다. 그래서 UI는 배치 단계가 아니라 **독립 프로세스**로서 `SessionStart`를 남기는 것이 맞다 —
그 줄이 싣는 `git_sha`·`source_mtime_max`가 "화면이 어느 코드로 도는가"(2026-08-05 P0-1)의
유일한 1차 증거다. 아래 마지막 테스트가 그 전제를 고정한다.
"""

from __future__ import annotations

import io
import json
from datetime import date


def _capture_setup() -> list[dict]:
    """`_logging_ready()`를 두 번 부르고 stdout으로 나간 JSON 줄을 모은다."""
    import sys

    from messiah.ui import app

    buffer = io.StringIO()
    original = sys.stdout
    sys.stdout = buffer
    try:
        app._logging_ready.clear()  # st.cache_resource 캐시 초기화
        assert app._logging_ready() is True
        # **Streamlit은 매 상호작용·매 5초 fragment마다 스크립트를 통째로 재실행한다.**
        # 가드가 없으면 `setup()`이 매번 돌아 `SessionStart`가 렌더 수만큼 쌓인다.
        assert app._logging_ready() is True
    finally:
        sys.stdout = original
    return [
        json.loads(line) for line in buffer.getvalue().splitlines() if line.strip().startswith("{")
    ]


def test_session_start_is_written_exactly_once_across_reruns() -> None:
    records = _capture_setup()
    starts = [r for r in records if r["tag"] == "SessionStart"]
    assert len(starts) == 1, f"재실행마다 찍히면 안 된다 — {len(starts)}건 나왔다"
    assert starts[0]["instance_id"] == "command-center-ui"


def test_session_start_carries_the_code_identity() -> None:
    """화면이 어느 코드로 도는지를 말하는 유일한 1차 증거다 (2026-08-05 P0-1 · G-C)."""
    starts = [r for r in _capture_setup() if r["tag"] == "SessionStart"]
    assert starts[0]["git_sha"]
    assert "source_mtime_max" in starts[0]


def test_freshness_lines_actually_reach_stdout() -> None:
    """`setup()` 없이는 `mlog.log()`가 조용히 버려진다 — 그것이 D-2의 실체였다."""
    import sys

    from messiah.core import logging as mlog
    from messiah.ui import app

    app._logging_ready.clear()
    buffer = io.StringIO()
    original = sys.stdout
    sys.stdout = buffer
    try:
        app._logging_ready()
        mlog.log(
            "UISnapshotFreshness",
            "첫 렌더",
            mode="LIVE",
            topics={},
            chart_date="2026-08-20",
            chart_lag_calendar_days=0,
        )
    finally:
        sys.stdout = original
    tags = [
        json.loads(line)["tag"]
        for line in buffer.getvalue().splitlines()
        if line.strip().startswith("{")
    ]
    assert "UISnapshotFreshness" in tags


def test_ui_session_start_does_not_enter_the_restart_count() -> None:
    """이 변경이 재기동 계수를 오염시키지 않는다는 **전제**를 고정한다.

    전제가 깨지면(예: `log_paths_for`가 ui 로그를 포함하게 되면) 여기서 잡힌다 —
    안 잡히면 2026-08-19 1-2와 같은 계열의 오판이 조용히 새로 생긴다.
    """
    from messiah.ops.integrity_report import log_paths_for
    from messiah.ops.observation_gaps import parse_ui_starts

    paths = log_paths_for(date(2026, 8, 20))
    assert set(paths) == {"l1_daily", "g2_paper"}, "UI 로그가 analyze_logs 시야에 들어오면 안 된다"

    # UI 기동 수는 Uvicorn 줄로 센다 — 구조화 `SessionStart`는 세지 않는다.
    text = (
        '{"ts": "2026-08-20T08:20:22+09:00", "level": "INFO", "tag": "SessionStart"}\n'
        "2026-08-20 08:20:22.440 Uvicorn server started on :::8511\n"
    )
    assert parse_ui_starts(text) == ["08:20:22"]
