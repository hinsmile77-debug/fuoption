"""네이티브 크래시 포렌식 무장 — `faulthandler` 상시화 (2026-08-03 일일점검 대응).

## 왜 이 모듈이 생겼나

Command Center UI가 **5거래일 연속 같은 fault offset**으로 죽었다
(`_polars_runtime.pyd` +0x083973c7, 0xc0000005 — 07-29 2건·07-30 6건·07-31 6건·08-03 2건).
그 5일 동안 세 번 "고쳤다"고 판정했고 세 번 재발했다. 매번 가설을 세워 고친 이유는 단순하다
— **증거가 없었다**. 네이티브 크래시는 파이썬 인터프리터를 거치지 않고 프로세스를 즉사시키므로
`logs/ui_*.log`에 traceback은커녕 한 줄도 안 남는다. 그래서 매번 정황(로그 시각, 커밋 시점,
동시에 찍힌 다른 에러)으로 범인을 추정할 수밖에 없었다.

`faulthandler`는 그 공백을 정확히 메운다. Windows에서 CPython의 faulthandler는 SEH 예외
핸들러를 걸어 **EXCEPTION_ACCESS_VIOLATION(0xc0000005)에도 동작**한다 — 즉 죽는 그 순간
파이썬 스택을 뱉는다. `all_threads=True`면 **모든 스레드의 스택**을 뱉으므로,
"polars 네이티브 호출에 동시에 들어간 스레드가 몇 개였나"라는 07-31 이후의 핵심 질문이
크래시 한 번으로 판정된다.

비용은 사실상 0이다(핸들러 등록뿐, 정상 경로에 오버헤드 없음). 그래서 "의심스러울 때만
켜는 디버그 도구"가 아니라 **상시 무장**으로 둔다 — 다음 크래시가 언제일지 모르는데 그때
켜져 있지 않으면 또 한 거래일을 증거 없이 보낸다.

## 어디에 뱉나

기본은 `sys.stderr`다. 이유는 배선이 이미 되어 있기 때문이다 — UI는 런처가
(`core/ui_launcher.py`) stdout/stderr를 `logs/ui_{date}.log`로 리다이렉트하고, l1/g2는
배치가 `2>&1`로 같은 일일 로그에 합친다. 즉 stderr로 뱉으면 **사람이 이미 매일 보는 파일**과
**무결성 리포트가 이미 수집하는 파일**(`ops/integrity_report.py`의 `log_paths_for()`)에
자동으로 들어간다. 별도 크래시 파일을 만들면 그 파일을 아무도 안 본다.

stderr가 `fileno()`를 못 주는 환경(pytest 캡처 등)에서만 `log_dir` 아래 전용 파일로
폴백한다 — faulthandler는 파일 객체가 아니라 **파일 디스크립터**를 잡아두기 때문에
fileno()가 없으면 애초에 등록이 안 된다.

주의: 이 모듈은 크래시를 **막지 않는다**. 진단 가능하게 만들 뿐이다. 크래시 자체를 무해하게
만드는 건 `ui/bar_reader.py`(봉 파싱을 자식 프로세스로 밀어내기)가 담당한다 — 둘은 같은
사고에 대한 서로 다른 축의 대응이라 어느 하나가 다른 하나를 대체하지 않는다.
"""

from __future__ import annotations

import faulthandler
import io
import sys
import threading
from contextlib import suppress
from pathlib import Path
from typing import IO, Any

_lock = threading.Lock()

# 무장 실패 설명의 접두사 — `is_armed()`의 판정 기준이자 로그 grep 키다. ASCII인 이유는
# 아래 마커 줄과 같다(자식 프로세스의 로케일 인코딩 문제).
UNAVAILABLE_PREFIX = "unavailable"

_armed_description: str | None = None
# faulthandler는 등록 시점의 **파일 디스크립터**만 붙잡아둔다 — 파이썬 파일 객체가 GC되면
# fd가 닫혀 크래시 순간에 쓸 곳이 사라진다. 그래서 폴백 파일 핸들을 모듈 전역으로 붙잡아
# 프로세스 수명 내내 살려둔다(일부러 닫지 않는다).
_fallback_handle: IO[Any] | None = None


