"""차트용 봉 시계열 읽기 — **부모(Command Center UI) 쪽** (2026-08-03 P0-1(b)).

`data/bar_export.py`를 자식 프로세스로 띄워 JSON을 받아 `BarSeries`로 만든다. 왜 이런
구조인지는 그 모듈의 docstring에 있다(요약: UI가 5거래일 연속 polars 네이티브 크래시로
죽어서, 파싱을 자식에게 넘겨 **크래시를 가둔다**).

## 이 모듈이 지켜야 하는 성질

**① polars를 임포트하지 않는다.** 이게 전부다. 이 파일이 어떤 경로로든 polars를 끌어오면
자식 프로세스로 미룬 의미가 사라진다 — 런타임은 여전히 부모에 로드되고, 크래시도 부모에서
난다. `tests/ui/test_bar_reader.py`가 이걸 직접 못박는다.

**② 자식의 죽음을 예외로 바꾼다.** access violation으로 죽은 자식은 부모에게 0이 아닌
종료 코드로 나타난다(Windows에선 `0xC0000005`를 부호 있는 정수로 해석한 `-1073741819`).
그걸 `BarExportError`로 올리면 `ui/app.py`의 기존 "읽기 실패 → 직전 성공본으로 버틴다"
경로에 그대로 흡수된다. **조용히 빈 시계열을 돌려주지 않는다**(L18) — 그러면 사고가
"데이터가 없는 날"로 위장된다.

**③ 무한정 기다리지 않는다.** 자식이 멈추면 부모의 렌더 스레드가 같이 멈춘다. 타임아웃을
걸어 최악의 경우에도 화면은 직전 값으로 살아남게 한다.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Sequence

from messiah.core.messages import Horizon
from messiah.data.bar_export import PAYLOAD_MARKER
from messiah.ui.bar_series import BarSeries

# 하루치 400행 남짓을 뽑는 데 프로세스 기동+polars 임포트가 약 0.8초(실측). 여유를 넉넉히
# 두되 무한은 아니게 — 이 시간을 넘기면 자식이 멈춘 것이므로 직전 성공본으로 버티는 게 맞다.
DEFAULT_TIMEOUT_SECONDS = 30.0

# Windows에서 자식이 access violation으로 죽었을 때의 종료 코드(0xC0000005를 부호 있는
# 32비트로 읽은 값). 이 값을 알아보면 로그에 "그냥 실패"가 아니라 **바로 그 크래시**라고
# 쓸 수 있다 — 다음 일일점검에서 `native_crashes`와 대조할 근거가 된다.
_ACCESS_VIOLATION_RETURNCODE = -1073741819


class BarExportError(RuntimeError):
    """자식 프로세스가 봉을 못 내놨다 — 크래시·타임아웃·깨진 출력 전부 포함."""


@dataclass(frozen=True, slots=True)
class _Completed:
    """`subprocess.run()` 결과 중 이 모듈이 쓰는 부분만 — 테스트 주입을 위한 최소 계약."""

    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[Sequence[str], float], _Completed]


def _run_subprocess(command: Sequence[str], timeout: float) -> _Completed:
    kwargs = {}
    if sys.platform == "win32":
        # 자식이 뜰 때마다 콘솔 창이 깜빡이는 걸 막는다 — LIVE 차트는 봉 마감마다 자식을
        # 띄우므로 이게 없으면 장중 내내 창이 명멸한다.
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    result = subprocess.run(  # noqa: S603 — 명령은 전부 이 모듈이 조립한다(사용자 입력 아님)
        list(command),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        **kwargs,
    )
    return _Completed(result.returncode, result.stdout or "", result.stderr or "")


def _describe_failure(result: _Completed) -> str:
    if result.returncode == _ACCESS_VIOLATION_RETURNCODE:
        # 5거래일 연속 UI를 죽인 바로 그 크래시가 이제 자식에서 났다는 뜻 — 화면은 살아있다.
        reason = "네이티브 크래시(access violation)"
    else:
        reason = f"종료 코드 {result.returncode}"
    tail = result.stderr.strip().splitlines()[-1:] or [""]
    return f"{reason}: {tail[0]}" if tail[0] else reason


def _payload_from_stdout(stdout: str) -> dict | None:
    """표식 뒤의 마지막 비어있지 않은 줄만 JSON으로 읽는다 — 라이브러리가 stdout에 흘린
    경고가 payload를 오염시키지 못하게 한다."""
    marker_at = stdout.rfind(PAYLOAD_MARKER)
    if marker_at < 0:
        raise BarExportError("자식 출력에 payload 표식이 없음 — 익스포터가 중간에 죽었을 수 있음")
    after_marker = stdout[marker_at + len(PAYLOAD_MARKER) :]
    lines = [line for line in after_marker.splitlines() if line.strip()]
    if not lines:
        raise BarExportError("자식이 payload 표식만 남기고 아무것도 안 뱉음")
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise BarExportError(f"자식 출력이 JSON이 아님: {exc}") from exc


def _series_from_payload(payload: dict) -> BarSeries:
    return BarSeries(
        x_kst=tuple(datetime.fromisoformat(ts) for ts in payload["x_kst"]),
        o_ticks=tuple(payload["o_ticks"]),
        h_ticks=tuple(payload["h_ticks"]),
        l_ticks=tuple(payload["l_ticks"]),
        c_ticks=tuple(payload["c_ticks"]),
    )


def read_day_series(
    bar_dir: Path,
    symbol: str,
    horizon: Horizon,
    day: date,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    runner: Runner = _run_subprocess,
    python_executable: str | None = None,
) -> BarSeries | None:
    """하루치 봉 스냅샷. 그날 데이터가 없으면 None.

    입력: bar_dir는 **절대 경로로 넘기는 것이 안전하다** — 자식의 작업 디렉터리를 지정하지
         않으므로 상대 경로는 부모와 자식의 cwd가 같다는 가정에 기댄다.
    실패 조건: `BarExportError` — 자식이 죽었거나(크래시 포함), 타임아웃이거나, 출력이
              깨졌을 때. 호출측(`ui/app.py`)은 이걸 "지금은 못 읽음"으로 다뤄 직전 성공본을
              쓴다. **빈 시계열로 뭉개지 않는다**(L18).
    """
    command = [
        python_executable or sys.executable,
        "-m",
        "messiah.data.bar_export",
        "--bar-dir",
        str(bar_dir),
        "--symbol",
        symbol,
        "--horizon",
        horizon.value,
        "--day",
        day.isoformat(),
    ]

    try:
        result = runner(command, timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        raise BarExportError(f"봉 익스포터 응답 없음({timeout_seconds:.0f}초 초과)") from exc
    except OSError as exc:
        raise BarExportError(f"봉 익스포터를 띄우지 못함: {exc}") from exc

    if result.returncode != 0:
        raise BarExportError(f"봉 익스포터 실패 — {_describe_failure(result)}")

    payload = _payload_from_stdout(result.stdout)
    if payload is None:
        return None  # 그날 데이터 없음 — 실패가 아니다
    try:
        return _series_from_payload(payload)
    except (KeyError, TypeError, ValueError) as exc:
        raise BarExportError(f"봉 payload 형태가 계약과 다름: {exc}") from exc
