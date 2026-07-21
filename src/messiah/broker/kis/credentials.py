"""KIS API 인증정보 — BrokerConfig(instance.yaml)의 env: 참조를 해석해 만든다.

시크릿(앱키·시크릿·계좌번호)은 .env에만 존재한다 (SYSTEM.md §3) — git에는 env: 참조만 남는다.
"""

from __future__ import annotations

from dataclasses import dataclass

from messiah.core.config import BrokerConfig, resolve_secret


@dataclass(frozen=True)
class KISCredentials:
    app_key: str
    app_secret: str
    account_no: str = ""
    account_product_code: str = "01"
    is_mock: bool = True

    @classmethod
    def from_broker_config(cls, cfg: BrokerConfig) -> "KISCredentials":
        return cls(
            app_key=resolve_secret(cfg.app_key_ref),
            app_secret=resolve_secret(cfg.app_secret_ref),
            account_no=resolve_secret(cfg.account_ref),
            account_product_code=cfg.account_product_code,
            is_mock=cfg.is_paper,
        )
