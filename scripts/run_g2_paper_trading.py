"""G2 페이퍼 트레이딩 일일 운영 — Master Plan Ver 2.0 §8·§9 W39~40.

`scripts/run_l1_daily.py`(L1 수집만)와 **같은 시각에, 별도 프로세스로, 나란히** 돈다:
`run_l1_daily.py`가 이미 실계좌 WS 연결로 틱을 모아 `bar.*`/`feat.*`를 버스에 발행하고
있다는 전제 위에서, 이 스크립트는 전략 전 경로(FuturesAIService·TradingPipeline·
SimBroker)와 Phase 5 진화 루프(ShadowManager·Self Evaluation)를 그 버스에 **구독자로만**
붙인다.

## 이 스크립트는 자기 자신의 TickCollector를 열지 않는다 (2026-07-29 [MW0601] 수정)

**예전엔 그렇지 않았다** — 처음 구현 당시 이 스크립트는 `run_l1_daily.py`와 완전히
동일한 `TickCollector`(같은 계좌 자격증명·같은 심볼·같은 TR)를 자기 것으로 또 하나
만들어 독자적인 WS 연결을 열고 있었다. `Docs/capability_matrix.md`(2026-07-23)에 이미
실측으로 확정돼 있던 사실 — **동일 계좌로 WS 연결을 2개 열면 서로 반복적으로 끊긴다**
(원인 미확인, approval_key 재발급이 같은 계좌의 다른 세션을 무효화하는 것으로 추정) —
과 정면으로 충돌하는 설계였다. 이 상태로 Task Scheduler에 `run_l1_daily.bat`와 같은
08:35 트리거로 등록했다면, 매일 아침 L1과 G2가 동시에 각자 WS 연결을 열어 그 반복
단절 버그를 재현했을 것이고, 최악의 경우 아직 아무 가치도 못 내는 G2(Registry가
비어 있어 거래가 전혀 안 남, 아래 문단)를 위해 실제로 가치 있는 L1의 실데이터 수집을
매일 망가뜨렸을 것이다(G1 관문 달성을 위한 데이터 축적이 지금 단계의 유일한 목적).

`MultiSymbolTickCollector`(단일 연결·다중 subscribe, 2026-07-23 신설)가 이 버그의
구조적 해법이지만, 그건 **같은 프로세스 안에서** 선물+옵션처럼 여러 (심볼,TR)을 함께
구독하는 경우의 해법이지 **서로 다른 두 프로세스가 각자 별도 WS 연결을 여는 경우**는
애초에 해결한 적이 없다 — 이 스크립트가 자기 WS 연결 자체를 아예 안 여는 것(TickCollector
·MultiHorizonBarComposer·FeatureEngine을 전부 제거하고 `bar.*`/`feat.*` 구독만 남김)이
유일하게 안전한 해법이다. 대가는 이 스크립트가 이제 `run_l1_daily.py`(또는 동등한 데이터
발행자)가 이미 살아서 버스에 발행 중이라는 전제 없이는 아무 신호도 못 받는다는 것 —
그런데 애초에 그게 이 스크립트의 원래 설계 의도였다("G2가 실시간 버스 위에서 페이퍼
브로커를 돌린다").

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
Windows 작업 스케줄러에 "Messiah-G2"(평일 08:36, `run_g2_paper_trading.bat`)로 등록해
`run_l1_daily.py`("Messiah", 08:35)와 나란히 돈다(2026-07-29 [MW0601] — 1분 차이는 엄격한
순서 보장이 필요해서가 아니라 두 프로세스의 동시 기동 부하를 살짝 어긋내기 위한 것뿐,
WS 연결을 더는 안 열게 된 이후로는 순서 자체는 무관해졌다). `stop_l1_daily.bat`(15:40
워치독)도 이 스크립트의 명령줄 패턴을 함께 정리한다.
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

from messiah.broker.kis import symbol_master  # noqa: E402
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
from messiah.execution.order_gateway import OrderGateway  # noqa: E402
from messiah.models.registry import BundleStatus, ModelRegistry  # noqa: E402
from messiah.models.self_evaluation import run_self_evaluation  # noqa: E402
from messiah.models.shadow_manager import ShadowManager, evaluate_promotion  # noqa: E402
from messiah.simulator.engine import LiveSimBrokerFeed  # noqa: E402
from messiah.strategy.futures.service import FuturesAIService  # noqa: E402
from messiah.strategy.pipeline import TradingPipeline  # noqa: E402

REGULAR_SESSION_STOP = (DEFAULT_SESSION.close_time.hour, DEFAULT_SESSION.close_time.minute)
HARD_SHUTDOWN_DEADLINE = (15, 40)

_MASTER_CACHE_DIR = Path(".cache/kis_symbol_master")
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
    futures_service: FuturesAIService,
    pipeline: TradingPipeline,
    sim_feed: LiveSimBrokerFeed,
    shadow_manager: ShadowManager,
) -> None:
    """이 스크립트의 데이터 소스는 `run_l1_daily.py`가 같은 버스에 이미 발행 중인 `bar.*`/
    `feat.*`뿐이다 — 여기엔 자체 TickCollector가 없다(모듈 docstring 참고, WS 이중 연결
    사고 대응). 넷 다 순수 구독자라 이 asyncio.gather()가 여는 WS 연결은 0개."""
    await asyncio.gather(
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
    bus: MessageBus,
    broker: SimBroker,
    shadow_manager: ShadowManager,
    registry: ModelRegistry,
    symbol: str,
    start_equity: Decimal,
    today: str,
) -> None:
    """봉 flush는 `run_l1_daily.py`의 책임(그 프로세스가 실제 Collector/Composer를 갖고
    있다) — 이 스크립트는 자기 것이 없으므로 flush할 것도 없다."""
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

    symbol = await asyncio.to_thread(_resolve_front_month_symbol)
    print(
        f"근월물 심볼: {symbol} — 데이터는 run_l1_daily.py가 버스에 발행한 bar.*/feat.*를 "
        f"구독(자체 WS 연결 없음)",
        flush=True,
    )

    registry = ModelRegistry(_REGISTRY_DB)
    bus = MessageBus(cfg.redis_url, cfg.instance_id)
    await bus.connect()

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
                _run_regular_session(futures_service, pipeline, sim_feed, shadow_manager),
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
