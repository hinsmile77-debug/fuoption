"""주문체결통보(H0IFCNI0/H0IFCNI9) 실측 프로브 — 모의계좌로 실제 통보를 받아본다.

`broker/kis/order_notice.py`는 단위 테스트를 전부 통과하지만, 그 테스트가 검증하는 것은
**우리가 가정한 프로토콜대로라면 우리 코드가 맞게 동작한다**는 것뿐이다. 가정 자체가
맞는지는 실제 KIS 서버만 답할 수 있고, 이 프로젝트가 반복해서 배운 것이 정확히 그
구분이다("구현됨 ≠ 검증됨", `broker/base.py`).

이 스크립트가 확인하려는 가정은 넷이다. 각각 틀렸을 때 증상이 **전부 똑같이**
"통보가 안 온다"라서, 실서비스에서 마주치면 원인 분리에 며칠이 걸린다:

  A. 체결통보 도메인(모의=ops:31000)이 맞고, 그 도메인이 모의 앱키를 받아준다
  B. tr_key가 HTS ID가 맞다 (계좌번호나 앱키가 아니라)
  C. 구독 성공 응답의 iv/key로 본문이 실제로 복호된다
  D. 복호 결과가 정말 22개 필드이고, 순서가 공식 샘플 컬럼 목록과 일치한다

D는 특히 중요하다 — 순서가 하나만 밀려도 코드는 조용히 동작하고 체결가 자리에
주문수량이 들어온다. 그래서 이 스크립트는 판정하지 않고 **받은 것을 전부 그대로 찍는다.**
사람이 눈으로 대조하는 것이 이 단계에서 유일하게 믿을 수 있는 검증이다.

## 실행

    python scripts/probe_order_notice.py                 # 구독만 (주문 없이 대기)
    python scripts/probe_order_notice.py --submit A05609 # 구독 + 시장가 1계약 매수

`--submit` 없이는 **아무 주문도 내지 않는다.** 통보는 실제 주문에만 오므로 구독만으로는
A와 B의 일부(구독이 거부되지 않는다)까지만 확인되고 C·D는 확인되지 않는다 — 그래서
`--submit`이 필요하지만, 주문을 내는 스위치가 기본값이어서는 안 된다.

**모의계좌 전용이다.** `instance.yaml`의 broker.is_paper가 False면 시작하지 않는다 —
실측 편의를 위해 실계좌에 시험 주문을 내는 일은 없어야 한다.

## 장중에만 의미가 있다

정규장 밖에서는 주문이 거부되거나 접수만 되고 체결 통보가 오지 않는다. 거부 통보
자체도 관측 가치가 있지만(`rfus_yn`/`acpt_yn` 코드 체계 확인), 체결(`cntg_yn="2"`)
경로를 보려면 장중에 돌려야 한다.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from messiah.broker.kis.adapter import KISBrokerAdapter  # noqa: E402
from messiah.broker.kis.credentials import KISCredentials  # noqa: E402
from messiah.broker.kis.order_notice import (  # noqa: E402
    ORDER_NOTICE_FIELDS,
    OrderNotice,
    OrderNoticeStream,
)
from messiah.core import logging as mlog  # noqa: E402
from messiah.core.config import load_instance  # noqa: E402
from messiah.core.console import ensure_utf8_console  # noqa: E402
from messiah.core.messages import OrderKind, OrderRequest, Side  # noqa: E402

_SEPARATOR = "=" * 78


def _dump(notice: OrderNotice, seq: int) -> None:
    """통보 1건을 필드명과 함께 전부 출력 — 해석하지 않고 그대로 보여준다."""
    print(f"\n{_SEPARATOR}\n[통보 #{seq}] 원본 22개 필드\n{_SEPARATOR}")
    for i, name in enumerate(ORDER_NOTICE_FIELDS, start=1):
        value = getattr(notice, name)
        print(f"  {i:2d}. {name:<16} = {value!r}")
    print("-" * 78)
    print(
        f"  해석(문서 기준, 미검증): is_fill={notice.is_fill} "
        f"is_rejected={notice.is_rejected} filled_qty={notice.filled_qty}"
    )
    print(
        "  ↑ 이 세 값이 위 원본과 어긋나 보이면 `order_notice.OrderNotice`의 프로퍼티가"
        " 아니라 **필드 순서**를 의심할 것 (모듈 docstring D 항목)."
    )
    print(_SEPARATOR)


async def _probe(submit_symbol: str | None, qty: int) -> None:
    cfg = load_instance()
    if not cfg.broker.is_paper:
        raise SystemExit(
            "실계좌 설정이다 — 이 프로브는 모의계좌 전용이다 (instance.yaml broker.is_paper 확인)"
        )

    creds = KISCredentials.from_broker_config(cfg.broker)
    if not creds.hts_id:
        raise SystemExit("KIS_HTS_ID 미설정 — .env 확인")

    # 로깅을 세우지 않으면 `OrderNoticeSubscribed`(INFO)가 어디에도 안 나온다 — 구독이
    # 성공했는지 여부가 이 프로브의 첫 번째 관측 대상이라 반드시 켠다.
    mlog.setup(cfg.instance_id)

    seen = 0

    async def on_notice(notice: OrderNotice) -> None:
        nonlocal seen
        seen += 1
        _dump(notice, seen)

    stream = OrderNoticeStream(creds, on_notice=on_notice)
    print(f"체결통보 구독 시작 — tr_id={stream._tr_id} (모의={creds.is_mock})")
    print("Ctrl+C로 종료. 구독 성공 응답과 통보가 여기 그대로 찍힌다.\n")

    task = asyncio.create_task(stream.run_forever())

    if submit_symbol:
        # 구독이 먼저 자리를 잡아야 자기 주문의 통보를 받는다. 구독 전에 주문이 나가면
        # 통보를 놓치고 "통보가 안 온다"는 **잘못된 결론**을 얻는다.
        await asyncio.sleep(3.0)
        adapter = KISBrokerAdapter(creds, tick_size=Decimal(cfg.futures_tick_size))
        await adapter.connect()
        print(f">>> 시장가 매수 {qty}계약 제출: {submit_symbol}")
        result = await adapter.submit(
            OrderRequest(
                intent_id="probe-order-notice",  # 버스를 거치지 않는 프로브라 추적할 원 의도가 없다
                symbol=submit_symbol,
                kind=OrderKind.ENTRY,
                side=Side.LONG,
                qty=qty,
                limit_price_ticks=None,  # 시장가 — 체결 통보를 보는 것이 목적이라 확실히 체결시킨다
            )
        )
        print(f">>> 주문 결과: ok={result.ok} 주문번호={result.broker_order_no!r} {result.error}")
        if not result.ok:
            print(">>> 주문이 거부됐다 — 체결 통보는 오지 않는다. 거부 사유부터 해결할 것.")

    try:
        await task
    except asyncio.CancelledError:
        pass


def main() -> None:
    ensure_utf8_console()  # 판정보다 출력이 먼저 죽는 것을 막는다 (1-13)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--submit",
        metavar="SYMBOL",
        default=None,
        help="구독 후 이 종목으로 시장가 매수 1계약을 낸다 (예: A05609). 생략하면 주문 없이 대기.",
    )
    parser.add_argument("--qty", type=int, default=1, help="주문 수량 (기본 1)")
    args = parser.parse_args()

    try:
        asyncio.run(_probe(args.submit, args.qty))
    except KeyboardInterrupt:
        print("\n종료.")


if __name__ == "__main__":
    main()
