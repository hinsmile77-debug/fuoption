"""faulthandler 덤프 수집 검증 (2026-08-03 고도화 D).

이 모듈의 가치는 덤프를 예쁘게 파싱하는 데 있지 않다 — **"크래시는 났는데 증거가 없다"를
말하는 것**에 있다. 5거래일 동안 그 상태로 세 번 잘못된 가설을 세웠는데, 그때는 증거가
없다는 사실조차 리포트에 안 나왔다. 검증도 거기에 무게를 둔다.
"""

from __future__ import annotations

import textwrap
from datetime import date
from pathlib import Path

from messiah.ops import crash_dumps
from messiah.ops.crash_dumps import collect_crash_forensics, parse_dumps

_DAY = date(2026, 8, 3)

# 2026-08-03에 실제로 채집한 덤프 형태 그대로(`core/crash_forensics.py` 실측 검증 출력).
_REAL_DUMP = textwrap.dedent(
    """\
    [crash_forensics] armed tag=ui target=stderr
    Windows fatal exception: access violation

    Thread 0x000029c4 (most recent call first):
      File "C:\\proj\\probe.py", line 6 in worker
      File "C:\\Python\\Lib\\threading.py", line 1010 in run

    Current thread 0x00003fd8 (most recent call first):
      File "C:\\Python\\Lib\\ctypes\\__init__.py", line 525 in string_at
      File "C:\\proj\\probe.py", line 10 in <module>
    """
)


def test_parses_kind_thread_count_and_crashing_frame():
    dumps = parse_dumps("ui", _REAL_DUMP)

    assert len(dumps) == 1
    dump = dumps[0]
    assert dump.kind == "access violation"
    # 스레드 수가 07-31 이후의 핵심 질문("동시에 polars에 들어간 스레드가 몇 개였나")의 답이다.
    assert dump.thread_count == 2
    # 죽은 스레드(`Current thread`)의 프레임만 남긴다 — 나머지는 개수만.
    assert dump.crashing_frames == ["__init__.py:525 string_at", "probe.py:10 <module>"]


def test_multiple_dumps_in_one_log_are_all_found():
    """UI는 하루에 여러 번 죽는다(07-30 6건, 07-31 6건) — 재기동해도 같은 로그 파일에
    이어 쓰이므로 한 파일에서 전부 뽑혀야 한다."""
    dumps = parse_dumps("ui", _REAL_DUMP + "\n" + _REAL_DUMP)

    assert len(dumps) == 2


def test_log_without_a_dump_yields_nothing():
    assert parse_dumps("ui", "Uvicorn server started on :::8511\n") == []


def test_crash_with_no_dump_is_reported_as_unexplainable(tmp_path: Path):
    """이 테스트가 고도화 D 전체의 요점이다.

    2026-08-03을 그대로 재현한다 — 네이티브 크래시 2건, 무장 마커 없음, 덤프 0건. 그날
    리포트는 "네이티브 크래시 2건"까지만 말하고 **원인을 밝힐 수단이 없다는 사실은 말하지
    않았다**. 그 침묵이 세 번의 오진을 낳았다.
    """
    (tmp_path / "ui_20260803.log").write_text(
        "Uvicorn server started on :::8511\n", encoding="utf-8"
    )

    forensics = collect_crash_forensics(
        _DAY, log_dir=tmp_path, native_crash_count=2, native_crashes_available=True
    )

    assert forensics.armed == {"ui": False}
    assert forensics.dumps == []
    joined = " | ".join(forensics.findings)
    assert "무장 마커 없음" in joined
    assert "faulthandler 덤프 0건" in joined


def test_armed_session_with_a_dump_has_no_unexplainable_finding(tmp_path: Path):
    """무장된 세션에서 크래시가 나면 덤프가 남는다 — 그건 사고지만 **설명 가능한** 사고다."""
    (tmp_path / "ui_20260803.log").write_text(_REAL_DUMP, encoding="utf-8")

    forensics = collect_crash_forensics(
        _DAY, log_dir=tmp_path, native_crash_count=1, native_crashes_available=True
    )

    assert forensics.armed == {"ui": True}
    assert len(forensics.dumps) == 1
    assert forensics.findings == []


def test_unarmed_session_is_flagged_even_without_any_crash(tmp_path: Path):
    """크래시가 없던 날에도 무장이 안 됐으면 말해야 한다 — 다음 크래시 때 또 증거가 없다.
    사고가 난 뒤에 "그때 무장돼 있었나"를 묻는 건 이미 늦다."""
    (tmp_path / "l1_daily_20260803.log").write_text("정상 종료.\n", encoding="utf-8")

    forensics = collect_crash_forensics(
        _DAY, log_dir=tmp_path, native_crash_count=0, native_crashes_available=True
    )

    assert any("무장 마커 없음" in f for f in forensics.findings)


