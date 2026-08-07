"""정본 소비자 검사 — 2026-08-07 고도화 2.

이 테스트가 깨지면 "정본은 있는데 아무도 안 쓴다"가 다시 생긴 것이다. 고치는 방법은
**해당 소비자가 그 정본을 부르게 하는 것**이지 등록부에서 지우는 것이 아니다 — 지우려면
`CANONICAL`의 `why`에 적힌 근거가 왜 더 이상 성립하지 않는지를 먼저 적어야 한다.
"""

from __future__ import annotations

from pathlib import Path

from messiah.ops import canonical_consumers as cc


def test_every_canonical_symbol_is_actually_consumed():
    """저장소 실제 상태 — 이게 이 모듈의 유일한 목적이다."""
    assert cc.findings() == []


def test_registry_is_not_empty():
    """등록부가 비면 검사는 항상 통과한다 — 그건 검사가 아니다."""
    assert cc.CANONICAL


def test_every_canon_declares_home_and_reason():
    for canon in cc.CANONICAL:
        assert Path(canon.home).parts, canon.symbol
        assert canon.expected_consumers, canon.symbol
        assert canon.why.strip(), canon.symbol


def test_missing_consumer_is_reported(tmp_path):
    """소비자가 그 심볼을 언급조차 안 하면 잡힌다."""
    (tmp_path / "src").mkdir()
    target = tmp_path / "src" / "consumer.py"
    target.write_text("# 아무것도 안 부른다\n", encoding="utf-8")
    canon = cc.Canon(
        symbol="some_canonical_rule",
        home="src/home.py",
        expected_consumers=("src/consumer.py",),
        why="테스트",
    )
    assert canon.missing(tmp_path) == ["src/consumer.py"]

    target.write_text("x = some_canonical_rule()\n", encoding="utf-8")
    assert canon.missing(tmp_path) == []


def test_absent_file_is_reported_distinctly(tmp_path):
    """파일이 사라진 것과 안 쓰는 것은 다른 사고다 — 문구가 갈려야 조사가 갈린다."""
    canon = cc.Canon(
        symbol="rule", home="src/home.py", expected_consumers=("src/gone.py",), why="테스트"
    )
    assert canon.missing(tmp_path) == ["src/gone.py (파일 없음)"]
