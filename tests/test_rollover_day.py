"""월물 롤 당일의 심볼 해석 — 2026-08-14 F-A · F-2.

## 왜 이 파일이 따로 있나

2026-08-14는 이 저장소의 **첫 월물 롤**이었고, 하루에 롤 관련 결함이 서로 독립인 네 곳에서
터졌다 — 피처 웜스타트 · 국면 웜스타트 · 옵션체인 기준가 시드 · 장후 배치 진입점. 넷 다
"심볼이 계약의 이름이지 시계열의 이름이 아니다"라는 같은 착오인데, **한 곳을 고쳐도 다른
곳은 안 고쳐진다.**

롤은 4주에 한 번이라 다음 기회(2026-09-14 근방)까지 다섯 번째 지점이 있는지 알 방법이 없다.
그래서 심볼을 다루는 진입점마다 "롤 당일 해석이 새 월물을 내는가"를 **한 파일에 모아** 건다.
새 진입점이 생기면 여기에 케이스를 더한다.

실측 기준일 (`backfill.front_month_code_for_day`로 확인, 2026-08-14):

    2026-08-12 → A05608      2026-08-13 → A05608  (8월물 만기일 당일까지 근월)
    2026-08-14 → A05609      ← 롤     2026-09-10 → A05609
    2026-09-11 → A05610      ← 다음 롤
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import run_postmarket as rp  # noqa: E402  (scripts/)
import self_check as sc  # noqa: E402

from messiah.core import symbol_resolution  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _REPO_ROOT / "scripts"

# 만기가 있는 값을 소스에 박아도 되는 곳 — **재현성이 목적인 스모크·연구 스크립트뿐이다**
# (2026-08-14 G-10). 이들은 특정 아카이브 날짜에 고정돼 있고 그 날짜의 근월물이 곧 그
# 심볼이다. 운영 경로(수집·매매·장후 배치·화면)는 예외 없이 날짜에서 해석해야 한다.
#
# **이 목록에 새 이름을 추가할 때는 그 스크립트가 운영 경로가 아님을 확인할 것.**
_FIXED_SYMBOL_ALLOWED = {
    "run_backtest_harness.py",  # 고정 아카이브 구간 재현
    "run_full_path_smoke.py",  # 2026-07-24 아카이브 고정
    "run_formal_expert_training_smoke.py",  # 학습 스모크 — 고정 심볼·고정 Horizon
    "run_regime_ai_smoke.py",  # 국면 스모크 — 30분봉 1건 고정
    "probe_ui_concurrency.py",  # UI 동시성 프로브 — 심볼은 부하 생성용 더미
    "run_chaos_check.py",  # 카오스 점검 — 심볼은 시나리오 상수
    "verify_kill_switch.py",  # 킬스위치 검증 — 심볼은 시나리오 상수
}

_ROLL_DAY = date(2026, 8, 14)
_DAY_BEFORE_ROLL = date(2026, 8, 13)
_OLD = "A05608"
_NEW = "A05609"


def _touch_day(bar_dir: Path, symbol: str, horizon: str, day: date) -> None:
    """그날 통합본이 있는 것처럼 만든다 — `bar_paths`는 이름과 stat만 보므로 내용은 불필요."""
    target = bar_dir / symbol / horizon / f"{day.isoformat()}.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.touch()


# ------------------------------------------------------------------ F-A: 심볼 해석


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 8, 12), _OLD),
        (_DAY_BEFORE_ROLL, _OLD),  # 만기일 **당일**까지는 그 달 월물이 근월
        (_ROLL_DAY, _NEW),
        (date(2026, 9, 10), _NEW),
        (date(2026, 9, 11), "A05610"),
    ],
)
def test_symbol_is_resolved_from_the_date_not_from_today(day: date, expected: str) -> None:
    """소급 실행이 정상 경로다 — 해석이 오늘이 아니라 `--date`를 따라야 한다.

    이 케이스가 F-A의 회귀 방지선이다. 마스터파일 조회(`front_month_future_code()`)는
    날짜를 안 받아 어떤 과거일에 대해서도 오늘의 근월물을 답한다 — 그러면 소급 실행이
    성공으로 끝나면서 리포트만 거짓이 된다.

    출처 문구는 G-7에서 `core/symbol_resolution`의 어휘로 통일됐다(런타임 기록이 없는
    과거일이므로 "만기 규칙 계산"). 이 테스트가 보는 것은 **심볼이 날짜를 따르는가**다.
    """
    symbol, origin = rp._resolve_symbol(None, day)
    assert symbol == expected
    assert origin == "만기 규칙 계산"


def test_explicit_symbol_overrides_resolution_and_says_so() -> None:
    symbol, origin = rp._resolve_symbol(_OLD, _ROLL_DAY)
    assert (symbol, origin) == (_OLD, "명시")


def test_has_day_sees_both_canonical_and_shards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """통합본(장후)과 조각 디렉터리(장중) 둘 다 "있다"로 읽어야 한다.

    경로를 직접 조립하면 장중에 그날 데이터가 통째로 안 보인다(`data/bar_paths.py` 모듈
    docstring) — 가드가 그 함정에 빠지면 정상일 오전마다 배치를 거부하게 된다.
    """
    _touch_day(tmp_path, _NEW, "1m", _ROLL_DAY)
    shard_dir = tmp_path / _OLD / "1m" / _ROLL_DAY.isoformat()  # 장중 조각 디렉터리
    shard_dir.mkdir(parents=True)
    (shard_dir / "09.parquet").touch()
    empty_shard = tmp_path / "A05610" / "1m" / _ROLL_DAY.isoformat()  # 디렉터리만 있고 비었다
    empty_shard.mkdir(parents=True)
    monkeypatch.setattr(rp, "_BAR_DIR", tmp_path)

    assert rp._has_day(_NEW, _ROLL_DAY) is True
    assert rp._has_day(_OLD, _ROLL_DAY) is True  # 조각만 있어도 있는 것이다
    # **빈 조각 디렉터리는 데이터가 아니다.** 디렉터리 존재만 보고 "있다"고 하면, 수집이
    # 디렉터리만 만들어 놓고 죽은 날에 가드가 통과해 버린다 — 가드를 세운 이유가 사라진다.
    assert rp._has_day("A05610", _ROLL_DAY) is False
    assert rp._symbols_holding_day(_ROLL_DAY) == [_OLD, _NEW]


def test_batch_refuses_when_resolved_symbol_has_no_data_that_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """**오늘의 사고 그 자체**: 조회 대상이 그날 데이터를 안 가졌는데 배치가 완주했다.

    2026-08-14에 5단계 중 4단계가 만기된 A05608을 조회했고, 네 도구가 저마다 "0행"을 정상
    산출로 썼다. 아무도 실패하지 않아 종료 코드는 0이었다 — 그리고 `fix_verification`이
    그 리포트를 읽어 재발 12건을 찍었다(1건 허위·3건 수치 오류).

    단계에 **들어가기 전에** 멈추는지, 그리고 "그럼 누가 갖고 있나"를 답하는지 본다.
    """
    _touch_day(tmp_path, _NEW, "1m", _ROLL_DAY)  # 실제 데이터는 새 월물에만 있다
    monkeypatch.setattr(rp, "_BAR_DIR", tmp_path)
    monkeypatch.setattr(rp.session_guard, "refuse_if_regular_session", lambda *a, **k: None)
    monkeypatch.setattr(rp.mlog, "setup", lambda *a, **k: None)

    logged: list[tuple[str, dict]] = []
    monkeypatch.setattr(rp.mlog, "log", lambda tag, msg, **f: logged.append((tag, f)))

    # 사람이 옛 심볼을 명시한 경우 — 자동 해석이 있어도 이 방어선은 따로 서 있어야 한다.
    monkeypatch.setattr(
        sys, "argv", ["run_postmarket.py", "--date", _ROLL_DAY.isoformat(), "--symbol", _OLD]
    )

    assert rp.main() == rp._SYMBOL_MISMATCH_EXIT_CODE
    assert rp._SYMBOL_MISMATCH_EXIT_CODE != rp.session_guard.REFUSED_EXIT_CODE

    tags = [tag for tag, _ in logged]
    assert "SymbolResolutionMismatch" in tags
    mismatch = next(fields for tag, fields in logged if tag == "SymbolResolutionMismatch")
    assert mismatch["symbol"] == _OLD
    assert mismatch["symbols_holding_day"] == [_NEW]  # "그럼 누가 갖고 있나"에 답한다
    # 단계에 들어가기 전에 멈췄으므로 SessionEnd는 "중단"이고 steps_run은 0이다.
    end = next(fields for tag, fields in logged if tag == "SessionEnd")
    assert end["steps_run"] == 0
    assert "조회 대상 불일치" in capsys.readouterr().err


def test_batch_proceeds_when_resolution_matches_the_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """정상일에는 가드가 조용하다 — 넓은 그물은 늑대소년을 만든다."""
    _touch_day(tmp_path, _NEW, "1m", _ROLL_DAY)
    monkeypatch.setattr(rp, "_BAR_DIR", tmp_path)
    monkeypatch.setattr(rp.session_guard, "refuse_if_regular_session", lambda *a, **k: None)
    monkeypatch.setattr(rp.mlog, "setup", lambda *a, **k: None)
    logged: list[str] = []
    monkeypatch.setattr(rp.mlog, "log", lambda tag, msg, **f: logged.append(tag))
    # 단계 실행은 대체한다 — 이 테스트가 보는 것은 가드이지 5단계가 아니다.
    monkeypatch.setattr(rp, "_run_step", lambda step: rp.StepResult(step.name, True, "완료"))
    monkeypatch.setattr(sys, "argv", ["run_postmarket.py", "--date", _ROLL_DAY.isoformat()])

    assert rp.main() == 0
    assert "SymbolResolutionMismatch" not in logged
    assert "SymbolResolved" in logged


# ------------------------------------------------------------------ F-2: 자가점검


def test_self_check_announces_the_rollover_with_available_history(tmp_path: Path) -> None:
    """롤 당일 자가점검이 **먼저** 말한다 — 2026-08-14엔 PASS만 세 번 냈다."""
    for day in (date(2026, 8, 11), date(2026, 8, 12), _DAY_BEFORE_ROLL):
        _touch_day(tmp_path, _OLD, "30m", day)

    result = sc.check_rollover(bar_dir=tmp_path, today=_ROLL_DAY)

    assert result.ok is True  # 롤은 정상이다 — 기동을 막지 않는다
    assert "월물 롤 당일" in result.detail
    assert f"{_OLD} → {_NEW}" in result.detail
    assert "신규 월물 30m 아카이브 0일" in result.detail  # 웜스타트의 재료가 0이라는 사실
    assert "직전 월물 3일" in result.detail


def test_self_check_is_quiet_on_a_normal_day(tmp_path: Path) -> None:
    result = sc.check_rollover(bar_dir=tmp_path, today=_DAY_BEFORE_ROLL)
    assert result.ok is True
    assert "비-롤일" in result.detail
    assert _OLD in result.detail


def test_self_check_counts_only_history_before_today(tmp_path: Path) -> None:
    """오늘 생긴 봉은 웜스타트의 재료가 아니다 — 기동 시점엔 아직 없다."""
    _touch_day(tmp_path, _NEW, "30m", _ROLL_DAY)  # 오늘 것
    result = sc.check_rollover(bar_dir=tmp_path, today=_ROLL_DAY)
    assert "신규 월물 30m 아카이브 0일" in result.detail


def test_normal_day_announces_the_next_rollover(tmp_path: Path) -> None:
    """비-롤일엔 **다음 롤을 예고한다** — 4주에 한 번뿐이라 사람이 잊는다 (G-10)."""
    result = sc.check_rollover(bar_dir=tmp_path, today=_DAY_BEFORE_ROLL)
    assert "비-롤일" in result.detail
    assert "다음 롤 2026-08-14" in result.detail


# ------------------------------------------------------- G-10: 진입점 전수 게이트


def test_no_operational_entrypoint_hardcodes_an_expiring_symbol() -> None:
    """**사람이 손으로 하던 grep을 테스트가 한다** (2026-08-14 G-10).

    2026-08-14 하루에 롤 결함이 서로 독립인 **네 곳**에서 터졌다: 아카이브 조회 · 장후 배치
    진입점 · 화면 상수 · 옵션체인 기준가 시드. 넷 다 "심볼이 시계열의 이름인 줄 알았다"는
    같은 착오인데 **한 곳을 고쳐도 다른 곳은 안 고쳐진다.**

    롤은 4주에 한 번이라 다음 기회(2026-09-14 근방)까지 다섯 번째 지점이 있는지 알 방법이
    없다. 그날 F-A를 쓰면서 `grep -rn 'default="A056' scripts/`를 손으로 돌려야 한다고 적은
    것이, 그 검사가 **자동화돼야 한다**는 뜻이었다. 이 테스트가 그 자리다.
    """
    offenders: list[str] = []
    pattern = re.compile(r'default\s*=\s*[\'"]A05\d{2}[\'"]')
    for path in sorted(_SCRIPTS_DIR.glob("*.py")):
        if path.name in _FIXED_SYMBOL_ALLOWED:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, "만기가 있는 심볼을 기본값으로 박은 운영 진입점:\n" + "\n".join(offenders)


def test_no_module_hardcodes_an_expiring_symbol_as_a_default() -> None:
    """`src/` 쪽도 같은 규율 — 2026-08-14엔 `ui/app.py`의 `DEFAULT_SYMBOL`이 그 자리였다."""
    offenders: list[str] = []
    pattern = re.compile(r'^\s*(?!#)\w*DEFAULT_SYMBOL\w*\s*[:=].*[\'"]A05\d{2}[\'"]')
    for path in sorted((_REPO_ROOT / "src").rglob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}: {line.strip()}")
    assert not offenders, "만기가 있는 심볼을 모듈 상수로 박았다:\n" + "\n".join(offenders)


@pytest.mark.parametrize(
    ("day", "expected"),
    [(_DAY_BEFORE_ROLL, False), (_ROLL_DAY, True), (date(2026, 9, 10), False)],
)
def test_rollover_day_is_one_question(day: date, expected: bool) -> None:
    """자가점검·장후 배치·CI 게이트가 각자 날짜 산술을 하면 세 곳이 다르게 답할 수 있다."""
    assert symbol_resolution.is_rollover_day(day) is expected


def test_next_rollover_is_computable_ahead_of_time() -> None:
    """G-1(사전 백필)이 **언제 돌아야 하는지**를 달력에서 바로 답할 수 있어야 한다."""
    assert symbol_resolution.next_rollover_day(_DAY_BEFORE_ROLL) == _ROLL_DAY
    nxt = symbol_resolution.next_rollover_day(_ROLL_DAY)
    assert nxt > _ROLL_DAY
    assert symbol_resolution.is_rollover_day(nxt)


# ------------------------------------------------------- G-7: 해석의 단일 정본


def test_runtime_record_wins_over_computation(tmp_path: Path) -> None:
    """런타임이 실제로 고른 심볼이 정본이다 — 그 프로세스가 그것으로 구독하고 적재한다."""
    symbol_resolution.record(_ROLL_DAY, "A99999", log_dir=tmp_path)
    symbol, origin = symbol_resolution.resolve_for_tools(_ROLL_DAY, log_dir=tmp_path)
    assert (symbol, origin) == ("A99999", "런타임 기록")


def test_explicit_beats_everything(tmp_path: Path) -> None:
    """소급 조사·수동 복구에는 사람의 명시가 이겨야 한다."""
    symbol_resolution.record(_ROLL_DAY, "A99999", log_dir=tmp_path)
    symbol, origin = symbol_resolution.resolve_for_tools(_ROLL_DAY, explicit=_OLD, log_dir=tmp_path)
    assert (symbol, origin) == (_OLD, "명시")


def test_record_for_another_day_is_ignored(tmp_path: Path) -> None:
    """파일 안의 날짜가 요청과 다르면 버린다 — 어느 쪽을 믿을지 조용히 고르지 않는다.

    `fix_verification.load_daily_reports()`가 2026-08-14에 바로 그 형태로 9거래일간
    오염돼 있었다(보존본이 정본을 덮었다).
    """
    symbol_resolution.record(_DAY_BEFORE_ROLL, _OLD, log_dir=tmp_path)
    assert symbol_resolution.recorded(_ROLL_DAY, log_dir=tmp_path) is None
    symbol, origin = symbol_resolution.resolve_for_tools(_ROLL_DAY, log_dir=tmp_path)
    assert (symbol, origin) == (_NEW, "만기 규칙 계산")
