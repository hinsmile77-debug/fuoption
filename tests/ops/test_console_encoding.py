# -*- coding: utf-8 -*-
"""[MW0601] 운영 스크립트가 콘솔 인코딩 때문에 죽지 않는다 (F-81 · 대응 이상점 1-13).

## 왜 있는가

2026-08-31, 사용자가 그날 1순위 조치로 지정된 `scripts/git_lock_guard.py --check` 를
한글 Windows 파워셸에서 실행했다가 이걸 받았다:

    File "scripts\\git_lock_guard.py", line 249, in main
        print(_fmt(info))
    UnicodeEncodeError: 'cp949' codec can't encode character '\\u2014'

죽은 자리가 **판정이 다 끝난 뒤의 출력 단계**였다. 즉 도구는 답을 계산해 놓고
말하다 넘어졌다. 그 결과 종료 코드가 판정값(0 정상 / 2 스테일 / 3 판정보류)이 아니라
예외값 1 로 나가서, 이 rc 로 분기하려던 상위 절차(F-78)의 분기가 정의되지 않는다.

## 무엇을 지키는가

방어가 2중이고 **둘 다** 검사한다.

1. `_ensure_utf8_console()` 이 있고 `main()` 이 그것을 **가장 먼저** 부른다.
2. 그것이 없어도(구형 파이썬 · 파이프로 감싸인 스트림) **출력 문자열 자체가
   cp949 로 인코딩된다.** 이 두 번째가 본질이다 — 1번은 환경에 의존하고 2번은 안 한다.
"""

from __future__ import unicode_literals

import io
import os
import re
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GUARD = os.path.join(REPO_ROOT, "scripts", "git_lock_guard.py")


def _src(path):
    return io.open(path, encoding="utf-8").read()


# --------------------------------------------------------------------------
# 1차 방어 — 스트림 재구성이 있고, main() 이 그것을 가장 먼저 부른다
# --------------------------------------------------------------------------


def test_guard_has_console_reconfigure():
    """`_ensure_utf8_console()` 이 정의돼 있고 utf-8 · errors=replace 로 재구성한다."""
    s = _src(GUARD)
    assert "def _ensure_utf8_console(" in s, "재구성 헬퍼가 없다"
    assert 'encoding="utf-8"' in s, "utf-8 로 재구성하지 않는다"
    assert 'errors="replace"' in s, (
        "errors=replace 가 없다 — 재구성이 부분적으로만 먹는 환경에서 "
        "판정 결과가 통째로 사라진다"
    )
    assert (
        'hasattr(stream, "reconfigure")' in s
    ), "hasattr 가드가 없다 — reconfigure() 없는 스트림(py3.6 · 파이프)에서 터진다"


def test_guard_main_calls_reconfigure_first():
    """`main()` 의 **첫 실행문**이 재구성이다.

    argparse 조립이나 판정보다 뒤에 있으면, 그 사이에서 나는 출력이 보호되지 않는다.
    """
    s = _src(GUARD)
    m = re.search(r"def main\(argv=None\):\n(.*?)\n\n\nif __name__", s, re.S)
    assert m, "main() 을 찾지 못했다"
    body = [ln for ln in m.group(1).splitlines() if ln.strip() and not ln.strip().startswith("#")]
    assert body, "main() 본문이 비었다"
    assert "_ensure_utf8_console()" in body[0], (
        "main() 첫 실행문이 _ensure_utf8_console() 이 아니다 — 실제 첫 줄: %r" % body[0]
    )


# --------------------------------------------------------------------------
# 2차 방어 — 재구성이 없어도 출력 문자열이 cp949 로 인코딩된다 (본질)
# --------------------------------------------------------------------------


def _cp949_offenders(text):
    """cp949 로 인코딩 못 하는 문자들."""
    bad = set()
    for ch in text:
        try:
            ch.encode("cp949")
        except (UnicodeEncodeError, LookupError):
            bad.add(ch)
    return bad


@pytest.mark.parametrize(
    "verdict_builder",
    [
        # inspect() 가 만드는 판정 문구 전량 — 실제 코드와 같은 리터럴을 쓴다.
        lambda: "git 저장소 아님",
        lambda: "정상 - 락 없음",
        lambda: "락 stat 실패: %s" % OSError("boom"),
        lambda: "크기 %s바이트(0 아님 - 인덱스 쓰기가 진행됐다)" % 12,
        lambda: "나이 %.0f초 <= 임계 %d초(아직 실행 중일 수 있다)" % (3.0, 600),
        lambda: "git 프로세스 **미측정**(0으로 간주하지 않는다)",
        lambda: "git 프로세스 %d개 실행 중" % 2,
        lambda: "판정보류 - " + " / ".join(["a", "b"]),
        lambda: "스테일 확정 - 0바이트 · %.1f시간 · git 프로세스 0개 "
        "-> 이 저장소는 커밋 불가 상태다" % 7.1,
        lambda: "회수 취소 - 판정 직후 크기가 %s바이트로 변했다" % 40,
        lambda: "회수 실패: %s" % OSError("boom"),
        lambda: "회수 완료 - " + "스테일 확정 - 0바이트",
    ],
)
def test_verdict_strings_encode_in_cp949(verdict_builder):
    """모든 판정 문구가 한글 콘솔 기본 인코딩에서 출력 가능하다."""
    text = verdict_builder()
    bad = _cp949_offenders(text)
    assert not bad, "cp949 로 못 찍는 문자 %r 가 판정 문구에 있다: %s" % (sorted(bad), text)


