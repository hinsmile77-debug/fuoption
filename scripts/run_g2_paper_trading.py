"""G2 페이퍼 트레이딩 일일 운영 — Master Plan Ver 2.0 §8·§9 W39~40.

`scripts/run_l1_daily.py`(L1 수집만)의 구조를 그대로 확장한다: 웜업 → 정규장 운영 →
장후 종료(Self Evaluation 포함). L1(Collector·Composer·FeatureEngine)에 더해 이번엔
전략 전 경로(FuturesAIService·TradingPipeline·SimBroker)와 Phase 5 진화 루프
(ShadowManager·Self Evaluation)까지 같은 실시간 버스에 붙인다.

## 이 스크립트를 오늘 당장 돌려도 거래가 발생하지 않는다 (의도된 정직한 상태)

`ModelRegistry`에 아직 `live` 상태 번들이 하나도 없다(G1 백테스트 관문을 실제 데이터로
통과한 모델이 없음 — capability_matrix.md 반복 기록) — `_load_futures_service()`가
Horizon별로 `registry.get_live(h)`를 조회해 없으면 그냥 건너뛰므로, `FuturesAIService`는
전문가 0개로 기동돼 `intel.futures`를 발행하지 않고, 따라서 `MetaDecisionEngine`도 판단할
게 없어 거래가 전혀 나지 않는다. **이 스크립트가 오늘 증명하는 것은 "시스템이 장중 내내
안 죽고 도는가"(Ver 2.0 §8 G2 통과기준 "시스템 무중단")이지 "우위가 있는가"가 아니다** —
실제 손익이 의미를 가지려면 먼저 Horizon별 `live` 번들이 실제 데이터로 학습·검증되어
Registry에 승격되어 있어야 한다.

## Regime AI는 이번 스코프에서 결선하지 않는다

실측 데이터 부족으로 아직 학습된 `RegimeAI` 인스턴스가 없다(W20~21 알려진 갭 — 합성
데이터로만 검증됨). `RegimeState`를 한 번도 못 받은 `FuturesAIService`는 국면을
`UNKNOWN`으로 유지하는데(모듈 기본값), 이는 Meta Decision Engine 규칙 ②("Regime=이벤트
또는 UNKNOWN → NO TRADE")가 그대로 지켜지는 안전한 기본 동작이다 — Regime AI가 없다고
불안전한 판단을 하지는 않는다.

## Self Evaluation 슬리피지 대사·Conformal 갱신은 이번 스코프 밖

이 스크립트는 하루치 `OrderRequest`/`OrderAck`/`Fill` 이력을 아직 수집하지 않는다(Position
Reconciler가 없어 어차피 챔피언 실현손익을 정확히 계산 못 하는 것과 같은 근본 이유) —
`run_self_evaluation()`을 빈 시퀀스로 호출해 슬리피지는 항상 "예측값만, 실현 0건"으로
찍힌다. Conformal 상태(`conformal_state.json`) 갱신도 예측 로그 vs 실제 결과 재라벨링
파이프라인이 별도로 필요해 다음 착수 항목으로 남긴다.

## 챔피언 일일수익률은 "포트폴리오 평가액 변화" 근사다

Position Reconciler 부재로 거래별 실현손익을 매칭할 수 없다 — 대신 그날 시작/종료
`SimBroker.account().total_equity`의 변화율을 하루 1개 표본으로 `logs/g2_daily_returns.jsonl`에
누적한다. Sharpe/MDD는 이 누적 파일 전체(오늘까지 쌓인 모든 거래일)로 계산 — G2 관문의
"40거래일"이 실제로 의미를 가지려면 이 파일이 40줄 이상 쌓여야 한다.

## Docker Desktop 자동 기동

`run_l1_daily.py`와 동일하게 `_ensure_docker_ready()`를 self_check보다 먼저 실행한다
(`core/docker_bootstrap.py` 참고, 2026-07-29 추가) — Redis(`messiah-redis`)가 안 떠 있으면
스스로 Docker Desktop을 띄운 뒤 진행한다.

사용: python scripts/run_g2_paper_trading.py [--configs configs]
Windows 작업 스케줄러 등록은 안 함(`run_l1_daily.py`와 동일 이유) — 매일 무인 자동화는
사용자 확인 후 별도 진행.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from messiah.broker.kis import symbol_master, tr_codes  # noqa: E402
from messiah.broker.kis.credentials import KISCredentials  # noqa: E402
from messiah.broker.simulator.adapter import SimBroker  # noqa: E402
from messiah.core import logging as mlog  # noqa: E402
from messiah.core.bus import MessageBus  # noqa: E402
from messiah.core.config import InstanceConfig, load_instance  # noqa: E402
from messiah.core.docker_bootstrap import (  # noqa: E402
    DEFAULT_DOCKER_DESKTOP_EXE,
    ensure_docker_ready,
)
from messiah.core.event_calendar import DEFAULT_SESSION, EventCalendar  # noqa: E402
from messiah.core.messages import Horizon  # noqa: E402
from messiah.core.timeutil import now_kst  # noqa: E402
from messiah.core.ui_launcher import launch_command_center  # noqa: E402
from messiah.data.archiver import ParquetArchiver  # noqa: E402
from messiah.data.bar_composer import MultiHorizonBarComposer  # noqa: E402
from messiah.data.collector import TickCollector  # noqa: E402
from messiah.data.normalizer import parse_futures_tick  # noqa: E402
from messiah.execution.order_gateway import OrderGateway  # noqa: E402
from messiah.features.engine import FeatureEngine  # noqa: E402
from messiah.models.registry import BundleStatus, ModelRegistry  # noqa: E402
from messiah.models.self_evaluation import run_self_evaluation  # noqa: E402
from messiah.models.shadow_manager import ShadowManager, evaluate_promotion  # noqa: E402
from messiah.simulator.engine import LiveSimBrokerFeed  # noqa: E402
from messiah.strategy.futures.service import FuturesAIService  # noqa: E402
from messiah.strategy.pipeline import TradingPipeline  # noqa: E402

REGULAR_SESSION_STOP = (DEFAULT_SESSION.close_time.hour, DEFAULT_SESSION.close_time.minute)
HARD_SHUTDOWN_DEADLINE = (15, 40)

_MASTER_CACHE_DIR = Path(".cache/kis_symbol_master")
_DATA_DIR = Path("data") / "bars"
_LOG_DIR = Path("logs")
_REGISTRY_DB = Path("data/models/registry.db")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _today_at(reference_kst: datetime, hour: int, minute: int) -> datetime:
    return reference_kst.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _launch_ui(today_str: str) -> subprocess.Popen | None:
    """`core/ui_launcher.py`의 얇은 래퍼(`run_l1_daily.py`와 동일 패턴) — G2도 거래일 확인
    직후 Command Center를 별도 백그라운드 프로세스로 띄운다. 오늘(2026-07-29) 조사 시점
    기준 `ModelRegistry`에 `live` 번들이 0개라 화면의 AI Decision 존은 사실상 비어 있지만
    (알려진 갭, ui/app.py 모듈 docstring), G2가 나중에 Task Scheduler로 무인 전환될 때를
    대비해 지금 통합해 둔다(사용자 승인, 2026-07-29). 중복 기동 방지(포트 응답 확인)는
    공용 모듈이 담당 — `run_l1_daily.py`가 이미 UI를 띄운 상태에서 G2를 실행해도 두 번째
    Streamlit이 뜨지 않는다(2026-07-30 추가: 실측 결과 포트 충돌이 에러 없이 조용히
    두 프로세스 모두 LISTENING 상태로 남는 위험한 상황이 실제 재현돼 방어 코드 추가)."""
    return launch_command_center(
        caller_tag="run_g2_paper_trading",
        project_root=_PROJECT_ROOT,
        log_path=Path("logs") / f"ui_{today_str}.log",
    )


def _ensure_docker_ready() -> None:
    """`run_l1_daily.py`의 `_ensure_docker_ready()`와 동일 — G2도 같은 Redis 의존성을
    갖는다(`core/docker_bootstrap.py` 모듈 docstring 참고)."""
    exe_path = Path(os.environ.get("MESSIAH_DOCKER_DESKTOP_EXE", str(DEFAULT_DOCKER_DESKTOP_EXE)))
    result = ensure_docker_ready(exe_path=exe_path)
    if not result.ready:
        raise SystemExit("Docker Desktop이 대기 시간 내에 준비되지 않음 — 기동 중단 (Ver 1.1 §7.3)")
    if not result.already_running:
        print(
            f"[run_g2_paper_trading] Docker Desktop 자동 기동 완료 "
            f"({result.waited_seconds:.0f}초 대기)",
            flush=True,
        )


def _run_self_check(config_dir: str) -> None:
    result = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "self_check.py"), "--configs", config_dir],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    if result.returncode != 0:
        raise SystemExit("self_check 실패 — 기동 중단 (Ver 1.1 §7.3)")


def _resolve_front_month_symbol() -> str:
    master = symbol_master.load_index_derivatives_master(_MASTER_CACHE_DIR)
    symbol = master.front_month_future_code(product_type=symbol_master.PRODUCT_TYPE_MINI_FUTURES)
    if symbol is None:
        raise RuntimeError("미니선물 근월물 심볼 확인 실패 — 마스터파일에 해당 상품 없음")
    return symbol


def _load_futures_service(
    registry: ModelRegistry, symbol: str, bus: MessageBus, feature_set: str
) -> FuturesAIService:
    """Horizon별 `live` 번들을 Registry에서 조회해 결선한다 — 없는 Horizon은 건너뛴다
    (모듈 docstring "오늘 당장 돌려도 거래가 발생하지 않는다" 참고)."""
    experts = {}
    meta_labelers = {}
    for horizon in Horizon:
        live = registry.get_live(horizon)
        if live is None:
            continue
        experts[horizon] = live.load_expert()
        meta_labelers[horizon] = live.load_meta_labeler()
    print(f"live 번들 결선: {[h.value for h in experts]} (feature_set={feature_set})", flush=True)
    return FuturesAIService(symbol, experts, bus, meta_labelers=meta_labelers)


def _load_shadow_manager(registry: ModelRegistry, symbol: str, bus: MessageBus) -> ShadowManager:
    manager = ShadowManager(symbol, bus)
    for record in registry.list_by_status(BundleStatus.SHADOW):
        manifest = record.manifest()
        try:
            meta = record.load_meta_labeler()
        except FileNotFoundError:
            meta = None
        manager.add_shadow_bundle(record.bundle_id, record.load_expert(), meta)
        print(f"shadow 번들 결선: {record.bundle_id} ({manifest.horizon.value})", flush=True)
    return manager


async def _run_regular_session(
    collector: TickCollector,
    composer: MultiHorizonBarComposer,
    engine: FeatureEngine,
    futures_service: FuturesAIService,
    pipeline: TradingPipeline,
    sim_feed: LiveSimBrokerFeed,
    shadow_manager: ShadowManager,
) -> None:
    await asyncio.gather(
        collector.run_forever(),
        composer.run_forever(),
        engine.run_forever(),
        futures_service.run_forever(),
        pipeline.run_forever(),
        sim_feed.run_forever(),
        shadow_manager.run_forever(),
    )


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


async def _daily_close(
    *,
    collector: TickCollector,
    composer: MultiHorizonBarComposer,
    bus: MessageBus,
    broker: SimBroker,
    shadow_manager: ShadowManager,
    registry: ModelRegistry,
    symbol: str,
    start_equity: Decimal,
    today: str,
) -> None:
    await collector.flush_final_bar()
    await composer.flush_all_final()

    end_equity = (await broker.account()).total_equity
    daily_return = float((end_equity - start_equity) / start_equity) if start_equity > 0 else 0.0
    returns_path = _LOG_DIR / "g2_daily_returns.jsonl"
    _append_jsonl(returns_path, {"date": today, "symbol": symbol, "return": daily_return})
    champion_returns = [r["return"] for r in _read_jsonl(returns_path) if r.get("symbol") == symbol]

    report = run_self_evaluation(
        date=today,
        symbol=symbol,
        champion_returns=champion_returns,
        n_shadow_bundles=len(shadow_manager.active_bundles),
    )
    (_LOG_DIR / f"self_eval_{today}.json").write_text(
        report.model_dump_json(indent=2), encoding="utf-8"
    )
    print(
        f"Self Evaluation: 누적 {len(champion_returns)}거래일 · "
        f"Sharpe {report.sharpe:.2f} · MDD {report.max_drawdown:.1%} · "
        f"Shadow {report.n_shadow_bundles}개",
        flush=True,
    )

    for bundle_id in shadow_manager.active_bundles:
        shadow_fills = shadow_manager.fills_for(bundle_id)
        shadow_returns = [
            f.net_return_ticks for f in shadow_fills if f.net_return_ticks is not None
        ]
        if not shadow_returns:
            continue
        record = registry.get(bundle_id)
        horizon = record.manifest().horizon if record else Horizon.M5
        proposal = evaluate_promotion(
            bundle_id=bundle_id,
            horizon=horizon,
            trading_days_observed=len(champion_returns),
            champion_returns=champion_returns,
            shadow_returns=shadow_returns,
        )
        _append_jsonl(
            _LOG_DIR / "promotion_proposals.jsonl",
            json.loads(proposal.model_dump_json()),
        )

    for event in registry.drain_events():
        await bus.publish("sys.registry", event)

    await bus.close()


async def main(cfg: InstanceConfig) -> None:
    mlog.setup(cfg.instance_id)

    today = now_kst().date()
    if not EventCalendar.from_file().is_trading_day(today):
        print(f"{today.isoformat()}은 KRX 휴장일 — G2 운영 생략, 즉시 종료", flush=True)
        return

    _launch_ui(today.strftime("%Y%m%d"))

    creds = KISCredentials.from_broker_config(cfg.broker)
    symbol = await asyncio.to_thread(_resolve_front_month_symbol)
    tick_size = Decimal(cfg.futures_tick_size)
    print(f"근월물 심볼: {symbol} (tick_size={tick_size})", flush=True)

    registry = ModelRegistry(_REGISTRY_DB)
    bus = MessageBus(cfg.redis_url, cfg.instance_id)
    await bus.connect()

    archiver = ParquetArchiver(_DATA_DIR)
    collector = TickCollector(
        creds=creds,
        symbol=symbol,
        tr_id=tr_codes.WS_TR_FUTURES_CONTRACT,
        parse_tick=parse_futures_tick,
        tick_size=tick_size,
        archiver=archiver,
        bus=bus,
    )
    composer = MultiHorizonBarComposer(symbol=symbol, archiver=archiver, bus=bus)
    engine = FeatureEngine(symbol, bus, feature_set=cfg.feature_set)
    futures_service = _load_futures_service(registry, symbol, bus, cfg.feature_set)
    shadow_manager = _load_shadow_manager(registry, symbol, bus)

    broker = SimBroker(cash=cfg.capital.total)
    await broker.connect()
    start_equity = (await broker.account()).total_equity
    gateway = OrderGateway(broker)
    pipeline = TradingPipeline(
        symbol, broker, gateway, bus, event_calendar=EventCalendar.from_file()
    )
    sim_feed = LiveSimBrokerFeed(symbol, broker, gateway, bus)

    now = now_kst()
    session_stop = _today_at(now, *REGULAR_SESSION_STOP)
    hard_deadline = _today_at(now, *HARD_SHUTDOWN_DEADLINE)

    remaining = (session_stop - now_kst()).total_seconds()
    if remaining > 0:
        print(
            f"G2 페이퍼 운영 시작 — {session_stop.isoformat()}까지 ({remaining:.0f}초)", flush=True
        )
        try:
            await asyncio.wait_for(
                _run_regular_session(
                    collector, composer, engine, futures_service, pipeline, sim_feed, shadow_manager
                ),
                timeout=remaining,
            )
        except TimeoutError:
            print("운영 중단 신호 도달 — 장후 종료 절차 시작", flush=True)
    else:
        print(f"이미 {session_stop.isoformat()} 이후 — 운영 생략, 바로 종료 절차", flush=True)

    shutdown_budget = max((hard_deadline - now_kst()).total_seconds(), 30.0)
    try:
        await asyncio.wait_for(
            _daily_close(
                collector=collector,
                composer=composer,
                bus=bus,
                broker=broker,
                shadow_manager=shadow_manager,
                registry=registry,
                symbol=symbol,
                start_equity=start_equity,
                today=today.isoformat(),
            ),
            timeout=shutdown_budget,
        )
    except TimeoutError:
        mlog.log(
            "DailyCloseTimeout", f"daily_close()가 {shutdown_budget:.0f}초 내에 못 끝남 — 강제 종료"
        )
        registry.close()
        raise SystemExit(1) from None

    registry.close()
    print("정상 종료.", flush=True)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MESSIAH G2 페이퍼 트레이딩 일일 운영")
    parser.add_argument("--configs", default="configs")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    _ensure_docker_ready()
    _run_self_check(args.configs)
    instance_cfg = load_instance(args.configs)
    if instance_cfg.mode not in ("paper", "dev"):
        raise SystemExit(
            f"G2는 paper(또는 리허설용 dev) 모드 전용 — 현재 mode={instance_cfg.mode!r}"
        )
    asyncio.run(main(instance_cfg))
