"""완성봉(BarClosed) Parquet 적재 — Master Plan Ver 2.0 §9 "L1 DATA: Archiver(Parquet)".

Digital Twin(W9~11)이 날짜·심볼·Horizon 단위로 바로 읽을 수 있게
{base_dir}/{symbol}/{horizon}/{date}.parquet로 파티셔닝한다.

## 조각 쓰기 + 장후 통합 (고도화 3, 2026-07-30)

**장중에는** `{symbol}/{horizon}/{date}/{HH}.parquet`(시간대 조각)에 쓰고, **장 마감 후**
`compact_day()`가 그 조각들을 하루 1개 `{date}.parquet`로 합친 뒤 조각을 지운다.

이유는 append 비용이다. 원래는 봉 하나를 추가할 때마다 **그날 파일 전체**를 읽어-합쳐-다시
썼다 — 하루가 갈수록 다시 쓰는 양이 선형으로 늘어 총비용이 O(n²)이 된다(1분봉 정규장 405분
기준 마지막 봉 하나를 넣으려고 404행을 다시 쓴다). 조각으로 나누면 다시 쓰는 범위가 한 시간
(최대 60행)으로 고정된다.

**저장 포맷을 바꾼 것이 아니다** — 하루가 끝나면 물리 배치가 조각화 이전과 정확히 같아진다.
Digital Twin·백테스트 하니스·Replay는 전부 지난 날짜만 보므로 영향이 없고, 당일 데이터를
보는 소비자(Command Center UI·피처 웜스타트·무결성 리포트)는 `read_day()`/`day_sources()`
/`available_days()`를 쓰면 두 배치의 차이를 몰라도 된다. **경로를 직접 조립하지 말 것** —
그러면 장중에 그날 데이터가 통째로 안 보인다.

2026-07-23 발견: 경로·중복제거 키에 horizon이 없었다(symbol/date만) — M1만 있을 때는 문제가
없었지만, W6~8에서 3/5/10/15/30m 합성봉을 같은 심볼·같은 날짜에 적재하기 시작하면 서로 다른
Horizon의 봉이 같은 bar_open_kst를 가질 수 있어(예: 5m봉과 1m봉이 둘 다 09:30:00에 시작)
unique()가 이걸 중복으로 오인해 조용히 하나를 지우는 사고가 날 뻔했다 — 실제로 여러 Horizon을
적재해보기 전에는 아무도 몰랐던 문제. 경로와 dedup 키 둘 다에 horizon을 추가해 원천 차단.

주의(2026-07-22 실측): polars는 tz-aware datetime 컬럼을 Parquet에 쓸 때 내부적으로 UTC로
정규화하고, 다시 읽을 때 zoneinfo로 "UTC" 존을 조회한다 — Windows에는 시스템 tzdata가 없어
`tzdata` 패키지가 설치돼 있지 않으면 읽기 단계에서 ZoneInfoNotFoundError가 난다
(pyproject.toml에 sys_platform=='win32' 조건부 의존성으로 추가함).

## 원자적 쓰기 (2026-07-30 사고 대응)

`append_bar()`는 원래 `combined.write_parquet(path)`로 **대상 파일을 제자리에서 덮어썼다** —
즉 쓰기 도중에는 파일이 truncate된 중간 상태로 관측 가능했다. 같은 파일을 Command Center
UI(`ui/app.py`의 `_load_bars()`)가 **다른 프로세스에서 5초마다** 읽고 있었기 때문에, 이
중간 상태를 읽은 UI가 하루 1~2회꼴로 네이티브 크래시(access violation, `_polars_runtime.pyd`,
0xc0000005)로 죽었다 — 2026-07-29 13:28 / 15:21, 2026-07-30 08:57 실측 3건(fault offset
세 번 모두 동일). 스크래치 프로브로 재현도 확인했다: 쓰기 1 + 읽기 2 프로세스를 60초 돌리자
읽기측이 전부 `range end index 90 out of range for slice of length 0`(= 길이 0인 파일을 읽음)
으로 죽었다.

그래서 이제 같은 디렉터리의 임시 파일에 먼저 쓰고 `os.replace()`로 교체한다 — rename은
원자적이라 읽는 쪽은 **항상 이전 완본 아니면 새 완본**만 보고, 찢어진 중간 상태를 볼 수 없다.

임시 파일 이름에 PID를 넣는 건 여러 프로세스가 같은 경로에 동시에 append할 때 서로의 임시
파일을 덮어쓰지 않게 하기 위함이다(지금은 수집기 하나뿐이지만, 그 전제가 조용히 깨지면
데이터가 섞이는 형태로 나타나므로 미리 막아둔다).

**재시도가 필요한 이유(Windows 고유)**: polars의 읽기는 파일을 메모리 매핑한다 — 매핑이
살아있는 동안 그 파일을 rename/삭제하려 하면 Windows가 `OSError(winerror 1224,
ERROR_USER_MAPPED_FILE)`로 거부한다(위 프로브에서 쓰기측이 실제로 60초에 6회 관측). 예전
코드는 이 실패를 호출측(`collector.py`/`bar_composer.py`)이 로깅만 하고 넘겼기 때문에 **그 봉이
아카이브에서 영구 소실**됐다 — 다음 append는 파일을 새로 읽어 합치므로 빠진 봉은 두 번 다시
안 채워진다. 짧은 백오프로 몇 번 재시도하면 UI의 매핑은 곧 풀리므로 실질적으로 사라진다.
"""