def test_guard_printed_literals_encode_in_cp949():
    """`print(...)` 로 나가는 리터럴과 판정 문구를 소스에서 훑어 전수 검사한다.

    주석·docstring 은 파일 인코딩(utf-8)으로만 존재하고 출력되지 않으므로 제외한다.
    """
    offenders = []
    for i, line in enumerate(_src(GUARD).splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
            continue
        is_output = (
            'verdict"] =' in line
            or "fails.append(" in line
            or "print(" in line
            or stripped.startswith('"')
        )
        if not is_output:
            continue
        bad = _cp949_offenders(line)
        if bad:
            offenders.append((i, sorted(bad), stripped[:70]))
    assert not offenders, "cp949 로 못 찍는 출력 리터럴이 남아 있다: %r" % offenders


# --------------------------------------------------------------------------
# 실환경 재현 — 자식 프로세스를 cp949 로 묶어 돌린다 (이 테스트가 본체다)
# --------------------------------------------------------------------------


def test_guard_runs_under_cp949_stdout():
    """`PYTHONIOENCODING=cp949` 로 자식 프로세스를 돌려 **rc 가 판정값으로 나오는지** 본다.

    이것이 2026-08-31 사고의 정확한 재현이다. 기대 rc 는 0(정상) · 2(스테일) ·
    3(판정보류) 중 하나이며, **1(실행 오류)이면 회귀**다.
    """
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "cp949"
    env.pop("PYTHONUTF8", None)  # -X utf8 우회가 결과를 가리지 않게 한다
    p = subprocess.Popen(
        [sys.executable, GUARD, "--check", "--repo", REPO_ROOT],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=REPO_ROOT,
    )
    out, err = p.communicate(timeout=60)
    stderr = err.decode("utf-8", "replace")
    assert "UnicodeEncodeError" not in stderr, "cp949 출력에서 여전히 죽는다:\n%s" % stderr
    assert p.returncode in (0, 2, 3), (
        "종료 코드가 판정값(0/2/3)이 아니다: rc=%s\nstdout=%r\nstderr=%s"
        % (p.returncode, out[:400], stderr)
    )


def test_guard_json_mode_runs_under_cp949():
    """`--json` 경로도 같은 자리에서 죽었다(`ensure_ascii=False`). 그쪽도 검사한다."""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "cp949"
    env.pop("PYTHONUTF8", None)
    p = subprocess.Popen(
        [sys.executable, GUARD, "--check", "--json", "--repo", REPO_ROOT],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=REPO_ROOT,
    )
    out, err = p.communicate(timeout=60)
    stderr = err.decode("utf-8", "replace")
    assert "UnicodeEncodeError" not in stderr, "--json 경로가 cp949 에서 죽는다:\n%s" % stderr
    assert p.returncode in (0, 2, 3), "rc=%s\nstderr=%s" % (p.returncode, stderr)


# --------------------------------------------------------------------------
# 회귀 방지 — scripts/ 하위 신규 스크립트가 같은 함정에 빠지지 않게 한다
# --------------------------------------------------------------------------

#: 이미 알려진 미보호 스크립트. **줄이기만 하고 늘리지 않는다.**
#: 새 스크립트를 여기 추가하려거든 그 대신 _ensure_utf8_console() 을 붙일 것.
_KNOWN_UNPROTECTED = set()


def _scripts_printing_nonascii():
    """`scripts/` 에서 cp949 미지원 문자를 print 경로로 내보내며 재구성이 없는 파일."""
    found = []
    scripts_dir = os.path.join(REPO_ROOT, "scripts")
    for name in sorted(os.listdir(scripts_dir)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(scripts_dir, name)
        try:
            s = _src(path)
        except Exception:
            continue
        # 보호된 것으로 인정하는 세 형태:
        #   ① 공용 헬퍼 호출  ② 자체 재구성(git_lock_guard 처럼 의존성 없는 단일 파일)
        #   ③ 환경변수로 지정
        if "ensure_utf8_console(" in s or "reconfigure(" in s or "PYTHONIOENCODING" in s:
            continue
        for line in s.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""'):
                continue
            if "print(" not in line:
                continue
            if _cp949_offenders(line):
                found.append(name)
                break
    return set(found)


def test_no_new_unprotected_console_scripts():
    """새로 생긴 미보호 스크립트가 없다.

    이 테스트가 깨지면 둘 중 하나다 — 새 스크립트에 재구성을 붙이거나,
    출력에서 cp949 미지원 문자를 빼거나. **목록에 추가하는 것은 답이 아니다.**
    """
    new = _scripts_printing_nonascii() - _KNOWN_UNPROTECTED
    assert not new, (
        "cp949 콘솔에서 죽을 수 있는 스크립트가 새로 생겼다: %s\n"
        "→ _ensure_utf8_console() 을 붙이거나 출력 문자를 ASCII 로 바꿀 것" % sorted(new)
    )
