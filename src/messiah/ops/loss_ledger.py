"""오늘 **영구히 잃은 것**의 실시간 장부 (2026-08-10 B-2).

## 왜 이 모듈이 생겼나 — 아는 데 7시간이 걸렸다

2026-08-10에 08:20 정시 트리거가 기동 창 가드에 막혀 08:58에야 수집이 떴다. 그 38분의
체결틱·수급·옵션체인은 **과거 조회 경로가 없어 영원히 없다.** 그리고 그 사실이 사람 눈에
닿은 것은 **15:45 장후 리포트**였다.

화면은 그 시각 내내 초록이었다. 정확히 말하면 화면이 틀린 게 아니다 — 컴포넌트 넷은
정말로 살아 있었다. 화면이 답하던 질문은 *"지금 살아 있나"*였고, 아무 자리도
*"오늘 이미 잃은 것이 있나"*를 묻지 않았다.

같은 날 오후에도 같은 형태가 반복됐다: 14:30에 옵션 다리 1개, 10:46·15:19·15:31에 수급
3행이 사라졌는데 전부 장후에야 드러났다.

## 왜 장부를 따로 두나 — 장후 축과 무엇이 다른가

`ops/series_coverage.py`는 **아카이브를 읽어** 같은 것을 잰다. 정확하지만 장중에 쓰기엔
비싸다(계열 5개의 하루치 파케이를 15초마다 읽게 된다) — 게다가 아카이버가 같은 파일을
쓰는 중이다.

그래서 이쪽은 **일어나는 순간 세는** 인프로세스 카운터다. 두 축은 대체재가 아니라 서로의
검산이다: 장중엔 이 장부가, 장후엔 커버리지가 답하고, **둘이 어긋나면 그 자체가 볼 것이다**
(한쪽은 못 받은 것을 세고 한쪽은 안 쌓인 것을 센다 — 발행과 적재 사이가 새면 갈린다).

## 정확히 무엇을 세는가

- **기동 지연**: 정시 트리거와 실제 기동의 차. 프로세스가 뜨는 순간 한 번 적는다.
  오늘 잃은 것 중 **가장 큰 덩어리**가 대개 이것이다(08-10에 38분).
- **끝내 실패한 조회**: `data/poll_retry.py`가 재시도를 다 쓰고도 못 받은 항목.
  재시도로 살아난 것은 **안 센다** — 그건 손실이 아니라 서버 상태의 기록이다.

세지 않는 것도 분명히 해 둔다: 봉은 KIS 분봉 API로 되메울 수 있어 이 장부의 관심사가
아니다. 여기 오르는 것은 **소급 경로가 없는 것뿐**이고, 그래서 이 숫자는 "나중에 고치면
되는 것"이 아니라 "이미 영원히 없는 것"이다.

## 프로세스 로컬이다

`run_l1_daily.py` 한 프로세스가 폴러와 상태판을 모두 들고 있으므로 전역 상태로 충분하다.
프로세스가 재기동하면 0으로 시작하는데 그건 결함이 아니라 사실이다 — 새 프로세스는 그
전에 무엇을 잃었는지 모르고, 그 구간은 장후 커버리지 축이 아카이브로 판정한다.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

_lock = threading.Lock()
_start_lag_minutes: float | None = None
_lost_by_series: dict[str, int] = {}
# 이번 세션이 **그날 첫 기동이 아니다** (2026-08-19 F-2). 재기동한 프로세스는 이전 세션이
# 무엇을 봤는지 모르므로(모듈 docstring "프로세스 로컬이다"), 정시 트리거와의 차를
# 「기동 지연」이라 부르면 **이미 수집된 아침을 없던 것으로 만든다**.
_restarted_mid_day: bool = False


@dataclass(frozen=True)
class LossLedger:
    """오늘 지금까지의 소급 불가 손실."""

    start_lag_minutes: float | None = None
    lost_by_series: dict[str, int] = field(default_factory=dict)
    # 이번 세션이 장중 재기동인가 (2026-08-19 F-2). True면 `start_lag_minutes`는 **손실이
    # 아니라 경과 시간**이다 — 아래 `describe()`/`clean`이 그렇게 다룬다.
    restarted_mid_day: bool = False

    @property
    def lost_items(self) -> int:
        return sum(self.lost_by_series.values())

    @property
    def clean(self) -> bool:
        """잃은 것이 없는가 — 기동 지연은 `_LAG_FLOOR_MINUTES` 미만이면 없는 것으로 본다.

        **재기동 세션은 지연으로 판정하지 않는다** (2026-08-19 F-2). 그 값은 손실이 아니라
        경과 시간이고, 그날 실제로 잃은 것은 장후 축(`abnormal_exits`의 `mid_session`)이
        아카이브를 읽어야 알 수 있다. 다만 `clean`은 False로 둔다 — 재기동이 있었다는 것
        자체가 「오늘 아무 일도 없었다」는 아니기 때문이다.
        """
        if self.lost_items:
            return False
        if self.restarted_mid_day:
            return False
        return self.start_lag_minutes is None or self.start_lag_minutes <= _LAG_FLOOR_MINUTES

    def describe(self) -> str:
        """화면 한 줄 — 잃은 것이 없으면 **그렇다고 말한다.**

        "0" 을 찍는 이유는 커버리지 표가 정상 계열도 찍는 이유와 같다: *"봤는데 없다"*와
        *"이 자리가 아무것도 안 본다"*가 구분돼야 한다. 후자가 2026-08-10의 상태였다.
        """
        parts: list[str] = []
        if self.restarted_mid_day:
            # **숫자를 지어내지 않는다** (2026-08-19 F-2). 2026-08-19 오후 내내 화면은
            # "오늘 영구 소실 — 기동 지연 249분"이라고 말했다. 그날 08:20~09:50은 정상
            # 수집됐고 실제 손실은 09:50~12:29의 158.9분이었다. 이 프로세스는 그 구간을
            # 알 수 없으므로 **모른다고 말한다**.
            parts.append("장중 재기동 — 이전 세션 소실분은 장후 축이 판정(지금은 미상)")
        elif self.start_lag_minutes is not None and self.start_lag_minutes > _LAG_FLOOR_MINUTES:
            parts.append(f"기동 지연 {self.start_lag_minutes:.0f}분")
        parts.extend(
            f"{name} {count}건" for name, count in sorted(self.lost_by_series.items()) if count
        )
        if not parts:
            return "오늘 소급 불가 손실 없음"
        return "오늘 영구 소실 — " + " · ".join(parts)

    def to_dict(self) -> dict[str, object]:
        return {
            # 재기동 세션에서는 **기동 지연이 아니다** — 그 사실을 필드 이름으로 가른다
            # (2026-08-19 F-2). 같은 키에 다른 뜻을 담으면 읽는 쪽이 알 방법이 없다.
            "start_lag_minutes": None if self.restarted_mid_day else self.start_lag_minutes,
            "minutes_since_trigger": self.start_lag_minutes,
            "restarted_mid_day": self.restarted_mid_day,
            "lost_by_series": dict(self.lost_by_series),
            "lost_items": self.lost_items,
            "clean": self.clean,
            "summary": self.describe(),
        }


# 기동 지연을 손실로 부르는 하한 — `integrity_report.DEFAULT_THRESHOLDS`의
# `collection_start_lag_minutes`와 **같은 값**이다. 두 자리가 같은 질문에 답하는데 임계가
# 다르면 화면과 리포트가 다른 말을 하게 된다(이 저장소가 반복해서 배운 것).
_LAG_FLOOR_MINUTES = 5.0


def record_start_lag(minutes: float | None, *, restarted_mid_day: bool = False) -> None:
    """수집 기동이 정시 트리거보다 얼마나 늦었나 — 프로세스당 한 번.

    None은 **판정 불가**다(등록 정본을 못 읽음) — 0과 다르다(L18).

    `restarted_mid_day`가 True면 그 차는 **지연이 아니라 경과 시간**이다 (2026-08-19 F-2) —
    `ops/session_guard.prior_sessions_today()`가 판정한다.
    """
    global _start_lag_minutes, _restarted_mid_day
    with _lock:
        _start_lag_minutes = minutes
        _restarted_mid_day = restarted_mid_day


def record_lost(series: str, items: int = 1) -> None:
    """소급 경로가 없는 항목을 끝내 못 받았다 — `data/poll_retry.py`가 부른다.

    **재시도로 살아난 것은 부르지 않는다.** 그건 손실이 아니라 서버 상태의 기록이고,
    둘을 같은 숫자로 세면 이 장부가 "오늘 잃은 것"을 더 이상 뜻하지 않게 된다
    (`poll_retry` 모듈 docstring의 태그 3분화와 같은 규율).
    """
    if items <= 0:
        return
    with _lock:
        _lost_by_series[series] = _lost_by_series.get(series, 0) + items


def current() -> LossLedger:
    with _lock:
        return LossLedger(
            start_lag_minutes=_start_lag_minutes,
            lost_by_series=dict(_lost_by_series),
            restarted_mid_day=_restarted_mid_day,
        )


def reset() -> None:
    """테스트 전용 — 전역 상태를 쓰는 모듈이 테스트끼리 새지 않게 한다."""
    global _start_lag_minutes, _restarted_mid_day
    with _lock:
        _start_lag_minutes = None
        _restarted_mid_day = False
        _lost_by_series.clear()
