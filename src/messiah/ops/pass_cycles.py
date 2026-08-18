"""판단이 meta 게이트를 넘은 사이클의 **입력 보존** — 고도화 G-0818P-3 (2026-08-18).

## 왜 만들었나

2026-08-18 14:30, 관측 이래 **처음으로** meta 게이트를 넘은 판단이 나왔다:

    14:30:00     RegimeClassified  TREND_UP (0.9946)
    14:30:00.878 DecisionEmitted   gate=pass  S=0.511 (임계 ±0.2) LONG, n_experts=1
    14:30:00.908 RiskReject        "Net ER -1.62틱 ≤ 0 (Ver 1.1 §4-2)"

하루 14사이클 중 1건이다. 그리고 **그 사이클이 남긴 것은 위 로그 3줄이 전부였다** —
어떤 `ExpertView`가 그 `S=0.511`을 만들었는지, `Net ER -1.62`가 어떤 ATR·비용에서 나왔는지,
meta 확률이 임계를 얼마나 넘겼는지는 어디에도 없다. 다음에 언제 또 나올지 모르는 사건의
입력을 그렇게 흘려보내면, 그날이 오기 전까지 분석할 표본이 0건이다.

W-21(`blocked_by_meta` 벽) 뒤의 경로가 살아 있다는 첫 증거였고, G2 40거래일 관문에서
쌓여야 할 것이 정확히 이 표본이다. 리허설·백테스트 경로와 대조할 **첫 라이브 기준선**이기도
하다.

## 무엇을 남기나

`decide()`가 `NO_TRADE`가 아닌 판단을 낸 사이클마다 파일 하나:

    logs/pass_cycles/2026-08-18T143000_A05609.json

`FuturesView` 전체 · Horizon별 `ExpertView` 원본 · 그 판정에 쓰인 `meta_features` ·
`DecisionIntent` · Net ER 계산 내역(edge·ATR·비용·결과) · 리스크 판정 · 그리고 **그 사이클이
어디서 끝났는지**(`outcome`).

`outcome`이 특히 중요하다. 통과했다고 전부 주문이 되지는 않는다 — 정규장 밖이면 멈추고,
ATR 워밍업 미달이면 멈추고, 리스크단이 기각하면 멈춘다. 08-18의 그 한 건도 `risk_reject`
였다. **어디서 멈췄는지가 곧 다음에 무엇을 고쳐야 하는지**이므로 결과와 함께 적는다.

## 왜 로그가 아니라 파일인가

구조화 로그 한 줄에 넣기엔 `ExpertView` 셋 + meta_features 십여 개가 너무 크고, 무엇보다
사후 분석이 **재현**을 목적으로 한다 — 그때 그 입력을 그대로 다시 먹여 보려면 통짜 JSON이
편하다. 하루 최대 몇 건(2026-08-18 기준 1건)이라 용량 부담이 없다.

## 실패해도 거래를 막지 않는다

관측 도구가 거래 경로를 죽이면 본말전도다(`ops/task_exit_codes`·`ops/observation_gaps`와
같은 규율). 다만 조용히 넘기지도 않는다(R10) — 실패는 `PassCycleSnapshotFailed`(WARNING)로
남는다.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from messiah.core import logging as mlog
from messiah.core.timeutil import to_kst

DEFAULT_DIR = Path("logs") / "pass_cycles"


def _jsonable(value: Any) -> Any:
    """pydantic 메시지·Decimal·datetime을 JSON으로 — 실패하면 문자열로 접는다.

    한 필드의 직렬화 실패가 스냅샷 전체를 날리면 안 된다. 보존이 목적이므로 **모양이 조금
    상하더라도 남기는 쪽**을 고른다.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        try:
            return json.loads(value.model_dump_json())
        except Exception:  # noqa: BLE001 — 아래 문자열 폴백
            pass
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


def snapshot_path(as_of: datetime, symbol: str, *, base_dir: Path = DEFAULT_DIR) -> Path:
    """`logs/pass_cycles/2026-08-18T143000_A05609.json` — KST 기준.

    파일명이 KST인 이유: 이 파일을 여는 사람은 그날 로그·리포트와 나란히 놓고 본다.
    그쪽이 전부 KST 표기다(`ops/integrity_report`의 시각 필드와 같은 규율).
    """
    stamp = to_kst(as_of).strftime("%Y-%m-%dT%H%M%S")
    return base_dir / f"{stamp}_{symbol}.json"


def record(
    *,
    as_of: datetime,
    symbol: str,
    view: Any,
    intent: Any,
    expert_views: dict[str, Any] | None = None,
    meta_features: dict[str, Any] | None = None,
    net_er: dict[str, Any] | None = None,
    outcome: str,
    risk: dict[str, Any] | None = None,
    base_dir: Path = DEFAULT_DIR,
) -> Path | None:
    """pass 사이클 하나를 파일로 남긴다 — 실패하면 `None`(거래 경로는 계속).

    `outcome` 허용값(파이프라인이 실제로 멈출 수 있는 지점 전부):

        out_of_session   정규장 밖이라 주문 생략 — 판단은 있었다
        atr_warmup       ATR 워밍업 미달로 사이징 전에 멈춤
        risk_reject      리스크단이 기각 — 2026-08-18 14:30이 이 값이다
        zero_qty         Sizer가 0계약 산출
        submitted        주문 발행까지 감
    """
    payload = {
        "as_of_kst": to_kst(as_of).isoformat(),
        "symbol": symbol,
        "outcome": outcome,
        "view": _jsonable(view),
        "intent": _jsonable(intent),
        "expert_views": _jsonable(expert_views or {}),
        "meta_features": _jsonable(meta_features or {}),
        "net_er": _jsonable(net_er or {}),
        "risk": _jsonable(risk or {}),
    }
    path = snapshot_path(as_of, symbol, base_dir=base_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 — 관측이 거래를 막지 않는다
        mlog.log(
            "PassCycleSnapshotFailed",
            f"pass 사이클 스냅샷 실패 — 거래 경로는 계속: {exc}",
            symbol=symbol,
            outcome=outcome,
            error=str(exc),
        )
        return None
    mlog.log(
        "PassCycleSnapshot",
        f"pass 사이클 보존 — {outcome} · {path.name}",
        symbol=symbol,
        outcome=outcome,
        path=str(path),
    )
    return path
