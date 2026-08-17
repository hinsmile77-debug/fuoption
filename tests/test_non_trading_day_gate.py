"""비거래일 게이트 — "기동해도 운영하지 않는다"를 코드가 강제하는가 (2026-08-17 F-1/F-2/F-3).

## 이 파일이 지키는 사실

2026-08-15(토)·16(일)·17(광복절 대체공휴일) 사흘의 실측이 이 테스트들의 근거다:

    · 진입점은 휴장 판정을 `main()` **안**에서 했다 — Docker 21초 기동 + self_check 14항목이
      먼저 두 벌 돌았다(부팅 트리거 + 정시 트리거).
    · 휴장 조기 종료가 `print()` 한 줄이라 `SessionEnd`가 사흘간 0건이었다. 그래서 일일점검이
      그 사흘을 "중복 기동 + 비정상 종료 의심"으로 읽었다.
    · 장후 배치에는 휴장 가드가 아예 없었다 — 그날 1분봉 부재를 "조회 대상 불일치"(ERROR +
      exit 3)로 읽었다. 같은 코드의 안내문은 "휴장일이면 정상이다"라고 적혀 있었다.

## 왜 스크립트를 서브프로세스로 안 돌리나

`run_l1_daily.py`의 `__main__`은 Docker와 KIS를 건드린다 — 게이트가 **그 앞에** 있다는 것이
바로 검증 대상이므로, 게이트가 깨진 채로 이 테스트를 돌리면 테스트가 진짜 수집을 띄운다.
그래서 게이트의 판정 함수(`session_guard`)를 직접 검사하고, 진입점이 그 함수를 **Docker보다
먼저** 부르는지는 소스 순서로 확인한다(`test_entrypoints_gate_before_docker`).
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from messiah.core import logging as mlog
from messiah.core.event_calendar import EventCalendar
from messiah.ops import session_guard

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"

# 평일 하나를 휴장일로 등재한 달력. 2026-08-17은 실제 광복절 대체공휴일이다.
_CAL = EventCalendar(frozenset({date(2026, 8, 17)}), years=frozenset({2026}))


# ---------------------------------------------------------------- 판정


def test_trading_day_returns_none() -> None:
    assert session_guard.non_trading_day_reason(day=date(2026, 8, 18), calendar=_CAL) is None


def test_registered_holiday_names_the_calendar() -> None:
    reason = session_guard.non_trading_day_reason(day=date(2026, 8, 17), calendar=_CAL)
    assert reason is not None
    assert "휴장일" in reason
    # 달력이 틀렸을 때 사람이 어디를 볼지 문장이 알려줘야 한다.
    assert "krx_holidays.yaml" in reason


def test_weekend_is_worded_differently_from_a_holiday() -> None:
    """주말과 등재 휴장일을 문장에서 가른다 — 출처가 다르다(계산 vs 사람이 확인한 사실).

    섞어 적으면 로그를 읽는 사람이 "달력이 맞았는가"를 물을 수 없다.
    """
    saturday = session_guard.non_trading_day_reason(day=date(2026, 8, 15), calendar=_CAL)
    sunday = session_guard.non_trading_day_reason(day=date(2026, 8, 16), calendar=_CAL)
    assert saturday is not None and "주말" in saturday
    assert sunday is not None and "주말" in sunday
    assert "krx_holidays.yaml" not in saturday  # 주말은 파일이 아니라 계산이다


def test_unknown_year_folds_to_trading_day(capsys: pytest.CaptureFixture[str]) -> None:
    """**판정 불가는 「거래일」로 접는다** — 비대칭이 그 방향이다.

    미등재 연도에 예외를 올리면 2027년 첫 거래일 수집이 달력 미갱신 하나로 통째로 죽는다.
    그 손실(체결틱·수급·옵션체인 영구 소실)이 휴장일 하루 적재보다 훨씬 비싸다.

    조용히 접지는 않는다 — 그 사실이 표준출력에 남는다(L18).
    """
    assert session_guard.non_trading_day_reason(day=date(2027, 1, 4), calendar=_CAL) is None
    assert "판정 불가" in capsys.readouterr().out


def test_unreadable_calendar_folds_to_trading_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """달력 파일이 없어도 게이트가 수집을 막지 않는다."""
    monkeypatch.chdir(tmp_path)  # `EventCalendar.from_file()`은 상대 경로를 쓴다
    assert session_guard.non_trading_day_reason(day=date(2026, 8, 17)) is None
    assert "판정 불가" in capsys.readouterr().out


# ---------------------------------------------------------------- 종료 마커


def test_announce_emits_session_end_with_machine_readable_reason(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`msg`는 사람용, `reason`은 기계용 — 그 구분이 이 마커의 존재 이유다.

    새 태그를 만들지 않는다: "끝났다"를 세는 소비처(`_abnormal_exits`, `task_exit_codes`,
    다이제스트 §2)가 전부 알아야만 맞고, 하나만 모르면 그 소비처에서 이 날은 비정상 종료다.
    """
    # 마커는 구조화 로그로 나간다 — 호출측이 이미 `mlog.setup()`을 끝낸 상태를 전제한다
    # (세 진입점 모두 `_arm_forensics_and_logging()`/`mlog.setup()`이 먼저다).
    mlog.setup("test-instance")
    session_guard.announce_non_trading_day("l1_daily", "2026-08-17(월)은 KRX 휴장일")
    out = capsys.readouterr().out
    assert '"tag": "SessionEnd"' in out
    assert f'"reason": "{session_guard.NON_TRADING_DAY_REASON}"' in out
    assert '"process": "l1_daily"' in out
    assert "운영 생략" in out


