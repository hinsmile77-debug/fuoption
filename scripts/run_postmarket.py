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

그래서 종료 코드를 셋으로 읽는다:

    0                      완료
    1                      완료 — **발견한 것이 있다**(리포트를 읽어야 한다). 실패 아님
    2(REFUSED_EXIT_CODE)   session_guard가 거부 — 실패
    그 밖                   진짜 실패

사용:
    python scripts/run_postmarket.py                    # 오늘
    python scripts/run_postmarket.py --date 2026-08-06  # 특정일 소급
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

from messiah.ops import session_guard  # noqa: E402

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _PROJECT_ROOT / "scripts"

# 한 단계가 이만큼 넘게 걸리면 뒤 단계까지 밀린다 — 15:45에 시작해 장 마감 리뷰 시각까지는
# 끝나야 한다. 변동성 채점(20거래일 재계산)이 가장 무거워 여유를 크게 잡는다.
_STEP_TIMEOUT_SECONDS = 900


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
    parser.add_argument("--symbol", default="A05608")
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


def _steps(args: argparse.Namespace, day: date) -> list[Step]:
    """실행할 단계 목록 — **순서가 계약이다**(모듈 docstring)."""
    stamp = day.isoformat()
    planned: list[Step] = [
        Step(
            "1/4 상위 Horizon 재합성",
            [
                _python(),
                str(_SCRIPTS / "run_recompose.py"),
                "--symbol",
                args.symbol,
                "--start",
                stamp,
                "--end",
                stamp,
                "--include-today",
            ],
            # 재합성에는 "발견"이라는 개념이 없다 — 1이 나오면 진짜 실패다.
            one_means_finding=False,
        )
    ]
    if not args.skip_rest:
        planned.append(
            Step(
                "2/4 공식 분봉 대비 거래량 대조",
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
            "3/4 변동성 축 채점",
            [
                _python(),
                str(_SCRIPTS / "run_vol_scorecard.py"),
                "--date",
                stamp,
                "--symbol",
                args.symbol,
                "--configs",
                args.configs,
            ],
            one_means_finding=True,
        )
    )
    # 반드시 마지막 — 앞 단계들의 산출물을 읽어 `unmeasured`를 비운다.
    planned.append(
        Step(
            "4/4 무결성 리포트 재생성",
            [
                _python(),
                str(_SCRIPTS / "daily_integrity_report.py"),
                "--date",
                stamp,
                "--symbol",
                args.symbol,
                "--configs",
                args.configs,
            ],
            one_means_finding=True,  # 1 = 임계 초과 있음(그 CLI의 명시된 규약)
        )
    )
    return planned


def main() -> int:
    args = _parse_args()
    session_guard.refuse_if_regular_session("장후 절차 일괄 실행", force=args.force_intraday)

    day = args.date or datetime.now().astimezone().date()  # noqa: DTZ005 — 로컬=KST 전제
    print(f"=== MESSIAH 장후 절차 — {day.isoformat()} / {args.symbol} ===", flush=True)
    if args.skip_rest:
        print("  (--skip-rest: 거래량 대조 생략 — 리포트에 '미측정'으로 남는다)", flush=True)

    # 각 단계에 `--force-intraday`를 물려준다 — 이 스크립트가 이미 그 판단을 한 번 했는데
    # 자식이 다시 거부하면 "통과시킨 줄 알았는데 아무것도 안 돌았다"가 된다.
    passthrough = ["--force-intraday"] if args.force_intraday else []

    results = [
        _run_step(Step(step.name, step.argv + passthrough, step.one_means_finding))
        for step in _steps(args, day)
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


if __name__ == "__main__":
    raise SystemExit(main())