from __future__ import annotations

import io
import os
import time
from contextlib import suppress
from datetime import date
from pathlib import Path
from typing import Callable, Sequence

import polars as pl

from messiah.core.messages import BarClosed, BarSession, Horizon
from messiah.core.timeutil import to_kst
from messiah.data import bar_paths

# `core/timeutil.py`의 KST는 고정 오프셋 timezone 객체라 polars의 `convert_time_zone()`에
# 그대로 못 넘긴다(존 이름 문자열을 받는다) — `ui/app.py`가 쓰는 것과 같은 IANA 이름을 쓴다.
_KST_ZONE_NAME = "Asia/Seoul"

# 교체 재시도 — UI의 mmap이 풀리기를 기다리는 용도라 짧게 여러 번이 맞다(총 대기 최대 ~0.35초,
# 1분에 한 번 도는 봉 적재 주기에 비해 무시할 수 있는 시간).
_REPLACE_ATTEMPTS = 5
_REPLACE_BACKOFF_SECONDS = 0.05


class TornParquetError(ValueError):
    """Parquet으로서 형태 자체가 성립하지 않는 바이트 — 쓰기 도중 파일을 읽었을 때 나온다.

    `ValueError`를 상속해 호출측의 일반적인 "읽기 실패" 처리 경로에 자연히 걸린다."""


_PARQUET_MAGIC = b"PAR1"
_PARQUET_MIN_SIZE = 12  # 헤더 매직(4) + 최소 footer 길이(4) + 꼬리 매직(4)


