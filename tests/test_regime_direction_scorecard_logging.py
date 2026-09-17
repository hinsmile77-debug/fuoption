"""국면별 방향 채점 도구도 구조화 로그를 내는가 — 2026-09-17 F-115.

`scripts/run_regime_direction_scorecard.py`는 2026-09-02 신설 이래 `core.logging.setup()`을
한 번도 부르지 않았다. 그래서 이 프로세스가 부르는 `data.backfill.compute_roll_offsets()`의
`RollBasisUnmeasured`(등록부 **WARNING**)는 파이썬 표준 `logging`의 `lastResort` 핸들러로
포맷 없이 표준오류에 떨어졌고, 09-02~09-17 장후 로그 어디에도 그 태그의 JSON 줄이 **0건**
이었다 — `tag_counts` 집계·`FixVerificationScoreboard`·자동 적신호가 이 신호를 원천적으로
못 봤다(R6). 2026-08-20 F-B(UI)와 같은 결함이다: 계기를 만들어 두고 배선을 안 했다.

세 가지를 고정한다 — 배선이 있는가 · 배선이 재기동 오탐을 만들지 않는가 · 배선 없이는
정말로 조용히 사라졌는가.
"""

from __future__ import annotations

import io
import json
import logging as stdlib_logging
import sys
from pathlib import Path

from messiah.core import logging as mlog

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
_SCORECARD = _SCRIPTS / "run_regime_direction_scorecard.py"


def _capture(fn) -> list[dict]:
    """`fn()`이 도는 동안 stdout으로 나간 JSON 줄만 모은다."""
    buffer = io.StringIO()
    original_handlers = list(mlog._logger.handlers)
    original_stdout = sys.stdout
    sys.stdout = buffer
    try:
        fn(buffer)
    finally:
        sys.stdout = original_stdout
        mlog._logger.handlers[:] = original_handlers
    return [
        json.loads(line) for line in buffer.getvalue().splitlines() if line.strip().startswith("{")
    ]


def test_scorecard_wires_structured_logging() -> None:
    """배선 자체 — `main()`이 `setup()`을 부른다. 이 줄이 빠진 것이 F-115의 절반이었다."""
    source = _SCORECARD.read_text(encoding="utf-8")
    assert "from messiah.core import logging as mlog" in source
    assert "mlog.setup(" in source


def test_nested_marker_keeps_the_batch_from_looking_like_a_restart(monkeypatch) -> None:
    """**판정 불변** — 배치 도구가 하나 늘어도 재기동 집계는 그대로다 (2026-08-14 F-13).

    `run_postmarket._run_step()`이 모든 자식에 `MESSIAH_NESTED_SESSION`을 세우고,
    `session_start()`가 그 환경변수만 보고 이름을 가른다 — 도구 목록을 따로 들고 있지
    않다. 그래서 5번째 도구에 `setup()`을 붙여도 `SessionStart`는 늘지 않는다.
    """
    monkeypatch.setenv(mlog.NESTED_SESSION_ENV, "1")
    records = _capture(lambda buf: mlog.setup("regime-direction", stream=buf))

    tags = [r["tag"] for r in records]
    assert "NestedSessionStart" in tags
    assert "SessionStart" not in tags


def test_roll_tag_is_invisible_without_setup_and_structured_with_it(monkeypatch) -> None:
    """배선 없이는 조용히 사라진다 — 그것이 09-02~09-17 태그 0건의 정체다."""
    monkeypatch.setenv(mlog.NESTED_SESSION_ENV, "1")

    def _without_setup(buf: io.StringIO) -> None:
        mlog._logger.handlers.clear()  # `setup()`을 한 번도 안 부른 프로세스의 상태
        mlog.log("RollBasisUnmeasured", "basis 측정 불가", outgoing="A05608", incoming="A05609")

    assert _capture(_without_setup) == []

    def _with_setup(buf: io.StringIO) -> None:
        mlog.setup("regime-direction", stream=buf)
        mlog.log("RollBasisUnmeasured", "basis 측정 불가", outgoing="A05608", incoming="A05609")

    roll = [r for r in _capture(_with_setup) if r["tag"] == "RollBasisUnmeasured"]
    assert len(roll) == 1
    # 등록부가 정한 심각도로 나가야 태그 기반 집계·경보가 성립한다 (R6).
    assert roll[0]["level"] == stdlib_logging.getLevelName(mlog.TAG_LEVELS["RollBasisUnmeasured"])
    assert roll[0]["outgoing"] == "A05608"
