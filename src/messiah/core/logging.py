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
from math import isfinite
from typing import Any

from messiah.core.timeutil import now_kst

# 태그 등록부: 태그 = 심각도 1개 고정 (신규 태그는 여기 등록 후 사용)
TAG_LEVELS: dict[str, int] = {
    "SessionStart": logging.INFO,
    "BarClosed": logging.DEBUG,
    "FeaturePublish": logging.DEBUG,
    "FeatureNaN": logging.WARNING,
    # 가격이 아예 안 움직여 롤링 표준편차 계열이 **정의 불가**가 된 경우 — 데이터 사고가
    # 아니라 시장 상태다(2026-07-31 상한가 고착 구간). `FeatureNaN`과 같은 WARNING이지만
    # 태그를 나눠야 무결성 리포트 집계에서 "결측"과 "퇴화"가 구분된다.
    "FeatureDegenerate": logging.WARNING,
    # 직전 완성봉 종가 대비 시가가 임계 이상 벌어짐 — 스테일 프린트/피드 이상 의심
    # (2026-07-31 09:05봉이 6.1% 점프인데도 quality_ok=True로 통과했다).
    "TickPriceJump": logging.WARNING,
    "FeatureSetMismatch": logging.ERROR,  # L3: 침묵(DEBUG) 금지 — 무조건 ERROR
    "OrderSubmit": logging.INFO,
    "OrderPendingSet": logging.INFO,
    "FillMatched": logging.INFO,
    "FillUnmatched": logging.CRITICAL,  # L1: 미매칭 체결 = CRITICAL 정지
    "OrderExpired": logging.INFO,  # SimBroker: TTL 경과 미체결 자동 취소 — 정상 동작
    "RiskReject": logging.INFO,  # 거부는 정상 동작 — 예외 밀도에 안 섞이게 INFO
    "KillSwitch": logging.CRITICAL,
    "DataFallback": logging.WARNING,  # L18: 폴백은 시끄럽게
    "InsertFailRate": logging.WARNING,  # L16: 삽입 실패율 경보
    "SelfCheckFail": logging.CRITICAL,
    "SchedulerTickMissed": logging.WARNING,  # L20: 드리프트를 침묵시키지 않음
    "SchedulerCallbackError": logging.ERROR,  # L22: 콜백 실패가 루프를 안 죽여도 조용히는 안 됨
    "CollectorProcessingError": logging.ERROR,  # L22: 완성봉 적재/버스 발행 실패 — WS 루프는 계속
    "CollectorWSDisconnected": logging.WARNING,  # WS 단절 — run_forever()가 백오프 후 재연결 시도
    "CollectorWSReconnected": logging.INFO,  # 끊긴 뒤 재연결 성공(수신 재개)
    "FeaturePublishError": logging.ERROR,  # FeatureEngine 발행 실패 — L22: 처리 루프는 계속
    "DailyCloseTimeout": logging.CRITICAL,  # 장후 종료 절차가 안전판 시각까지 못 끝남 — 강제 종료
    "DecisionEmitted": logging.INFO,  # Meta Decision — NO TRADE도 포함해 전부 기록(Ver 2.0 §3.2)
    "SizerZeroQty": logging.INFO,  # Sizer 계산 결과 0계약 — 주문 생성 안 함(정상 동작)
    "KillSwitchLiquidating": logging.WARNING,  # Kill Switch 발동에 따른 강제청산 주문 발행
    "InvestorFlowPollError": logging.WARNING,  # REST 폴링 1회 실패 — 다음 틱에 자연 재시도(L22)
    "OptionChainPollEmpty": logging.WARNING,  # 근월물 체인이 비어있음 — 마스터파일 갱신 필요할 수도
    "OptionChainPollError": logging.WARNING,  # 다리 1개 조회/발행 실패 — 나머지는 계속 시도(L22)
    "OptionsCandidateRejected": logging.INFO,  # 안전규칙 기각 — 정상 동작(§6 하드룰 의도대로 작동)
    "RegistryBundleRegistered": logging.INFO,  # 신규 번들 candidate 등록 (Ver 1.6 §9.2)
    "RegistryTransitionRejected": logging.ERROR,  # 상태기계 위반 전이 시도 — 호출부 버그 신호
    "RegistryLiveRetired": logging.INFO,  # 신규 live 승격에 따른 이전 live 자동 retired
    "ShadowFillRecorded": logging.DEBUG,  # Shadow 가상 체결 — 고빈도, 정상 동작
    "ShadowPromotionProposed": logging.INFO,  # 승격 제안 발행 — 자동 승격 아님(사람 승인 전제)
    "SelfEvalReportGenerated": logging.INFO,  # 일일 자가평가 리포트 발행
    "CircuitBreakerSuspected": logging.WARNING,  # 거래소 CB 의심 — WSDisconnected와 동급
    "CircuitBreakerConfirmed": logging.WARNING,  # 거래소 CB 추정 확정 — 신규진입 차단
    "CircuitBreakerResumed": logging.INFO,  # CB 해제 추정(데이터 재수신) — WSReconnected와 동급
    "CircuitBreakerLiquidating": logging.WARNING,  # CB 재개 직후 자동 강제청산 — KillSwitch와 동급
    "CollectorTickStall": logging.WARNING,  # 소켓은 살아있는데 틱이 끊김 — 강제 재연결
    "CollectorFirstTick": logging.INFO,  # 세션 첫 틱 수신 시각 — 장전 구간 유입 여부 진단용
    "CommandCenterUIDown": logging.WARNING,  # UI 프로세스 사망 감지 — 자동 재기동 시도
    "CommandCenterUIRestarted": logging.INFO,  # UI 자동 재기동 성공
    "CommandCenterUIRestartGaveUp": logging.ERROR,  # 재기동 한도 소진 — 사람이 봐야 함
    "FeatureWarmStart": logging.INFO,  # 기동 시 과거 봉으로 롤링 윈도 사전 충전
    "FeatureWarmStartFailed": logging.WARNING,  # 웜스타트 실패 — 수집은 계속(콜드스타트로 진행)
    "HealthPublishError": logging.ERROR,  # sys.health heartbeat 발행 실패 — 처리 루프는 계속(L22)
    "IntegrityReportGenerated": logging.INFO,  # 일일 무결성 리포트 산출
    "IntegrityThresholdBreached": logging.WARNING,  # 무결성 지표가 임계 초과 — 사람이 봐야 함
    "ArchiveCompacted": logging.INFO,  # 장중 조각 파일 → 일자 파일 통합 완료
    "ArchiveCompactionFailed": logging.WARNING,  # 통합 실패 — 조각은 그대로 남아 읽기는 계속 가능
    "OutOfSessionNoTrade": logging.INFO,  # 정규장 밖 주문 생략 — 정상 동작(RiskReject와 동급)
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
            # 사람이 읽는 로그라 표시는 KST(+09:00, 거래소 시각)로 — 내부 데이터(BusMessage.
            # ts_utc 등)는 여전히 UTC가 표준(SYSTEM.md R3, core/timeutil.py)이며 이건 로그
            # 표시 전용 변경이다. ISO8601 오프셋이 그대로 찍히므로 값 자체로 시간대가
            # 명확해 혼동 소지 없음(2026-07-24, 사용자 요청 — 실제 운영 로그 리뷰 중 UTC라
            # 장 시각과 안 맞아 읽기 불편하다는 피드백).
            "ts": now_kst().isoformat(),
            "level": record.levelname,
            "tag": getattr(record, "tag", "-"),
            "msg": record.getMessage(),
        }
        extra = getattr(record, "fields", None)
        if extra:
            payload.update(extra)
        return json.dumps(_json_safe(payload), ensure_ascii=False, default=_json_default)


def _json_default(o: Any) -> str:
    if isinstance(o, datetime):
        return o.isoformat()
    return str(o)


def _json_safe(o: Any) -> Any:
    """비유한 float(inf/-inf/nan)을 null로 바꾼다 — `json.dumps`는 이걸 `Infinity`/`NaN`이라는
    **JSON 표준에 없는 리터럴**로 그대로 찍는다(파이썬 자신은 되읽지만 표준 파서는 거부).

    실제로 2026-07-29 `CircuitBreakerConfirmed` 라인이 `"data_age_seconds": Infinity`로
    남았다 — 콜드스타트 구간의 `_data_age_seconds()`가 `inf`를 돌려주던 시절의 로그다.
    그 오탐 자체는 이미 고쳤지만(`strategy/pipeline.py`), 로그가 표준 JSON이 아니게 되는
    경로는 그대로 남아 있었다. 로그 1줄 = JSON 1줄은 사후 집계(`scripts/agenda.py`)의
    전제라(모듈 docstring) 여기서 원천 차단한다.
    """
    if isinstance(o, float):
        return o if isfinite(o) else None
    if isinstance(o, dict):
        return {k: _json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_json_safe(v) for v in o]
    return o


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
