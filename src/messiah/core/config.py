"""설정 로더 — SYSTEM.md R4: 하드코딩 금지, 전부 YAML + .env.

- configs/{mode}.yaml : 모드 공통 설정 (dev / paper / live)
- configs/instance.yaml : 인스턴스별 차이 (계좌 참조·자본·한도) — 복제 배포의 유일한 차이점
- 시크릿(.env): 앱키·계좌번호. 설정 파일에는 env:KEY 참조만 적는다 (git에 시크릿 금지)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class BrokerConfig(BaseModel):
    name: str = "kis"  # kis | ls | simulator
    account_ref: str = "env:KIS_ACCOUNT"  # 실제 값은 .env에서
    app_key_ref: str = "env:KIS_APP_KEY"
    app_secret_ref: str = "env:KIS_APP_SECRET"
    is_paper: bool = True


class CapitalConfig(BaseModel):
    total: int = 50_000_000
    daily_loss_limit_pct: float = 2.0  # R2 → Kill Switch
    margin_cap_pct: float = 40.0  # R3 (Holding Policy §3)
    overnight_margin_cap_pct: float = 25.0  # R4
    max_overnight_positions: int = 2  # R5


class InstanceConfig(BaseModel):
    """인스턴스 정의 — 멀티 PC 복제 배포 시 PC마다 이 파일 하나만 다르다 (Ver 1.1 §7.2)."""

    instance_id: str = "messiah-dev-01"
    mode: str = "dev"  # dev | paper | live | replay
    broker: BrokerConfig = Field(default_factory=BrokerConfig)
    secondary_broker: BrokerConfig | None = None  # LS 이중화 (데이터 교차검증)
    capital: CapitalConfig = Field(default_factory=CapitalConfig)
    universe: list[str] = Field(default_factory=lambda: ["K200_MINI_FUT", "K200_OPT"])
    model_bundle: str = "none"  # 릴리스 번들 ID (예: messiah-2026.08)
    redis_url: str = "redis://localhost:6379/0"


def resolve_secret(ref: str) -> str:
    """'env:KEY' 참조를 .env/환경변수에서 해석. 실제 시크릿은 로그·설정에 남기지 않는다."""
    if ref.startswith("env:"):
        key = ref[4:]
        val = os.environ.get(key)
        if not val:
            raise RuntimeError(f"환경변수 {key} 미설정 — .env 확인 (시크릿은 git 금지)")
        return val
    return ref


def load_instance(config_dir: str | Path = "configs") -> InstanceConfig:
    """instance.yaml 로드. 없으면 dev 기본값 (실전 모드에서는 파일 필수)."""
    path = Path(config_dir) / "instance.yaml"
    if not path.exists():
        return InstanceConfig()
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg = InstanceConfig.model_validate(raw)
    if cfg.mode == "live" and cfg.model_bundle == "none":
        raise RuntimeError("live 모드는 model_bundle 지정 필수 (기동 자가 점검, Ver 1.1 §7.3)")
    return cfg
