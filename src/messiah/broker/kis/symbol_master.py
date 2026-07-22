"""KIS 종목코드 마스터파일(지수선물옵션) 다운로드·파싱 — 근월물 확정(base.py 계명 9)·옵션 체인
구성의 기반.

마흐디(선행 옵션 프로젝트) mahdi/data/symbol_master.py에서 2026-07-22 이식(pandas 대신 polars —
이 프로젝트는 polars를 쓰고 pandas는 의존성에 없음). 이식 과정에서 KISBrokerAdapter 실계좌 실측
세션(docs/capability_matrix.md "알려진 갭" 참고)에서 발견한 마흐디 원본의 갭 2건을 반영했다:

1. 상품종류 코드에 미니선물이 없었다 — 마흐디는 정규선물("1")만 알고 있었다(옵션 전용 프로젝트라
   미니선물을 쓸 일이 없었음). 2026-07-22 실측으로 미니선물은 상품종류="B"(한글종목명 "미니F
   202608" 형식)임을 확인 — PRODUCT_TYPE_MINI_FUTURES로 추가. MESSIAH의 표준 상품은 미니선물
   (Broker_API_Ranking·Holding Policy)이라 이게 실제로 필요한 값이다.
2. 선물 행의 실제 월물랭크(1=근월,2=차근월,...)는 마흐디가 "월물구분코드"라 이름 붙인 자리
   (5번째 필드)가 아니라 그 다음 자리(6번째 필드, 마흐디가 "ATM구분"이라 이름 붙인 옵션 전용
   컬럼)에 들어있다 — 선물 행에서 5번째 필드는 항상 공란이었다(2026-07-22 실측: KOSPI200 정규
   선물 7종목·미니선물 6종목 전부 확인). 마흐디는 이 사실을 몰랐지만 마스터파일이 우연히 근월
   순으로 정렬되어 있어 (전부 공란인 컬럼으로 정렬 → 원래 순서 유지) front_month_future_code()가
   결과적으로 맞는 값을 반환했을 뿐이다 — 이 포트에서는 6번째 필드(series_rank)로 명시적으로
   정렬한다.
   (옵션 행 파싱은 마흐디가 확인한 필드 배치를 그대로 쓴다 — 옵션 필터링/정렬은 상품종류·
   기초자산명·행사가·한글종목명에서 정규식으로 뽑은 만기만 쓰고 5/6번째 필드는 안 쓰이므로
   이번 재해석과 무관하다.)

2026-07-22 실측(같은 날 이어서): futures()/front_month_future_code()는 KISBrokerAdapter.
probe_front_month() 경유로, options()/nearest_expiry_chain()/option_symbol()은 실제 마스터파일로
regular(콜/풋 각 390건, 만기 202608)·weekly_mon(116/116)·weekly_thu(150/150) 체인을 직접 조회해
검증했다. 체인에서 뽑은 종목코드로 KISRestClient.get_quote()를 실호출해 rt_cd=0·실제 체결가까지
확인 — 내부적으로만 일관된 코드가 아니라 실제 거래 가능한 코드임을 확인했다(docs/
capability_matrix.md 참고). 위클리 요일 대응(N/O=월요일,L/M=목요일)은 마흐디의 2026-07-10 단일
실측(대시보드 표시명 교차확인)에 의존 중이었으나, 2026-07-22 다른 날짜·다른 방법(각 series
근월물의 실제 get_quote() futs_last_tr_date를 Python weekday()로 계산)으로 재검증 완료 —
weekly_mon 근월 만기 20260727=월요일, weekly_thu 근월 만기 20260723=목요일, 둘 다 일치.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZipFile

import httpx
import polars as pl

MASTER_FILE_URL = "https://new.real.download.dws.co.kr/common/master/fo_idx_code_mts.mst.zip"
MASTER_FILE_NAME = "fo_idx_code_mts.mst"

_COLUMNS = [
    "product_type",  # 상품종류
    "symbol",  # 단축코드
    "standard_code",  # 표준코드
    "name_kr",  # 한글종목명
    "month_rank_code",  # 마흐디 명명 "월물구분코드" — 옵션 행에서 고정값(대략 "2"), 선물 행은 공란
    "strike",  # 행사가 — 선물 행은 "00000.00"
    "series_rank",  # 마흐디 명명 "ATM구분" — 선물 행에서는 실제 월물랭크(1=근월,2=차근월,...)
    "underlying_symbol",  # 기초자산단축코드
    "underlying_name",  # 기초자산명
]

# 상품종류 코드 (기초자산명="KOSPI200" 행 기준 실측 — 마흐디 2026-07-06/07-10 실측 + 이번 이식
# 2026-07-22 실측(미니선물 "B" 추가) 종합)
PRODUCT_TYPE_FUTURES = "1"  # 지수선물 (정규)
PRODUCT_TYPE_MINI_FUTURES = "B"  # 지수 미니선물 — 2026-07-22 실측 추가 (MESSIAH 표준 상품)
PRODUCT_TYPE_OPTION_CALL = "5"  # 지수옵션 콜 (정규, 월물)
PRODUCT_TYPE_OPTION_PUT = "6"  # 지수옵션 풋 (정규, 월물)
PRODUCT_TYPE_MINI_OPTION_CALL = "D"  # 미니옵션 콜
PRODUCT_TYPE_MINI_OPTION_PUT = "E"  # 미니옵션 풋
PRODUCT_TYPE_WEEKLY_MON_OPTION_CALL = "N"  # 위클리(월) 콜
PRODUCT_TYPE_WEEKLY_MON_OPTION_PUT = "O"  # 위클리(월) 풋
PRODUCT_TYPE_WEEKLY_THU_OPTION_CALL = "L"  # 위클리(목) 콜
PRODUCT_TYPE_WEEKLY_THU_OPTION_PUT = "M"  # 위클리(목) 풋

_WEEKLY_SERIES = ("weekly_mon", "weekly_thu")

_SERIES_PRODUCT_TYPES = {
    "regular": ((PRODUCT_TYPE_OPTION_CALL,), (PRODUCT_TYPE_OPTION_PUT,)),
    "mini": ((PRODUCT_TYPE_MINI_OPTION_CALL,), (PRODUCT_TYPE_MINI_OPTION_PUT,)),
    "weekly_mon": ((PRODUCT_TYPE_WEEKLY_MON_OPTION_CALL,), (PRODUCT_TYPE_WEEKLY_MON_OPTION_PUT,)),
    "weekly_thu": ((PRODUCT_TYPE_WEEKLY_THU_OPTION_CALL,), (PRODUCT_TYPE_WEEKLY_THU_OPTION_PUT,)),
}

# 한글종목명에서 만기를 뽑는 정규식 — 마흐디 2026-07-06(정규/미니)·07-10(위클리 목) 실측.
# 정규/미니: "C 202607   545.0" 형식(YYYYMM). 위클리는 "위클리M C 2607W1 ..."(월)/"위클리C 2607W3
# ..."(목) 형식(YYMM+주차) — 접두어의 "M" 유무와 무관하게 마지막 C/P + 공백 + YYMM+주차만 잡는다.
_EXPIRY_FROM_NAME = r"^[CP]\s*(\d{6})"
_WEEKLY_EXPIRY_FROM_NAME = r"[CP]\s+(\d{4}W\d)"


def download_master_zip(dest_dir: Path, client: httpx.Client | None = None) -> Path:
    """
    입력: 저장할 디렉터리.
    계산: MASTER_FILE_URL에서 zip을 내려받아 dest_dir에 저장.
    실패 조건: 4xx/5xx면 httpx.HTTPStatusError 전파.
    """
    client = client or httpx.Client(timeout=30.0, follow_redirects=True)
    response = client.get(MASTER_FILE_URL)
    response.raise_for_status()
    dest_dir.mkdir(parents=True, exist_ok=True)
    zip_path = dest_dir / "fo_idx_code_mts.mst.zip"
    zip_path.write_bytes(response.content)
    return zip_path


def extract_master_file(zip_path: Path, dest_dir: Path) -> Path:
    """zip을 dest_dir에 풀고 .mst 파일 경로를 반환."""
    with ZipFile(zip_path) as zf:
        zf.extractall(dest_dir)
    return dest_dir / MASTER_FILE_NAME


def parse_master_file(mst_path: Path) -> pl.DataFrame:
    """
    입력: 압축 해제된 .mst 파일 경로.
    계산: pipe(|) 구분·cp949 인코딩으로 읽어 9개 컬럼 문자열 DataFrame으로 변환한다. 전부
         문자열로 고정하는 이유는 마흐디가 겪은 문제(상품종류가 전부 숫자인 파일에서 정수로
         잘못 추론돼 문자열 상수 비교가 조용히 실패)를 원천 차단하기 위함 — 숫자가 필요한 필드
         (strike·series_rank)는 사용하는 쪽(futures()/options())에서 그때그때 캐스팅한다.
    실패 조건: 파일이 없으면 FileNotFoundError.
    """
    text = mst_path.read_bytes().decode("cp949")
    return pl.read_csv(
        io.BytesIO(text.encode("utf-8")),
        separator="|",
        has_header=False,
        new_columns=_COLUMNS,
        schema_overrides=dict.fromkeys(_COLUMNS, pl.Utf8),
    )


def load_index_derivatives_master(
    cache_dir: Path, client: httpx.Client | None = None
) -> "IndexDerivativesMaster":
    """다운로드→압축해제→파싱을 한 번에 수행하는 편의 함수(하루 1회 호출 용도 — 호출측이 캐싱)."""
    zip_path = download_master_zip(cache_dir, client=client)
    mst_path = extract_master_file(zip_path, cache_dir)
    return IndexDerivativesMaster(parse_master_file(mst_path))


@dataclass(frozen=True)
class OptionLeg:
    option_type: str  # "C" | "P"
    strike: float
    symbol: str
    month_label: str


class IndexDerivativesMaster:
    """지수선물옵션 종목코드 마스터 조회 헬퍼. 기본 기초자산은 KOSPI200(정규 계약)."""

    def __init__(self, df: pl.DataFrame) -> None:
        self._df = df

    @classmethod
    def from_file(cls, mst_path: Path) -> "IndexDerivativesMaster":
        return cls(parse_master_file(mst_path))

    def futures(
        self, underlying: str = "KOSPI200", product_type: str = PRODUCT_TYPE_FUTURES
    ) -> pl.DataFrame:
        """product_type: PRODUCT_TYPE_FUTURES(정규, 기본) | PRODUCT_TYPE_MINI_FUTURES(미니)."""
        return (
            self._df.filter(
                (pl.col("product_type") == product_type) & (pl.col("underlying_name") == underlying)
            )
            .with_columns(pl.col("series_rank").str.strip_chars().cast(pl.Int64))
            .sort("series_rank")
        )

    def front_month_future_code(
        self, underlying: str = "KOSPI200", product_type: str = PRODUCT_TYPE_FUTURES
    ) -> str | None:
        """
        계산: series_rank(선물 행의 실제 월물랭크, 모듈 docstring 참고)가 가장 작은(=1, 근월) 행의
             단축코드.
        실패 조건: 해당 기초자산·상품종류의 선물이 없으면 None.
        """
        rows = self.futures(underlying, product_type)
        if rows.is_empty():
            return None
        return rows["symbol"][0]

    def options(
        self, option_type: str, underlying: str = "KOSPI200", series: str = "regular"
    ) -> pl.DataFrame:
        """
        option_type: "C" 또는 "P". series: "regular"(정규 월물, 기본값) | "mini"(미니) |
        "weekly_mon"(위클리 월요일 만기) | "weekly_thu"(위클리 목요일 만기).

        해석: 반환 DataFrame에 "expiry" 컬럼을 추가한다 — 한글종목명에서 정규식으로 뽑은 만기
             문자열(regular/mini: YYYYMM, weekly: YYMMWk)이며, 만기가 없는 상품(파싱 실패)은
             null. strike는 Float64로 캐스팅해 반환(정렬·비교용 — 원본 컬럼은 문자열).
        실패 조건: option_type이 "C"/"P"가 아니거나 series가 미지원 값이면 ValueError.
        """
        if option_type not in ("C", "P"):
            raise ValueError("option_type은 'C' 또는 'P'여야 합니다")
        if series not in _SERIES_PRODUCT_TYPES:
            raise ValueError(f"series는 {sorted(_SERIES_PRODUCT_TYPES)} 중 하나여야 합니다")
        call_types, put_types = _SERIES_PRODUCT_TYPES[series]
        product_types = call_types if option_type == "C" else put_types
        pattern = _WEEKLY_EXPIRY_FROM_NAME if series in _WEEKLY_SERIES else _EXPIRY_FROM_NAME

        rows = self._df.filter(
            pl.col("product_type").is_in(product_types) & (pl.col("underlying_name") == underlying)
        ).with_columns(
            [
                pl.col("name_kr").str.extract(pattern, 1).alias("expiry"),
                pl.col("strike").cast(pl.Float64),
            ]
        )
        return rows.sort(["expiry", "strike"])

    def nearest_expiry_chain(
        self, underlying: str = "KOSPI200", series: str = "regular"
    ) -> list[OptionLeg]:
        """
        계산: 콜/풋 각각에서 만기(expiry 정렬키)가 가장 빠른 값의 전 행사가 목록을 반환.
        해석: 반환된 각 OptionLeg가 옵션 체인 스냅샷의 기초 재료다 — 실시간 값(IV/Greeks/OI)은
             각 symbol로 KISRestClient.get_quote()를 호출해 채워야 한다(REST에는 체인 전체를
             한 번에 주는 호출이 없음).
        실패 조건: 콜/풋 데이터가 없으면 빈 리스트.
        """
        result: list[OptionLeg] = []
        for option_type in ("C", "P"):
            rows = self.options(option_type, underlying, series=series).drop_nulls("expiry")
            if rows.is_empty():
                continue
            nearest_expiry = rows["expiry"].min()
            nearest = rows.filter(pl.col("expiry") == nearest_expiry)
            for row in nearest.iter_rows(named=True):
                result.append(
                    OptionLeg(
                        option_type=option_type,
                        strike=row["strike"],
                        symbol=row["symbol"],
                        month_label=row["name_kr"].strip(),
                    )
                )
        return sorted(result, key=lambda leg: (leg.option_type, leg.strike))

    def option_symbol(
        self,
        option_type: str,
        strike: float,
        underlying: str = "KOSPI200",
        series: str = "regular",
    ) -> str | None:
        """
        계산: 만기가 가장 빠른 값 중 option_type/strike가 일치하는 단축코드를 찾는다.
        해석: 요청한 strike가 실제 상장된 행사가 격자에 없으면 None — 호출측이 해당 strike
             구독을 건너뛰어야 한다.
        실패 조건: 일치하는 행이 없으면 None.
        """
        rows = self.options(option_type, underlying, series=series).drop_nulls("expiry")
        if rows.is_empty():
            return None
        nearest_expiry = rows["expiry"].min()
        match = rows.filter((pl.col("expiry") == nearest_expiry) & (pl.col("strike") == strike))
        if match.is_empty():
            return None
        return match["symbol"][0]
