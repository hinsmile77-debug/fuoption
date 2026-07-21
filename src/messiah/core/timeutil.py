"""시간 유틸 — SYSTEM.md R3: naive datetime 생성 금지 (레슨런 L21).

모든 시각은 이 모듈을 통해서만 생성한다.
- 내부 표준: UTC (tz-aware)
- 표시·거래소 시각: KST 변환 함수 사용
- `datetime.now()` / `datetime.utcnow()` 직접 호출은 ruff DTZ 규칙으로 차단된다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

UTC = timezone.utc  # datetime.UTC는 3.11+ 전용 — 호환성 위해 timezone.utc 사용
KST = timezone(timedelta(hours=9), name="Asia/Seoul")


def now_utc() -> datetime:
    """현재 시각 (UTC, tz-aware). 시스템 전체의 유일한 '현재' 소스."""
    return datetime.now(UTC)


def now_kst() -> datetime:
    """현재 시각 (KST, tz-aware). 표시·거래소 경계 판정용."""
    return now_utc().astimezone(KST)


def to_kst(dt: datetime) -> datetime:
    """UTC(또는 aware) 시각 → KST. naive 입력은 즉시 거부한다."""
    _reject_naive(dt)
    return dt.astimezone(KST)


def to_utc(dt: datetime) -> datetime:
    """aware 시각 → UTC. naive 입력은 즉시 거부한다."""
    _reject_naive(dt)
    return dt.astimezone(UTC)  # noqa: DTZ  (UTC 상수 사용)


def ensure_aware(dt: datetime) -> datetime:
    """naive datetime이 시스템 경계를 넘는 것을 차단하는 검문소."""
    _reject_naive(dt)
    return dt


def _reject_naive(dt: datetime) -> None:
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise ValueError(
            "naive datetime 금지 (SYSTEM.md R3 / 레슨런 L21). "
            "now_utc()/now_kst()로 생성하거나 tzinfo를 부여할 것."
        )
