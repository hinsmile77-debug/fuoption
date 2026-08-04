from pathlib import Path

import pytest

from messiah.broker.kis import symbol_master as sm

# 실제 마스터파일과 같은 필드 배치(9개, pipe 구분, cp949)의 축소판. 근월물 정렬 회귀 테스트를 위해
# 선물 행은 일부러 원거리월물을 먼저 적는다 — 마흐디 원본 버그(월물구분코드가 늘 공란인 걸 모르고
# 그 컬럼으로 정렬하려다 실제로는 파일 순서에 우연히 기대고 있었음)가 재발하면 이 테스트가 깨진다.
_ROWS = [
    # 정규선물 — 차근월을 먼저 적음(파일 순서로 정렬되면 안 됨을 검증)
    ["1", "A01612", "STD2", "F 202612", " ", "00000.00", "2", "2001", "KOSPI200"],
    ["1", "A01609", "STD1", "F 202609", " ", "00000.00", "1", "2001", "KOSPI200"],
    # 미니선물 — 마찬가지로 차근월 먼저
    ["B", "A05609", "STD4", "미니F 202609", " ", "00000.00", "2", "2001", "KOSPI200"],
    ["B", "A05608", "STD3", "미니F 202608", " ", "00000.00", "1", "2001", "KOSPI200"],
    # 정규 콜옵션 — 근월(202608) 행사가 2개 + 원월(202609) 1개
    ["5", "B05608C5450", "STD6", "C 202608   545.0", "2", "545.00", " ", "2001", "KOSPI200"],
    ["5", "B05608C5400", "STD5", "C 202608   540.0", "2", "540.00", " ", "2001", "KOSPI200"],
    ["5", "B05609C5400", "STD7", "C 202609   540.0", "2", "540.00", " ", "2001", "KOSPI200"],
    # 정규 풋옵션 — 근월 1개
    ["6", "B05608P5400", "STD8", "P 202608   540.0", "2", "540.00", " ", "2001", "KOSPI200"],
    # 위클리(월) 콜 — 근월 위클리 1개
    ["N", "B0W1C1130", "STD9", "위클리M C 2607W1 1,130.0", "2", "1130.00", " ", "2001", "KOSPI200"],
    # 다른 기초자산 — 필터에서 제외돼야 함
    ["1", "Z99999", "STDX", "F 202609", " ", "00000.00", "1", "2001", "KSQ150"],
]


@pytest.fixture
def master(tmp_path: Path) -> sm.IndexDerivativesMaster:
    content = "\n".join("|".join(row) for row in _ROWS) + "\n"
    mst_path = tmp_path / sm.MASTER_FILE_NAME
    mst_path.write_bytes(content.encode("cp949"))
    return sm.IndexDerivativesMaster.from_file(mst_path)


def test_front_month_future_code_sorts_by_series_rank_not_file_order(master):
    # 파일에는 202612(차근월)이 202609(근월)보다 먼저 나온다 — 그래도 근월이 나와야 한다.
    assert master.front_month_future_code() == "A01609"


def test_front_month_future_code_supports_mini_futures(master):
    assert master.front_month_future_code(product_type=sm.PRODUCT_TYPE_MINI_FUTURES) == "A05608"


def test_front_month_future_code_returns_none_when_no_match(master):
    assert master.front_month_future_code(underlying="NONEXISTENT") is None


def test_futures_excludes_other_underlyings(master):
    rows = master.futures()
    assert set(rows["underlying_name"].to_list()) == {"KOSPI200"}


def test_options_adds_expiry_column_and_sorts_by_expiry_then_strike(master):
    rows = master.options("C")
    assert rows["expiry"].to_list() == ["202608", "202608", "202609"]
    assert rows["strike"].to_list() == [540.0, 545.0, 540.0]


def test_options_rejects_invalid_option_type(master):
    with pytest.raises(ValueError):
        master.options("X")


def test_options_rejects_invalid_series(master):
    with pytest.raises(ValueError):
        master.options("C", series="bogus")


def test_nearest_expiry_chain_returns_only_nearest_month_both_legs(master):
    chain = master.nearest_expiry_chain()

    assert len(chain) == 3  # 근월(202608) 콜 2개 + 풋 1개, 원월(202609) 콜은 제외
    call_strikes = sorted(leg.strike for leg in chain if leg.option_type == "C")
    assert call_strikes == [540.0, 545.0]
    put = next(leg for leg in chain if leg.option_type == "P")
    assert put.symbol == "B05608P5400"


def test_option_symbol_finds_matching_strike(master):
    assert master.option_symbol("C", 545.0) == "B05608C5450"


def test_option_symbol_returns_none_for_unlisted_strike(master):
    assert master.option_symbol("C", 999.0) is None


def test_options_weekly_mon_parses_yymmwk_expiry(master):
    rows = master.options("C", series="weekly_mon")
    assert rows["expiry"].to_list() == ["2607W1"]
    assert rows["symbol"].to_list() == ["B0W1C1130"]
