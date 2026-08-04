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
    (dm / "DECISION_LOG.md").write_text(
        "**검증**: Redis 연동은 **라이브 미검증** — 기한 없음\n", encoding="utf-8"
    )
    out = agenda_mod.build_agenda(tmp_path, tmp_path / "no.log")
    assert "에이징" in out  # 60일 초과 항목 강조
    assert "검증 기한 미기재" in out  # L15 위반 자동 안건화
    assert "채택(티켓화) / 보류(기한 명시) / 폐기(사유 기록)" in out


def test_collect_unverified_ignores_plain_mentions_requires_bold_tag(tmp_path: Path) -> None:
    decision_log = tmp_path / "DECISION_LOG.md"
    decision_log.write_text(
        '> "라이브 미검증" 항목은 반드시 검증 기한을 명기한다 (L15).\n'
        "**검증**: 실계좌 확인. Redis 연동은 **라이브 미검증** (검증 기한: 2026-07-24).\n"
        '어딘가에서 "라이브 미검증"이라는 단어를 다시 인용만 하는 회고 문장도 있다.\n',
        encoding="utf-8",
    )
    out = agenda_mod.collect_unverified(decision_log)
    assert len(out) == 1  # 볼드 태그가 붙은 실제 항목 하나만 잡힘
    assert "검증 기한: 2026-07-24" in out[0]


def test_resolve_log_paths_glob_picks_recent_n_by_filename_order(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    for day in ("20260724", "20260726", "20260727"):
        (logs / f"l1_daily_{day}.log").write_text("", encoding="utf-8")
    resolved = agenda_mod.resolve_log_paths(tmp_path, "logs/l1_daily_*.log", days=2)
    assert [p.name for p in resolved] == ["l1_daily_20260726.log", "l1_daily_20260727.log"]


def test_resolve_log_paths_non_glob_pattern_returns_single_path_unchecked(
    tmp_path: Path,
) -> None:
    resolved = agenda_mod.resolve_log_paths(tmp_path, "logs/messiah.log", days=1)
    assert resolved == [tmp_path / "logs" / "messiah.log"]


def test_collect_log_alerts_reports_missing_when_glob_matches_nothing(tmp_path: Path) -> None:
    out = agenda_mod.collect_log_alerts(
        agenda_mod.resolve_log_paths(tmp_path, "logs/l1_daily_*.log", days=1)
    )
    assert out == ["(로그 없음: 패턴에 매치되는 파일 없음)"]


def test_collect_log_alerts_aggregates_across_multiple_daily_files(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "l1_daily_20260726.log").write_text(
        '{"level": "INFO", "tag": "SessionStart", "msg": "start"}\n'
        '{"level": "WARNING", "tag": "FeatureNaN"}\n',
        encoding="utf-8",
    )
    (logs / "l1_daily_20260727.log").write_text(
        '{"level": "INFO", "tag": "SessionStart", "msg": "start"}\n'
        '{"level": "WARNING", "tag": "FeatureNaN"}\n'
        '{"level": "CRITICAL", "tag": "FillUnmatched"}\n',
        encoding="utf-8",
    )
    paths = agenda_mod.resolve_log_paths(tmp_path, "logs/l1_daily_*.log", days=2)
    out = agenda_mod.collect_log_alerts(paths)
    joined = "\n".join(out)
    assert "WARNING [FeatureNaN] × 2" in joined  # 두 파일 합산
    assert "CRITICAL [FillUnmatched] × 1" in joined


def test_collect_log_alerts_scopes_to_last_session_start_per_file(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "l1_daily_20260727.log").write_text(
        '{"level": "WARNING", "tag": "BeforeRestart"}\n'
        '{"level": "INFO", "tag": "SessionStart", "msg": "restart"}\n'
        '{"level": "WARNING", "tag": "AfterRestart"}\n',
        encoding="utf-8",
    )
    out = agenda_mod.collect_log_alerts([logs / "l1_daily_20260727.log"])
    joined = "\n".join(out)
    assert "AfterRestart" in joined
    assert "BeforeRestart" not in joined  # 재시작 이전 경보는 이번 세션 집계에서 제외(L24)
