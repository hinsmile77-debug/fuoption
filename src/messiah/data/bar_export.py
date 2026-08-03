"""차트용 봉 시계열 익스포터 — **자식 프로세스로 실행되는 쪽** (2026-08-03 P0-1(b)).

`python -m messiah.data.bar_export --bar-dir ... --symbol ... --horizon ... --day ...`로
불려서 하루치 봉을 JSON 한 덩어리로 stdout에 뱉는다. 부모(Command Center UI)는
`ui/bar_reader.py`가 담당한다.

## 왜 별도 프로세스인가

Command Center UI가 **5거래일 연속 같은 fault offset**으로 죽었다(`_polars_runtime.pyd`
+0x083973c7, 0xc0000005 — 07-29 2건·07-30 6건·07-31 6건·08-03 2건). 그동안 세 번 원인을
추정해 고쳤고 세 번 재발했다(원자적 쓰기 → 꼬리 매직 검증 → 호출 직렬화). 네 번째 가설을
세우는 대신 **크래시가 나도 화면이 안 죽는 구조**로 바꾼 것이 이 모듈이다.

핵심은 이것이다: 파싱이 이 프로세스에서 일어나면 access violation은 **부모를 즉사**시킨다.
자식에서 일어나면 부모에겐 그냥 `returncode != 0`이다 — 이미 있는 "읽기 실패 → 직전
성공본으로 버틴다" 경로(`ui/app.py`의 `_BarFileCache._degraded()`)에 자연히 흡수된다.
즉 크래시를 **피하는** 게 아니라 **가둔다**. 원인을 끝내 못 밝혀도 화면은 산다.

부수 효과로 REPLAY(과거 날짜)도 같은 보호를 받는다 — 버스로 실시간 봉을 발행하는 방식은
과거 날짜를 못 덮어서 이 방식을 골랐다.

## 왜 비용을 감당할 만한가

프로세스 기동 + polars 임포트가 약 0.8초다(실측). 부모는 **파일이 바뀌었을 때만** 이걸
부른다(`data/bar_paths.py`의 `day_signature()`) — 즉 Horizon당 봉 마감 주기에 한 번,
5m 차트면 5분에 한 번이다. 5초 재렌더의 대부분은 캐시 히트라 자식을 아예 안 띄운다.

## 출력 계약

stdout에 JSON 한 줄. 그날 데이터가 없으면 `null`. 실패는 **stdout이 아니라 종료 코드와
stderr로** 알린다 — 빈 시계열과 실패를 절대 같은 값으로 뭉개지 않는다(L18).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from messiah.core.messages import Horizon

# 부모가 JSON을 payload로 인식하기 전에 반드시 확인하는 표식. polars/streamlit/기타 라이브러리가
# stdout에 경고를 흘려도 payload를 오염시키지 못하게 한다 — 표식 뒤의 마지막 한 줄만 읽는다.
PAYLOAD_MARKER = "@@MESSIAH_BAR_SERIES@@"


def export_day(bar_dir: Path, symbol: str, horizon: Horizon, day: date) -> dict[str, Any] | None:
    """하루치 봉 → JSON 직렬화 가능한 dict. 그날 데이터가 없으면 None.

    polars 임포트를 **함수 안에서** 한다 — 이 모듈을 부모가 실수로 임포트해도(테스트에서
    상수만 가져다 쓰는 경우 등) polars 런타임이 부모에 로드되지 않게 하려는 것이다. 이
    모듈의 존재 이유가 "부모에 polars를 안 올린다"이므로 그 성질을 모듈 자체가 지킨다.
    """
    import polars as pl  # noqa: PLC0415 — 위 docstring 참고(의도적 지연 임포트)

    from messiah.data.archiver import ParquetArchiver

    frame = ParquetArchiver(bar_dir).read_day(symbol, horizon, day)
    if frame is None:
        return None

    # `bar_open_kst`는 Parquet 왕복에서 UTC로 정규화돼 저장된다(`data/archiver.py` 모듈
    # docstring) — KST 벽시계로 되돌린 뒤 tzinfo를 떼어 naive로 넘긴다. tz-aware로 넘기면
    # plotly가 자기 기준으로 다시 해석할 여지가 생기는데, 그 버그는 2026-07-29에 이미 한 번
    # 겪었다(09:00 개장봉이 화면엔 00:00). `ui/bar_series.py`의 계약과 동일하다.
    frame = frame.with_columns(pl.col("bar_open_kst").dt.convert_time_zone("Asia/Seoul"))
    return {
        "x_kst": [ts.replace(tzinfo=None).isoformat() for ts in frame["bar_open_kst"].to_list()],
        "o_ticks": frame["o_ticks"].to_list(),
        "h_ticks": frame["h_ticks"].to_list(),
        "l_ticks": frame["l_ticks"].to_list(),
        "c_ticks": frame["c_ticks"].to_list(),
    }


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="차트용 봉 시계열을 JSON으로 내보낸다")
    parser.add_argument("--bar-dir", required=True, type=Path)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--horizon", required=True)
    parser.add_argument("--day", required=True, help="ISO 날짜(YYYY-MM-DD)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    payload = export_day(
        args.bar_dir, args.symbol, Horizon(args.horizon), date.fromisoformat(args.day)
    )
    print(PAYLOAD_MARKER)
    print(json.dumps(payload, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
