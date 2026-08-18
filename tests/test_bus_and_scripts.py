"""W1 잔여 검증 — 버스 코덱 왕복 · self_check · agenda 생성기."""

from __future__ import annotations

import sys
from datetime import date
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


# ---------------------------------------------------------------- 시간 동기 (2026-08-05)


def test_check_clock_fails_when_the_offset_is_large():
    """2026-08-04 회귀 — 이 PC의 시계가 실제보다 14.41초 느린 채로 8거래일을 돌았는데
    `check_timezone`은 매일 `[OK]`였다(그건 UTC 오프셋이 9시간인지만 봤다).

    SYSTEM.md §4-6은 기동 자가 점검에 "시간 동기"를 요구하고 실패 시 거래를 거부하라고
    적혀 있다 — 그 요건이 이제 실제로 측정된다.
    """
    result = sc.check_clock(offset_reader=lambda: (14.41, ""), service_reader=lambda: True)

    assert result.ok is False
    assert "기동 거부" in result.detail
    assert "+14.410s" in result.detail


def test_check_clock_warns_but_passes_in_the_grey_zone():
    """완성봉 유예 500ms보다는 크지만 거래를 막을 정도는 아닌 구간."""
    result = sc.check_clock(offset_reader=lambda: (3.0, ""), service_reader=lambda: True)

    assert result.ok is True
    assert "경고" in result.detail


def test_check_clock_passes_on_a_synced_clock():
    """2026-08-05 w32time 복구 후 실측(오프셋 0.0006초)."""
    result = sc.check_clock(offset_reader=lambda: (-0.0006, ""), service_reader=lambda: True)

    assert result.ok is True
    assert "경고" not in result.detail
    assert "w32time=Running" in result.detail


def test_check_clock_flags_a_stopped_time_service():
    """근본 원인이었던 상태 — 서비스가 `Stopped`/`Manual`이라 부팅해도 동기가 안 됐다.
    오프셋을 못 재도 이건 확실한 결함이므로 반드시 화면에 남는다."""
    result = sc.check_clock(
        offset_reader=lambda: (None, "측정 실패(오프라인)"), service_reader=lambda: False
    )

    assert "w32time=Stopped" in result.detail
    assert "측정 실패" in result.detail


def test_check_clock_does_not_block_startup_when_it_cannot_measure():
    """오프라인·차단 망에서 수집조차 못 하게 만드는 것은 과하다 — 사실만 남기고 통과."""
    result = sc.check_clock(
        offset_reader=lambda: (None, "측정 실패(응답에 오프셋 없음)"),
        service_reader=lambda: True,
    )

    assert result.ok is True
    assert "측정 실패" in result.detail


# ------------------- git 점검이 진짜 원인을 말하는가 (2026-08-06 P2-2)


class _GitRun:
    """`subprocess.run` 대역 — git이 어떻게 실패했는지를 흉내낸다."""

    def __init__(self, *, code: int = 0, out: str = "", err: str = "", raises=None) -> None:
        self.code, self.out, self.err, self.raises = code, out, err, raises

    def __call__(self, cmd, **_kwargs):  # noqa: ANN001
        import subprocess as sp

        if self.raises is not None:
            raise self.raises
        return sp.CompletedProcess(cmd, self.code, stdout=self.out, stderr=self.err)


def test_git_rejection_quotes_what_git_actually_said(monkeypatch) -> None:
    """**2026-08-06 실측 대응.** 그날 10:25 기동에서 `git 저장소 아님`이 찍혔는데, 같은
    디렉터리가 두 시간 전에는 `clean`이었다. `except Exception:` 하나가 모든 실패를 하나의
    고정된 거짓말로 덮었고, 진짜 원인은 예외와 함께 버려져 지금도 확정할 수 없다.
    """
    monkeypatch.setattr(
        sc.subprocess,
        "run",
        _GitRun(code=128, err="fatal: Unable to create '.git/index.lock': File exists."),
    )

    result = sc.check_git_state("dev")

    assert "index.lock" in result.detail, "git이 한 말이 사라졌다"
    assert "저장소 아님" not in result.detail


def test_missing_git_binary_says_so(monkeypatch) -> None:
    monkeypatch.setattr(sc.subprocess, "run", _GitRun(raises=FileNotFoundError()))

    assert "실행 파일 없음" in sc.check_git_state("dev").detail


