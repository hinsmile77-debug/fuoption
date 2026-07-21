"""W1 잔여 검증 — 버스 코덱 왕복 · self_check · agenda 생성기."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import agenda as agenda_mod  # noqa: E402  (scripts/)
import self_check as sc  # noqa: E402
from messiah.core.bus import STREAM_TOPICS, decode, encode, registered_types
from messiah.core.messages import (
    DecisionIntent,
    Fill,
    Horizon,
    OrderKind,
    OrderRequest,
    Side,
)
from messiah.core.timeutil import now_kst

# ---------------------------------------------------------------- 코덱 (서버 불필요)


def test_codec_roundtrip_intent() -> None:
    original = DecisionIntent(
        symbol="K200_MINI_FUT",
        side=Side.LONG,
        confidence=0.71,
        uncertainty=0.09,
        horizon=Horizon.M5,
        top_features=[("ms_ofi_20", 0.32), ("fl_frgn_net_20", 0.21)],
        rationale="S=+0.42 trend_up",
    )
    restored = decode(encode(original))
    assert isinstance(restored, DecisionIntent)
    assert restored.symbol == original.symbol
    assert restored.confidence == pytest.approx(0.71)
    assert restored.ts_utc == original.ts_utc  # tz-aware 왕복 보존
    assert restored.top_features[0][0] == "ms_ofi_20"


def test_codec_roundtrip_fill_and_order() -> None:
    req = OrderRequest(
        intent_id="i1",
        symbol="K200_MINI_FUT",
        kind=OrderKind.ENTRY,
        side=Side.SHORT,
        qty=2,
        limit_price_ticks=41250,
    )
    fill = Fill(
        broker_order_no="B1",
        symbol="K200_MINI_FUT",
        qty=2,
        price_ticks=41250,
        ts_exchange=now_kst(),
        pending_matched=True,
    )
    assert decode(encode(req)).qty == 2
    assert decode(encode(fill)).pending_matched is True


def test_codec_rejects_unknown_type() -> None:
    with pytest.raises(ValueError, match="미등록 메시지 타입"):
        decode(b'{"_type": "HackedMessage", "payload": {}}')


def test_registry_covers_core_messages() -> None:
    names = registered_types()
    for expected in (
        "Tick",
        "BarClosed",
        "FeatureVector",
        "DecisionIntent",
        "OrderRequest",
        "Fill",
        "Health",
        "KillSignal",
    ):
        assert expected in names
    assert "decision.intent" in STREAM_TOPICS


# ---------------------------------------------------------------- self_check


def test_self_check_passes_in_dev(tmp_path: Path) -> None:
    """dev 모드 + redis 생략 → 전 항목 통과해야 한다."""
    results = sc.run_all(config_dir=str(tmp_path), skip_redis=True)  # instance.yaml 없음 → dev 기본
    failed = [r.name for r in results if not r.ok]
    assert not failed, f"self-check 실패: {failed}"


def test_self_check_blocks_live_without_bundle(tmp_path: Path) -> None:
    (tmp_path / "instance.yaml").write_text(
        "instance_id: x\nmode: live\nmodel_bundle: none\n", encoding="utf-8"
    )
    results = sc.run_all(config_dir=str(tmp_path), skip_redis=True)
    cfg_check = next(r for r in results if r.name == "config")
    assert not cfg_check.ok  # live + 번들 미지정 = 기동 거부


# ---------------------------------------------------------------- agenda


def test_agenda_flags_aging_and_unverified(tmp_path: Path) -> None:
    dm = tmp_path / "dev_memory"
    dm.mkdir()
    (dm / "NEXT_TODO.md").write_text(
        "- [ ] 오래된 항목 (2026-01-05 등록)\n- [ ] 최근 항목 (2026-07-20 등록)\n",
        encoding="utf-8",
    )
    (dm / "DECISION_LOG.md").write_text("**검증**: 라이브 미검증 — 기한 없음\n", encoding="utf-8")
    out = agenda_mod.build_agenda(tmp_path, tmp_path / "no.log")
    assert "에이징" in out  # 60일 초과 항목 강조
    assert "검증 기한 미기재" in out  # L15 위반 자동 안건화
    assert "채택(티켓화) / 보류(기한 명시) / 폐기(사유 기록)" in out
