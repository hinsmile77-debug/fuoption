"""기동 창 거절이 Docker·자가점검 **뒤에** 있었다 — 2026-08-20 F-D.

2026-08-17 F-3이 비거래일 게이트에 대해 이미 한 이동을, 기동 창 게이트에는 안 했다.
그래서 판정 자리가 `main()` 안, 즉 `_ensure_docker_ready()`·`_run_self_check()`가 **이미
다 돈 뒤**였다.

부팅 트리거는 **매일** 발화한다(2026-08-20 아침 06:42:31 실측 — 그 7ms 뒤에 거절됐다).
즉 거절되는 날마다 Docker 기동 21초 + self_check 14항목이 **두 프로세스분** 헛돌았고,
그것이 매일 반복됐다.

## 여기서 지키는 두 가지

1. **순서** — 게이트가 Docker·self_check보다 앞이다. `test_non_trading_day_gate.py`의
   같은 이름 테스트와 같은 방식(소스 순서)으로 본다. 진짜 스크립트를 서브프로세스로 돌리면
   게이트가 깨진 채일 때 테스트가 실제 수집을 띄운다.

2. **종료 코드 분기가 함께 왔는가** — 이것이 이 이동의 유일한 실질 위험이다.
   2026-08-10에 이 경로가 조용히 0으로 끝나 스케줄러에 `LastTaskResult=0`(성공)으로 남았고,
   그날 오전이 통째로 사라지는 동안 모든 계기가 정상이라고 말했다. 정시 트리거 거절은
   `REFUSED_EXIT_CODE`(2)여야 하고, 부팅 트리거 거절은 0이어야 한다(그건 실패가 아니다).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
_ENTRYPOINTS = ["run_l1_daily.py", "run_g2_paper_trading.py"]


def _main_block(script: str) -> str:
    text = (_SCRIPTS / script).read_text(encoding="utf-8")
    return text.split('if __name__ == "__main__":', 1)[1]


@pytest.mark.parametrize("script", _ENTRYPOINTS)
def test_launch_window_gate_runs_before_docker_and_self_check(script: str) -> None:
    body = _main_block(script)
    gate = body.index("_refuse_outside_launch_window")
    assert gate < body.index(
        "_ensure_docker_ready"
    ), "기동 창 밖인데 Docker를 21초 띄우는 것은 매일 반복되는 순수 낭비다"
    assert gate < body.index("_run_self_check"), "거절할 기동에 자가점검 14항목을 돌릴 이유가 없다"


@pytest.mark.parametrize("script", _ENTRYPOINTS)
def test_non_trading_day_gate_still_comes_first(script: str) -> None:
    """휴장일 판정이 여전히 맨 앞이다 — 휴장일엔 기동 창 자체를 물을 필요가 없다."""
    body = _main_block(script)
    assert body.index("non_trading_day_reason") < body.index("_refuse_outside_launch_window")


@pytest.mark.parametrize("script", _ENTRYPOINTS)
def test_scheduled_refusal_still_exits_two(script: str) -> None:
    """**이 이동의 유일한 실질 위험** — 종료 코드 분기를 빠뜨리면 2026-08-10 P0가 재발한다."""
    text = (_SCRIPTS / script).read_text(encoding="utf-8")
    gate_fn = text.split("def _refuse_outside_launch_window", 1)[1].split("\nif __name__", 1)[0]
    assert (
        "refused_a_scheduled_launch()" in gate_fn
    ), "정시 트리거 거절과 부팅 트리거 거절을 안 가르면 스케줄러가 오전 내내 성공이라 기록한다"
    assert re.search(r"raise SystemExit\(session_guard\.REFUSED_EXIT_CODE\)", gate_fn)
    # 부팅 트리거 거절은 0이다 — 그건 실패가 아니다(비거래일 종료와 같은 판단).
    assert re.search(r"raise SystemExit\(0\)", gate_fn)


@pytest.mark.parametrize("script", _ENTRYPOINTS)
def test_refusal_still_leaves_the_structured_tag(script: str) -> None:
    """`LaunchWindowRefused`가 없으면 무결성 리포트가 그 `SessionStart`를 기동으로 센다.

    2026-08-07이 그랬다 — 재기동 1회 + 관측 공백 73분 + 전 계열 머리 구멍이 전부 오탐이었다.
    로깅 무장이 태그보다 **먼저** 와야 그 줄이 파일에 실제로 들어간다.
    """
    text = (_SCRIPTS / script).read_text(encoding="utf-8")
    gate_fn = text.split("def _refuse_outside_launch_window", 1)[1].split("\nif __name__", 1)[0]
    assert "_arm_forensics_and_logging" in gate_fn
    assert gate_fn.index("_arm_forensics_and_logging") < gate_fn.index("LaunchWindowRefused")


@pytest.mark.parametrize("script", _ENTRYPOINTS)
def test_main_no_longer_repeats_the_verdict(script: str) -> None:
    """같은 질문을 두 곳에서 하면 둘이 갈린다 — 2026-08-20 장전 1-3이 그 사례다."""
    text = (_SCRIPTS / script).read_text(encoding="utf-8")
    after = text.split("async def main(", 1)[1]
    # `main()` **본문만** 자른다 — 다음 최상위 정의(= 새로 만든 게이트 함수)까지 포함하면
    # 옮겨 놓은 판정을 "안 옮겼다"고 읽는다.
    cut = re.search(r"\n(?:async )?def ", after)
    main_body = after[: cut.start()] if cut else after
    assert "launch_window_verdict" not in main_body
