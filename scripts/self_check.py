"""기동 자가 점검 (Self-Check) — Ver 1.1 §7.3, 레슨런 L11·L17.

모든 프로세스는 이 점검을 통과해야만 거래(또는 수집)를 개시한다.
실패 시 exit code 1 — 기동 스크립트는 이를 보고 기동을 중단한다.

사용:  python scripts/self_check.py [--configs configs] [--skip-redis]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

# Windows 콘솔 기본 코드페이지(cp949)가 한글 출력을 깨뜨리는 것 방지
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

# src 레이아웃 실행 지원
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from messiah.core.bus import registered_types  # noqa: E402
from messiah.core.config import InstanceConfig, load_instance  # noqa: E402
from messiah.core.event_calendar import EventCalendar  # noqa: E402
from messiah.core.messages import SCHEMA_VERSION, Horizon  # noqa: E402
from messiah.core.timeutil import now_kst, now_utc  # noqa: E402
from messiah.data import backfill, bar_paths  # noqa: E402


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


def check_config(config_dir: str) -> tuple[CheckResult, InstanceConfig | None]:
    try:
        cfg = load_instance(config_dir)
        return CheckResult("config", True, f"instance={cfg.instance_id} mode={cfg.mode}"), cfg
    except Exception as e:  # noqa: BLE001 — 자가 점검은 모든 실패를 보고
        return CheckResult("config", False, str(e)), None


def check_schema() -> CheckResult:
    n = len(registered_types())
    ok = SCHEMA_VERSION >= 1 and n >= 5
    return CheckResult("schema", ok, f"version={SCHEMA_VERSION} types={n}")


def check_timezone() -> CheckResult:
    """시각 건전성 — naive 금지 체계 확인 + KST 오프셋 검증 (L21).

    표시는 KST로 통일한다(2026-07-29 [MW0601]) — 내부 데이터 표준은 여전히 UTC(SYSTEM.md
    R3, `core/logging.py`의 `ts_utc`와 동일 원칙)지만, 이 줄은 `run_l1_daily.py`/
    `run_g2_paper_trading.py`가 부팅 시 그대로 로그 파일에 찍는 사람이 읽는 출력이고, 그
    로그의 나머지 모든 줄(`ts` 필드, `core/logging.py`)은 이미 KST다 — 이 한 줄만 UTC로
    찍혀 같은 순간을 두 다른 표기로 섞어 보여주는 게 실제 로그 점검 중 눈에 띄어 정리."""
    u, k = now_utc(), now_kst()
    ok = (
        u.tzinfo is not None
        and k.utcoffset() is not None
        and k.utcoffset().total_seconds() == 9 * 3600
    )
    return CheckResult("timezone", ok, f"kst={k.isoformat(timespec='seconds')}")


# 이 값을 넘으면 기동을 거부한다 (SYSTEM.md §4-6 "시간 동기 실패 시 거래 거부").
# 2026-08-05 실측: NTP 동기 직후 오프셋 0.0006초. 5초는 그보다 3자릿수 넉넉한 미검증
# 초기값이고, 이만큼 어긋나면 `EventCalendar.minutes_to_close` 기반 마감 전 청산·세션
# 게이트가 전부 그만큼 늦게 걸린다.
CLOCK_OFFSET_FAIL_SECONDS = 5.0
# 넘으면 경고만 — 완성봉 유예 500ms가 무의미해지기 시작하는 지점
# (`ops/clock_skew.py` WARN_THRESHOLD_SECONDS와 같은 값).
CLOCK_OFFSET_WARN_SECONDS = 2.0


def _ntp_offset_seconds(runner=subprocess.run) -> tuple[float | None, str]:
    """`w32tm /stripchart` 1샘플로 로컬 시계와 기준 시각의 차이를 잰다. (오프셋, 설명).

    측정 못 하면 `None`이다 — **0초와 구분한다**(L18). 오프라인이거나 방화벽에 막힌 PC를
    "시계가 정확하다"고 통과시키면, 이 검사가 있다는 사실 자체가 거짓 안심이 된다.

    출력은 로케일 언어라(한글 Windows면 한국어) 문장은 안 읽고 `+00.0005732s` 형태의
    **숫자 토큰만** 정규식으로 뽑는다 — `ops/integrity_report.py`가 이벤트로그에서 로케일
    문자열을 피해 `Properties` 배열을 직접 읽는 것과 같은 이유다.

    **표본을 3개 뽑는 이유**: 첫 표본이 자주 `0x800705B4`(타임아웃)로 실패한다(2026-08-05
    실측 — 같은 명령을 연달아 돌려도 됐다 안 됐다 한다). 1개만 뽑으면 시계가 멀쩡한 날에도
    "측정 실패"가 되고, 그러면 이 검사가 있으나 마나가 된다. 하나라도 성공하면 그 값을 쓴다.
    """
    if sys.platform != "win32":
        return None, "Windows 전용 측정 — 건너뜀"
    try:
        result = runner(
            [
                "w32tm",
                "/stripchart",
                "/computer:time.windows.com",
                "/samples:3",
                "/dataonly",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except Exception as e:  # noqa: BLE001 — 못 재는 것과 0초는 다르다
        return None, f"측정 실패({e.__class__.__name__})"

    matches = re.findall(r"([+-]\d+\.\d+)s", result.stdout or "")
    if not matches:
        return None, "측정 실패(응답에 오프셋 없음 — 오프라인/차단 가능)"
    return float(matches[-1]), ""


def _w32time_running(runner=subprocess.run) -> bool | None:
    """Windows Time 서비스가 돌고 있나. None은 확인 불가(비Windows 등)."""
    if sys.platform != "win32":
        return None
    try:
        result = runner(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "(Get-Service w32time).Status.ToString()",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except Exception:  # noqa: BLE001
        return None
    return "Running" in (result.stdout or "")


def check_clock(
    *, offset_reader=_ntp_offset_seconds, service_reader=_w32time_running
) -> CheckResult:
    """시간 동기 — SYSTEM.md §4-6이 기동 자가 점검에 요구하는 항목 (2026-08-05 신설).

    ## 왜 `check_timezone`으로는 부족했나

    그 검사는 **UTC 오프셋이 9시간인지만** 봤다. 2026-08-04 로그를 되짚어 보니 이 PC의
    시계는 실제 시각보다 **14.41초 느렸고**(외부 기준 `w32tm /stripchart` 실측), 하루
    4~5초씩 더 느려지고 있었다 — Windows Time 서비스가 `Stopped`/`Manual`이라 부팅해도
    안 켜졌기 때문이다. 그 14초 동안 `check_timezone`은 8거래일 내내 `[OK]`였다.

    측정 못 한 경우를 통과시키는 이유: 이 검사의 실패는 **거래 거부**다. 오프라인 PC나
    NTP가 막힌 망에서 수집조차 못 하게 만드는 것은 과하다 — 대신 그 사실을 detail에 남기고,
    서비스가 멈춰 있으면 그건 측정과 무관하게 확실한 결함이므로 경고한다.
    """
    running = service_reader()
    offset, note = offset_reader()

    parts: list[str] = []
    if offset is not None:
        parts.append(f"offset={offset:+.3f}s")
    else:
        parts.append(note or "offset=측정 불가")
    if running is False:
        parts.append("w32time=Stopped(자동 동기 없음 — 시계가 하루 4~5초씩 밀린다)")
    elif running is True:
        parts.append("w32time=Running")

    if offset is not None and abs(offset) > CLOCK_OFFSET_FAIL_SECONDS:
        parts.append(f"임계 {CLOCK_OFFSET_FAIL_SECONDS:.0f}초 초과 — 기동 거부")
        return CheckResult("clock", False, " · ".join(parts))
    if offset is not None and abs(offset) > CLOCK_OFFSET_WARN_SECONDS:
        parts.append(f"경고: 완성봉 유예 500ms보다 큼(임계 {CLOCK_OFFSET_WARN_SECONDS:.0f}초)")
    return CheckResult("clock", True, " · ".join(parts))


def check_host(*, collector=None) -> CheckResult:
    """호스트 기초 위생 — 디스크 여유·전원 계획·Docker (2026-08-05 신설, 고도화 5).

    ## 왜 자가 점검이 애플리케이션 바깥까지 보나

    2026-08-04에 로컬 시계가 14.41초 밀린 채 8거래일을 돌았는데, 원인은 코드가 아니라
    **Windows Time 서비스가 꺼져 있었던 것**이었다. 그때까지 이 스크립트는 설정·스키마·
    시크릿·번들·Redis만 봤다 — 프로세스 바깥은 점검 범위에 아예 없었다.

    복제 배포(SYSTEM.md §4-6)에서 특히 중요하다: 인스턴스 차이는 `configs/instance.yaml`
    하나뿐이라는 것이 원칙인데 **호스트 상태는 그 파일에 안 적힌다**.

    **기동은 막지 않는다.** 시간 동기만 `check_clock`이 거부하고(그건 봉 경계와 청산
    타이밍을 직접 어긋나게 한다), 나머지는 경고다 — 임계가 전부 미검증 초기값이고,
    디스크가 좀 부족하다고 그날 수집을 통째로 포기하는 것은 본말전도다.
    """
    from messiah.ops import host_health

    health = (collector or host_health.collect)()
    parts = [f"{c.name}={c.detail}" for c in health.checks]
    if health.degraded:
        parts.append(f"경고: {' · '.join(health.degraded)}")
    return CheckResult("host", True, " · ".join(parts))


def check_bar_close(cfg: InstanceConfig) -> CheckResult:
    """1분봉 확정 방식을 기동 로그에 노출한다 (2026-08-12 F-5 / NEXT_TODO R-1).

    ## 왜 설정 파일만으로는 부족한가

    `configs/instance.yaml`의 `minute_bar_close`는 **G-4 승격의 대상 그 자체**다(마지막 틱
    구동 → 거래소 시각 경계 구동). 2026-08-12 점검에서 그 값이 `timer`인 것은 파일로
    확인됐지만 **기동 로그 어디에도 안 찍혔다** — 즉 "그날 프로세스가 실제로 어느 방식으로
    돌았나"를 사후에 로그만 보고는 말할 수 없었다. 승격 여부는 `late_bar_drops: 0`과
    커버리지 100%로 **간접** 확인됐을 뿐이고, 직접 관측 축이 없었다.

    이 저장소가 반복해 온 실패 형태가 정확히 그것이다(결선했다고 믿는데 안 붙어 있음).
    설정과 실제가 어긋나는 날 이 한 줄이 그 사실을 그날 아침에 말한다.

    **판정하지 않는다** — 두 값 다 정당한 설정이다. 노출이 목적이다.
    """
    mode_value = getattr(cfg, "minute_bar_close", None)
    if mode_value == "timer":
        detail = "1분봉 확정: timer (거래소 시각 경계 구동)"
    elif mode_value:
        detail = f"1분봉 확정: {mode_value} (마지막 틱 구동 — timer 승격 대상)"
    else:
        detail = "1분봉 확정: 설정 없음(기본값 사용)"
    return CheckResult("bar_close", True, detail)


def check_prev_postmarket(
    *, log_dir: Path = Path("logs"), today: date | None = None
) -> CheckResult:
    """**직전 거래일의 장후 배치가 끝까지 갔는가** (2026-08-12 F-5).

    ## 왜 여기서 보나 — 순서 함정

    장후 배치(`run_postmarket.py`)의 5/5단계가 그날 무결성 리포트를 **자기가** 만든다.
    그러니 그 리포트가 쓰이는 시점에 postmarket 자신의 `SessionEnd`는 아직 안 찍혀 있다 —
    당일 리포트에 이 판정을 넣으면 **매일 오탐 1건**이 생긴다(2026-08-12 P1-3의
    `daily-axes-measured`와 정확히 같은 형태의 함정이다).

    그래서 하루 뒤에, 파일이 완결된 뒤에 본다. 장전 자가점검이 그 자리다.

    **기동은 막지 않는다.** 어제 장후 배치가 실패한 것이 오늘 수집을 포기할 이유는 아니다
    (`check_host`와 같은 원칙) — 대신 그 사실을 detail에 남겨 아침에 눈에 띄게 한다.
    """
    today = today or now_kst().date()
    logs = sorted(log_dir.glob("postmarket_*.log"))
    previous = [p for p in logs if p.stem[len("postmarket_") :] < today.strftime("%Y%m%d")]
    if not previous:
        return CheckResult("postmarket", True, "직전 장후 배치 로그 없음 — 판정 불가")

    path = previous[-1]
    stamp = path.stem[len("postmarket_") :]
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        return CheckResult("postmarket", True, f"{stamp} 로그 읽기 실패: {exc}")

    # 태그로만 센다 — 설명 문구 안의 단어("no-silent-process-death"의 해설 등)에 걸리면
    # 안 돈 날을 돈 날로 읽는다(2026-08-12에 실제로 그 문자열이 로그에 있었다).
    ended = '"tag": "SessionEnd"' in text
    if not ended:
        return CheckResult(
            "postmarket",
            True,
            f"경고: {stamp} 장후 배치가 SessionEnd를 안 남겼다 — 중간에 죽었을 수 있다"
            f" (그날 리포트의 `미측정`이 그대로 남는다: run_postmarket.py --date {stamp})",
        )
    return CheckResult("postmarket", True, f"{stamp} 장후 배치 정상 종료 확인")


def check_rollover(
    *, bar_dir: Path = Path("data") / "bars", today: date | None = None
) -> CheckResult:
    """**오늘이 월물 롤 당일인가, 그리고 새 월물의 이력은 얼마나 있는가** (2026-08-14 F-2).

    ## 왜 자가점검이 이걸 먼저 외쳐야 하나

    2026-08-14는 첫 월물 롤이었다(`A05608` → `A05609`). 그날 자가점검은 **PASS를 세 번**
    냈고, 그 뒤에 벌어진 일은 이랬다:

        · 피처 롤링 윈도 전 Horizon 0봉  → 1m NaN 84.7%로 개장
        · 국면 이력 0봉 < 하한 22봉      → 종일 UNKNOWN, 판단 14/14가 NO_TRADE
        · 옵션체인 기준가 시드 없음      → 장전 10사이클 스킵(영구 결손)
        · 장후 배치 4/5단계가 만기 월물 조회 → 하루치 채점 오염

    넷 다 같은 원인 하나("심볼이 바뀌면 심볼로 색인된 것이 전부 빈다")인데, **기동 시점에
    그 사실을 말하는 축이 하나도 없었다.** 롤은 4주에 한 번뿐이라 사람도 잊는다.

    ## 기동은 막지 않는다

    롤 자체는 정상이고 수집은 그대로 해야 한다(`check_host`·`check_prev_postmarket`과 같은
    원칙). 대신 **[WARN] 문구와 가용 봉 수를 남겨** 아침에 눈에 띄게 한다 — 웜스타트가
    선행 월물을 읽는지(F-1) 확인할 첫 지점이 여기다.
    """
    today = today or now_kst().date()
    try:
        calendar: EventCalendar | None = EventCalendar.from_file()
    except Exception:  # noqa: BLE001 — 달력 부재로 기동을 막지 않는다
        calendar = None

    try:
        symbol = backfill.front_month_code_for_day(today, calendar)
        # 달력이 있으면 휴장일을 건너뛴 직전 거래일, 없으면 전날. 달력이 등록 연도 밖을
        # 물으면 ValueError를 던지므로(그쪽 docstring) 그것도 여기서 받는다.
        if calendar is not None:
            previous = calendar.previous_trading_day(today)
        else:
            previous = today - timedelta(days=1)
        previous_symbol = backfill.front_month_code_for_day(previous, calendar)
    except Exception as exc:  # noqa: BLE001
        return CheckResult("rollover", True, f"근월물 해석 실패({exc}) — 판정 불가")

    if symbol == previous_symbol:
        return CheckResult("rollover", True, f"비-롤일 — 근월물 {symbol} 유지")

    # 롤 당일이다. 신규 월물이 **오늘 이전에** 가진 30m 아카이브 일수가 곧 웜스타트의 재료다
    # (국면 구동 Horizon이 30m — `strategy/regime/runtime.py`).
    def _days_before(sym: str) -> int:
        return len([d for d in bar_paths.available_days(bar_dir, sym, Horizon.M30) if d < today])

    new_days, old_days = _days_before(symbol), _days_before(previous_symbol)
    return CheckResult(
        "rollover",
        True,
        f"경고: 월물 롤 당일 — {previous_symbol} → {symbol}. "
        f"신규 월물 30m 아카이브 {new_days}일 · 직전 월물 {old_days}일. "
        f"웜스타트가 선행 월물을 못 읽으면 오늘 국면은 UNKNOWN으로 시작한다"
        f"(FeatureWarmStart.bars_by_source 확인)",
    )


def check_git_state(mode: str) -> CheckResult:
    """계명 10: 커밋 안 된 수정을 실전에 반입하지 않는다 (live/paper에서만 강제).

    ## 왜 실패 사유를 나눠 적나 (2026-08-06 실측)

    종전에는 `except Exception:` 하나로 받아 **언제나 같은 문구**를 돌려줬다:
    `"git 저장소 아님 (dev에서만 허용)"`.

    그날 10:25 재기동에서 그 줄이 찍혔는데, **같은 디렉터리가 두 시간 전 08:35에는
    `clean`이었다.** `.bat`이 `cd /d "%~dp0.."`로 저장소 루트에서 도는데 저장소가 아닐 리가
    없다. 진짜 원인(재부팅이 남긴 `index.lock` 추정)은 예외 텍스트와 함께 버려져서 지금도
    확정할 수 없다 — **하나의 고정된 거짓말이 모든 실패를 덮었다.**

    dev라서 PASS로 넘어갔지만, live/paper였으면 **틀린 이유로 기동이 거부**됐을 것이다.
    그때 사람이 보는 첫 문장이 "저장소 아님"이면 조사는 엉뚱한 곳에서 시작한다.
    """
    try:
        completed = subprocess.run(  # noqa: S603 — 고정 인자
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
    except FileNotFoundError:
        return CheckResult("git", mode == "dev", "git 실행 파일 없음 (dev에서만 허용)")
    except subprocess.TimeoutExpired:
        return CheckResult("git", mode == "dev", "git status 20초 내 무응답 (dev에서만 허용)")
    except Exception as exc:  # noqa: BLE001 — 남은 경우도 **무엇이었는지** 말한다
        return CheckResult(
            "git", mode == "dev", f"git status 실행 실패: {type(exc).__name__} (dev에서만 허용)"
        )

    if completed.returncode != 0:
        # git이 돌긴 했는데 거부했다 — 저장소가 아닌 것도 이 경우에 들지만 유일하지 않다
        # (index.lock 선점·권한·손상 등). **git이 한 말을 그대로 옮긴다.**
        reason = (completed.stderr or "").strip().splitlines()
        detail = reason[0] if reason else f"exit {completed.returncode}"
        return CheckResult("git", mode == "dev", f"git status 거부: {detail} (dev에서만 허용)")

    dirty = (completed.stdout or "").strip()
    if dirty and mode in ("live", "paper"):
        return CheckResult("git", False, f"미커밋 변경 {len(dirty.splitlines())}건 — 계명 10")
    return CheckResult("git", True, "clean" if not dirty else f"dirty({mode} 허용)")


def check_secrets(cfg: InstanceConfig) -> CheckResult:
    """live/paper는 브로커 시크릿 env 필수. 값은 절대 출력하지 않는다."""
    if cfg.mode == "dev" or cfg.broker.name == "simulator":
        return CheckResult("secrets", True, "dev/simulator — 생략")
    import os

    missing = [
        ref[4:]
        for ref in (cfg.broker.account_ref, cfg.broker.app_key_ref, cfg.broker.app_secret_ref)
        if ref.startswith("env:") and not os.environ.get(ref[4:])
    ]
    return CheckResult("secrets", not missing, f"missing={missing}" if missing else "ok")


def check_bundle(cfg: InstanceConfig) -> CheckResult:
    """live는 모델 번들 필수 + 번들 매니페스트 존재 확인 (L11 — 릴리스 일치)."""
    if cfg.mode != "live":
        return CheckResult("bundle", True, f"{cfg.mode} — 생략")
    if cfg.model_bundle in ("", "none"):
        return CheckResult("bundle", False, "live 모드에 model_bundle 미지정")
    manifest = Path("data/models") / cfg.model_bundle / "manifest.yaml"
    return CheckResult("bundle", manifest.exists(), str(manifest))


def check_registry_consistency(cfg: InstanceConfig) -> CheckResult:
    """live 모드에서 릴리스(`model_bundle`)가 가리키는 각 Horizon 번들이 Registry상
    지금도 `live` 상태인지 교차검증 — "번들 손상 배포"(Ver 1.6 §12 실패모드표) 방어,
    Ver 2.0 §9 W37~38. `check_bundle()`은 manifest.yaml의 존재만 보므로, 그 릴리스가
    가리키는 개별 번들이 이후 강등/재승격돼 릴리스 스냅샷과 어긋난 상태는 놓친다 — 이
    검사가 그 간극을 메운다. Registry/모델 모듈은 지연 import(ml extras 없이도 dev/replay
    모드는 self-check 전체가 죽지 않게)."""
    if cfg.mode != "live":
        return CheckResult("registry", True, f"{cfg.mode} — 생략")
    if cfg.model_bundle in ("", "none"):
        return CheckResult("registry", False, "live 모드에 model_bundle 미지정")
    try:
        from messiah.models.registry import ModelRegistry
        from messiah.models.release import load_release_manifest, verify_release

        release_dir = Path("data/models") / cfg.model_bundle
        manifest = load_release_manifest(release_dir)
        registry = ModelRegistry(Path("data/models/registry.db"))
        try:
            problems = verify_release(registry, manifest)
        finally:
            registry.close()
        if problems:
            return CheckResult("registry", False, "; ".join(problems))
        detail = f"bundles={manifest.bundles}"
        if manifest.missing_horizons:
            detail += f" (경고: missing_horizons={manifest.missing_horizons})"
        return CheckResult("registry", True, detail)
    except Exception as e:  # noqa: BLE001 — 자가 점검은 모든 실패를 보고
        return CheckResult("registry", False, str(e))


def check_redis(cfg: InstanceConfig, skip: bool) -> CheckResult:
    if skip:
        return CheckResult("redis", True, "--skip-redis")
    try:
        import asyncio

        import redis.asyncio as aioredis

        async def ping() -> bool:
            r = aioredis.from_url(cfg.redis_url)
            try:
                return bool(await r.ping())
            finally:
                await r.aclose()

        return CheckResult("redis", asyncio.run(ping()), cfg.redis_url)
    except Exception as e:  # noqa: BLE001
        return CheckResult("redis", False, str(e))


def run_all(config_dir: str = "configs", skip_redis: bool = False) -> list[CheckResult]:
    results: list[CheckResult] = []
    cfg_result, cfg = check_config(config_dir)
    results.append(cfg_result)
    results.append(check_schema())
    results.append(check_timezone())
    results.append(check_clock())
    results.append(check_host())
    # 어제 장후 배치의 결말 — 오늘 아침이 그것을 볼 수 있는 첫 시점이다(F-5의 순서 함정).
    results.append(check_prev_postmarket())
    # 오늘이 월물 롤 당일인가 — 4주에 한 번뿐이라 사람이 잊는다(2026-08-14 F-2).
    results.append(check_rollover())
    if cfg is not None:
        results.append(check_bar_close(cfg))
        results.append(check_git_state(cfg.mode))
        results.append(check_secrets(cfg))
        results.append(check_bundle(cfg))
        results.append(check_registry_consistency(cfg))
        results.append(check_redis(cfg, skip_redis))
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description="MESSIAH self-check")
    ap.add_argument("--configs", default="configs")
    ap.add_argument("--skip-redis", action="store_true")
    args = ap.parse_args()

    results = run_all(args.configs, args.skip_redis)
    all_ok = all(r.ok for r in results)
    for r in results:
        print(f"[{'OK ' if r.ok else 'FAIL'}] {r.name:<10} {r.detail}")
    print(f"\nself-check: {'PASS — 기동 허용' if all_ok else 'FAIL — 기동 거부 (Ver 1.1 §7.3)'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
