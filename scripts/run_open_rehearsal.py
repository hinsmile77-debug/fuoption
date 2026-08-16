"""개장 리허설 — 다음 거래일 아침을 **오늘 미리 돌려본다** (2026-08-16 신설).

## 왜 만들었나

2026-08-12부터 매 거래일 아침, 판단 사슬을 막는 새 갈래가 **하나씩** 드러났다:
웜스타트 콜드스타트(08-12) → 롤 경계 심볼(08-14) → 장후 배치 심볼 하드코딩(08-14) →
그리고 이 스크립트가 찾은 웜스타트 적재 필터(08-16). 매번 **개장 후에** 알았고 그날
하루를 잃었다. G2 40거래일이 시작되면 그 하루가 관문 분모에서 사라진다.

전부 **아카이브만으로 개장 전에 알 수 있는 것**이었다. 이 스크립트가 그 확인을 한다 —
네트워크를 쓰지 않고, 아무것도 쓰지 않고(읽기 전용), 실제 운영과 **같은 함수**를 부른다.

## 첫 실행이 곧바로 P0을 찾았다 (2026-08-16, 대상일 2026-08-18)

`ParquetArchiver.load_recent_bars_by_source()`는 롤 경계에서 직전 월물까지 이어 읽고
그 봉의 심볼을 **일부러 안 바꾼다**(출처가 데이터에 남아야 한다). 그런데 그걸 받는
`FeatureEngine.warm_start()`/`RegimeRuntime.warm_start()`의 필터가 자기 심볼만 받아
**로더가 건넨 것을 전량 버리고 있었다**:

    로더:  30m 200봉 (A05609 15 · A05608 185)
    적재:  30m  15봉                      ← 하한 22봉 미달 → UNKNOWN 개장 확정

2026-08-14 저녁의 F-1 커밋은 체인 해석과 로더까지만 고쳤고, 자가점검은 로더의 답인
`직전 25일`을 보고하고 있었다. **두 수가 다를 수 있다는 것을 아무도 몰랐다.** 수정 후
전 Horizon 200봉 · 국면 `TREND_DOWN`(0.999)으로 바뀌었다.

## 무엇을 보고 무엇을 안 보나

보는 것: ① 웜스타트가 **실제로 적재한** 봉 수(로더가 건넨 양과 나란히) ② 그 버퍼로
`classify()`가 내는 국면 ③ live 번들이 붙은 채로 봉을 흘렸을 때의 `n_experts`와,
0이면 `AggregatorNoContribution`의 **어느 갈래**인지.

안 보는 것: 오늘 시장이 어떻게 움직일지. 이건 배관 점검이지 예측이 아니다. 리허설이
`TREND_DOWN`을 냈다고 그날이 하락장이라는 뜻이 아니다 — **판정이 나온다는 것**만이
이 스크립트의 산출이다.

## 이어붙인 봉의 가격 점프는 보정되지 않는다 (알려진 한계)

롤 경계에서 월물 간 basis가 그대로 들어온다. 2026-08-14 롤의 경계 점프는 **1990틱**
이었고, 같은 창의 일자 경계 갭 13건이 중앙 817 · 최대 3160틱이었으므로 그 안에 있다 —
다만 과거 7개 롤의 basis(중앙 116틱 · 최대 240틱, G-2 실측)만큼은 인공물이다. 이번
롤은 겹침이 없어 basis를 **측정조차 못 했다**(`matched_minute=None`). 구조적 해법은
G-1(롤 D-1 사전 백필)이고 여기서 보정하지 않는다 — 보정하면 원본과 조정본이 섞인다.

사용:
    python scripts/run_open_rehearsal.py                     # 다음 거래일을 리허설
    python scripts/run_open_rehearsal.py --date 2026-08-18
    python scripts/run_open_rehearsal.py --date 2026-08-18 --replay 2026-08-14
    python scripts/run_open_rehearsal.py --no-replay         # 웜스타트 예보만
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from messiah.core.config import load_instance  # noqa: E402
from messiah.core.event_calendar import EventCalendar  # noqa: E402
from messiah.core.messages import FuturesView, Horizon  # noqa: E402
from messiah.core.timeutil import now_kst  # noqa: E402
from messiah.data import backfill  # noqa: E402
from messiah.data.archiver import ParquetArchiver  # noqa: E402
from messiah.features import sidecar  # noqa: E402
from messiah.features import spec as feature_spec  # noqa: E402
from messiah.features.engine import FeatureEngine  # noqa: E402
from messiah.models.registry import ModelRegistry  # noqa: E402
from messiah.simulator.inprocess_bus import InProcessBus  # noqa: E402
from messiah.simulator.replay import ParquetBarReplaySource  # noqa: E402
from messiah.strategy.futures.service import FuturesAIService  # noqa: E402
from messiah.strategy.regime.runtime import RegimeRuntime  # noqa: E402
from messiah.strategy.regime.service import RegimeAI  # noqa: E402

_BAR_DIR = Path("data") / "bars"
_REGISTRY_DB = Path("data") / "models" / "registry.db"
_REGIME_MODEL_PATH = Path("data") / "models" / "regime_ai"


class _TagCollector(logging.Handler):
    """`AggregatorNoContribution`의 갈래를 그대로 받아 적는다 — 화면에 안 나오면 못 본다."""

    def __init__(self, tags: set[str]) -> None:
        super().__init__(level=logging.DEBUG)
        self._tags = tags
        self.records: list[tuple[str, str, dict]] = []

    def emit(self, record: logging.LogRecord) -> None:
        tag = getattr(record, "tag", "")
        if tag in self._tags:
            self.records.append((tag, record.getMessage(), getattr(record, "fields", {}) or {}))


def _resolve_symbol(archiver: ParquetArchiver, day: date, explicit: str | None) -> str:
    """오늘의 근월물 — 명시하지 않으면 **아카이브가 답한다**(네트워크 없이).

    `symbol_master`는 마스터파일 다운로드가 필요하고 이 스크립트는 오프라인이 원칙이다.
    아카이브에서 `day` 이전 1분봉이 가장 최근에 있는 심볼을 고르면 같은 답이 나온다 —
    다르면 그 자체가 볼 것이므로 조용히 넘기지 않는다.
    """
    if explicit:
        return explicit
    best: tuple[date, str] | None = None
    for path in sorted(_BAR_DIR.glob("*")):
        if not path.is_dir() or path.name.startswith("SYN"):
            continue
        days = [d for d in archiver.available_days(path.name, Horizon.M1) if d <= day]
        if days and (best is None or max(days) > best[0]):
            best = (max(days), path.name)
    if best is None:
        raise SystemExit(f"{day} 이전 1분봉 아카이브가 없다 — --symbol로 직접 지정할 것")
    return best[1]


def _next_trading_day() -> date:
    try:
        return EventCalendar.from_file().next_trading_day(now_kst().date())
    except Exception as exc:  # noqa: BLE001 — 달력이 없어도 리허설은 되어야 한다
        fallback = now_kst().date() + timedelta(days=1)
        print(f"달력을 못 읽어 다음 날짜로 진행한다({exc}) — {fallback}", flush=True)
        return fallback


def _warm_start_report(
    archiver: ParquetArchiver,
    engine: FeatureEngine,
    runtime: RegimeRuntime | None,
    chain: list[str],
    on_or_before: date,
    header: str,
) -> None:
    """운영과 **같은 호출**로 채우고, 로더가 건넨 양과 적재된 양을 나란히 적는다."""
    print(f"\n=== {header} (기준일 {on_or_before}) ===", flush=True)
    history: dict[Horizon, list] = {}
    offered: dict[Horizon, dict[str, int]] = {}
    for horizon in Horizon:
        bars, by_source = archiver.load_recent_bars_by_source(
            chain, horizon, on_or_before=on_or_before, max_bars=engine.history_capacity
        )
        history[horizon] = bars
        offered[horizon] = by_source

    loaded = engine.warm_start(history, accept_symbols=chain)

    print(f"  {'Horizon':>8} {'로더':>6} {'적재':>6}   출처", flush=True)
    for horizon in Horizon:
        n_offered = sum(offered[horizon].values())
        n_loaded = loaded.get(horizon, 0)
        flag = "" if n_offered == n_loaded else "   ** 버려진 봉 있음 **"
        print(
            f"  {horizon.value:>8} {n_offered:>6} {n_loaded:>6}   {offered[horizon]}{flag}",
            flush=True,
        )

    if runtime is None:
        print(
            "  국면: 학습된 RegimeAI가 없어 판정 불가 — scripts/train_regime_ai.py 확인",
            flush=True,
        )
        return

    bars30, src30 = archiver.load_recent_bars_by_source(
        chain, Horizon.M30, on_or_before=on_or_before, max_bars=runtime.history_capacity
    )
    n = runtime.warm_start(bars30, accept_symbols=chain)
    minimum = runtime.min_bars_for_classify
    verdict = "하한 충족" if n >= minimum else "** 하한 미달 → UNKNOWN 개장 **"
    print(f"  국면 버퍼: 로더 {len(bars30)}봉 → 적재 {n}봉 (하한 {minimum}) {verdict}", flush=True)
    if n:
        state = runtime.classify_now()
        note = "   ← UNKNOWN이면 그날 판단은 전량 NO_TRADE다"
        print(
            f"  국면 판정: {state.regime.value} (확신도 {state.confidence:.3f})"
            + (note if state.regime.value == "UNKNOWN" else ""),
            flush=True,
        )


async def main(args: argparse.Namespace) -> None:
    cfg = load_instance(args.configs)
    archiver = ParquetArchiver(_BAR_DIR)
    target = date.fromisoformat(args.date) if args.date else _next_trading_day()
    symbol = _resolve_symbol(archiver, target, args.symbol)
    chain = backfill.warmstart_symbol_chain(symbol, target)

    print(f"개장 리허설 — 대상일 {target} · 심볼 {symbol} · 체인 {chain}", flush=True)
    print(f"feature_set={cfg.feature_set}", flush=True)

    regime_ai = None
    if _REGIME_MODEL_PATH.with_suffix(".json").exists():
        try:
            regime_ai = RegimeAI.load(_REGIME_MODEL_PATH)
        except (OSError, ValueError, KeyError) as exc:
            print(f"RegimeAI 로드 실패 — 국면 축 없이 진행: {exc}", flush=True)

    def _new_engine(sym: str) -> FeatureEngine:
        return FeatureEngine(
            sym,
            InProcessBus(instance_id="rehearsal"),
            feature_set=cfg.feature_set,
            sidecars=sidecar.build(feature_spec.resolve(cfg.feature_set)),
        )

    # ── 1. D-day 예보 — 그날 아침 08:20에 실제로 적재될 양 ────────────────────
    bus_forecast = InProcessBus(instance_id="rehearsal-forecast")
    engine_forecast = _new_engine(symbol)
    runtime_forecast = RegimeRuntime(symbol, regime_ai, bus_forecast) if regime_ai else None
    _warm_start_report(
        archiver, engine_forecast, runtime_forecast, chain, target, "1. D-day 웜스타트 예보"
    )

    if not args.replay_enabled:
        return

    # ── 2. 세션 리허설 — 전 거래일까지로 채운 뒤 그날 봉을 흘린다 ─────────────
    replay_day = date.fromisoformat(args.replay) if args.replay else None
    if replay_day is None:
        candidates = [d for d in archiver.available_days(symbol, Horizon.M1) if d <= target]
        if not candidates:
            print("\n재생할 날이 없다 — 리허설의 2단계를 건너뛴다", flush=True)
            return
        replay_day = max(candidates)

    bus = InProcessBus(instance_id="rehearsal-session")
    engine = FeatureEngine(
        symbol,
        bus,
        feature_set=cfg.feature_set,
        sidecars=sidecar.build(feature_spec.resolve(cfg.feature_set)),
    )
    runtime = RegimeRuntime(symbol, regime_ai, bus) if regime_ai else None
    # **전 거래일까지로** 채운다 — 재생할 날을 웜스타트에 넣으면 그날을 두 번 먹인다.
    _warm_start_report(
        archiver,
        engine,
        runtime,
        chain,
        replay_day - timedelta(days=1),
        f"2. 세션 리허설 웜스타트 ({replay_day} 재생 직전 상태)",
    )

    registry = ModelRegistry(_REGISTRY_DB)
    experts = {}
    metas = {}
    for horizon in Horizon:
        live = registry.get_live(horizon)
        if live is None:
            continue
        experts[horizon] = live.load_expert()
        try:
            metas[horizon] = live.load_meta_labeler()
        except FileNotFoundError:
            pass
    print(f"\n  live 번들 결선: {[h.value for h in experts]}", flush=True)
    if not experts:
        print("  ** live 번들이 0개 — n_experts는 구조적으로 0이다(갈래 ①) **", flush=True)

    service = FuturesAIService(symbol, experts, bus, meta_labelers=metas)
    await engine.run_forever()
    await service.run_forever()
    if runtime is not None:
        await runtime.run_forever()

    views: list[FuturesView] = []

    async def _collect(msg) -> None:
        if isinstance(msg, FuturesView):
            views.append(msg)

    await bus.subscribe(["intel.futures"], _collect)

    collector = _TagCollector({"AggregatorNoContribution", "RegimeClassified"})
    messiah_logger = logging.getLogger("messiah")
    # **레벨을 안 내리면 INFO 태그가 핸들러에 닿지도 않는다.** 첫 실행에서 실제로 그랬다 —
    # `AggregatorNoContribution`·`RegimeClassified`가 둘 다 INFO라 수집이 0건이었고,
    # 화면은 그걸 "갈래 없음"으로 읽어 **n_experts=0인 15사이클을 정상처럼 보고**했다.
    # 계측기가 자기 계측 공백을 정상으로 읽는 형태다(L18).
    previous_level = messiah_logger.level
    messiah_logger.setLevel(logging.DEBUG)
    messiah_logger.addHandler(collector)
    try:
        bars = ParquetBarReplaySource(_BAR_DIR, symbol).load(replay_day, replay_day)
        print(f"  재생: {replay_day} {len(bars)}봉", flush=True)
        for bar in sorted(bars, key=lambda b: (b.bar_open_kst, b.horizon.value)):
            await bus.publish(f"bar.{bar.horizon.value}.{symbol}", bar)
    finally:
        messiah_logger.removeHandler(collector)
        messiah_logger.setLevel(previous_level)

    regimes = [f["regime"] for tag, _m, f in collector.records if tag == "RegimeClassified"]
    print(f"\n=== 3. 리허설 결과 ({replay_day} 재생) ===", flush=True)
    if regimes:
        counts: dict[str, int] = {}
        for r in regimes:
            counts[r] = counts.get(r, 0) + 1
        print(f"  국면 판정 {len(regimes)}건 분포: {counts}", flush=True)
    else:
        print("  국면 판정 0건 — RegimeRuntime이 붙지 않았거나 30m 봉이 없다", flush=True)

    if views:
        n_experts = [v.n_experts for v in views]
        nonzero = sum(1 for n in n_experts if n > 0)
        print(
            f"  FuturesView {len(views)}건 · n_experts>0 {nonzero}건 "
            f"(최대 {max(n_experts)} · 최소 {min(n_experts)})",
            flush=True,
        )
    else:
        print("  FuturesView 0건 — feat.* 가 전문가에 닿지 않았다", flush=True)

    branches: dict[str, int] = {}
    for tag, _msg, fields in collector.records:
        if tag != "AggregatorNoContribution":
            continue
        for name in (
            "outside_weight_table",
            "zero_regime_weight",
            "blocked_by_meta",
            "blocked_by_uncertainty",
            "blocked_by_freshness",
        ):
            if fields.get(name):
                branches[name] = branches.get(name, 0) + 1
        if not fields.get("views_received"):
            branches["views_empty"] = branches.get("views_empty", 0) + 1
    zero_cycles = sum(1 for v in views if v.n_experts == 0)
    if branches:
        print(f"  n_experts=0 갈래별 관여: {branches}", flush=True)
    elif zero_cycles:
        # **갈래가 안 잡혔는데 0인 사이클이 있다** — 계측이 안 된 것이지 사유가 없는 게
        # 아니다. 둘을 같은 문장으로 내보내면 이 스크립트가 만들어진 이유가 무효가 된다.
        print(
            f"  ** n_experts=0 사이클이 {zero_cycles}건인데 갈래 기록이 0건 — "
            "계측 공백이다(사유 없음이 아니다) **",
            flush=True,
        )
    elif views:
        print("  n_experts=0 사이클 없음 — 전 사이클이 기여 의견을 받았다", flush=True)

    print(
        "\n※ 이것은 배관 점검이지 예측이 아니다 — "
        "판정이 **나온다**는 것만이 이 스크립트의 산출이다.",
        flush=True,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MESSIAH 개장 리허설 (읽기 전용·오프라인)")
    parser.add_argument("--date", help="리허설 대상 거래일 YYYY-MM-DD (기본: 다음 거래일)")
    parser.add_argument("--symbol", help="근월물 심볼 (기본: 아카이브에서 유도)")
    parser.add_argument("--replay", help="세션 재생에 쓸 아카이브 날짜 (기본: 가장 최근)")
    parser.add_argument(
        "--no-replay",
        dest="replay_enabled",
        action="store_false",
        default=True,
        help="웜스타트 예보만 내고 끝낸다",
    )
    parser.add_argument("--configs", default="configs")
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(main(_parse_args()))