# ---------------------------------------------------------------- 진입점 배치 (F-3)


@pytest.mark.parametrize("script", ["run_l1_daily.py", "run_g2_paper_trading.py"])
def test_entrypoints_gate_before_docker(script: str) -> None:
    """게이트가 `_ensure_docker_ready()`·`_run_self_check()`보다 **앞**이어야 한다.

    2026-07-27부터 `run_l1_daily.py` docstring은 *"휴장일이면 self_check조차 실행하지 않고
    즉시 종료"* 라고 선언했지만 실제 판정은 그 뒤에 있었다 — 선언과 코드가 4주 가까이
    어긋난 채였고, 순서는 눈으로 읽어야만 보이는 종류의 사실이라 아무도 안 봤다.
    """
    text = (_SCRIPTS / script).read_text(encoding="utf-8")
    body = text.split('if __name__ == "__main__":', 1)[1]
    gate = body.index("non_trading_day_reason")
    assert gate < body.index("_ensure_docker_ready"), "휴장일에 Docker를 띄우면 안 된다"
    assert gate < body.index("_run_self_check"), "휴장일에 self_check를 돌릴 이유가 없다"


@pytest.mark.parametrize("script", ["run_l1_daily.py", "run_g2_paper_trading.py"])
def test_entrypoints_exit_zero_on_non_trading_day(script: str) -> None:
    """종료 코드는 0이다 — 안 뜨는 것이 설계된 동작이므로 스케줄러에 실패로 남으면 안 된다."""
    body = (_SCRIPTS / script).read_text(encoding="utf-8").split('if __name__ == "__main__":', 1)[1]
    gate_block = body[body.index("non_trading_day_reason") :]
    assert re.search(r"raise SystemExit\(0\)", gate_block.split("_ensure_docker_ready")[0])


@pytest.mark.parametrize("script", ["run_l1_daily.py", "run_g2_paper_trading.py"])
def test_forensics_armed_even_on_non_trading_days(script: str) -> None:
    """휴장일에도 무장 마커를 남긴다 — 안 남기면 새 위양성을 만든다.

    `ops/crash_dumps.collect_crash_forensics()`는 그날 로그에 `CrashForensicsArmed`가 없으면
    *"그 세션은 네이티브 크래시가 나도 증거를 안 남긴다"* 로 찍는다. 없앤 위양성 자리에 다른
    위양성을 놓는 것은 고친 게 아니다.
    """
    body = (_SCRIPTS / script).read_text(encoding="utf-8").split('if __name__ == "__main__":', 1)[1]
    gate_block = body[body.index("non_trading_day_reason") : body.index("_ensure_docker_ready")]
    assert "_arm_forensics_and_logging" in gate_block


def test_postmarket_gates_before_symbol_resolution() -> None:
    """장후 배치의 게이트는 **심볼 해석보다 앞**이다 (F-2).

    비거래일에는 조회할 심볼 자체가 필요 없고, `--date`로 지난 휴장일을 가리킨 소급 실행에서도
    같은 순서가 맞는다. 그리고 `_has_day()`의 오조회 가드(2026-08-14 F-A)에 **도달하기 전에**
    걷어내야 한다 — 그 가드는 거래일의 진짜 오조회를 계속 잡아야 하므로 그대로 둔다.
    """
    text = (_SCRIPTS / "run_postmarket.py").read_text(encoding="utf-8")
    body = text.split("def main() -> int:", 1)[1]
    assert body.index("non_trading_day_reason") < body.index("_resolve_symbol(")
    # exit 3(조회 대상 불일치)은 살아 있어야 한다 — 거래일의 오조회는 여전히 실패다.
    assert "_SYMBOL_MISMATCH_EXIT_CODE" in body


def test_postmarket_no_longer_says_a_holiday_is_normal() -> None:
    """*"휴장일이면 정상이다"* 안내문은 삭제됐다.

    휴장일은 이제 그 분기에 도달하지 않는다. 문장을 남겨 두면 사람이 진짜 오조회를
    "휴장일이겠지"로 넘긴다 — 아는 사실을 판정에 쓰지 않고 각주로만 달아 둔 것이 2026-08-17에
    ERROR 한 건을 만든 원인이다.
    """
    text = (_SCRIPTS / "run_postmarket.py").read_text(encoding="utf-8")
    # 그 문장이 **사람에게 인쇄되던** 모양을 찾는다 — 삭제 사실을 설명하는 주석·docstring은
    # 남아 있어야 하므로(그게 왜 없어졌는지의 근거다) 문구 전체가 아니라 원문 그대로를 본다.
    assert "휴장일이면 정상이다. 아니라면" not in text
