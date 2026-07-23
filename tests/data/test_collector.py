import json
from decimal import Decimal
from pathlib import Path

import httpx
import polars as pl
import pytest
from messiah.broker.kis import tr_codes
from messiah.broker.kis.credentials import KISCredentials
from messiah.broker.kis.ws_client import ApprovalKeyIssuer
from messiah.core.timeutil import now_kst
from messiah.data.archiver import ParquetArchiver
from messiah.data.collector import TickCollector
from messiah.data.normalizer import parse_futures_tick

# 2026-07-22 ws_client.py 실측 세션에서 실제로 캡처한 라이브 WS 프레임(미니선물 A05608). 시각
# 필드(HHMMSS)만 실측값이고 날짜는 parse_futures_tick이 today 생략 시 오늘 KST 날짜를 쓰므로,
# 아카이브 파일 경로도 하드코딩하지 않고 실행 시점의 오늘 날짜로 계산한다(그래야 세션이 자정을
# 넘겨도 테스트가 안 깨짐).
_TODAY_STR = now_kst().date().isoformat()
_SUBSCRIBE_ACK = json.dumps(
    {
        "header": {"tr_id": "H0IFCNT0", "tr_key": "A05608", "encrypt": "N"},
        "body": {"rt_cd": "0", "msg_cd": "OPSP0000", "msg1": "SUBSCRIBE SUCCESS"},
    }
)
_REAL_TICK_1 = (
    "0|H0IFCNT0|001|A05608^152953^-0.54^5^-0.05^1080.30^1131.06^1146.44^1079.36^1^134935^"
    "7587962447^1082.72^-0.91^-0.22^0.00^0.00^5.22^76672^571^000000^5^-50.76^000000^5^-66.14^"
    "000000^2^0.94^0.49^94.95^-2.42^0^1.51^1080.30^1080.04^1^1^60234^56822^-3412^69150^65657^"
    "997^478^106.25^0^1091.10^1069.50^2"
)
_REAL_TICK_2 = (
    "0|H0IFCNT0|001|A05608^152954^-0.54^5^-0.05^1080.30^1131.06^1146.44^1079.36^1^134936^"
    "7588016462^1082.72^-0.91^-0.22^0.00^0.00^5.10^76672^571^000000^5^-50.76^000000^5^-66.14^"
    "000000^2^0.94^0.49^94.95^-2.42^0^1.51^1080.30^1080.26^1^1^60234^56823^-3411^69150^65658^"
    "943^484^106.25^0^1091.10^1069.50^2"
)
_TICK_NEXT_MINUTE = _REAL_TICK_1.replace("152953", "153010")

_TICK_SIZE = Decimal("0.02")


class FakeConnection:
    def __init__(self, incoming: list[str]):
        self.sent: list[str] = []
        self._incoming = list(incoming)
        self.closed = False

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str:
        if not self._incoming:
            raise ConnectionError("연결 종료(테스트 픽스처 소진)")
        return self._incoming.pop(0)

    async def close(self) -> None:
        self.closed = True


class _FakeConnectCM:
    def __init__(self, conn: FakeConnection) -> None:
        self._conn = conn

    async def __aenter__(self) -> FakeConnection:
        return self._conn

    async def __aexit__(self, *exc: object) -> None:
        await self._conn.close()


class FakeBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, object]] = []

    async def publish(self, topic: str, msg: object) -> None:
        self.published.append((topic, msg))


def _creds() -> KISCredentials:
    return KISCredentials(app_key="key", app_secret="secret", account_no="12345678", is_mock=True)


def _approval_issuer() -> ApprovalKeyIssuer:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"approval_key": "APV-TEST"})

    return ApprovalKeyIssuer(_creds(), client=httpx.Client(transport=httpx.MockTransport(handler)))


def _collector(
    tmp_path: Path, incoming: list[str], bus=None
) -> tuple[TickCollector, FakeConnection]:
    conn = FakeConnection(incoming)
    collector = TickCollector(
        creds=_creds(),
        symbol="A05608",
        tr_id=tr_codes.WS_TR_FUTURES_CONTRACT,
        parse_tick=parse_futures_tick,
        tick_size=_TICK_SIZE,
        archiver=ParquetArchiver(tmp_path),
        bus=bus,
        approval_issuer=_approval_issuer(),
        ws_connect=lambda uri: _FakeConnectCM(conn),
    )
    return collector, conn


