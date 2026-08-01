"""Command Center UI 네이티브 크래시 재현 프로브 (2026-07-31).

## 왜 이 스크립트가 있는가

2026-07-29·07-30·07-31 사흘 연속으로 UI가 **완전히 같은 주소**에서 죽었다
(`_polars_runtime.pyd` +0x083973c7, 0xc0000005 access violation, 총 10건). 그 사흘 동안 두 번
"고쳤다"고 판단했고 두 번 다 틀렸다 — 원자적 쓰기(07-30), PAR1 매직 검사(07-30)를 넣은 뒤에도
같은 오프셋으로 재발했다.

같은 실수를 세 번 하지 않으려면 **수정 전에 재현되고 수정 후에 안 재현되는 절차**가 있어야
한다. 이 스크립트가 그 절차다. 네이티브 크래시는 파이썬 예외가 아니라 프로세스 즉사라 traceback이
안 남으므로, 판정은 **종료 코드**로 한다(0이 아니면 죽은 것 — Windows access violation은
0xC0000005 = 3221225477).

## 무엇을 재현하는가

07-31 로그가 가리키는 유력 원인은 "찢어진 파일"이 아니라 **프로세스 내 동시성**이다:

- UI 로그에 `partially initialized module 'numpy'`가 3건 — 두 스레드가 동시에 numpy 최초
  임포트를 밟을 때만 나오는 형태다.
- 첫 polars 크래시가 `st.fragment(run_every=5)` 도입 53분 뒤였고, 그 이전 날짜엔 0건이었다.
- 07-31 크래시는 기동(08:35)이 아니라 **10:42**부터 시작됐다 — Streamlit은 브라우저 세션이
  붙어야 스크립트를 돌리므로, 사람이 화면을 연 시점이다.

그래서 이 프로브는 Streamlit 없이 그 구조만 떼어낸다: **읽기 스레드 N개**가 동시에
`_load_bars_with_status()` → `_candlestick_figure()`를 돌리고, 그 옆에서 **쓰기 스레드 1개**가
같은 파일을 계속 갈아끼운다(실제 L1 수집기와 같은 `ParquetArchiver.append_bar()`).

## ⚠ 현재 이 프로브는 크래시를 재현하지 못한다 (2026-07-31 실측)

수정 전 코드 경로를 그대로 되살린 대조군(락 없음 + polars 객체를 plotly에 직행)을 읽기
8스레드 × 30초로 돌렸더니 **차트 6,347회를 그리고도 안 죽었다**(파이썬 예외 0건, 종료 코드 0).
즉 **이 프로브의 통과는 "고쳤다"의 근거가 되지 못한다.** 정직하게 기록해 둔다.

못 잡는 이유로 생각해볼 것:
- numpy 최초 임포트 레이스는 이 스크립트가 `messiah.ui.app`을 임포트하는 순간 **메인
  스레드에서 이미 끝나버린다** — 실제 Streamlit은 ScriptRunner 스레드가 그 임포트를 처음
  밟는 구조라 조건 자체가 다르다.
- Streamlit의 ScriptRunner는 스레드마다 자기 컨텍스트·재실행 스케줄을 갖고, `st.fragment`가
  렌더 도중 스크립트를 다시 실행한다 — 단순 스레드 풀로는 그 재진입을 재현 못 한다.
- 07-31 크래시는 2시간에 6번, 즉 대략 20분에 한 번꼴이었다 — 30초 부하로는 표본이 모자랄
  수 있다(`--seconds 1800` 같은 장시간 실행이 필요할지도).

따라서 지금 이 프로브의 용도는 **회귀 하니스**(적어도 이 부하에서는 안 죽는다는 하한 보증)
이지 근본 원인 증명이 아니다. 진짜 판정은 다음 거래일 `logs/daily_integrity_*.json`의
`native_crashes`가 0건인지로 한다 — 07-29·07-30에 두 번 "고쳤다"고 판단했다가 같은 오프셋으로
재발한 이력이 있으므로, 그 확인 전에는 해결로 간주하지 않는다.

사용:
    python scripts/probe_ui_concurrency.py                 # 기본 20초·읽기 4스레드
    python scripts/probe_ui_concurrency.py --seconds 60 --readers 8
    python scripts/probe_ui_concurrency.py --no-writer     # 순수 동시 읽기만

종료 코드 0 = 살아남음. 그 외 = 네이티브 크래시(값이 곧 예외 코드).
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from messiah.core.messages import BarClosed, Horizon  # noqa: E402
from messiah.data.archiver import ParquetArchiver  # noqa: E402
from messiah.ui.app import _candlestick_figure, _load_bars_with_status  # noqa: E402

_KST = timezone(timedelta(hours=9))
_SYMBOL = "A05608"
_HORIZON = "1m"
_DAY = date(2026, 7, 31)
# 07-31 실제 하루치 봉 수(380행)에 맞춘다 — 프레임 크기가 작으면 경쟁 구간 자체가 짧아진다.
_SEED_BARS = 380


def _bar(index: int) -> BarClosed:
    return BarClosed(
        symbol=_SYMBOL,
        horizon=Horizon.M1,
        bar_open_kst=datetime(2026, 7, 31, 9, 0, tzinfo=_KST) + timedelta(minutes=index),
        o_ticks=51800 + (index % 7),
        h_ticks=51814,
        l_ticks=51790 + (index % 5),
        c_ticks=51805 + (index % 3),
        volume=1 + index % 17,
        quality_ok=True,
    )


def _seed(bar_dir: Path) -> None:
    archiver = ParquetArchiver(bar_dir)
    for i in range(_SEED_BARS):
        archiver.append_bar(_bar(i))


class _Counters:
    def __init__(self) -> None:
        self.reads = 0
        self.figures = 0
        self.writes = 0
        self.errors: list[str] = []
        self.lock = threading.Lock()


def _reader(bar_dir: Path, stop: threading.Event, counters: _Counters) -> None:
    while not stop.is_set():
        try:
            bars, _warning = _load_bars_with_status(_SYMBOL, _HORIZON, _DAY, bar_dir)
            with counters.lock:
                counters.reads += 1
            if bars is not None and not bars.is_empty():
                _candlestick_figure(bars, tick_size=0.02)
                with counters.lock:
                    counters.figures += 1
        except Exception as exc:  # noqa: BLE001 — 파이썬 예외는 크래시와 구분해 기록만 한다
            with counters.lock:
                counters.errors.append(f"{type(exc).__name__}: {exc}")


def _writer(bar_dir: Path, stop: threading.Event, counters: _Counters) -> None:
    archiver = ParquetArchiver(bar_dir)
    index = _SEED_BARS
    while not stop.is_set():
        try:
            archiver.append_bar(_bar(index % (_SEED_BARS + 60)))
            index += 1
            with counters.lock:
                counters.writes += 1
        except Exception as exc:  # noqa: BLE001
            with counters.lock:
                counters.errors.append(f"writer {type(exc).__name__}: {exc}")
        time.sleep(0.01)


def main() -> int:
    parser = argparse.ArgumentParser(description="Command Center UI 동시성 크래시 프로브")
    parser.add_argument("--seconds", type=float, default=20.0)
    parser.add_argument("--readers", type=int, default=4)
    parser.add_argument("--no-writer", action="store_true")
    args = parser.parse_args()

    workdir = Path(tempfile.mkdtemp(prefix="messiah_ui_probe_"))
    bar_dir = workdir / "bars"
    try:
        print(f"[probe] 시드 봉 {_SEED_BARS}개 생성 중 — {bar_dir}", flush=True)
        _seed(bar_dir)

        stop = threading.Event()
        counters = _Counters()
        threads = [
            threading.Thread(target=_reader, args=(bar_dir, stop, counters), daemon=True)
            for _ in range(args.readers)
        ]
        if not args.no_writer:
            threads.append(
                threading.Thread(target=_writer, args=(bar_dir, stop, counters), daemon=True)
            )

        print(
            f"[probe] 읽기 {args.readers}스레드"
            f"{'' if args.no_writer else ' + 쓰기 1스레드'} × {args.seconds:.0f}초 시작",
            flush=True,
        )
        for thread in threads:
            thread.start()
        time.sleep(args.seconds)
        stop.set()
        for thread in threads:
            thread.join(timeout=10.0)

        print(
            f"[probe] 완료 — 읽기 {counters.reads}회 · 차트 {counters.figures}회 · "
            f"쓰기 {counters.writes}회 · 파이썬 예외 {len(counters.errors)}건",
            flush=True,
        )
        for message in counters.errors[:5]:
            print(f"  - {message}", flush=True)
        print("[probe] 프로세스 생존 — 네이티브 크래시 없음", flush=True)
        return 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
