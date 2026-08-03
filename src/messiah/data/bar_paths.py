"""봉 아카이브의 **경로·발견 계층** — polars를 임포트하지 않는다 (2026-08-03 P0-1(b)).

## 왜 archiver.py에서 떼어냈나

Command Center UI는 봉 파일의 **내용**을 이 프로세스에서 파싱하지 않기로 했다
(`ui/bar_reader.py` — 5거래일 연속 같은 access violation으로 죽어서, 파싱을 자식 프로세스로
밀어냈다). 그런데 UI가 여전히 필요한 게 두 가지 남는다:

    ① 어떤 날짜가 있나          → `available_days()`   (REPLAY 날짜 선택기)
    ② 파일이 바뀌었나           → `day_sources()` + `day_signature()`  (재읽기 판단)

둘 다 **파일 이름과 stat만** 보면 되는 순수 경로 연산인데, `data/archiver.py`에 얹혀 있어서
그걸 임포트하는 순간 `import polars`가 딸려 들어왔다. 그러면 "UI 프로세스에 polars를 안
올린다"는 목표가 성립하지 않는다 — 파싱을 자식에게 넘겼는데 런타임은 여전히 부모에 로드된
상태가 된다.

그래서 polars가 필요 없는 부분만 여기로 내렸다. `ParquetArchiver`는 이 함수들에 위임하므로
기존 호출자(`archiver.day_sources(...)` 등)의 계약은 그대로다 — 이 파일은 **새 계층이지 새
규칙이 아니다**.

## 배치 규칙 (`data/archiver.py`의 "조각 쓰기"와 같은 규칙)

    통합본:      {base}/{symbol}/{horizon}/{date}.parquet        (장후)
    조각:        {base}/{symbol}/{horizon}/{date}/{HH}.parquet   (장중)

이름이 겹치지 않는 게 핵심이다 — `glob("*.parquet")`에 조각 디렉터리는 안 걸리고,
디렉터리 순회에 통합본 파일은 안 걸린다. **경로를 직접 조립하지 말 것** — 그러면 장중에
그날 데이터가 통째로 안 보인다.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Sequence

from messiah.core.messages import Horizon


def canonical_path(base_dir: Path, symbol: str, horizon: Horizon, day: date) -> Path:
    """장후 통합본 경로."""
    return base_dir / symbol / horizon.value / f"{day.isoformat()}.parquet"


def shard_dir(base_dir: Path, symbol: str, horizon: Horizon, day: date) -> Path:
    """장중 시간대 조각 디렉터리 — 통합본은 `2026-07-30.parquet`(파일), 조각 디렉터리는
    `2026-07-30`(디렉터리)이라 이름이 겹치지 않는다."""
    return base_dir / symbol / horizon.value / day.isoformat()


def day_sources(base_dir: Path, symbol: str, horizon: Horizon, day: date) -> list[Path]:
    """그날치를 구성하는 파일들 — **읽기 순서대로** 돌려준다(통합본 먼저, 조각 나중).

    이 순서가 중복 제거의 의미를 결정한다: 조각은 통합 이후에 다시 수집된 것이므로
    같은 봉이 양쪽에 있으면 조각이 이긴다(`keep="last"`). 장중 재시작으로 통합본이
    이미 있는 날에 조각이 새로 생기는 경우가 실제로 이 조합이다.
    """
    sources: list[Path] = []
    canonical = canonical_path(base_dir, symbol, horizon, day)
    if canonical.exists():
        sources.append(canonical)
    shards = shard_dir(base_dir, symbol, horizon, day)
    if shards.is_dir():
        sources.extend(sorted(shards.glob("*.parquet")))
    return sources


def available_days(base_dir: Path, symbol: str, horizon: Horizon) -> list[date]:
    """통합본과 조각 디렉터리 양쪽에서 날짜를 모아 오름차순으로."""
    horizon_dir = base_dir / symbol / horizon.value
    if not horizon_dir.is_dir():
        return []
    days = {day for day in (parse_day(p) for p in horizon_dir.glob("*.parquet")) if day}
    days |= {
        day for day in (parse_day_name(p.name) for p in horizon_dir.iterdir() if p.is_dir()) if day
    }
    return sorted(days)


def day_signature(paths: Sequence[Path]) -> tuple[tuple[str, int, int], ...]:
    """읽기 캐시용 지문 — 파일 집합의 (이름, mtime, 크기).

    조각화 이후로는 "하루치"가 파일 하나가 아니라 여러 개다(`day_sources()`) — 캐시가 파일
    하나의 mtime만 보면 새 시간대 조각이 생겼을 때 갱신을 놓친다. 존재하지 않게 된 파일은
    자연히 지문에서 빠지므로 통합(조각 삭제)도 변경으로 감지된다.
    """
    signature: list[tuple[str, int, int]] = []
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            continue  # 지금 막 사라진 파일(통합 중) — 다음 갱신에 자연히 반영된다
        signature.append((path.name, stat.st_mtime_ns, stat.st_size))
    return tuple(signature)


def parse_day(path: Path) -> date | None:
    """`{date}.parquet` 파일명에서 날짜만 — 임시 파일(`*.parquet.{pid}.tmp`)이나 사람이
    떨궈둔 다른 파일은 stem이 ISO 날짜가 아니므로 자연히 걸러진다."""
    return parse_day_name(path.stem)


def parse_day_name(name: str) -> date | None:
    try:
        return date.fromisoformat(name)
    except ValueError:
        return None
