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
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

from messiah.core import universe as universe_vocab

load_dotenv()


class BrokerConfig(BaseModel):
    name: str = "kis"  # kis | ls | simulator
    account_ref: str = "env:KIS_ACCOUNT"  # 실제 값은 .env에서
    app_key_ref: str = "env:KIS_APP_KEY"
    app_secret_ref: str = "env:KIS_APP_SECRET"
    account_product_code: str = "01"  # KIS 계좌상품코드(ACNT_PRDT_CD) — 시크릿 아님, 평문 고정값
    is_paper: bool = True


class CapitalConfig(BaseModel):
    total: int = 50_000_000
    daily_loss_limit_pct: float = 2.0  # R2 → Kill Switch
    margin_cap_pct: float = 40.0  # R3 (Holding Policy §3)
    overnight_margin_cap_pct: float = 25.0  # R4
    max_overnight_positions: int = 2  # R5


_MINUTE_BAR_CLOSE_MODES = frozenset({"tick", "timer"})


class InstanceConfig(BaseModel):
    """인스턴스 정의 — 멀티 PC 복제 배포 시 PC마다 이 파일 하나만 다르다 (Ver 1.1 §7.2)."""

    instance_id: str = "messiah-dev-01"
    mode: str = "dev"  # dev | paper | live | replay
    broker: BrokerConfig = Field(default_factory=BrokerConfig)
    secondary_broker: BrokerConfig | None = None  # LS 이중화 (데이터 교차검증)
    capital: CapitalConfig = Field(default_factory=CapitalConfig)
    # 2026-08-04 확정: 미니선물 + 먼쓰리/월위클리/목위클리 옵션 (`core/universe.py`).
    # 검증기를 붙인 이유는 종전 `K200_OPT`가 소비자 없이 설정에만 남아 있었기 때문이다 —
    # 기동 시점에 깨지는 편이 "수집되는 줄 알았다"보다 낫다.
    universe: list[str] = Field(default_factory=lambda: list(universe_vocab.DEFAULT_UNIVERSE))
    model_bundle: str = "none"  # 릴리스 번들 ID (예: messiah-2026.08)
    redis_url: str = "redis://localhost:6379/0"
    # FeatureVector.feature_set (Ver 1.4 §5.2). 이 이름 하나가 벡터 모양 하나를 정한다 —
    # 해석은 `features/spec.py`의 `FEATURE_SETS`. 검증기를 붙인 이유는 `universe`와 같다:
    # 오타(`v2026.08-f1`)가 조용히 기저 벡터(PX+VL)로 떨어지면 "FL을 켰는데 왜 그대로지"로
    # 몇 주를 쓴다. 기동 시점에 깨지는 편이 낫다.
    feature_set: str = "v2026.07"
    # 미니선물(A05608) 2026-07-22 실측값(호가 5단계 간격 역산) — 다른 상품/근월물에 그대로
    # 일반화하지 말 것(capability_matrix.md "알려진 갭" 참고, 상품별 실측 전까지는 이 값만 사용).
    futures_tick_size: str = "0.02"
    # 1분봉을 언제 닫는가 (2026-08-05 고도화 1, `data/normalizer.py` "봉을 언제 닫는가").
    #
    #   tick  — 다음 분의 첫 틱이 도착하면 닫는다(종전 동작, 기본값)
    #   timer — 거래소 시각이 경계+유예를 지나면 닫는다
    #
    # `timer`가 근본 처방이지만 **아직 기본이 아니다**: 유예 뒤에 도착한 틱을 버리게 되는데,
    # 그 크기를 정할 회선 지연 분포가 2026-08-05까지 측정된 적이 없었다. 같은 날 계측을
    # 붙였으므로(`TickDeliveryLatency`) 며칠 p99를 보고 승격한다. 실측 없이 임계를 정하지
    # 않는다 — 이 프로젝트가 반복해서 배운 것이다.
    #
    # 2026-08-11 G-4 진행 상황: 유예 상수는 3거래일 실측으로 확정했다(1.0 → 2.0초,
    # `data/normalizer.MINUTE_CLOSE_GRACE_SECONDS` 주석의 표). **남은 것은 이 한 줄뿐**이고,
    # 08-11 15:35에 4일째 표본이 나온 뒤 `timer`로 바꾼다. 되돌리려면 다시 `tick`으로.
    minute_bar_close: str = "tick"

    @field_validator("universe")
    @classmethod
    def _known_tokens_only(cls, v: list[str]) -> list[str]:
        return universe_vocab.validate(v)

    @field_validator("minute_bar_close")
    @classmethod
    def _known_close_mode_only(cls, v: str) -> str:
        """오타가 조용히 기본 동작으로 떨어지면 "켰는데 왜 그대로지"로 며칠을 쓴다 —
        `universe`·`feature_set`과 같은 이유로 기동 시점에 깨진다."""
        if v not in _MINUTE_BAR_CLOSE_MODES:
            raise ValueError(
                f"minute_bar_close는 {sorted(_MINUTE_BAR_CLOSE_MODES)} 중 하나여야 한다: '{v}'"
            )
        return v

    @field_validator("feature_set")
    @classmethod
    def _registered_feature_set_only(cls, v: str) -> str:
        # 지연 임포트 — `features/spec.py`는 계산기 모듈(→ polars)을 끌어오는데, 설정 모듈은
        # UI·스크립트가 가볍게 임포트하는 자리라 그 비용을 모듈 로드 시점에 지우지 않는다.
        from messiah.features import spec as feature_spec

        if v not in feature_spec.FEATURE_SETS:
            known = ", ".join(feature_spec.registered_names())
            raise ValueError(
                f"미등록 feature_set '{v}' — features/spec.py의 FEATURE_SETS에 등록된 이름만 "
                f"쓸 수 있다(현재: {known})"
            )
        return v


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
