"""사이드카 계약 — 봉 밖의 **일별 상태**를 피처에 공급하는 것들의 공통 규약
(2026-08-04 신설, F0-2).

## 무엇이 사이드카인가

PX/VL은 봉만 보면 계산된다. FL·OP·RG는 아니다 — 일별 투자자 순매수, ATM IV 252일 이력,
환율·금리 일봉처럼 **완성봉에 실려 오지 않는 상태**가 필요하다. 그 상태를 들고 있다가
"이 날짜 기준으로 뭘 알고 있었나"를 답하는 객체가 사이드카다.

`FlowHistory`(2026-08-04)가 첫 구현체였고, 이 모듈은 거기서 이미 굳어진 계약을 **다음
카테고리가 다시 발명하지 않도록** 이름 붙인 것이다.

## 두 종류가 있고, 섞으면 안 된다 (2026-08-04, F1)

- **관측 사이드카**(`DailySidecar`) — 시장을 관측해서 얻은 값. 일별 투자자 순매수(FL),
  ATM IV 이력(OP), 환율·금리 일봉(RG). 그날 값은 **장이 끝나야 확정되므로** 아래 "엄격히
  이전" 계약이 필수다.
- **참조 사이드카**(`ReferenceSidecar`) — 미리 공표된 규칙·일정. KRX 휴장일과 만기
  캘린더(EV). 내일이 휴장일이라는 걸 오늘 아는 것은 **미래 참조가 아니다** — 몇 달 전부터
  공표된 사실이다. 그래서 미래 날짜를 자유롭게 조회해도 된다.

이 구분을 안 하면 둘 중 하나가 틀린다: 참조 데이터에 "엄격히 이전"을 강요하면 `ev_dte_fut`
(만기까지 잔여 거래일)가 아예 계산 불가가 되고, 관측 데이터에 자유 조회를 허용하면 백테스트
성과가 극적으로 좋아진다(= 미래를 본다).

## 계약의 핵심은 하나뿐 — 요청일보다 엄격히 이전만 준다 (관측 사이드카)

그날의 값은 장이 끝나야 확정된다. D일 봉의 피처로 D일 값을 쓰면 그건 미래를 보는 것이다
(그날 외국인이 얼마나 샀는지를 아침에 알 수 없다). 그래서 `as_of(day)`/`recent(day, n)`는
**day보다 엄격히 이전** 거래일만 돌려준다.

이 규율이 없으면 백테스트 성과가 극적으로 좋아지고, 그게 곧 버그의 증상이 된다.

**자르는 곳은 사이드카 하나뿐이다.** 계산기(`fl_core` 등)는 이 계약을 그대로 믿고 쓰며
직접 날짜를 자르지 않는다 — 자르는 곳이 둘이면 한쪽만 고쳐지는 사고가 난다.

## 왜 Protocol인가

`FeatureEngine`은 사이드카가 무엇인지 몰라야 한다(`Mapping[str, object]`로 받아 계산기에
그대로 넘긴다). 그래도 "아무거나 넣어도 되는" 것은 아니라서, 타입 수준에서 계약을 적어
둔다 — `core/bus.BusLike`를 Protocol로 만든 것과 같은 이유다(구체 클래스에 묶지 않되
계약은 명시).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Protocol, Sequence, runtime_checkable


@runtime_checkable
class DailySidecar(Protocol):
    """일별 상태 공급자 — 구현체는 `data/investor_flow_history.FlowHistory` 참고.

    두 메서드 모두 **`day`보다 엄격히 이전**만 본다(모듈 docstring). 이력 시작 전이면
    `as_of()`는 None, `recent()`는 빈 목록 — 0으로 채우지 않는다. 0은 "값이 0이었다"는
    없는 사실을 주장하는 것이고, None은 "모른다"다.
    """

    def as_of(self, day: date) -> object | None:
        """`day`보다 엄격히 이전인 마지막 관측. 없으면 None."""
        ...

    def recent(self, day: date, n: int) -> Sequence[object]:
        """`day` 이전 최근 n개(오래된 것 → 최신). 누적·연속·표준화 계열의 입력."""
        ...


@runtime_checkable
class ReferenceSidecar(Protocol):
    """미리 공표된 규칙·일정 — 구현체는 `core/event_calendar.EventCalendar`.

    관측 사이드카와 달리 **미래 날짜 조회가 허용된다**(모듈 docstring "두 종류"). 최소
    계약은 "이 날이 거래일인가"뿐이고, 나머지 질의(만기·거래일 수 등)는 구현체가 정한다.
    """

    def is_trading_day(self, d: date) -> bool: ...


# 사이드카 키 — 문자열을 여기저기 흩어 쓰면 오타가 조용한 미주입으로 끝난다(엔진이 생성
# 시점에 거부하긴 하지만, 그 전에 이름을 한 곳에 모아 두는 편이 낫다).
FLOW = "flow"
CALENDAR = "calendar"


def build(
    spec,
    *,
    holidays_path: "str | Path | None" = None,
    flow_path: "str | Path | None" = None,
) -> dict[str, object]:
    """`FeatureSpec`이 요구하는 사이드카만 만들어 돌려준다.

    호출처가 넷(trainer·backtest harness·run_l1_daily·run_feature_gate)이라 각자 조립하면
    네 벌이 갈린다 — 한 곳에서만 만든다. 스펙이 안 요구하는 것은 **만들지 않는다**(엔진이
    잉여 주입을 거부하므로 만들어 봐야 기동이 깨진다).

    실패 조건: 요구된 사이드카의 원천을 못 읽으면 그대로 예외를 올린다. 조용히 빈 이력을
              돌려주면 그 카테고리가 전부 None인 채로 학습에 들어가고, 그건 `nan_ratio`로만
              흐릿하게 드러난다(L18 조용한 폴백 금지).
    """
    from messiah.core.event_calendar import DEFAULT_HOLIDAYS_PATH, EventCalendar

    out: dict[str, object] = {}
    for name in spec.required_sidecars:
        if name == CALENDAR:
            out[name] = EventCalendar.from_file(holidays_path or DEFAULT_HOLIDAYS_PATH)
        elif name == FLOW:
            from messiah.data import investor_flow_history as ifh

            if flow_path is None:
                raise ValueError(
                    f"feature_set '{spec.name}'은 '{FLOW}' 사이드카를 요구하는데 "
                    "flow_path가 안 주어졌다"
                )
            out[name] = ifh.FlowHistory(ifh.read(Path(flow_path)))
        else:
            raise ValueError(
                f"모르는 사이드카 '{name}' — features/sidecar.py의 build()에 조립 방법을 "
                "추가할 것(카테고리만 등록하고 조립을 안 붙이면 기동이 깨진다)"
            )
    return out


def describe(sidecars: dict[str, object]) -> str:
    """기동 로그용 한 줄 요약 — 무엇이 얼마나 실려 있는지.

    주입은 됐는데 이력이 비어 있는 경우(파일을 못 읽었다든가)를 첫날 로그에서 보이게 하는
    것이 목적이다. 그 상태에서는 해당 카테고리가 전부 None을 내는데, 벡터 모양은 정상이라
    `nan_ratio`로만 드러나고 원인은 안 보인다.
    """
    if not sidecars:
        return "사이드카 없음"
    parts: list[str] = []
    for key in sorted(sidecars):
        value = sidecars[key]
        try:
            parts.append(f"{key}={len(value)}건")  # type: ignore[arg-type]
        except TypeError:
            # 참조 사이드카(캘린더 등)는 "건수"라는 개념이 없다 — 타입명이라도 찍는다.
            # "?"보다 낫다: 무엇이 주입됐는지가 로그에 남아야 결선 확인이 된다.
            parts.append(f"{key}={type(value).__name__}")
    return " · ".join(parts)
