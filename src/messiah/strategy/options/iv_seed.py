"""IV Rank 이력 시드 — 기동 시 옵션체인 아카이브로 `IVHistory`를 채운다 (2026-09-02 신설).

## 왜 필요했나 — "저IV"가 사양과 다른 것을 재고 있었다

Ver 1.3 §3.1은 IV Rank를 **"최근 252일 내 위치"**로 정의한다. 그런데 `vol_metrics.IVHistory`는
프로세스 메모리 deque이고(그 클래스 docstring이 *"재시작 시 이력이 비어 초기 구간은 rank()가
None"*이라고 알려진 갭으로 적어 뒀다) **g2는 매 거래일 재기동한다.** 즉 라이브가 판정하던
"저IV"는 252일 대비가 아니라 **그날 아침 대비**였다.

같은 20거래일을 두 기준으로 재생하면 셀 분포가 통째로 달라진다(2026-09-02 실측):

    이력 기준        중립·저IV   중립·중IV   중립·고IV   미판정
    누적(사양 취지)      75%        21%        2%        1%
    일일 리셋(라이브)     32%        34%       23%       10%

특히 **중립·고IV가 2% → 23%로 부푼다.** 그 셀은 `IRON_CONDOR`(만들 수 있는 구조)를 배정하므로,
깨진 랭크로 신용 구조 후보를 과잉 발행하게 된다 — 지금은 화면 표시뿐이지만 이 값은 곧 주문
경로의 입력이다. 그래서 **셀을 어떻게 배정할지 정하기 전에 입력부터 고친다.**

## 무엇을 시드하나 — 하루에 한 표본

거래일마다 **마지막 유효 스마일의 ATM IV 하나**를 쓴다. 사이클 값을 전부 넣으면 하루가 15표본이
되어 252 창이 17거래일로 쪼그라든다 — §3.1이 말하는 "252일"은 **일 단위**다.

## 남는 갭 — 입도가 섞인다 (일부러 여기서 안 고친다)

시드는 일 1표본인데 라이브는 사이클마다 `add()`한다. 그래서 장이 진행될수록 창 안에서 당일
표본의 비중이 커진다(하루 15사이클이면 창의 5.6%). 이걸 고치려면 `IVHistory`가 "일별 대표값"을
알아야 하는데, 그건 랭크 판정의 의미를 바꾸는 변경이라 R18 대상이다 — **입력 복원(이 파일)과
입도 정의 변경은 다른 결정**이고, 후자는 사람 몫으로 남긴다(`NEXT_TODO` O-5).

## 실패해도 기동을 막지 않는다 — 대신 조용하지 않다

아카이브가 없는 환경(새 인스턴스·테스트)에서도 서비스는 서야 한다. 시드 0건이면 종전과 같은
동작(초기 구간 `rank()=None` → "IV Rank 이력 부족")으로 돌아갈 뿐이고, **몇 건을 시드했는지는
항상 로그에 남는다** — "고쳤는데 새 경로가 한 번도 안 쓰였다"를 잡는 것이 이 저장소의 규율이다
(`ui/data_source._threshold_uses`와 같은 계열).
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from messiah.core import logging as mlog
from messiah.core.messages import OptionQuoteSnapshot
from messiah.data.option_chain_archiver import read_day
from messiah.strategy.options.chain_smile import build_smile, parse_leg
from messiah.strategy.options.vol_metrics import DEFAULT_IV_HISTORY_WINDOW, IVHistory

# 아카이브를 며칠까지 거슬러 훑을 것인가. 252거래일을 채우려면 달력일로는 그보다 길다 —
# 주말·휴장을 감안해 넉넉히 잡되, 파일이 없는 날은 즉시 건너뛰므로 비용은 존재하는 파일 수에
# 비례한다(현재 수집분은 2026-08-05부터라 20일).
_MAX_LOOKBACK_CALENDAR_DAYS = 400


def _atm_iv_of_day(base_dir: Path, series: str, day: date) -> float | None:
    """그날 **마지막 유효 스마일**의 ATM IV — 못 만들면 None.

    마지막을 쓰는 이유: 장 초반은 거래량이 얇아 낡은 체결가가 섞이고(`chain_smile` §1 확정 ④),
    종가 근처 스냅샷이 그날을 대표하는 값으로 관례적이다.
    """
    frame = read_day(base_dir, series, day)
    if frame is None or frame.is_empty():
        return None

    rows = frame.sort("ts_kst").to_dicts()
    # 스냅샷 사이클 단위로 뒤에서부터 시도한다 — 마지막 사이클이 얇으면 그 앞을 쓴다.
    by_cycle: dict[str, list[dict]] = {}
    for row in rows:
        stamp = str(row.get("ts_kst"))[:15]  # 분 단위 앞 15자 = 사이클 묶음(초는 다리마다 다르다)
        by_cycle.setdefault(stamp, []).append(row)

    for stamp in sorted(by_cycle, reverse=True):
        legs = []
        for row in by_cycle[stamp]:
            snapshot = _snapshot_from_row(row, series)
            if snapshot is None:
                continue
            leg = parse_leg(snapshot)
            if leg is not None:
                legs.append(leg)
        smile, _ = build_smile(legs)
        if smile is not None:
            return smile.iv_at(smile.forward)
    return None


def _snapshot_from_row(row: dict, series: str) -> OptionQuoteSnapshot | None:
    """아카이브 한 행 → `OptionQuoteSnapshot` — `chain_smile.parse_leg()`를 그대로 재사용한다.

    아카이브는 `_flatten_quote()`가 output1을 접두어 없이 펴 놓은 형태라, 그 컬럼들을 다시
    `raw={"output1": {...}}`로 감싸면 **라이브와 같은 해석기**를 탄다. 필드 의미를 두 번
    구현하지 않기 위해서다(2026-09-02 확정 매핑은 한 곳에만 있어야 한다).
    """
    symbol, option_type = row.get("symbol"), row.get("option_type")
    if not isinstance(symbol, str) or not isinstance(option_type, str):
        return None
    try:
        strike = float(row.get("strike"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return OptionQuoteSnapshot(
        underlying="KOSPI200",
        series=series,
        option_type=option_type,
        strike=strike,
        expiry=str(row.get("expiry", "")),
        symbol=symbol,
        raw={"output1": {key: row.get(key) for key in _RAW_KEYS if key in row}},
    )


# `chain_smile`이 읽는 필드만 되싣는다 — 아카이브 49컬럼을 통째로 넘길 이유가 없다.
_RAW_KEYS = (
    "futs_prpr",
    "hts_rmnn_dynu",
    "hts_otst_stpl_qty",
    "acml_vol",
    "acpr",
    "hts_ints_vltl",
)


def seed_iv_history(
    base_dir: Path,
    *,
    series: str = "regular",
    today: date,
    maxlen: int = DEFAULT_IV_HISTORY_WINDOW,
    lookback_days: int = _MAX_LOOKBACK_CALENDAR_DAYS,
) -> IVHistory:
    """아카이브에서 일별 ATM IV를 복원한 `IVHistory` — **오늘은 넣지 않는다**(라이브가 쌓는다).

    반환은 항상 `IVHistory`다. 아카이브가 없으면 빈 이력이 나오고 그 사실이 로그에 남는다 —
    호출측이 시드 실패를 이유로 서비스를 안 세우면 안 된다(부가 축의 실패가 본 축을 막지 않는다).
    """
    history = IVHistory(maxlen=maxlen)
    values: list[tuple[date, float]] = []
    cursor = today - timedelta(days=1)
    oldest = today - timedelta(days=lookback_days)
    while cursor >= oldest and len(values) < maxlen:
        try:
            atm_iv = _atm_iv_of_day(base_dir, series, cursor)
        except Exception as exc:  # noqa: BLE001 — 한 날의 파일이 깨져도 나머지는 시드한다
            mlog.log(
                "IVHistorySeedDayFailed",
                f"{cursor.isoformat()} 시드 실패 — {exc.__class__.__name__}: {exc}",
                series=series,
                day=cursor.isoformat(),
            )
            atm_iv = None
        if atm_iv is not None:
            values.append((cursor, atm_iv))
        cursor -= timedelta(days=1)

    for _, atm_iv in reversed(values):  # 오래된 것부터 넣어야 deque 순서가 시간순이 된다
        history.add(atm_iv)

    span = f"{values[-1][0].isoformat()}~{values[0][0].isoformat()}" if values else "없음"
    mlog.log(
        "IVHistorySeeded",
        f"IV Rank 이력 {len(values)}거래일 시드 ({span}) — "
        + (
            "랭크 판정이 첫 사이클부터 선다"
            if len(values) >= 2
            else "표본 부족: 종전대로 초기 구간은 'IV Rank 이력 부족'으로 나간다"
        ),
        series=series,
        seeded_days=len(values),
        window=maxlen,
    )
    return history