def read_parquet_without_mmap(path: Path) -> pl.DataFrame:
    """파일 전체를 바이트로 먼저 읽고, **온전한 Parquet인지 확인한 뒤** 메모리에서 파싱한다.

    ## 왜 mmap을 피하나

    `pl.read_parquet(path)`는 파일을 메모리 매핑한다(모듈 docstring "재시도가 필요한 이유").
    매핑을 안 남기면 ① 이 프로세스가 남의 `os.replace()`를 1224로 막지 않고 ② 읽는 도중
    파일이 교체돼도 이미 읽어둔 바이트로 계속 파싱한다(매핑된 페이지가 무효화돼 access
    violation으로 죽는 경로가 없어진다).

    ## 바이트 복사만으로는 부족했다 (2026-07-30 10:01 실측)

    바이트로 읽도록 바꾼 뒤에도 Command Center UI가 **같은 fault offset으로 또 죽었다**
    (`_polars_runtime.pyd` +0x083973c7). mmap 무효화는 없앴지만 **잘린 바이트를 네이티브
    파서에 그대로 넘기는 경로**가 남아 있었던 것이다 — 제자리 덮어쓰기 중인 파일을 읽으면
    내용이 중간까지만 있는데, polars의 Parquet 디코더는 그 안의 오프셋을 믿고 읽다가 범위
    밖을 건드린다(파이썬 예외가 아니라 프로세스 즉사).

    그래서 파싱 전에 형태를 먼저 본다: Parquet은 파일 맨 앞과 맨 뒤가 모두 `PAR1`이어야
    한다. 쓰기는 순차적이므로 **꼬리 매직은 완성된 파일에만 존재한다** — 이 검사 하나로
    "아직 다 안 써진 파일"이 네이티브 파서에 도달하는 것을 원천 차단한다. 비용은 바이트
    4개 비교다.

    실패 조건: `TornParquetError` — 호출측은 이걸 "지금은 읽을 수 없음"으로 다루면 된다
              (`ui/app.py`는 직전 성공본으로 버티고, `load_recent_bars()`는 그 파일을
              건너뛴다). 조용히 빈 프레임을 돌려주지 않는다(L18).
    """
    raw = path.read_bytes()
    if len(raw) < _PARQUET_MIN_SIZE:
        raise TornParquetError(f"{path.name}: {len(raw)}바이트 — 쓰기 중인 파일로 보임")
    if not raw.startswith(_PARQUET_MAGIC) or not raw.endswith(_PARQUET_MAGIC):
        raise TornParquetError(f"{path.name}: PAR1 매직 불일치 — 쓰기 중인 파일로 보임")
    return pl.read_parquet(io.BytesIO(raw))