async def test_run_once_subscribes_and_archives_completed_bar(tmp_path: Path):
    collector, conn = _collector(
        tmp_path, [_SUBSCRIBE_ACK, _REAL_TICK_1, _REAL_TICK_2, _TICK_NEXT_MINUTE]
    )

    with pytest.raises(ConnectionError):
        await collector.run_once()

    sent = json.loads(conn.sent[0])
    assert sent["body"]["input"] == {"tr_id": tr_codes.WS_TR_FUTURES_CONTRACT, "tr_key": "A05608"}

    df = pl.read_parquet(tmp_path / "A05608" / "1m" / f"{_TODAY_STR}.parquet")
    assert df.height == 1
    row = df.row(0, named=True)
    assert row["o_ticks"] == 54015  # 1080.30 / 0.02
    assert row["volume"] == 2  # 두 틱(qty=1씩) 누적


async def test_run_once_publishes_ticks_and_bars_when_bus_provided(tmp_path: Path):
    bus = FakeBus()
    collector, _ = _collector(
        tmp_path, [_SUBSCRIBE_ACK, _REAL_TICK_1, _REAL_TICK_2, _TICK_NEXT_MINUTE], bus=bus
    )

    with pytest.raises(ConnectionError):
        await collector.run_once()

    tick_topics = [t for t, _ in bus.published if t.startswith("md.tick.")]
    bar_topics = [t for t, _ in bus.published if t.startswith("bar.")]
    assert tick_topics == ["md.tick.A05608"] * 3  # 틱 3건(마지막은 다음 분봉 시작 틱)
    assert bar_topics == ["bar.1m.A05608"]


async def test_run_once_works_without_bus(tmp_path: Path):
    collector, _ = _collector(
        tmp_path, [_SUBSCRIBE_ACK, _REAL_TICK_1, _REAL_TICK_2, _TICK_NEXT_MINUTE]
    )

    with pytest.raises(ConnectionError):
        await collector.run_once()  # bus=None이어도 예외 없이 archiver까지는 정상 동작해야 함

    assert (tmp_path / "A05608" / "1m" / f"{_TODAY_STR}.parquet").exists()


async def test_handle_message_ignores_json_control_messages(tmp_path: Path):
    collector, _ = _collector(tmp_path, [_SUBSCRIBE_ACK])

    with pytest.raises(ConnectionError):
        await collector.run_once()

    assert not (tmp_path / "A05608" / "1m" / f"{_TODAY_STR}.parquet").exists()  # 완성봉 없음(정상)


async def test_archiver_failure_is_logged_and_does_not_crash_loop(tmp_path: Path, monkeypatch):
    logged: list[tuple[str, str]] = []

    def fake_log(tag: str, msg: str, **fields) -> None:
        logged.append((tag, msg))

    monkeypatch.setattr("messiah.data.collector.mlog.log", fake_log)

    collector, _ = _collector(
        tmp_path, [_SUBSCRIBE_ACK, _REAL_TICK_1, _REAL_TICK_2, _TICK_NEXT_MINUTE]
    )
    broken_dir = tmp_path / "A05608"  # append_bar가 만들 디렉터리 자리에 파일을 미리 둬서 실패 유도
    broken_dir.parent.mkdir(parents=True, exist_ok=True)
    broken_dir.write_text("not a directory")

    with pytest.raises(ConnectionError):
        await collector.run_once()  # archiver 실패에도 루프는 끝까지(ConnectionError까지) 돈다

    assert any(tag == "CollectorProcessingError" for tag, _ in logged)


