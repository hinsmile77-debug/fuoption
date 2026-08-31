# -*- coding: utf-8 -*-
"""[MW0601] 콘솔 출력이 인코딩 때문에 죽지 않게 한다 (F-81 · 대응 이상점 1-13).

## 왜 있는가

2026-08-31, 그날 1순위 사용자 조치로 지정된 `scripts/git_lock_guard.py --check` 를
한글 Windows 파워셸에서 실행했다가 이걸 받았다:

    File "scripts\\git_lock_guard.py", line 249, in main
        print(_fmt(info))
    UnicodeEncodeError: 'cp949' codec can't encode character '\\u2014'

죽은 자리가 **판정이 다 끝난 뒤의 출력 단계**였다. 도구는 답을 계산해 놓고 말하다
넘어졌고, 그 바람에 종료 코드가 판정값(0/2/3)이 아니라 예외값 1 로 나갔다.
이 rc 로 분기하려던 상위 절차(F-78)의 분기가 정의되지 않게 된다.

## 왜 이 저장소에서 잘 안 드러났는가

배치는 출력을 파일로 흘린다 — `run_postmarket.bat` 의 `Out-File -Encoding utf8`.
즉 **파일로 나가는 경로는 원래 UTF-8 이라 살아남았고, 콘솔로 직접 찍는 경로만
노출된다.** 사람이 손으로 스크립트를 돌리는 순간에만 터지므로 몇 달을 조용히 지났다.

## 왜 `errors="replace"` 인가

재구성이 어떤 이유로든 부분적으로만 먹는 환경에서도 **판정 결과는 나와야 한다.**
글자가 깨지는 것은 답이 없는 것보다 낫다. 이 모듈의 목적은 예쁜 출력이 아니라
**출력 단계가 판정을 잡아먹지 않게 하는 것**이다.

## 쓰는 법

`main()` 의 **첫 실행문**으로 부른다. argparse 조립이나 판정보다 뒤에 있으면
그 사이에서 나는 출력(사용법 · 오류 메시지)이 보호되지 않는다.

    from messiah.core.console import ensure_utf8_console

    def main() -> int:
        ensure_utf8_console()
        ...

`scripts/git_lock_guard.py` 는 **이 모듈을 쓰지 않는다** — 그 파일은 py3.7/py3.10
양쪽에서 도는 의존성 없는 단일 파일로 유지해야 하고 복사본이 두 저장소에 있다.
같은 로직을 그 안에 자기 것으로 갖고 있으며, 그 중복은 의도된 것이다.
"""

from __future__ import annotations

import sys
from typing import IO, Any

__all__ = ["ensure_utf8_console"]


def ensure_utf8_console(*streams: IO[Any]) -> None:
    """표준 출력·오류를 UTF-8 로 재구성한다. 실패해도 예외를 올리지 않는다.

    Args:
        streams: 재구성할 스트림. 생략하면 `sys.stdout` 과 `sys.stderr`.

    `hasattr` 가드를 두는 이유: `reconfigure()` 는 Python 3.7+ 의
    `io.TextIOWrapper` 메서드다. 파이프나 캡처 객체로 감싸인 스트림
    (pytest 의 `capsys`, 일부 CI 러너)에는 이 메서드가 없을 수 있다.
    없으면 조용히 넘어간다 — 호출부는 출력 문자열 자체를 ASCII 로 두는
    2차 방어를 함께 갖는 것을 전제한다.
    """
    for stream in streams or (sys.stdout, sys.stderr):
        try:
            reconfigure = getattr(stream, "reconfigure", None)
            if reconfigure is not None:
                reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            # 재구성 실패가 호출부의 본업을 막아서는 안 된다.
            continue
