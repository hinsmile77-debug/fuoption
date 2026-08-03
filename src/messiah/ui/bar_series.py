"""차트가 그리는 봉 데이터의 **불변 스냅샷** (2026-08-03 P0-2).

## 왜 DataFrame을 그대로 돌려주면 안 되나

2026-07-31에 UI 크래시 대응으로 `_BarFileCache`에 프로세스 전역 락을 넣었다. 의도는
"모든 ScriptRunner 스레드의 polars 네이티브 호출을 직렬화한다"였는데, **코드상 그게 성립하지
않았다**: 락은 Parquet **파싱**만 감쌌고, `load()`는 캐시에 든 `pl.DataFrame` **객체 자체**를
돌려줬다. 그래서 실제 소비 경로는 전부 락 밖이었다 —

    bars.is_empty()                    # app.py, 락 밖
    bars[col].to_list()          × 4   # _candlestick_figure, 락 밖
    bars["bar_open_kst"].to_list()     # _candlestick_figure, 락 밖

즉 **여러 스레드가 같은 폴라스 객체를 동시에 만지는 경로가 그대로 남아 있었고**, 08-03에
같은 fault offset으로 2건 더 죽었다(11:25:18·14:20:18).

이 클래스는 그 구멍을 구조로 막는다. 캐시가 프레임이 아니라 **이미 파이썬 기본형으로 변환이
끝난 불변 스냅샷**을 들고 있으면, 락 밖으로 나가는 값에 polars 객체가 **하나도 없다**. 변환은
락 안에서 정확히 한 번 일어난다. "락을 잘 걸었나"를 사람이 매번 확인해야 하는 규율 문제가,
"락 밖에 polars 타입이 존재할 수 없다"는 타입 문제로 바뀐다.

부수 효과로 재변환도 사라진다 — 예전엔 5초마다 재렌더할 때마다 `to_list()`를 5번씩 다시
돌렸는데(캐시 히트여도), 이제 파일이 바뀔 때만 변환한다.

## 왜 틱을 그대로 담나

`tick_size`는 사이드바 입력값이라 **파일이 안 바뀌어도 사람이 바꿀 수 있다**. 가격으로
환산해 캐시하면 틱 크기를 바꿨을 때 캐시가 낡은 값을 계속 준다. 환산은 렌더 시점에 한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class BarSeries:
    """차트 1개를 그리는 데 필요한 전부 — 전부 파이썬 기본형이고 전부 불변이다.

    `x_kst`는 **naive KST 벽시계**다(tzinfo 없음). tz-aware로 넘기면 plotly가 자기 기준으로
    다시 해석할 여지가 생기는데, 그 버그를 2026-07-29에 이미 한 번 겪었다(09:00 개장봉이
    화면엔 00:00으로 찍힘). 여기서 벽시계 값으로 못박아 그 여지를 없앤다.

    가격은 **틱 단위 정수**다(모듈 docstring "왜 틱을 그대로 담나").
    """

    x_kst: tuple[datetime, ...]
    o_ticks: tuple[int, ...]
    h_ticks: tuple[int, ...]
    l_ticks: tuple[int, ...]
    c_ticks: tuple[int, ...]

    def __post_init__(self) -> None:
        lengths = {
            len(self.x_kst),
            len(self.o_ticks),
            len(self.h_ticks),
            len(self.l_ticks),
            len(self.c_ticks),
        }
        if len(lengths) != 1:
            raise ValueError(f"컬럼 길이가 서로 다름: {lengths}")

    def __len__(self) -> int:
        return len(self.x_kst)

    @property
    def is_empty(self) -> bool:
        return not self.x_kst
