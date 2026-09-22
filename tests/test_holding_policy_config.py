"""보유·청산 정책 설정 (2026-09-22 F-121) — Holding Policy Ver1.0 §5-4.

그 문서는 2026-07-21에 "모든 수치는 `configs/holding_policy.yaml`"이라고 적었으나 두 달간
그 파일이 없었다. 이 파일이 지키는 것 셋:

  ① **없어도 기동한다** — 없는 설정 파일이 재생·스모크를 깨면 안 된다
  ② 그러나 없을 때의 기본값은 **비무장**이다 — 설정을 못 읽었는데 주문이 나가면 안 된다
  ③ 저장소에 실제로 들어 있는 파일이 코드가 읽을 수 있는 모양이다
"""

from __future__ import annotations

from pathlib import Path

from messiah.core.config import HoldingPolicyConfig, load_holding_policy
from messiah.strategy import position_exit

_REPO_CONFIGS = Path(__file__).resolve().parents[1] / "configs"


def test_missing_file_falls_back_to_defaults(tmp_path: Path):
    """①"""
    cfg = load_holding_policy(tmp_path)

    assert isinstance(cfg, HoldingPolicyConfig)
    assert cfg.futures_exit.stop_atr_mult is None, "모듈 상수를 여기 복사하지 않는다"


def test_missing_file_means_unarmed(tmp_path: Path):
    """② — 안전 기본값은 언제나 "주문을 안 낸다"이다."""
    assert load_holding_policy(tmp_path).futures_exit.armed is False


def test_empty_file_means_unarmed(tmp_path: Path):
    """파일이 있는데 내용이 비었을 때도 같다 — 빈 파일이 무장으로 읽히면 안 된다."""
    (tmp_path / "holding_policy.yaml").write_text("", encoding="utf-8")

    assert load_holding_policy(tmp_path).futures_exit.armed is False


def test_the_repo_file_parses_and_says_what_it_means():
    """③ — 저장소에 실제로 있는 파일을 읽는다. 2026-09-22 사용자 결정은 즉시 실청산이다."""
    cfg = load_holding_policy(_REPO_CONFIGS)

    assert cfg.futures_exit.armed is True
    assert cfg.futures_exit.stop_atr_mult == 1.0
    assert cfg.futures_exit.take_profit_atr_mult is None, "익절은 이번 스코프에 없다"
    assert cfg.futures_exit.resubmit_cooldown_seconds == 120.0


def test_the_repo_stop_multiple_matches_the_module_default():
    """**핵심 회귀** — 설정과 모듈 상수가 어긋나면 사이저 전제와의 정합이 조용히 깨진다.

    손절폭 1.0의 근거는 취향이 아니라 `risk/sizer.py`가 쓰는 `stop_distance_ticks`
    (= `ATR(M1,14) × 1.0`)와 같은 축이라는 것이다. 한쪽만 바뀌면 R1이 다시 종이가 된다.
    """
    cfg = load_holding_policy(_REPO_CONFIGS)

    assert cfg.futures_exit.stop_atr_mult == position_exit.DEFAULT_STOP_ATR_MULT