def _usable_stream(stream: IO[Any] | None) -> IO[Any] | None:
    """faulthandler에 넘길 수 있는 스트림인지 — `fileno()`가 실제 fd를 줘야만 쓸 수 있다."""
    if stream is None:
        return None
    try:
        fileno = stream.fileno()
    except (AttributeError, OSError, io.UnsupportedOperation, ValueError):
        return None
    return stream if isinstance(fileno, int) and fileno >= 0 else None


def enable(
    *,
    tag: str,
    log_dir: Path = Path("logs"),
    stream: IO[Any] | None = None,
) -> str:
    """이 프로세스의 네이티브 크래시 덤프를 무장한다. 반환값은 덤프가 갈 곳의 설명.

    입력: tag는 폴백 파일명에만 쓰이는 프로세스 식별자(`"ui"`, `"l1_daily"` 등).
         stream을 주면 그것을 쓴다(테스트 주입점) — 안 주면 `sys.stderr`.
    계산: `faulthandler.enable(all_threads=True)`. 이미 무장했으면 아무것도 안 하고 기존
         설명을 그대로 돌려준다 — Streamlit은 재실행마다 스크립트를 통째로 다시 돌리므로
         (`ui/app.py`) 이 함수가 5초마다 불린다. 멱등성이 요구사항이다.
    실패 조건: 없다. 어떤 이유로든 무장에 실패하면 그 사실을 설명 문자열로 돌려주고 넘어간다
              — **포렌식 도구가 본 기능을 막으면 안 된다**(`core/docker_bootstrap.py`류의
              "부가 정보 실패가 본 기능을 막지 않는다" 원칙과 동일).
    """
    global _armed_description, _fallback_handle

    with _lock:
        if _armed_description is not None:
            return _armed_description

        target = _usable_stream(stream if stream is not None else sys.stderr)
        description: str
        if target is not None:
            description = "stderr"
        else:
            try:
                log_dir.mkdir(parents=True, exist_ok=True)
                path = log_dir / f"crash_{tag}.log"
                _fallback_handle = path.open("a", encoding="utf-8", buffering=1)
                target = _fallback_handle
                description = str(path)
            except OSError as exc:
                _armed_description = f"{UNAVAILABLE_PREFIX}: 로그 파일 열기 불가({exc})"
                return _armed_description

        try:
            faulthandler.enable(file=target, all_threads=True)
        except (RuntimeError, ValueError, OSError) as exc:
            _armed_description = f"{UNAVAILABLE_PREFIX}: {exc.__class__.__name__}({exc})"
            return _armed_description

        # 무장 사실을 **덤프가 갈 바로 그 스트림에** 한 줄 남긴다. 호출측이 각자 print하면
        # Streamlit처럼 스크립트를 반복 실행하는 환경에서 5초마다 같은 줄이 쌓이는데,
        # 여기서 남기면 멱등 가드 안쪽이라 프로세스당 정확히 한 번이 보장된다. 그리고 이
        # 줄이 없는 로그는 "무장 안 된 채로 돈 세션"이라는 뜻이 되어, 다음 크래시에
        # 덤프가 없을 때 원인을 바로 안다.
        #
        # **ASCII로만 쓴다**: UI 런처는 로그 파일을 `encoding="utf-8"`로 열지만
        # (`core/ui_launcher.py`), 자식 프로세스는 그 fd에 **자기 로케일 인코딩**(이 PC는
        # cp949)으로 쓴다 — 한글을 넣으면 utf-8 파일 안에 cp949 바이트가 섞여 깨진다.
        # 이건 사람이 읽는 문장이 아니라 기계 판독용 마커이므로 ASCII가 맞다.
        with suppress(Exception):  # noqa: BLE001 — 마커 실패가 무장을 취소시키면 안 됨
            print(f"[crash_forensics] armed tag={tag} target={description}", file=target)
            target.flush()

        _armed_description = description
        return description


def is_armed() -> bool:
    """무장 시도가 실제로 성공했는지 — 실패 설명은 `UNAVAILABLE_PREFIX`로 시작한다."""
    return _armed_description is not None and not _armed_description.startswith(UNAVAILABLE_PREFIX)


def _reset_for_tests() -> None:
    """테스트 전용 — 모듈 전역 상태를 지운다(멱등성 자체를 검증해야 하므로 필요)."""
    global _armed_description, _fallback_handle

    with _lock:
        faulthandler.disable()
        if _fallback_handle is not None:
            _fallback_handle.close()
            _fallback_handle = None
        _armed_description = None
