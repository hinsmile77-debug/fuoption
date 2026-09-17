"""장후 배치가 자식의 표준오류를 자식이 쓴 코덱으로 읽는가 — 2026-09-17 F-115.

2026-09-14~09-17 나흘간 `logs/postmarket_<날짜>.log`의 롤 안내문 한 줄이 사람이 읽을 수
없는 글자로 저장됐다. `_run_step()`의 `subprocess.run(..., text=True)`에 `encoding=`이
없어, 자식이 UTF-8로 쓴 바이트를 부모가 로캘(한국어 Windows = cp949)로 해독했기 때문이다.

여기서 고정하는 것은 둘이다: **계약**(자식 코덱을 명시해서 부른다)과 **왕복**(실제 자식
프로세스를 띄워 한글·em dash가 원문 그대로 돌아온다). 계약 쪽이 따로 필요한 이유는 UTF-8
로캘 기계에서는 왕복만으로 이 버그를 못 잡기 때문이다 — 버그가 로캘 의존이라 테스트가
로캘에 기대면 그 기계에서만 초록이 된다.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import run_postmarket  # noqa: E402

# 2026-09-14~09-17 실제로 깨졌던 그 줄 (`data/backfill.compute_roll_offsets`)
_ROLL_LINE = (
    "A05608→A05609 (2026-08-13) basis 측정 불가 — 겹침 하루가 없어 "
    "offset 0으로 잇는다(scripts/run_roll_overlap.py --date 2026-08-13)"
)


def test_child_stderr_is_decoded_as_utf8(monkeypatch):
    """계약 — 로캘이 무엇이든 UTF-8로 해독한다고 명시해서 부른다."""
    seen: dict[str, object] = {}

    def _spy(*args, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(args=["python"], returncode=0, stderr="")

    monkeypatch.setattr(run_postmarket.subprocess, "run", _spy)
    run_postmarket._run_step(run_postmarket.Step("2/7 재합성", ["python", "y.py"]))

    assert seen["encoding"] == "utf-8"
    # 코덱을 맞춘 뒤에도 남는 깨짐은 **조용히 사라지지 않고 자국으로 남아야** 한다
    # (금지계명 12 · `core/logging.setup`의 2026-08-21 F-1과 같은 취지).
    assert seen["errors"] == "backslashreplace"
    assert seen["text"] is True


def test_real_child_utf8_stderr_round_trips(capsys):
    """왕복 — 실제 자식이 쓴 한글·화살표·em dash가 원문 그대로 다시 나간다."""
    child = (
        "import sys; sys.stderr.reconfigure(encoding='utf-8'); "
        f"print({_ROLL_LINE!r}, file=sys.stderr)"
    )
    result = run_postmarket._run_step(
        run_postmarket.Step("test 왕복", [sys.executable, "-c", child])
    )

    assert result.ok is True
    echoed = capsys.readouterr().err
    assert _ROLL_LINE in echoed
    # 손상의 지문 — 로캘 오디코딩이 남기던 `\xNN` 리터럴이 하나도 없어야 한다.
    assert r"\x" not in echoed


def test_classification_is_unchanged_by_the_encoding_fix(monkeypatch):
    """**판정 불변** — 코덱만 바꿨다. 세 갈래 분류(정상·발견·크래시)는 그대로다."""
    traceback_line = (
        "Traceback (most recent call last):\n"
        '  File "x.py", line 1, in main\n'
        "ValueError: 한글이 섞인 예외\n"
    )
    finding_step = run_postmarket.Step(
        "3/7 거래량 대조", ["python", "x.py"], one_means_finding=True
    )

    def _run(returncode: int, stderr: str = ""):
        monkeypatch.setattr(
            run_postmarket.subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(
                args=["python"], returncode=returncode, stderr=stderr
            ),
        )
        return run_postmarket._run_step(finding_step)

    assert (_run(0).ok, _run(0).finding) == (True, False)
    assert (_run(1).ok, _run(1).finding) == (True, True)
    crash = _run(1, traceback_line)
    assert (crash.ok, crash.finding) == (False, False)
