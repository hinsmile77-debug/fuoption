"""거래 대상(유니버스) 어휘 — 2026-08-04 확정.

    선물: KOSPI200 **미니**선물
    옵션: **먼쓰리**(정규 월물) · **월위클리** · **목위클리**

`configs/instance.yaml`의 `universe`, `KISBrokerAdapter.probe_front_month()`의 product
인자, `OptionChainPoller`의 series 인자가 전부 이 모듈의 토큰을 쓴다 — 어휘가 세 곳에
흩어져 있으면 "설정엔 있는데 아무도 안 읽는 항목"이 생기기 때문이다. 실제로 그랬다(아래).

## 왜 옵션을 시리즈별 토큰으로 쪼갰나

종전 어휘는 옵션이 `K200_OPT` **하나**였다. 그런데 2026-08-04 점검에서 드러난 실상은:

- `_PROBE_PRODUCT_TYPES`(adapter.py)에 `K200_OPT`가 없어 `probe_front_month()`는 ValueError
- `OptionChainPoller`는 `series="regular"` 기본값으로 **먼쓰리만** 볼 수 있었고, 애초에
  **어떤 스크립트에도 결선돼 있지 않았다**(테스트에서만 인스턴스화)
- 즉 `K200_OPT`는 설정 파일에 적혀 있을 뿐 **아무도 소비하지 않는 죽은 토큰**이었다.
  `InvestorFlowPoller`가 2026-07-27~08-04에 그랬던 것과 정확히 같은 실패 형태다.

먼쓰리/월위클리/목위클리는 **만기 주기가 달라 체인 크기·잔존만기·롤 시점·그릭스 민감도가
전부 다르다.** 하나의 토큰으로 묶으면 "옵션을 수집한다"고 적어 놓고 실제로는 셋 중 어느
것도 안 하거나 하나만 하는 상태를 구분할 수 없다. 그래서 시리즈마다 토큰을 준다 —
설정을 읽는 것만으로 무엇이 켜져 있는지 확정되게.

## 미니옵션은 유니버스에 없다

`symbol_master`에 코드(`D`/`E`)가 정의돼 있고 `series="mini"`로 조회도 되지만, 2026-07-22
실측에서 **상장 종목이 0/0이었다**(같은 날 먼쓰리는 콜 390·풋 390). 물건이 없어서 뺀 것이지
설계상 배제한 게 아니다 — 상장이 확인되면 토큰만 추가하면 된다.

## 정합성은 테스트가 지킨다

`OPTION_SERIES_BY_TOKEN`의 값은 `broker/kis/symbol_master.py`의 series 이름과 같아야 하는데,
여기서 import하지 않고 문자열로 적는다: core가 broker를 import하면 의존 방향이 뒤집힌다.
대신 `tests/test_universe.py`가 두 목록의 일치를 검사한다(어긋나면 조용히 빈 체인을 도는
대신 테스트가 깨진다).
"""

from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------- 토큰

K200_MINI_FUT: Final = "K200_MINI_FUT"  # KOSPI200 미니선물 (틱 0.02 실측, A056xx)
K200_OPT_MONTHLY: Final = "K200_OPT_MONTHLY"  # 먼쓰리 — 정규 월물 옵션
K200_OPT_WEEKLY_MON: Final = "K200_OPT_WEEKLY_MON"  # 월위클리 — 월요일 만기
K200_OPT_WEEKLY_THU: Final = "K200_OPT_WEEKLY_THU"  # 목위클리 — 목요일 만기

# 2026-08-04 확정 유니버스(모듈 docstring). 순서는 선물 → 옵션(만기 주기 긴 것부터).
DEFAULT_UNIVERSE: Final[tuple[str, ...]] = (
    K200_MINI_FUT,
    K200_OPT_MONTHLY,
    K200_OPT_WEEKLY_MON,
    K200_OPT_WEEKLY_THU,
)

# 옵션 토큰 -> symbol_master의 series 이름(`_SERIES_PRODUCT_TYPES` 키).
OPTION_SERIES_BY_TOKEN: Final[dict[str, str]] = {
    K200_OPT_MONTHLY: "regular",
    K200_OPT_WEEKLY_MON: "weekly_mon",
    K200_OPT_WEEKLY_THU: "weekly_thu",
}

# 선물 토큰 -> `KISBrokerAdapter.probe_front_month()`가 받는 product 문자열.
# 정규선물(`K200_FUT`)은 어댑터가 여전히 받지만 유니버스에는 없다 — 교차검증·비교용이지
# 거래 대상이 아니다(Holding Policy "미니선물 표준").
FUTURES_TOKENS: Final[frozenset[str]] = frozenset({K200_MINI_FUT})

KNOWN_TOKENS: Final[frozenset[str]] = FUTURES_TOKENS | frozenset(OPTION_SERIES_BY_TOKEN)


class UnknownUniverseTokenError(ValueError):
    """유니버스에 소비자가 없는 토큰이 들어왔다 — 설정에 적어 두고 아무도 안 읽는 상태를
    기동 시점에 막는다(`K200_OPT`가 그랬다, 모듈 docstring)."""


def validate(tokens: list[str]) -> list[str]:
    """
    입력: `configs/instance.yaml`의 `universe` 목록.
    실패 조건: 모르는 토큰이 하나라도 있으면 `UnknownUniverseTokenError`. 조용히 무시하면
         "옵션도 수집하는 줄 알았는데 안 하고 있었다"가 그대로 재현된다.
    반환: 입력 그대로(체이닝 편의).
    """
    unknown = [t for t in tokens if t not in KNOWN_TOKENS]
    if unknown:
        raise UnknownUniverseTokenError(
            f"소비자가 없는 유니버스 토큰: {unknown} — 사용 가능: {sorted(KNOWN_TOKENS)}. "
            f"(2026-08-04 이전의 'K200_OPT'는 시리즈별 토큰 3개로 쪼개졌다: "
            f"{sorted(OPTION_SERIES_BY_TOKEN)})"
        )
    return tokens


def option_series(tokens: list[str]) -> list[str]:
    """유니버스에서 옵션 시리즈 이름만 뽑는다 — `OptionChainPoller(series=...)`에 그대로
    넘길 수 있는 형태. 선물 토큰은 걸러진다. 순서는 `tokens` 순서를 따른다."""
    return [OPTION_SERIES_BY_TOKEN[t] for t in tokens if t in OPTION_SERIES_BY_TOKEN]


def futures_tokens(tokens: list[str]) -> list[str]:
    """유니버스에서 선물 토큰만 — `probe_front_month(product=...)`에 그대로 넘긴다."""
    return [t for t in tokens if t in FUTURES_TOKENS]
