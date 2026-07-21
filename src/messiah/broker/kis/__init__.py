"""KIS(한국투자증권) OpenAPI 저수준 트랜스포트 계층 — 마흐디(선행 프로젝트) 이식.

이 패키지는 인증(token_daemon)·REST(rest_client)·WS(ws_client)·주문 상태(order_state_machine)만
책임진다. BrokerAdapter(messiah.broker.base) 구현은 이 계층을 감싸는 별도 어댑터(추후)의 몫이다.
"""

from __future__ import annotations