def test_git_timeout_is_named(monkeypatch) -> None:
    import subprocess as sp

    monkeypatch.setattr(sc.subprocess, "run", _GitRun(raises=sp.TimeoutExpired("git", 20)))

    assert "무응답" in sc.check_git_state("dev").detail


def test_dirty_tree_still_blocks_live(monkeypatch) -> None:
    """계명 10은 그대로다 — 실패 사유를 나눈 것이지 관문을 푼 것이 아니다."""
    monkeypatch.setattr(sc.subprocess, "run", _GitRun(out=" M src/a.py\n?? b.py\n"))

    assert sc.check_git_state("live").ok is False
    assert sc.check_git_state("dev").ok is True


def test_clean_tree_passes(monkeypatch) -> None:
    monkeypatch.setattr(sc.subprocess, "run", _GitRun(out=""))

    result = sc.check_git_state("live")

    assert result.ok is True
    assert result.detail == "clean"


# -------------------------------------- 완성봉 유예 ↔ 회선 실측 (2026-08-18 G-0818P-2)
#
# `check_clock`은 시계 오프셋을 재며 "완성봉 유예 500ms보다 큼"을 경고한다 — 즉 완성봉
# 예산을 이미 판단 기준으로 쓰고 있었다. 그런데 그 예산을 실제로 잡아먹는 **회선 지연**은
# 어느 축도 예산과 대조하지 않았다. 2026-08-18 실측 p50 0.5204 — 중앙값이 이미 예산을 넘는다.


def _integrity_report(directory, day: str, p90: float | None) -> None:
    import json

    payload = {"date": day, "symbol": "A05609"}
    if p90 is not None:
        payload["delivery_latency"] = {"p50": p90 - 0.4, "p90": p90, "p99": p90 + 0.1}
    (directory / f"daily_integrity_{day.replace('-', '')}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def test_the_grace_note_warns_when_the_line_is_slower_than_the_budget(tmp_path: Path) -> None:
    """2026-08-18 실측 형태(p90 927ms > 유예 500ms) — 이 문장이 매 아침 분포를 쌓는다."""
    _integrity_report(tmp_path, "2026-08-18", 0.9271)

    note = sc._grace_vs_latency_note(today=date(2026, 8, 19), log_dir=tmp_path)

    assert "경고" in note
    assert "927ms" in note
    assert "2026-08-18" in note, "어느 날 실측인지 없으면 사람이 다시 캐야 한다"


def test_the_grace_note_is_quiet_when_the_line_is_fast_enough(tmp_path: Path) -> None:
    _integrity_report(tmp_path, "2026-08-18", 0.21)

    note = sc._grace_vs_latency_note(today=date(2026, 8, 19), log_dir=tmp_path)

    assert "경고" not in note
    assert "210ms" in note, "조용해도 값은 남긴다 — 분포는 매일 쌓여야 한다"


def test_a_missing_report_says_so_instead_of_passing_silently(tmp_path: Path) -> None:
    """못 잰 것을 "정상"으로 접지 않는다(L18)."""
    note = sc._grace_vs_latency_note(today=date(2026, 8, 19), log_dir=tmp_path)

    assert "대조 불가" in note


def test_a_report_without_the_latency_field_is_not_treated_as_zero(tmp_path: Path) -> None:
    """이 필드 이전에 쓰인 옛 리포트 — 0ms로 읽으면 영원히 조용한 축이 된다."""
    _integrity_report(tmp_path, "2026-08-18", None)

    note = sc._grace_vs_latency_note(today=date(2026, 8, 19), log_dir=tmp_path)

    assert "회선 지연 없음" in note


def test_today_report_is_not_used_as_yesterday(tmp_path: Path) -> None:
    """오늘 리포트는 아직 없다 — 기동 시점에 존재하면 그건 어제 것이어야 한다."""
    _integrity_report(tmp_path, "2026-08-19", 5.0)  # 오늘(있을 수 없는 값)
    _integrity_report(tmp_path, "2026-08-18", 0.9271)

    note = sc._grace_vs_latency_note(today=date(2026, 8, 19), log_dir=tmp_path)

    assert "927ms" in note
    assert "5000ms" not in note


def test_the_grace_comes_from_the_composer_constant() -> None:
    """두 번째 상수를 만들지 않는다 — 합성기에서 바뀌면 이 대조도 따라가야 한다."""
    from messiah.data.bar_composer import _BOUNDARY_GRACE_SECONDS

    assert sc._boundary_grace_seconds() == float(_BOUNDARY_GRACE_SECONDS)
