"""구조화 JSON 로깅 — SYSTEM.md R6.

규칙 (레슨런 L10·L24):
- 태그 1개 = 심각도 1개: log(tag=...)의 레벨은 태그 등록부(TAG_LEVELS)에서 고정.
  같은 태그로 다른 레벨을 찍으려는 시도는 그 자체가 버그 → ValueError.
- 세션 경계 마커: 프로세스 기동 시 session_start()가 기동시각·instance_id·git SHA를
  첫 줄에 남긴다. 로그 분석은 이 마커 이후 구간만 보는 것이 기본.
- 모든 라인은 JSON 1줄 — 사후 집계(회의 안건 자동 생성)의 원천.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import datetime
from typing import Any

from messiah.core.timeutil import now_utc

# 태그 등록부: 태그 = 심각도 1개 고정 (신규 태그는 여기 등록 후 사용)
TAG_LEVELS: dict[str, int] = {
    "SessionStart": logging.INFO,
    "BarClosed": logging.DEBUG,
    "FeaturePublish": logging.DEBUG,
    "FeatureNaN": logging.WARNING,
    "FeatureSetMismatch": logging.ERROR,  # L3: 침묵(DEBUG) 금지 — 무조건 ERROR
    "OrderSubmit": logging.INFO,
    "OrderPendingSet": logging.INFO,
    "FillMatched": logging.INFO,
    "FillUnmatched": logging.CRITICAL,  # L1: 미매칭 체결 = CRITICAL 정지
    "RiskReject": logging.INFO,  # 거부는 정상 동작 — 예외 밀도에 안 섞이게 INFO
    "KillSwitch": logging.CRITICAL,
    "DataFallback": logging.WARNING,  # L18: 폴백은 시끄럽게
    "InsertFailRate": logging.WARNING,  # L16: 삽입 실패율 경보
    "SelfCheckFail": logging.CRITICAL,
}

_logger = logging.getLogger("messiah")


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return "nogit"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": now_utc().isoformat(),
            "level": record.levelname,
            "tag": getattr(record, "tag", "-"),
            "msg": record.getMessage(),
        }
        extra = getattr(record, "fields", None)
        if extra:
            payload.update(extra)
        return json.dumps(payload, ensure_ascii=False, default=_json_default)


def _json_default(o: Any) -> str:
    if isinstance(o, datetime):
        return o.isoformat()
    return str(o)


def setup(instance_id: str, stream: Any = None) -> None:
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter())
    _logger.handlers.clear()
    _logger.addHandler(handler)
    _logger.setLevel(logging.DEBUG)
    session_start(instance_id)


def session_start(instance_id: str) -> None:
    """세션 경계 마커 (L24) — 분석 도구는 마지막 마커 이후만 본다."""
    log(
        "SessionStart",
        "process start",
        instance_id=instance_id,
        git_sha=_git_sha(),
        pid=__import__("os").getpid(),
    )


def log(tag: str, msg: str, **fields: Any) -> None:
    """태그 기반 로깅. 레벨은 태그 등록부가 결정한다 — 호출부는 레벨을 선택할 수 없다."""
    if tag not in TAG_LEVELS:
        raise ValueError(
            f"미등록 태그 '{tag}' — core/logging.py TAG_LEVELS에 등록 후 사용 (SYSTEM.md R6)"
        )
    _logger.log(TAG_LEVELS[tag], msg, extra={"tag": tag, "fields": fields})