class ParquetArchiver:
    def __init__(
        self,
        base_dir: Path,
        *,
        replace: Callable[[Path, Path], None] = os.replace,
        sleep: Callable[[float], None] = time.sleep,
        replace_attempts: int = _REPLACE_ATTEMPTS,
        replace_backoff_seconds: float = _REPLACE_BACKOFF_SECONDS,
    ) -> None:
        """replace/sleep은 재시도 경로를 실제 파일 잠금 없이 테스트하기 위한 주입점이다
        (`core/docker_bootstrap.py`·`core/ui_launcher.py`와 같은 원칙)."""
        self._base_dir = base_dir
        self._replace = replace
        self._sleep = sleep
        self._replace_attempts = max(1, replace_attempts)
        self._replace_backoff_seconds = replace_backoff_seconds

    def append_bar(self, bar: BarClosed) -> None:
        """
        계산: bar를 1행으로 만들어 **그 시각이 속한 시간대 조각 파일**에 합쳐 넣는다 —
             `{symbol}/{horizon}/{date}/{HH}.parquet`. 기존 조각이 있으면 읽어 합치고,
             `bar_open_kst`+`horizon` 기준 중복 제거(나중 값 유지)·정렬한 뒤 임시 파일에
             썼다가 `os.replace()`로 원자적으로 교체한다.
        해석: 같은 분에 대해 두 번 불려도(재시작 후 재처리) 마지막 값으로 덮어써지고 행이
             두 번 쌓이지 않는다 — 조각화 이전과 같은 계약이다. 다시 쓰는 범위만 하루치에서
             한 시간치로 줄었다(모듈 docstring "조각 쓰기" 참고).
        실패 조건: 교체가 재시도 한도까지 계속 실패하면 임시 파일을 지우고 원래 예외를 그대로
                  올린다 — 호출측(collector/bar_composer)이 로깅한다.
        """
        path = self._shard_path_for(bar)
        path.parent.mkdir(parents=True, exist_ok=True)
        new_row = self._bar_to_frame(bar)

        if path.exists():
            # 기존 조각이 옛 스키마일 수 있다(`read_day()`의 `diagonal_relaxed`와 같은 이유).
            combined = pl.concat([read_parquet_without_mmap(path), new_row], how="diagonal_relaxed")
        else:
            combined = new_row

        combined = combined.unique(subset=["bar_open_kst", "horizon"], keep="last").sort(
            "bar_open_kst"
        )
        self._write_atomic(combined, path)

    def write_day(self, symbol: str, horizon: Horizon, bars: Sequence[BarClosed]) -> int:
        """하루치를 통합본 1개로 **통째로 교체**한다 — 백필 전용(`data/backfill.py`).

        `append_bar()`와 두 가지가 다르고, 둘 다 백필이라는 용도에서 나온다:

        1. **행 단위가 아니라 하루 단위**다. 백필은 410봉을 한 번에 받아오는데 그걸
           `append_bar()`로 넣으면 조각 파일을 410번 읽고 쓴다(하루가 갈수록 커지는 그
           O(n²)를 피하려고 조각화를 도입한 것인데 그 비용을 그대로 되살리는 셈).
        2. **합치지 않고 덮어쓴다.** 백필의 목적 자체가 "우리가 수집한 값을 거래소 공식
           값으로 갈아끼우는 것"이라(2026-08-04 — WS 다중 레코드 유실로 거래량이 절반이었다,
           `data/normalizer.py` 모듈 docstring) 기존 행과 병합하면 안 된다. 조각도 함께
           지운다 — 안 지우면 `read_day()`가 `keep="last"`로 **조각을 이긴 것으로** 취급해
           (조각이 통합본보다 나중 소스) 옛 오염 값이 되살아난다.

        반환: 쓴 행 수. `bars`가 비면 아무것도 안 하고 0(빈 파일로 그날을 지우지 않는다 —
             백필 실패와 "그날 휴장"을 구분할 수 없게 되므로 호출측이 판단하게 둔다).
        """
        if not bars:
            return 0
        day = to_kst(bars[0].bar_open_kst).date()
        frame = pl.concat([self._bar_to_frame(bar) for bar in bars], how="diagonal_relaxed")
        frame = frame.unique(subset=["bar_open_kst", "horizon"], keep="last").sort("bar_open_kst")

        path = self._canonical_path(symbol, horizon, day)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_atomic(frame, path)

        shard_dir = self._shard_dir(symbol, horizon, day)
        if shard_dir.is_dir():
            for shard in shard_dir.glob("*"):
                shard.unlink(missing_ok=True)
            with suppress(OSError):
                shard_dir.rmdir()
        return frame.height

    # ------------------------------------------------------------ 읽기 (조각 + 통합본)

    @property
    def base_dir(self) -> Path:
        """읽기 캐시가 키에 포함해야 하는 값 — 같은 (심볼, Horizon, 날짜)라도 아카이브
        루트가 다르면 다른 데이터다(테스트가 실제로 이 충돌을 잡아냈다)."""
        return self._base_dir

    def day_sources(self, symbol: str, horizon: Horizon, day: date) -> list[Path]:
        """그날치를 구성하는 파일들 — 실제 규칙은 `data/bar_paths.py`에 있다(2026-08-03에
        polars 없는 경로 계층으로 분리 — Command Center UI가 polars를 임포트하지 않고도
        "파일이 바뀌었나"를 판단해야 해서). 계약은 그대로다."""
        return bar_paths.day_sources(self._base_dir, symbol, horizon, day)

    def read_day(self, symbol: str, horizon: Horizon, day: date) -> pl.DataFrame | None:
        """하루치를 하나의 프레임으로 — 통합 전이든 후든 호출측은 차이를 몰라도 된다.

        읽기에 실패한 파일은 건너뛴다(쓰기 중인 조각을 만났을 때). 전부 실패하거나 아무
        파일도 없으면 None.
        """
        frames: list[pl.DataFrame] = []
        for path in self.day_sources(symbol, horizon, day):
            try:
                frames.append(read_parquet_without_mmap(path))
            except Exception:  # noqa: BLE001 — 조각 하나가 나머지를 못 읽게 만들면 안 됨
                continue
        if not frames:
            return None
        # `how="diagonal_relaxed"`: 스키마가 서로 다른 조각도 합친다(없는 컬럼은 null).
        # 컬럼이 추가되는 날(2026-07-31의 `session`)에는 **하루 안에서도** 통합본은 옛 스키마,
        # 새 조각은 새 스키마인 상태가 실제로 생긴다 — 기본 `concat`은 그 순간 예외를 던지고,
        # 그러면 그날 데이터가 UI·웜스타트·리포트에서 통째로 사라진다(스키마 변경이 조용한
        # 데이터 사고로 번지는 전형적 경로).
        return (
            pl.concat(frames, how="diagonal_relaxed")
            .unique(subset=["bar_open_kst", "horizon"], keep="last")
            .sort("bar_open_kst")
        )

    def read_day_bars(self, symbol: str, horizon: Horizon, day: date) -> list[BarClosed]:
        """`read_day()`의 BarClosed 판 — 프레임이 아니라 도메인 객체가 필요한 소비자용
        (백필의 연속 시계열 구성, 상위 Horizon 오프라인 재합성). 그날 데이터가 없으면 빈 목록.

        이게 없던 동안 호출측이 `_frame_to_bars()`를 직접 불렀는데, 그건 private이라
        스키마 보정 로직(옛 Parquet의 없는 `session` 컬럼 처리 등)이 바뀔 때 같이 안 따라올
        위험이 있었다.
        """
        frame = self.read_day(symbol, horizon, day)
        if frame is None or frame.height == 0:
            return []
        return self._frame_to_bars(frame, symbol, horizon)

    def available_days(self, symbol: str, horizon: Horizon) -> list[date]:
        """통합본과 조각 디렉터리 양쪽에서 날짜를 모아 오름차순으로 (`data/bar_paths.py`)."""
        return bar_paths.available_days(self._base_dir, symbol, horizon)

    # ------------------------------------------------------------ 장후 통합

    def compact_day(self, symbol: str, horizon: Horizon, day: date) -> int:
        """조각들을 하루 1개 통합본(`{date}.parquet`)으로 합치고 조각 디렉터리를 지운다.

        **과거 데이터의 물리 배치를 조각화 이전과 동일하게 유지하는 것**이 이 함수의 목적이다
        — Digital Twin·백테스트 하니스·Replay가 전부 `{date}.parquet`를 직접 여는 코드를
        갖고 있고(그 경로들은 하루가 끝난 뒤의 데이터만 본다), 그것들을 전부 고치는 대신
        "장중에만 조각, 장 끝나면 원래 모양"으로 되돌린다. 즉 조각화는 **장중 쓰기 비용을
        줄이는 내부 최적화**이지 저장 포맷 변경이 아니다.

        반환: 통합본에 들어간 행 수(조각이 없으면 0 — 이미 통합됐거나 그날 데이터가 없음).
        순서: 통합본을 원자적으로 먼저 쓰고 **그 다음에** 조각을 지운다 — 반대로 하면 중간에
             죽었을 때 데이터가 사라진다.
        """
        shard_dir = self._shard_dir(symbol, horizon, day)
        if not shard_dir.is_dir():
            return 0

        frame = self.read_day(symbol, horizon, day)
        if frame is None or frame.height == 0:
            return 0

        self._write_atomic(frame, self._canonical_path(symbol, horizon, day))
        for shard in shard_dir.glob("*"):
            shard.unlink(missing_ok=True)
        with suppress(OSError):  # 다른 프로세스가 조각을 새로 쓰는 중이면 다음 기회에 지운다
            shard_dir.rmdir()
        return frame.height

    def _write_atomic(self, frame: pl.DataFrame, path: Path) -> None:
        tmp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        frame.write_parquet(tmp_path)
        try:
            self._replace_with_retry(tmp_path, path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

    def _replace_with_retry(self, tmp_path: Path, path: Path) -> None:
        for attempt in range(self._replace_attempts):
            try:
                self._replace(tmp_path, path)
                return
            except OSError:
                if attempt == self._replace_attempts - 1:
                    raise
                self._sleep(self._replace_backoff_seconds)

    def load_recent_bars(
        self, symbol: str, horizon: Horizon, *, on_or_before: date, max_bars: int
    ) -> list[BarClosed]:
        """
        입력: on_or_before는 KST 벽시계 날짜(파일 이름과 같은 기준). 그날 파일도 포함한다 —
             장중 재시작 시 오늘 오전에 이미 쌓인 봉을 되찾는 것이 이 함수의 주요 용도라
             "오늘 제외"면 그 목적을 못 이룬다.
        계산: 해당 (symbol, horizon) 디렉터리에서 on_or_before 이하 날짜 파일을 최신부터
             역순으로 읽어 max_bars개가 찰 때까지 모은 뒤, 시간 오름차순으로 정렬해 돌려준다.
        해석: `bar_open_kst`는 Parquet 왕복에서 UTC로 정규화돼 저장되므로(모듈 docstring)
             여기서 KST로 되돌린다 — 이 값의 `.date()`가 `SessionState`의 일자 롤오버 판정에
             쓰이는데, UTC 벽시계 그대로면 장전 08:45 KST 봉이 전일(23:45 UTC)로 잡혀 세션
             경계가 어긋난다.
        실패 조건: 읽기에 실패한 파일은 건너뛴다 — 웜스타트는 부가 기능이지 기동 전제조건이
                  아니다(`core/docker_bootstrap.py`류의 원칙과 동일). 디렉터리 자체가 없으면
                  빈 리스트.
        """
        if max_bars <= 0:
            return []

        collected: list[BarClosed] = []
        for day in sorted(self.available_days(symbol, horizon), reverse=True):
            if day > on_or_before:
                continue
            frame = self.read_day(symbol, horizon, day)
            if frame is None:
                continue  # 깨진 파일 하나가 웜스타트 전체를 막지 않는다
            collected = self._frame_to_bars(frame, symbol, horizon) + collected
            if len(collected) >= max_bars:
                break

        collected.sort(key=lambda b: b.bar_open_kst)
        return collected[-max_bars:]

    @staticmethod
    def _frame_to_bars(frame: pl.DataFrame, symbol: str, horizon: Horizon) -> list[BarClosed]:
        frame = frame.with_columns(
            pl.col("bar_open_kst").dt.convert_time_zone(_KST_ZONE_NAME)
        ).sort("bar_open_kst")
        return [
            BarClosed(
                symbol=symbol,
                horizon=horizon,
                bar_open_kst=row["bar_open_kst"],
                o_ticks=row["o_ticks"],
                h_ticks=row["h_ticks"],
                l_ticks=row["l_ticks"],
                c_ticks=row["c_ticks"],
                volume=row["volume"],
                quality_ok=row["quality_ok"],
                # `session`은 2026-07-31에 추가됐다 — 그 전 Parquet에는 컬럼 자체가 없고,
                # `diagonal_relaxed` 병합에서는 null로 들어온다. 둘 다 REGULAR로 읽는다
                # (`core/messages.py`의 `BarClosed.session` 기본값과 같은 근거).
                session=row.get("session") or BarSession.REGULAR,
            )
            for row in frame.iter_rows(named=True)
        ]

    def _canonical_path(self, symbol: str, horizon: Horizon, day: date) -> Path:
        return bar_paths.canonical_path(self._base_dir, symbol, horizon, day)

    def _shard_dir(self, symbol: str, horizon: Horizon, day: date) -> Path:
        return bar_paths.shard_dir(self._base_dir, symbol, horizon, day)

    def _shard_path_for(self, bar: BarClosed) -> Path:
        day = bar.bar_open_kst.date()
        return self._shard_dir(bar.symbol, bar.horizon, day) / f"{bar.bar_open_kst:%H}.parquet"

    @staticmethod
    def _bar_to_frame(bar: BarClosed) -> pl.DataFrame:
        return pl.DataFrame(
            {
                "symbol": [bar.symbol],
                "horizon": [bar.horizon.value],
                "bar_open_kst": [bar.bar_open_kst],
                "o_ticks": [bar.o_ticks],
                "h_ticks": [bar.h_ticks],
                "l_ticks": [bar.l_ticks],
                "c_ticks": [bar.c_ticks],
                "volume": [bar.volume],
                "quality_ok": [bar.quality_ok],
                "session": [bar.session.value],
            }
        )


# 2026-08-03에 `data/bar_paths.py`로 옮겼다 — 기존 임포트 경로
# (`from messiah.data.archiver import day_signature`)를 깨지 않으려고 여기서 재수출한다.
day_signature = bar_paths.day_signature
