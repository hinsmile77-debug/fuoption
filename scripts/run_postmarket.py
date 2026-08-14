"""장후 절차 일괄 실행 — 사람 손을 뺀다 (2026-08-06 P0-3b).

## 왜 생겼나

무결성 리포트의 `unmeasured`를 비우려면 장후에 두 도구를 돌려야 한다고 `NEXT_TODO.md`에
적혀 있었다:

    python scripts/verify_archive_volume.py --date <오늘>
    python scripts/run_vol_scorecard.py     --date <오늘>

**이틀 연속 안 돌았다.** 2026-08-05에 한 번(그날 커밋 제목이 "그것을 쓰라던 절차는 조용히
안 돌았다"였다), 그 교훈을 기록한 **다음 거래일 2026-08-06에 또**. 그래서 등록부의
`daily-axes-measured`가 등록 다음 날 바로 `재발`로 찍혔다.

절차를 문서에 적는 것으로는 안 돈다 — 이틀치 실측이다. 스케줄러가 부르는 하나의 진입점으로
묶는다("Messiah-Postmarket", 평일 15:45).

## 왜 `run_l1_daily.py` 종료 절차가 아니라 별도인가

`verify_archive_volume.py`는 **KIS REST를 호출한다.** 15:35~15:40 종료 예산 안에 넣으면
종료 절차가 네트워크에 의존하게 되고, 그 판단은 이미 한 번 내려져 기록돼 있다
(`NEXT_TODO.md` "거래량 외부 대조의 장후 자동화"). 그 판단을 뒤집지 않으면서 사람 손만
뺀다 — 종료가 끝난 **뒤에** 별도 프로세스로 돈다.

네트워크를 안 타는 재합성은 반대로 종료 절차 **안**으로 들어갔다
(`run_l1_daily._recompose_today`) — 리포트가 그 결과를 보고 쓰여야 하기 때문이다.
여기서 한 번 더 부르는 것은 그 프로세스가 비정상 종료했을 때를 위한 그물이다.

## 순서가 강제되는 이유

    1. 재합성        상위 Horizon 봉을 1분봉과 정합시킨다 (네트워크 없음)
    2. 거래량 대조    공식 분봉 대비 — logs/volume_check_{날짜}.json
    3. 변동성 축 채점  20거래일 창 — logs/vol_scorecard_{날짜}.json
    4. 리포트 재생성   위 셋의 산출물을 읽어 `unmeasured`를 비운다  ← 반드시 마지막

4번이 마지막이 아니면 리포트는 여전히 "미측정"이라고 말한다. 그게 2026-08-06의 상태였다.

## 실패해도 다음 단계를 돈다

한 단계가 실패해도 멈추지 않는다 — 거래량 대조가 KIS 500으로 실패한 날에 변동성 채점까지
같이 못 하면 손해가 두 배다. 대신 **무엇이 실패했는지 요약에 남기고 종료 코드로 알린다**
(조용한 성공 금지, L18).

## "종료 코드 1"은 실패가 아니다 (2026-08-06 실측)

이 도구들의 공통 규약은 **exit 1 = "그 도구가 볼 것을 찾았다"**이지 "도구가 실패했다"가
아니다. `daily_integrity_report.py` 머리말에 그대로 적혀 있다 — *"임계 초과 항목이 있으면
1 — 사람이 요약을 읽어야만 알 수 있으면 결국 안 읽는다"*. `verify_archive_volume.py`도
의심일이 있으면 1을 낸다.

처음 짤 때 이 구분을 안 해서, 8/6 복구 실행에서 리포트가 정상 산출됐는데도(위반 13→8,
`horizon_findings` 5→0) 요약이 **"1개 단계 실패"** 라고 말했다. 임계 초과가 하나라도 있는
날은 매일 그렇게 찍힌다 — 이 프로젝트가 이름 붙여 경계해 온 늑대소년이 정확히 이 형태다
(`configs/pending_verifications.yaml`의 "넓은 그물은 늑대소년을 만든다").

그래서 종료 코드를 넷으로 읽는다:

    0                      완료
    1                      완료 — **발견한 것이 있다**(리포트를 읽어야 한다). 실패 아님
    2(REFUSED_EXIT_CODE)   session_guard가 거부 — 실패
    3                      조회 대상 심볼이 그날 아카이브에 없다 — 단계 진입 전 중단
    그 밖                   진짜 실패

## 심볼은 날짜가 정한다 (2026-08-14 F-A)

`--symbol`의 기본값은 **`--date`의 근월물**이다. 종전엔 `default="A05608"`이 소스에 박혀
있었고, 만기가 있는 값을 상수로 둔 대가를 2026-08-14(첫 월물 롤)에 치렀다 — 5단계 중
4단계가 **전날 만기된 월물**을 조회했고, 도구들은 저마다 "0행"을 정상 산출로 리포트에 썼다.
아무도 실패하지 않아 종료 코드는 0이었고, `fix_verification`이 그 리포트를 읽어 재발 12건을
찍었다(1건 허위·3건 수치 오류). 그날 3/5단계(`verify_archive_volume.py`)만 옳았는데,
이유는 그 도구가 `--symbol`을 아예 안 받고 스스로 찾도록 만들어졌기 때문이다.

해석은 `backfill.front_month_code_for_day(day)`가 한다 — **날짜를 받는다**는 것이 요점이다
(마스터파일 조회는 날짜를 안 받아 소급 실행에서 조용히 틀린다. `_resolve_symbol` 참고).

사용:
    python scripts/run_postmarket.py                     # 오늘 · 심볼 자동
    python scripts/run_postmarket.py --date 2026-08-06   # 특정일 소급 · 그날의 근월물
    python scripts/run_postmarket.py --symbol A05609     # 명시(자동 해석을 덮어쓴다)
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from messiah.core import logging as mlog  # noqa: E402
from messiah.core.config import load_instance  # noqa: E402
from messiah.core.event_calendar import EventCalendar  # noqa: E402
from messiah.core.messages import Horizon  # noqa: E402
from messiah.data import backfill, bar_paths  # noqa: E402
from messiah.ops import session_guard  # noqa: E402

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _PROJECT_ROOT / "scripts"
_BAR_DIR = _PROJECT_ROOT / "data" / "bars"

# 한 단계가 이만큼 넘게 걸리면 뒤 단계까지 밀린다 — 15:45에 시작해 장 마감 리뷰 시각까지는
# 끝나야 한다. 변동성 채점(20거래일 재계산)이 가장 무거워 여유를 크게 잡는다.
_STEP_TIMEOUT_SECONDS = 900

# 조회 대상 심볼이 그날 아카이브에 없다 — 5단계에 들어가기 전에 멈춘다 (2026-08-14 F-A).
# `session_guard.REFUSED_EXIT_CODE`(2)와 **다른 값이어야 한다**: 2는 "장중이라 거부"라는
# 뜻이고 이건 "볼 곳을 잘못 잡았다"라 원인도 조치도 다르다. 같은 숫자를 쓰면 요약이
# 두 사건을 한 문장으로 말하게 되고, 그게 오늘 리포트가 저지른 실수와 같은 종류다.
_SYMBOL_MISMATCH_EXIT_CODE = 3


@dataclass(frozen=True)
class Step:
    """실행할 단계 하나 — 이름과 명령줄, 그리고 **exit 1을 어떻게 읽을지**."""

    name: str
    argv: list[str]
    # exit 1이 "발견 있음"인 도구인가(모듈 docstring "종료 코드 1은 실패가 아니다").
    # 재합성처럼 발견이라는 개념이 없는 도구는 False — 거기서 1이 나오면 진짜 실패다.
    one_means_finding: bool = False


@dataclass(frozen=True)
class StepResult:
    name: str
    ok: bool
    detail: str
    # 도구가 "볼 것을 찾았다"고 말했는가 — 실패는 아니지만 사람이 읽어야 한다.
    finding: bool = False

    @property
    def mark(self) -> str:
        if not self.ok:
            return "❌"
        return "⚠" if self.finding else "✅"


def _parse_day(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()  # noqa: DTZ007 — 날짜만 다루는 CLI 인자


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MESSIAH 장후 절차 일괄 실행")
    parser.add_argument("--date", type=_parse_day, default=None, help="기본: 오늘 KST")
    parser.add_argument(
        "--symbol",
        default=None,
        help="기본: --date의 근월물을 만기 규칙으로 해석(월물 롤 당일에도 옳다)",
    )
    parser.add_argument("--configs", default="configs")
    parser.add_argument(
        "--skip-rest",
        action="store_true",
        help="KIS REST를 쓰는 단계(거래량 대조)를 건너뛴다 — 망 장애 시 나머지라도 돌리려고",
    )
    session_guard.add_force_intraday_argument(parser)
    return parser.parse_args()


def _python() -> str:
    """이 스크립트를 돌린 인터프리터를 그대로 쓴다 — `.venv` 밖에서 불려도 같은 환경."""
    return sys.executable


def _resolve_symbol(explicit: str | None, day: date) -> tuple[str, str]:
    """그날의 조회 대상 심볼과 그 출처 — (심볼, 출처 설명) (2026-08-14 F-A).

    ## 왜 마스터파일이 아니라 만기 규칙인가

    `symbol_master.front_month_future_code()`는 **날짜를 안 받는다** — 오늘 내려받은 마스터
    파일의 근월물을 답한다. 장후 배치는 `--date 2026-08-12` 같은 소급 실행이 정상 경로이고,
    그때 오늘의 근월물을 답하면 **조용히 틀린다**(그 실행은 성공으로 끝나고 리포트만 거짓이
    된다 — 2026-08-14에 정확히 그 형태로 하루치 채점이 오염됐다).

    `backfill.front_month_code_for_day()`는 날짜를 받고, 만기 규칙의 정본이
    `core/event_calendar.py` 하나로 모여 있다(그쪽 주석). 네트워크도 안 탄다.
    실측: 08-12·08-13 → A05608 / 08-14 → A05609 / 09-10 → A05609 / 09-11 → A05610.

    달력을 못 읽으면 만기 보정 없이 진행한다 — 부가 정보 하나 때문에 배치 전체가 죽는 것이
    훨씬 나쁘다(`EventCalendar` 예외를 삼키는 다른 소비처들과 같은 판단).
    """
    if explicit:
        return explicit, "명시"
    try:
        calendar: EventCalendar | None = EventCalendar.from_file()
    except Exception:  # noqa: BLE001 — 달력 부재로 배치를 죽이지 않는다
        calendar = None
    return backfill.front_month_code_for_day(day, calendar), "근월물 자동 해석"


def _has_day(symbol: str, day: date) -> bool:
    """그날 1분봉이 아카이브에 있는가 — 통합본이든 조각이든.

    경로를 직접 조립하지 않고 `bar_paths.day_sources()`에 위임한다(그쪽 모듈 docstring:
    *"경로를 직접 조립하지 말 것 — 그러면 장중에 그날 데이터가 통째로 안 보인다"*).
    """
    return bool(bar_paths.day_sources(_BAR_DIR, symbol, Horizon.M1, day))


def _symbols_holding_day(day: date) -> list[str]:
    """그날 1분봉을 실제로 가진 심볼들 — 오조회 시 "그럼 누가 갖고 있나"를 답한다."""
    if not _BAR_DIR.is_dir():
        return []
    return sorted(
        entry.name for entry in _BAR_DIR.iterdir() if entry.is_dir() and _has_day(entry.name, day)
    )


def _run_step(step: Step) -> StepResult:
    """한 단계를 자식 프로세스로 돌리고 종료 코드를 규약대로 읽는다.

    자식 프로세스인 이유: 네 도구 다 자기 `main()`에서 `sys.argv`를 파싱하고
    `session_guard`를 직접 부른다. 같은 프로세스에서 import해 부르면 argv를 갈아끼우는
    잔재주가 필요하고, 한 도구의 `SystemExit`가 나머지를 끊는다.
    """
    printable = " ".join(
        Path(part).name if part.endswith(".py") else part for part in step.argv[1:]
    )
    print(f"\n=== {step.name} — {printable}", flush=True)
    try:
        completed = subprocess.run(  # noqa: S603 — 인자는 전부 이 파일이 만든 고정 문자열
            step.argv,
            cwd=_PROJECT_ROOT,
            timeout=_STEP_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return StepResult(step.name, False, f"{_STEP_TIMEOUT_SECONDS}초 내에 안 끝남")
    except Exception as exc:  # noqa: BLE001 — 한 단계 실패가 나머지를 막지 않는다
        return StepResult(step.name, False, f"실행 실패: {exc}")

    code = completed.returncode
    if code == 0:
        return StepResult(step.name, True, "완료")
    if code == 1 and step.one_means_finding:
        # 도구가 정상 동작했고 볼 것을 찾았다 — 실패로 세면 늑대소년이 된다(docstring).
        return StepResult(step.name, True, "완료 — 볼 것이 있다(위 출력 참조)", finding=True)
    if code == session_guard.REFUSED_EXIT_CODE:
        return StepResult(step.name, False, "session_guard가 거부(장중이거나 아카이브 손상)")
    return StepResult(step.name, False, f"종료 코드 {code}")


def _steps(args: argparse.Namespace, day: date, symbol: str) -> list[Step]:
    """실행할 단계 목록 — **순서가 계약이다**(모듈 docstring).

    `symbol`을 `args`에서 다시 꺼내지 않고 인자로 받는다 — 해석된 값이 흘러야 하고,
    두 곳에서 각자 꺼내면 그 순간 갈라질 수 있다(오늘 사고의 형태가 정확히 그것이다).
    """
    stamp = day.isoformat()
    planned: list[Step] = [
        # **재합성보다 먼저** 조각을 통합한다 (2026-08-07 P1-1). 통합은 원래 `run_l1_daily.py`
        # 종료 시퀀스에만 있었는데, 그 시퀀스는 프로세스가 15:35까지 살아야 돈다 — 2026-08-07엔
        # 13:41에 죽어 1분봉이 조각 디렉터리로 남았다. 장후 절차는 프로세스가 죽어도 돈다.
        Step(
            "1/5 장중 조각 통합",
            [
                _python(),
                str(_SCRIPTS / "run_compact.py"),
                "--symbol",
                symbol,
                "--date",
                stamp,
            ],
            # 통합 실패는 데이터 손실이 아니지만(조각은 읽힌다) "발견"도 아니다 — 진짜 실패다.
            one_means_finding=False,
        ),
        Step(
            "2/5 상위 Horizon 재합성",
            [
                _python(),
                str(_SCRIPTS / "run_recompose.py"),
                "--symbol",
                symbol,
                "--start",
                stamp,
                "--end",
                stamp,
                "--include-today",
            ],
            # 재합성에는 "발견"이라는 개념이 없다 — 1이 나오면 진짜 실패다.
            one_means_finding=False,
        ),
    ]
    if not args.skip_rest:
        planned.append(
            Step(
                "3/5 공식 분봉 대비 거래량 대조",
                [
                    _python(),
                    str(_SCRIPTS / "verify_archive_volume.py"),
                    "--date",
                    stamp,
                    "--configs",
                    args.configs,
                ],
                one_means_finding=True,  # 1 = 의심일 발견(재백필 안내를 출력한다)
            )
        )
    planned.append(
        Step(
            "4/5 변동성 축 채점",
            [
                _python(),
                str(_SCRIPTS / "run_vol_scorecard.py"),
                "--date",
                stamp,
                "--symbol",
                symbol,
                "--configs",
                args.configs,
            ],
            one_means_finding=True,
        )
    )
    # 반드시 마지막 — 앞 단계들의 산출물을 읽어 `unmeasured`를 비운다.
    planned.append(
        Step(
            "5/5 무결성 리포트 재생성",
            [
                _python(),
                str(_SCRIPTS / "daily_integrity_report.py"),
                "--date",
                stamp,
                "--symbol",
                symbol,
                "--configs",
                args.configs,
            ],
            one_means_finding=True,  # 1 = 임계 초과 있음(그 CLI의 명시된 규약)
        )
    )
    return planned


def _instance_id(config_dir: str) -> str:
    try:
        return load_instance(config_dir).instance_id
    except Exception:  # noqa: BLE001 — 설정을 못 읽어도 장후 절차는 돌아야 한다
        return "unset"


def main() -> int:
    args = _parse_args()
    session_guard.refuse_if_regular_session("장후 절차 일괄 실행", force=args.force_intraday)

    # **이 프로세스도 자기 세션 경계를 남긴다** (2026-08-12 F-5).
    #
    # 종전엔 `postmarket_*.log`에 `SessionStart` 한 줄이 있었지만 그건 **자식**
    # (`daily_integrity_report.py`)이 찍은 것이었고, 이 프로세스 자신은 시작도 끝도 말하지
    # 않았다. 그래서 장후 배치가 3/5단계에서 죽어도 어떤 축도 조용했다 —
    # SYSTEM.md R13(종료 시퀀스 자기검증)·금지계명 14가 요구하는 것을, "장후 배치보다 먼저
    # 결론 내지 말라"는 운영 규율의 근거 자체가 못 갖추고 있었다.
    mlog.setup(_instance_id(args.configs))

    day = args.date or datetime.now().astimezone().date()  # noqa: DTZ005 — 로컬=KST 전제
    symbol, origin = _resolve_symbol(args.symbol, day)
    print(f"=== MESSIAH 장후 절차 — {day.isoformat()} / {symbol} ({origin}) ===", flush=True)
    mlog.log(
        "SymbolResolved",
        f"{day.isoformat()} 조회 대상 {symbol} ({origin})",
        date=day.isoformat(),
        symbol=symbol,
        origin=origin,
    )
    if args.skip_rest:
        print("  (--skip-rest: 거래량 대조 생략 — 리포트에 '미측정'으로 남는다)", flush=True)

    # 각 단계에 `--force-intraday`를 물려준다 — 이 스크립트가 이미 그 판단을 한 번 했는데
    # 자식이 다시 거부하면 "통과시킨 줄 알았는데 아무것도 안 돌았다"가 된다.
    passthrough = ["--force-intraday"] if args.force_intraday else []

    # 단계 목록은 **한 번만** 만든다 — 종전엔 `try`와 `finally`에서 각각 `_steps()`를 불러
    # 같은 계산이 두 벌 돌았다. 값이 같으니 무해했지만, 심볼 해석이 들어온 지금은 두 벌이
    # 갈릴 수 있는 자리다(그게 오늘 사고의 형태다).
    planned_steps = _steps(args, day, symbol)

    # `try/finally`인 이유: 예외로 죽는 경로에서도 마커를 남겨야 "죽었다"가 관측된다.
    # 마커가 예외 때만 빠지면 **가장 알고 싶은 날에만 없는 축**이 된다.
    results: list[StepResult] = []
    try:
        # **5단계에 들어가기 전에 조회 대상을 검증한다** (2026-08-14 F-A).
        #
        # 2026-08-14에 배치는 만기된 A05608을 조회했고, 네 도구가 저마다 "0행"을 정상
        # 산출로 리포트에 썼다. 아무도 실패하지 않았기 때문에 종료 코드도 0이었다 —
        # 그리고 `fix_verification`이 그 리포트를 읽어 재발 12건을 찍었다(1건 허위·3건
        # 수치 오류). **조회 대상이 틀린 것을 "데이터가 없다"로 통과시키면 하루치 채점
        # 전체가 조용히 거짓이 된다**(R10 · 금지계명 12).
        if not _has_day(symbol, day):
            holders = _symbols_holding_day(day)
            detail = (
                f"보유 심볼: {', '.join(holders)}" if holders else "그날 데이터를 가진 심볼 없음"
            )
            mlog.log(
                "SymbolResolutionMismatch",
                f"{day.isoformat()} {symbol}의 1분봉이 아카이브에 없다 — 중단 ({detail})",
                date=day.isoformat(),
                symbol=symbol,
                origin=origin,
                symbols_holding_day=holders,
            )
            print(
                f"\n  ** 조회 대상 불일치 ** — {symbol}/1m/{day.isoformat()} 부재. {detail}.\n"
                f"     휴장일이면 정상이다. 아니라면 --symbol로 명시하거나 해석 규칙을 확인할 것.",
                file=sys.stderr,
                flush=True,
            )
            return _SYMBOL_MISMATCH_EXIT_CODE

        results = [
            _run_step(Step(step.name, step.argv + passthrough, step.one_means_finding))
            for step in planned_steps
        ]

        print("\n=== 장후 절차 요약 ===", flush=True)
        for result in results:
            print(f"  {result.mark} {result.name} — {result.detail}", flush=True)

        failed = [r for r in results if not r.ok]
        if failed:
            # 조용한 실패 금지 — 무엇이 안 돌았는지 한 줄로 말하고 종료 코드로도 알린다.
            print(
                f"\n  ** {len(failed)}개 단계 실패 ** — 리포트의 `미측정`/`horizon_findings`가 "
                "그대로 남는다. 등록부(daily-axes-measured)가 다음 채점에서 잡는다.",
                file=sys.stderr,
                flush=True,
            )
            return 1

        findings = [r for r in results if r.finding]
        if findings:
            # 실패와 **다른 사건**이다 — 절차는 다 돌았고, 그 절차가 볼 것을 찾았다.
            print(
                f"\n  전 단계 완료 — 그중 {len(findings)}개가 볼 것을 찾았다. "
                "리포트의 임계 초과 목록을 읽을 것.",
                flush=True,
            )
            return 0
        print("\n  전 단계 완료 — 발견 없음. 리포트의 `미측정`이 비어 있어야 한다.", flush=True)
        return 0
    finally:
        planned = len(planned_steps)
        failed_count = sum(1 for r in results if not r.ok)
        # 문구는 `run_l1_daily.py`/`run_g2_paper_trading.py`와 같은 형식("정상 종료")을
        # 쓰되, 몇 단계까지 갔는지를 함께 남긴다 — 중간에 죽은 날 그 숫자가 곧 진단이다.
        mlog.log(
            "SessionEnd",
            "정상 종료" if len(results) == planned and not failed_count else "중단",
            process="postmarket",
            date=day.isoformat(),
            steps_planned=planned,
            steps_run=len(results),
            steps_failed=failed_count,
            steps_with_findings=sum(1 for r in results if r.finding),
        )


if __name__ == "__main__":
    raise SystemExit(main())