def test_uncountable_crashes_do_not_trigger_the_no_dump_finding(tmp_path: Path):
    """크래시 집계 자체가 불가능한 환경(비 Windows)에서 "덤프 0건"을 사고로 올리면
    매일 헛경고가 뜬다 — 못 센 것과 0건은 다르다(L18)."""
    (tmp_path / "ui_20260803.log").write_text(
        "[crash_forensics] armed tag=ui target=stderr\n", encoding="utf-8"
    )

    forensics = collect_crash_forensics(
        _DAY, log_dir=tmp_path, native_crash_count=0, native_crashes_available=False
    )

    assert not any("덤프 0건" in f for f in forensics.findings)


# ------------------------------------------- 무장 마커 오탐 (2026-08-04 실측, 08-05 수정)


_PS_WRAPPED_LOG = r"""python.exe : [crash_forensics] armed tag=l1_daily target=stderr
At line:1 char:1
+ & '.venv\Scripts\python.exe' 'scripts\run_l1_daily.py' 2>&1 | ForEach ...
+ ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    + CategoryInfo          : NotSpecified: ([crash_forensic...:String) [], RemoteException
    + FullyQualifiedErrorId : NativeCommandError

[OK ] config     instance=messiah-dev-01 mode=dev
"""


def test_powershell_prefixed_marker_is_still_recognised(tmp_path: Path):
    """2026-08-04 실측 회귀 — 무장은 됐는데 "무장 안 됨"으로 판정돼 ERROR 오탐이 났다.

    `.bat`가 stderr를 PowerShell 파이프라인에 태우면 PS 5.1이 네이티브 exe의 **첫 stderr
    줄**을 NativeCommandError로 감싸 `python.exe : ` 접두사를 붙인다. 마커는 프로세스가
    내는 첫 stderr 줄이라 정확히 그 자리에 걸렸고, 앵커(`^`) 매치가 깨졌다.

    `.bat` 쪽도 고쳤지만(cmd /c로 병합), 탐지기가 호스트 접두사 하나에 깨지는 것 자체가
    결함이다 — 검출 수단이 하나뿐이면 그 수단이 못 보는 결함은 안 보인다.
    """
    # PowerShell의 `Out-File -Encoding utf8`(5.1)은 BOM을 붙인다 — 실제 파일과 같게 만든다.
    (tmp_path / "l1_daily_20260804.log").write_text(_PS_WRAPPED_LOG, encoding="utf-8-sig")

    forensics = collect_crash_forensics(
        date(2026, 8, 4), log_dir=tmp_path, native_crash_count=0, native_crashes_available=True
    )

    assert forensics.armed == {"l1_daily": True}
    assert forensics.findings == []


def test_structured_log_alone_counts_as_armed(tmp_path: Path):
    """두 번째 출처 — stderr 마커가 통째로 사라져도 구조화 JSON 로그가 남는다."""
    (tmp_path / "g2_daily_20260805.log").write_text(
        '{"ts": "2026-08-05T08:36:08+09:00", "level": "INFO", "tag": "CrashForensicsArmed", '
        '"msg": "네이티브 크래시 덤프 무장 — 대상 stderr", "process": "g2_paper"}\n',
        encoding="utf-8",
    )

    forensics = collect_crash_forensics(
        date(2026, 8, 5), log_dir=tmp_path, native_crash_count=0, native_crashes_available=True
    )

    assert forensics.armed == {"g2_paper": True}
    assert forensics.findings == []


def test_a_session_with_neither_source_is_still_reported(tmp_path: Path):
    """느슨하게 만든 대가로 진짜 무장 누락을 놓치면 안 된다 — 둘 다 없어야 경보."""
    (tmp_path / "l1_daily_20260805.log").write_text(
        '{"ts": "2026-08-05T08:35:08+09:00", "level": "INFO", "tag": "SessionStart"}\n',
        encoding="utf-8",
    )

    forensics = collect_crash_forensics(
        date(2026, 8, 5), log_dir=tmp_path, native_crash_count=0, native_crashes_available=True
    )

    assert forensics.armed == {"l1_daily": False}
    assert any("무장 마커 없음" in f for f in forensics.findings)


# ------------------- 덤프 판독 (2026-08-06 P1-3)