class _FailingConnectCM:
    """__aenter__ 시점(연결 자체)에 실패 — 서버 거부 등 accept 이전 단계의 단절을 흉내낸다."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def __aenter__(self):
        raise self._exc

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _RotatingConnect:
    """호출마다 미리 준비된 커넥션/실패를 순서대로 돌려준다 — run_forever()의 재연결마다
    다른 시나리오(연결 자체 실패·구독 후 즉시 단절·정상 수신)를 검증하기 위함."""

    def __init__(self, cms: list) -> None:
        self._cms = cms
        self.calls = 0

    def __call__(self, uri: str):
        cm = self._cms[self.calls]
        self.calls += 1
        return cm


async def test_run_forever_reconnects_with_backoff_and_resumes_archiving(
    tmp_path: Path, monkeypatch
):
    logged: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "messiah.data.collector.mlog.log", lambda tag, msg, **f: logged.append((tag, msg))
    )
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("messiah.data.collector.asyncio.sleep", fake_sleep)

    conn_final_stop = FakeConnection([_SUBSCRIBE_ACK])

    async def _raise_value_error() -> str:
        raise ValueError("코드 문제 — 재시도 대상 아님")

    conn_final_stop.recv = _raise_value_error  # type: ignore[method-assign]

    conn_success_then_disconnect = FakeConnection(
        [_SUBSCRIBE_ACK, _REAL_TICK_1, _REAL_TICK_2, _TICK_NEXT_MINUTE]
    )
    connect = _RotatingConnect(
        [
            _FailingConnectCM(OSError("connection refused")),  # 시도 1: 연결 자체 실패
            _FailingConnectCM(OSError("connection refused")),  # 시도 2: 다시 실패(백오프 배증)
            _FakeConnectCM(conn_success_then_disconnect),  # 시도 3: 정상 수신, 1분봉 완성 후 단절
            _FakeConnectCM(conn_final_stop),  # 시도 4: ValueError로 루프 종료(재시도 대상 아님)
        ]
    )

    collector = TickCollector(
        creds=_creds(),
        symbol="A05608",
        tr_id=tr_codes.WS_TR_FUTURES_CONTRACT,
        parse_tick=parse_futures_tick,
        tick_size=_TICK_SIZE,
        archiver=ParquetArchiver(tmp_path),
        approval_issuer=_approval_issuer(),
        ws_connect=connect,
        reconnect_initial_backoff_seconds=5.0,
        reconnect_max_backoff_seconds=60.0,
    )

    with pytest.raises(ValueError):
        await collector.run_forever()

    assert connect.calls == 4
    # 시도1 실패(백오프 5초 대기) → 시도2도 실패(백오프 10초로 배증) → 시도3 연결 성공(백오프
    # 리셋) 후 단절(다시 5초 대기) → 시도4는 ValueError라 재시도 없음.
    assert slept == [5.0, 10.0, 5.0]

    disconnect_logs = [msg for tag, msg in logged if tag == "CollectorWSDisconnected"]
    reconnect_logs = [msg for tag, msg in logged if tag == "CollectorWSReconnected"]
    # 단절: 시도1 실패 시 1회(시도2는 연속 실패라 중복 안 함), 시도3 성공 후 다시 끊길 때 1회 = 2건
    assert len(disconnect_logs) == 2
    # 재연결: 시도3(시도1~2의 단절에서 복구), 시도4(시도3 이후 단절에서 복구) 각각 1회 = 2건
    assert len(reconnect_logs) == 2

    df = pl.read_parquet(tmp_path / "A05608" / "1m" / f"{_TODAY_STR}.parquet")
    assert df.height == 1  # 시도3에서 완성된 1분봉이 정상 적재됨


async def test_run_forever_propagates_non_disconnect_errors_immediately(
    tmp_path: Path, monkeypatch
):
    async def fake_sleep(seconds: float) -> None:
        raise AssertionError("재시도 대상이 아닌 예외인데 sleep이 호출됨")

    monkeypatch.setattr("messiah.data.collector.asyncio.sleep", fake_sleep)

    conn = FakeConnection([_SUBSCRIBE_ACK])

    async def _raise_value_error() -> str:
        raise ValueError("설정 문제")

    conn.recv = _raise_value_error  # type: ignore[method-assign]

    collector, _ = _collector(tmp_path, [])
    collector._ws_connect = lambda uri: _FakeConnectCM(conn)

    with pytest.raises(ValueError):
        await collector.run_forever()


async def test_flush_final_bar_archives_remaining_partial_bar(tmp_path: Path):
    collector, _ = _collector(tmp_path, [_SUBSCRIBE_ACK, _REAL_TICK_1])

    with pytest.raises(ConnectionError):
        await collector.run_once()
    partial_path = tmp_path / "A05608" / "1m" / f"{_TODAY_STR}.parquet"
    assert not partial_path.exists()  # 아직 봉 미완성(1틱뿐)

    await collector.flush_final_bar()

    df = pl.read_parquet(tmp_path / "A05608" / "1m" / f"{_TODAY_STR}.parquet")
    assert df.height == 1
    assert df.row(0, named=True)["quality_ok"] is False  # 1틱 < MIN_TICKS_FOR_QUALITY_OK(3)