_SURVIVING = """{"ts": "2026-08-06T08:36:01.155113+09:00", "level": "WARNING", "tag": "X"}
Windows fatal exception: access violation

Thread 0x00000914 (most recent call first):
  File "rest_client.py", line 86 in wait

{"ts": "2026-08-06T08:45:00.280270+09:00", "level": "INFO", "tag": "CollectorFirstTick"}
{"ts": "2026-08-06T10:04:00.741028+09:00", "level": "DEBUG", "tag": "FeaturePublish"}
"""

_RESTARTED = """{"ts": "2026-08-06T08:36:16.132944+09:00", "level": "INFO", "tag": "SessionStart"}
Windows fatal exception: access violation

Thread 0x00006508 (most recent call first):
  File "thread.py", line 89 in _worker

[OK ] config     instance=messiah-dev-01 mode=dev
self-check: PASS — 기동 허용
[crash_forensics] armed tag=g2_paper target=stderr
{"ts": "2026-08-06T10:26:05.249117+09:00", "level": "INFO", "tag": "SessionStart"}
"""


def test_a_dump_followed_by_more_logging_is_first_chance():
    """2026-08-06 l1_daily 실측 — 08:36에 덤프를 찍고 10:04까지 88분을 계속 로깅했다.
    프로세스는 안 죽었고, 리포트가 `네이티브 크래시 0건`과 나란히 찍던 그 덤프다."""
    [dump] = crash_dumps.parse_dumps("l1_daily", _SURVIVING)

    assert dump.survived is True
    assert "first-chance" in dump.verdict


def test_startup_banner_is_not_counted_as_survival():
    """재기동 프로세스의 self-check 출력(`[OK ] config ...`)은 `SessionStart`보다 먼저
    찍힌다 — 이걸 활동으로 세면 "죽고 다시 뜬 것"이 "살아서 계속 돈 것"으로 뒤집힌다
    (2026-08-06 g2_paper가 실제로 그렇게 잘못 판정됐다)."""
    [dump] = crash_dumps.parse_dumps("g2_paper", _RESTARTED)

    assert dump.survived is False
    assert "생존 미확인" in dump.verdict


def test_survival_false_is_not_called_fatal():
    """로그가 없다는 것은 죽었다는 뜻이 아니다 — 조용한 프로세스는 살아 있어도 비어 있다.
    사인 판정은 이벤트로그와 대조해야 나온다."""
    [dump] = crash_dumps.parse_dumps("g2_paper", _RESTARTED)

    assert "치명" not in dump.verdict


def test_a_dump_carries_a_lower_bound_timestamp():
    """faulthandler 출력에는 시각이 없다 — 직전 로그 줄로 "이 시각 이후"를 가둔다."""
    [dump] = crash_dumps.parse_dumps("l1_daily", _SURVIVING)

    assert dump.after_kst == "08:36:01"


def test_missing_current_thread_block_is_reported_as_a_fact():
    """`Current thread`가 없다는 것은 **파이썬 상태가 없는 스레드**에서 폴트가 났다는
    뜻이다 — "프레임 없음"이라고만 찍으면 그 정보가 사라진다."""
    [dump] = crash_dumps.parse_dumps("l1_daily", _SURVIVING)

    assert dump.crashing_frames == []
    assert "파이썬 스레드 아님" in dump.frame_summary


def test_restart_without_an_eventlog_crash_is_a_finding(tmp_path):
    """덤프 뒤 재기동인데 이벤트로그에 크래시가 없으면 **밖에서 종료된 것**일 수 있다 —
    2026-08-06의 호스트 재부팅이 정확히 그 형태였고, 원인이 다르면 처방도 다르다."""
    (tmp_path / "g2_daily_20260806.log").write_text(_RESTARTED, encoding="utf-8")

    forensics = crash_dumps.collect_crash_forensics(
        date(2026, 8, 6), log_dir=tmp_path, native_crash_count=0, native_crashes_available=True
    )

    assert any("밖에서 종료" in f for f in forensics.findings)


def test_format_line_answers_when_what_died_and_where():
    """덤프 한 줄로 판단이 끝나야 한다 — 종전에는 `프레임 없음`이 전부라 매번 로그를 열었다."""
    (line,) = crash_dumps.format_dump_lines(
        crash_dumps.CrashForensics(armed={}, dumps=crash_dumps.parse_dumps("l1_daily", _SURVIVING))
    )

    assert "08:36:01 이후" in line
    assert "first-chance" in line
    assert "access violation" in line
