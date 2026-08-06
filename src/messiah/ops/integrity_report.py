"""일일 무결성 리포트 — 고도화 2 (2026-07-30).

## 왜 만들었나

2026-07-30 일일 점검에서 나온 이상점은 전부 **사람이 손으로 파야만 보이는 것**이었다:
1분봉 Parquet을 직접 읽어 분 단위 연속성을 검사해야 30분 공백 2건이 나왔고, `FeaturePublish`
DEBUG 라인을 Horizon별로 집계해야 15m/30m가 하루 종일 NaN 2/3라는 게 보였고, `SessionStart`
개수를 세야 그날 6번 재시작됐다는 걸 알았고, Windows 이벤트 로그를 뒤져야 UI 크래시 3건이
나왔다. 그 조사를 다음날 다시 하려면 처음부터 똑같이 손으로 해야 한다.

이 모듈은 **그 조사 자체를 코드로 고정한 것**이다. `Docs/dailycheck_prompt.txt`의 반복
점검이 매번 수작업 포렌식이 되지 않게 하는 것이 목적이다.

## 지표 선정 근거 — 전부 "그날 실제로 놓쳤던 것"

| 지표 | 이 지표가 있었다면 잡았을 사고 |
|---|---|
| 봉 결손 분 수 / 최장 공백 | 07-28 10:13~10:43, 07-29 12:32~13:02 (각 29분 무성 단절) |
| 프로세스 재기동 횟수 | 07-29 L1 6회 재시작 → 피처 워밍업 전량 소실 |
| Horizon별 nan_ratio | 15m 0.678 / 30m 0.694 — 하루 종일 피처 2/3가 NaN |
| 네이티브 크래시 건수 | 07-29 2건 + 07-30 1건 UI 즉사(로그에 한 줄도 안 남음) |
| WARN/ERROR/CRITICAL 태그 집계 | 스톨·UI 사망·적재 실패가 새 태그로 이제 남는다 |

## 판정은 임계와 함께 한다

숫자만 뱉으면 결국 사람이 매일 읽고 판단해야 한다 — `DEFAULT_THRESHOLDS`를 넘긴 항목만
`breaches`로 따로 모아 준다. 임계값 자체는 지금까지의 실측(정상일이었던 07-27은 결손 0분,
재기동 1회)을 기준으로 잡은 **초기값이며 미검증**이다.

## 크래시 집계는 Windows 전용이다

`_collect_native_crashes()`는 `Get-WinEvent`(PowerShell)에 의존한다 — 다른 OS에서는 조용히
0건이 아니라 `available=False`로 "못 셌다"를 명시한다(값이 없는 것과 0인 것을 구분하는
마흐디 L18 원칙). 이 사고의 유일한 흔적이 그 로그였으므로 못 셌다는 사실 자체가 중요하다.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping, Sequence

import polars as pl

from messiah.core.messages import BarSession, Horizon
from messiah.data import tick_archiver
from messiah.data.archiver import ParquetArchiver
from messiah.ops import observation_gaps, series_coverage
from messiah.ops.crash_dumps import CrashForensics, collect_crash_forensics, format_dump_lines

_KST_ZONE_NAME = "Asia/Seoul"

# 정상 운영일의 실측(2026-07-27: 결손 0분·재기동 0회)을 기준으로 한 초기 임계 — 미검증.
DEFAULT_THRESHOLDS: dict[str, float] = {
    "missing_minutes": 5.0,  # 결손 5분 초과면 조사 대상
    "longest_gap_minutes": 3.0,  # 연속 3분 넘게 비면 스톨 의심(워치독 임계 120초와 정합)
    # 재기동은 **예정된 08:35 기동을 뺀 수**다(2026-08-03 정정 — 그 전엔 기동 횟수를 그대로
    # "재기동"이라 부르고 임계를 1로 뒀다. 판정 결과는 같지만 리포트에 "l1_daily 재기동 1회"로
    # 찍혀, 아무 일 없던 날이 매일 사고처럼 보였다). 한 번이라도 다시 떴으면 조사 대상이다.
    "restarts": 0.0,
    # Command Center UI 자동 재기동 (2026-08-03 추가). 그 전엔 UI가 죽어도 이 리포트가
    # 조용했다 — 08-03에 UI가 2번 죽었는데 breach로 잡힌 건 순전히 `native_crashes`
    # (Windows 전용) 덕분이었다. 즉 **Windows가 아니거나 파이썬 레벨로 죽은 날은 화면이
    # 두 번 사라져도 "깨끗한 날"로 보고된다**. 관측 도구가 관측 공백을 못 보면 안 된다.
    "ui_restarts": 0.0,
    "native_crashes": 0.0,  # 네이티브 크래시는 1건도 정상이 아니다
    "critical_log_lines": 0.0,
    # 체결틱 최소 적재량 (2026-08-04, F2). **하한 임계**라 다른 항목과 방향이 반대다 —
    # 이 값을 밑돌면 breach다. 1,000행은 실측 기준 매우 느슨하다(2026-07-23 실측 초당 3틱
    # ≈ 하루 5~10만행, 부하 테스트도 5만행 기준). "적게 쌓였다"가 아니라 **"결선이 끊겼다"**
    # 를 잡는 값이라 일부러 낮게 뒀다 — 거래가 아무리 한산해도 정규장 405분에 1,000틱은 넘는다.
    "min_tick_rows": 1000.0,
    # 거래소 시각과 로컬 시계의 어긋남 (2026-08-05, `ops/clock_skew.py`). |값|이 이걸 넘으면
    # 완성봉 규율의 500ms 유예가 의미를 잃는다. 2026-08-04 실측은 9.72초였다.
    "clock_skew_seconds": 2.0,
    # 상위 Horizon 버킷에서 빠진 1분봉 수 (2026-08-05 장중, `data/bar_composer.py`).
    #
    # **0인 이유**: 1건이 곧 상위 봉 하나가 한 분(分) 모자라게 확정됐다는 뜻이고, 그건
    # `analyze_horizon_consistency`가 검사하는 정의상의 항등식 위반이다. "조금은 괜찮다"가
    # 성립하는 축이 아니다.
    #
    # **`horizon_findings`와 왜 둘 다 두나**: 그쪽은 아카이브(결과)를, 이쪽은 로그(원인)를
    # 본다. 재합성(`run_recompose.py`)을 돌리면 아카이브는 복구되지만 **그날 수집이 실제로
    # 손상됐다는 사실은 남아야 한다** — 안 그러면 다음 날 "어제는 깨끗했는데"로 읽힌다.
    "late_bar_drops": 0.0,
    # 관측 공백(분) — 프로세스가 죽어 아무것도 못 본 구간 (2026-08-06 P1-1).
    #
    # **5분인 이유**: 부팅 트리거가 붙은 뒤 재부팅 복구의 설계값이 부팅 30초 + 트리거 지연
    # 1분 + 기동 30초 ≈ 2~3분이다(`scripts/install_scheduled_tasks.ps1`). 5분은 그 위에
    # 여유를 둔 값이고, 2026-08-06의 21분과는 4배 이상 떨어져 있다.
    #
    # `restarts`(횟수)와 **둘 다 두는 이유**: 2분 재기동과 21분 정지가 같은 "1회"로 세어지면
    # 안 된다. 횟수는 안정성을, 시간은 손실 크기를 말한다.
    "observation_gap_minutes": 5.0,
}


@dataclass
class BarContinuity:
    horizon: str
    rows: int
    first_bar_kst: str | None
    last_bar_kst: str | None
    missing_minutes: int
    longest_gap_minutes: int
    gaps: list[tuple[str, str, int]] = field(default_factory=list)


@dataclass
class NativeCrashes:
    available: bool
    count: int
    details: list[str] = field(default_factory=list)
    # 이 플랫폼에서 **집계가 원래 가능한가** (2026-08-05 분리). `available`만으로는
    # "리눅스라 원래 못 센다"와 "Windows인데 질의가 실패했다"가 구분되지 않아, 후자를
    # breach로 올릴 수가 없었다. 실제로 2026-08-04에 후자가 났는데 조용히 지나갔다.
    supported: bool = True


@dataclass
class IntegrityReport:
    date: str
    symbol: str
    instance_id: str
    bar_continuity: list[BarContinuity]
    restarts: int  # 프로세스별 최댓값 — 임계 판정용 스칼라
    # 기동과 재기동을 나눠 담는다(2026-08-03) — `starts`는 그날 프로세스가 뜬 총 횟수,
    # `restarts`는 거기서 **예정된 08:35 기동 1회를 뺀** 수다. 예전엔 전자를 "재기동"이라
    # 불러서, 정상일에도 "재기동 1회"가 찍혔다.
    starts_by_process: dict[str, int]
    restarts_by_process: dict[str, int]
    ui_restarts: int  # Command Center UI 자동 재기동 횟수 — 관측 공백의 직접 지표
    session_starts_kst: dict[str, list[str]]
    nan_ratio_by_horizon: dict[str, dict[str, float]]
    log_level_counts: dict[str, int]
    tag_counts: dict[str, int]
    circuit_breaker_events: dict[str, int]
    data_flow_findings: list[str]
    # 1분봉 ↔ 상위 Horizon 합성봉의 거래량 총합 항등식 위반 (2026-08-05).
    # 외부 기준이 필요 없는 **내부 정합성** 검사라 매일 자동으로 돈다
    # (`analyze_horizon_consistency` docstring — 2026-08-04 유실을 당일 잡았을 검사).
    horizon_findings: list[str]
    # 상위 Horizon 버킷에서 빠진 1분봉 수 (2026-08-05 장중). `ComposerLateBarDropped`(늦게
    # 와서 버림) + `ComposerFlushedIncomplete`(끝내 안 와서 못 넣음)의 합이다.
    #
    # `horizon_findings`가 **아카이브**를 보는 반면 이건 **수집 당시의 사건**을 센다. 장 종료
    # 후 `run_recompose.py`로 상위 Horizon을 재합성하면 전자는 깨끗해지는데, 그날 라이브
    # 수집이 손상됐다는 사실 자체는 지워지면 안 된다 — 그 사실이 곧 코드 결함의 신호다.
    late_bar_drops: int
    # 그날 거래소 시각 − 로컬 시계(초). None은 못 쟀다는 뜻 — 0초와 구분한다(L18).
    clock_skew_seconds: float | None
    # 회선 수신 지연 **초과분**의 분위수 (2026-08-05 고도화 1, `ops/clock_skew.py`).
    #
    # **판정을 안 하는 축이다.** 임계를 정할 근거가 아직 없다 — 이 값을 며칠 모으는 것이
    # 곧 근거를 만드는 일이고, 그 결과로 `MINUTE_CLOSE_GRACE_SECONDS`를 확정한 뒤
    # `minute_bar_close: timer`(1분봉 시각 확정)로 승격한다.
    #
    # 동시에 등록부의 **전제 지표**다: 겹②·겹④가 "1분봉이 경계 뒤 얼마 안에 도착한다"를
    # 전제하는데, 그 전제가 며칠 뒤 조용히 깨지는 것을 잡을 자리가 여기다.
    delivery_latency: dict[str, float] | None
    # 그날 프로세스가 실제로 돌던 커밋. 지금 HEAD와 다르면 그 수집분은 **그 시점 코드의
    # 산물**이라는 사실이 사후 조사에 필요하다 — 2026-08-04가 정확히 그 경우였다(WS 프레임
    # 절반 유실 수정이 12:22에 들어갔는데 수집 프로세스는 08:35에 뜬 옛 코드로 하루를 돌았다).
    # 판정(breach)은 하지 않는다: 연구 커밋이 잦은 이 프로젝트에서 매일 울리면 늑대소년이 된다.
    session_git_shas: list[str]
    # 세션 내내 상수이거나 항상 NaN이던 피처 (2026-08-05 고도화 3, `features/engine.py`).
    # `nan_ratio`가 못 보는 것을 본다 — `px_macd_h_5`는 값을 내므로 흔적이 없었다.
    degenerate_features: dict[str, dict[str, list[str]]]
    # 거래소 공식 분봉 대비 아카이브 거래량 비율 (2026-08-05 고도화 1).
    # `scripts/verify_archive_volume.py`가 남긴 파일을 읽는다 — 안 돌린 날은 None이고,
    # 그 사실은 `unmeasured`에 들어간다.
    volume_check: dict[str, Any] | None
    # 변동성 축 채점 (2026-08-05 고도화 4, `scripts/run_vol_scorecard.py`).
    vol_axis: dict[str, Any]
    # 호스트 위생 (2026-08-05 고도화 5, `ops/host_health.py`).
    host_health: dict[str, Any]
    # **못 잰 것 전부** (2026-08-05 고도화 2). 종전에는 항목마다 흩어져 있었고 대부분
    # 조용히 지나갔다 — 2026-08-04에 크래시 집계가 정확히 그렇게 사라졌다. 측정 불능이
    # 한자리에 모여야 "오늘 무엇을 모르는가"를 사람이 한 번에 본다.
    unmeasured: list[str]
    flat_price_minutes: int
    pre_open_minutes: int
    market_findings: list[str]
    native_crashes: NativeCrashes
    # 이벤트로그(위)와 짝을 이루는 **파이썬 레벨** 크래시 증거 (2026-08-03 고도화 D).
    # 둘을 대조해야 "크래시는 났는데 덤프가 없다"(= 원인 규명 불가)가 자동으로 드러난다.
    crash_forensics: CrashForensics
    # 그날 실제로 디스크에 적재된 체결틱 행 수 (2026-08-04, F2).
    #
    # **"좋아지는가"가 아니라 "존재하는가"를 재는 자리다.** 선행 프로젝트 마흐디가 2026-08-03에
    # 배운 것을 그대로 가져왔다 — 그날 예측 13개 중 12개가 자동 대조로 확인됐지만, 그 어떤
    # 가설도 `find_gamma_flip()`이 **전 이력에서 한 번도 값을 낸 적이 없다**는 사실을 잡지
    # 못했다. 아무도 "감마플립이 계산되는가"를 예측치로 적지 않았기 때문이다. 그 결과 앙상블
    # 멤버 하나가 넉 달간 죽어 있었고, 넉 달 동안 "개선"해 온 대상이 애초에 없었다.
    #
    # 틱 수집이 정확히 같은 위험에 있다: 백필 경로가 없어 안 쌓인 날은 영원히 없는데, 결선이
    # 조용히 안 붙어도 봉 수집은 멀쩡하므로 다른 지표는 전부 정상으로 보인다.
    tick_rows: int
    # **적재 계열 전수 커버리지** (2026-08-06 고도화 2, `ops/series_coverage.py`).
    #
    # `tick_rows`가 틱 하나에 대해 하는 일("존재하는가")을 **모든 계열**에 대해, 그리고
    # 행수가 아니라 **시간**으로 한다. 2026-08-06에 옵션체인 1,500다리와 수급 264행이
    # 사라졌는데 리포트가 완벽하게 조용했던 이유가 이 축의 부재였다 — 그날 regular는
    # 1,302행이었고, 행수만 보면 아무 문제가 없어 보인다(첫 사이클이 10:30이었다).
    series_coverage: list[dict[str, Any]]
    # 위 커버리지에서 나온 판정 문장 — `horizon_findings`와 같은 성격(정의 위반 목록)이다.
    series_findings: list[str]
    # **관측 공백과 그 원인** (2026-08-06 P1-1·P1-2, `ops/observation_gaps.py`).
    #
    # `restarts`가 **횟수**를 세는 자리라면 이쪽은 **시간과 원인**을 센다. 2026-08-06에
    # 리포트가 말한 것은 "재기동 1회"뿐이었고, 21분을 잃었다는 것도 왜 그랬는지도 없었다.
    # `ui_restarts`가 못 보는 UI의 공백도 여기서 보인다(그쪽은 인프로세스 워치독만 센다).
    observation_gaps: list[dict[str, Any]]
    host_events: list[dict[str, Any]]
    breaches: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------- 봉 연속성


def _load_day_frame(bar_dir: Path, symbol: str, horizon: Horizon, day: date) -> pl.DataFrame | None:
    """`ParquetArchiver.read_day()`를 쓴다 — 장중(조각)인지 장후(통합본)인지에 따라 물리
    배치가 다르므로(`data/archiver.py` "조각 쓰기"), 경로를 여기서 다시 조립하면 장중에
    리포트를 돌렸을 때 데이터가 없다고 나온다."""
    frame = ParquetArchiver(bar_dir).read_day(symbol, horizon, day)
    if frame is None:
        return None
    return frame.with_columns(pl.col("bar_open_kst").dt.convert_time_zone(_KST_ZONE_NAME)).sort(
        "bar_open_kst"
    )


def analyze_bar_continuity(
    bar_dir: Path, symbol: str, day: date, *, horizons: Sequence[Horizon] = (Horizon.M1,)
) -> list[BarContinuity]:
    """봉 사이 간격이 Horizon 길이보다 크면 그만큼을 결손으로 센다.

    M1만 기본으로 보는 이유: 굵은 Horizon은 M1이 비면 따라서 비므로 같은 사고를 중복 계상할
    뿐이고, 분 단위가 공백의 시작·끝을 가장 정확히 짚는다(실측 조사도 M1으로 했다).
    """
    out: list[BarContinuity] = []
    for horizon in horizons:
        step = _horizon_minutes(horizon)
        frame = _load_day_frame(bar_dir, symbol, horizon, day)
        if frame is None or frame.height == 0:
            out.append(BarContinuity(horizon.value, 0, None, None, 0, 0))
            continue

        stamps = frame["bar_open_kst"].to_list()
        gaps: list[tuple[str, str, int]] = []
        for earlier, later in zip(stamps, stamps[1:]):
            missing = int((later - earlier).total_seconds() // 60) - step
            if missing > 0:
                gaps.append((earlier.strftime("%H:%M"), later.strftime("%H:%M"), missing))

        out.append(
            BarContinuity(
                horizon=horizon.value,
                rows=len(stamps),
                first_bar_kst=stamps[0].strftime("%H:%M"),
                last_bar_kst=stamps[-1].strftime("%H:%M"),
                missing_minutes=sum(g[2] for g in gaps),
                longest_gap_minutes=max((g[2] for g in gaps), default=0),
                gaps=gaps,
            )
        )
    return out


def _horizon_minutes(horizon: Horizon) -> int:
    return int(horizon.value.rstrip("m"))


# ---------------------------------------------------------------- Horizon 총합 항등식


def analyze_horizon_consistency(bar_dir: Path, symbol: str, day: date) -> list[str]:
    """1분봉과 상위 Horizon 합성봉이 **같은 거래량 총합**을 갖는지 — 정의상 성립해야 한다.

    ## 왜 이 검사가 필요했나 (2026-08-04)

    그날 1분봉 합계는 84,346인데 3/5/10/15/30분봉이 전부 84,209였다. 차이 137은 정확히
    **15:34봉 하나의 거래량**이다 — 종료 시퀀스에서 마지막 1분봉이 버스 구독자(합성기)에
    도달하기 전에 `flush_all_final()`이 먼저 돌아, 그 분이 상위 Horizon 전부에서 빠졌다.

    그날 무결성 리포트는 "1분봉 410개, 결손 0분, CRITICAL 0 · ERROR 0 · WARNING 0"이었다.
    **리포트가 보는 축이 1분봉 연속성 하나뿐이라 이 유실을 볼 수단이 아예 없었다.**

    이 검사는 외부 기준이 필요 없다 — 상위 봉은 1분봉의 합이라는 것이 `compose_offline`의
    정의이므로, 어긋나면 반드시 어딘가 결함이다. 그래서 매일 자동으로 돌 수 있다.

    잡히는 것이 둘 더 있다.

    - 1분봉만 백필로 교체하고 상위 Horizon을 재합성하지 않은 상태(`scripts/run_recompose.py`
      모듈 docstring의 "1분봉은 거래소 공식값인데 5분봉은 옛 수집값").
    - 늦게 온 1분봉이 확정된 버킷을 다시 열어 **같은 시각의 합성봉이 덮어쓰인** 상태.
      `ParquetArchiver`가 `(bar_open_kst, horizon)`으로 `unique(keep="last")` 하므로
      행 수로는 아무 흔적이 없고(중복 행이 안 남는다), 오직 거래량 총합만 줄어든다.
      그래서 이 검사가 **행 수가 아니라 총합**을 보는 것이 중요하다.

    1분봉이 없는 날은 판정하지 않는다(빈 목록) — 그건 이 검사가 아니라 봉 연속성의 몫이다.
    """
    m1 = _load_day_frame(bar_dir, symbol, Horizon.M1, day)
    if m1 is None or m1.height == 0:
        return []
    m1_volume = int(m1["volume"].sum())

    findings: list[str] = []
    for horizon in Horizon:
        if horizon is Horizon.M1:
            continue
        frame = _load_day_frame(bar_dir, symbol, horizon, day)
        if frame is None or frame.height == 0:
            continue
        volume = int(frame["volume"].sum())
        if volume != m1_volume:
            diff = m1_volume - volume
            findings.append(
                f"{horizon.value} 거래량 합 {volume:,} ≠ 1분봉 합 {m1_volume:,} "
                f"(차이 {diff:+,}) — 상위 Horizon은 1분봉의 합이어야 한다"
                f"{'; 마지막 봉 유실 또는 재합성 누락 의심' if diff > 0 else ''}"
            )
    return findings


# ---------------------------------------------------------------- 로그 집계


def _iter_json_lines(paths: Iterable[Path]) -> Iterable[dict[str, Any]]:
    for path in paths:
        if not path.exists():
            continue
        # utf-8-sig: PowerShell tee가 BOM을 붙인다(`run_l1_daily.bat`) — 이걸 안 벗기면
        # 첫 줄이 JSON으로 안 읽힌다(실측).
        for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue  # self_check의 사람용 출력 등 — JSON 라인만 본다
            try:
                yield json.loads(line)
            except ValueError:
                continue


def analyze_logs(log_paths: Sequence[Path]) -> dict[str, Any]:
    level_counts: dict[str, int] = {}
    tag_counts: dict[str, int] = {}
    session_starts: list[str] = []
    session_git_shas: list[str] = []
    clock_skews: list[float] = []
    delivery_latency: dict[str, float] | None = None
    degenerate: dict[str, dict[str, list[str]]] = {}
    nan_by_horizon: dict[str, list[float]] = {}
    cb_events: dict[str, int] = {}

    # 그 프로세스가 **살아서 뭔가를 찍은 시각들** (2026-08-06). 관측 공백 계산의 재료다 —
    # 재기동 사이의 빈 구간이 얼마인지는 "마지막으로 뭔가 찍은 시각"과 "다음 기동 시각"
    # 사이로만 잴 수 있다(`ops/observation_gaps.py`).
    activity: list[str] = []

    for record in _iter_json_lines(log_paths):
        level = str(record.get("level", "-"))
        tag = str(record.get("tag", "-"))
        level_counts[level] = level_counts.get(level, 0) + 1
        tag_counts[tag] = tag_counts.get(tag, 0) + 1
        stamp = str(record.get("ts", ""))[11:19]
        if len(stamp) == 8:
            activity.append(stamp)

        if tag == "SessionStart":
            session_starts.append(str(record.get("ts", ""))[11:19])
            sha = record.get("git_sha")
            if isinstance(sha, str) and sha:
                session_git_shas.append(sha)
        elif tag in ("ClockSkewMeasured", "ClockSkewExceeded"):
            skew = record.get("skew_seconds")
            if isinstance(skew, (int, float)):
                clock_skews.append(float(skew))
        elif tag == "TickDeliveryLatency":
            # 회선 수신 지연 초과분 분포 (2026-08-05 고도화 1). **판정을 안 하는 축**이지만
            # 리포트에 있어야 한다 — 이 값이 `minute_bar_close: timer` 승격의 유일한 근거이고,
            # 등록부의 전제 지표(`fix_verification.py` "전제를 채점한다")로도 쓰인다.
            if record.get("measured"):
                delivery_latency = {
                    key: float(record[key])
                    for key in ("p50", "p90", "p99", "max", "samples")
                    if isinstance(record.get(key), (int, float))
                }
        elif tag in ("FeatureHealthSummary", "FeatureHealthDegenerate"):
            horizon = str(record.get("horizon", "?"))
            degenerate[horizon] = {
                "always_nan": list(record.get("always_nan") or []),
                "constant": list(record.get("constant") or []),
            }
        elif tag == "FeaturePublish":
            horizon = str(record.get("horizon", "?"))
            ratio = record.get("nan_ratio")
            if isinstance(ratio, (int, float)):
                nan_by_horizon.setdefault(horizon, []).append(float(ratio))
        elif tag.startswith("CircuitBreaker"):
            cb_events[tag] = cb_events.get(tag, 0) + 1

    nan_summary = {
        horizon: {
            "median": round(median(values), 4),
            "min": round(min(values), 4),
            "last": round(values[-1], 4),
            "samples": len(values),
        }
        for horizon, values in sorted(nan_by_horizon.items())
    }
    return {
        "level_counts": level_counts,
        "tag_counts": tag_counts,
        "session_starts": session_starts,
        "activity_kst": sorted(activity),
        "session_git_shas": sorted(set(session_git_shas)),
        # 절댓값이 가장 큰 표본 — 하루 중 시계가 동기되면 여러 값이 남는데, 그날 최악의
        # 상태가 판정 기준이다(그 시간대의 봉은 이미 그 스큐로 만들어졌다).
        "clock_skew_seconds": (max(clock_skews, key=abs) if clock_skews else None),
        "delivery_latency": delivery_latency,
        "degenerate_features": degenerate,
        "nan_ratio_by_horizon": nan_summary,
        "circuit_breaker_events": cb_events,
    }


# ---------------------------------------------------------------- 탐지·복구 소유권 (고도화 4)


def analyze_data_flow_ownership(tag_counts: Mapping[str, int]) -> list[str]:
    """L1(탐지·복구)과 G2(매매 판단)의 판정이 어긋난 흔적을 찾는다.

    계층 분리 자체는 `data/collector.py` 모듈 docstring "데이터 흐름의 1차 책임" 참고. 두
    판정을 런타임에 묶지 않기로 한 이상(한쪽 버그가 조용히 다른 쪽을 오염시키지 않게), 어긋난
    사실은 사후에라도 반드시 드러나야 한다 — 그게 이 함수다.

    잡아내는 두 가지:

    1. **L1이 감지했는데 복구를 못 했다** — `CollectorTickStall`은 났는데
       `CollectorWSReconnected`가 없다. 강제 재연결이 걸렸으면 재연결 로그가 따라야 한다.
    2. **G2만 알고 L1은 몰랐다** — `CircuitBreakerConfirmed`는 났는데 L1 쪽에 단절 흔적
       (`CollectorTickStall`/`CollectorWSDisconnected`)이 하나도 없다. 이건 2026-07-28·29의
       30분 공백과 정확히 같은 구조다: 거래는 멈췄는데 데이터 흐름은 아무도 안 고쳤다는 뜻.
    """
    stalls = tag_counts.get("CollectorTickStall", 0)
    disconnects = tag_counts.get("CollectorWSDisconnected", 0)
    reconnects = tag_counts.get("CollectorWSReconnected", 0)
    cb_confirmed = tag_counts.get("CircuitBreakerConfirmed", 0)
    cb_resumed = tag_counts.get("CircuitBreakerResumed", 0)

    findings: list[str] = []
    if stalls > 0 and reconnects == 0:
        findings.append(f"L1 스톨 감지 {stalls}회인데 재연결 성공 0회 — 탐지는 됐으나 복구가 안 됨")
    if cb_confirmed > 0 and (stalls + disconnects) == 0:
        findings.append(
            f"G2 CB 확정 {cb_confirmed}회인데 L1 단절 흔적 0건 — "
            "거래는 멈췄으나 데이터 흐름은 아무도 손대지 않음"
        )
    # 3. **정지와 해제의 짝이 안 맞는다** (2026-07-31 신규) — 그날 확정 5회에 해제 3회였고,
    #    나머지 2회는 게이트가 안 풀린 채 장이 끝났다(6시간 42분 halted). 위 두 규칙은 둘 다
    #    "한쪽에 흔적이 **아예** 없을 때"만 보므로 이 형태를 통과시켰다(그날 findings 0건).
    #
    #    "L1 재연결 N회인데 CB 확정 M회"류의 개수 비교 규칙도 후보였지만 채택하지 않았다 —
    #    진짜 단절이 나서 L1이 복구하고 CB가 정지시킨 **정상 시나리오**와 구분이 안 돼
    #    오탐만 늘린다. 짝 불일치는 그런 모호함이 없다: 확정됐으면 해제도 있어야 한다.
    if cb_confirmed > cb_resumed:
        findings.append(
            f"CB 확정 {cb_confirmed}회 대 해제 {cb_resumed}회 — "
            f"{cb_confirmed - cb_resumed}회가 해제 없이 남음(주문 게이트 잔류 정지 의심)"
        )
    return findings


# ---------------------------------------------------------------- 시장 상태 (2026-07-31)


# 가격이 이만큼 오래 1틱도 안 움직이면 "한산"이 아니라 별개의 시장 상태(상한/하한 고착,
# 일방시장)로 본다 — 스톨 오탐·CB 오탐·피처 퇴화가 전부 이 구간에서 나오므로, 사후 조사가
# 원인을 한 줄로 짚을 수 있어야 한다.
_FLAT_PRICE_ALERT_MINUTES = 30


def _bar_shape_metrics(bar_dir: Path, symbol: str, day: date) -> tuple[int, int]:
    """(가격 고정 분 수, 장전 봉 수) — 그날 1분봉의 "모양"에 대한 두 지표.

    **가격 고정(`o=h=l=c`)**: 2026-07-31엔 380봉 중 60봉이 이 형태였고(14:21 이후 마감까지
    51814 고정), 그게 그날 이상점 대부분의 공통 원인이었는데 리포트엔 그 사실을 가리키는
    숫자가 하나도 없었다 — 사람이 Parquet을 직접 열어야만 보였다.

    **장전 봉 수**: 같은 날 08:45~09:04의 20봉이 전부 스테일 프린트로 보였다
    (`core/messages.py`의 `BarSession`). 매일 몇 봉이 장전 구간에서 들어왔는지를 남겨,
    "그날 장전이 평소와 달랐는가"를 다음 점검이 손으로 안 파도 되게 한다.
    `session` 컬럼이 없던 시절 파일은 전부 정규장으로 읽히므로 0이 나온다(사실과 맞다 —
    그때는 구분 자체가 없었다).
    """
    frame = _load_day_frame(bar_dir, symbol, Horizon.M1, day)
    if frame is None or frame.height == 0:
        return 0, 0
    flat = (
        (pl.col("o_ticks") == pl.col("h_ticks"))
        & (pl.col("h_ticks") == pl.col("l_ticks"))
        & (pl.col("l_ticks") == pl.col("c_ticks"))
    )
    flat_minutes = int(frame.select(flat.sum()).item())
    if "session" not in frame.columns:
        return flat_minutes, 0
    pre_open = int(frame.select((pl.col("session") == BarSession.PRE_OPEN.value).sum()).item())
    return flat_minutes, pre_open


def analyze_market_state(flat_minutes: int, m1: BarContinuity | None) -> list[str]:
    """시장 자체가 평소와 달랐던 날을 드러낸다 — **장애가 아니라 상태**다.

    이걸 별도 항목으로 두는 이유: 이런 날은 결손·CB·NaN 임계가 한꺼번에 터지는데, 그 셋을
    각각 "장애"로만 보면 원인을 못 찾고 매번 처음부터 조사하게 된다. 시장 상태를 먼저 적어
    두면 나머지 초과 항목들이 그 결과라는 게 바로 읽힌다.
    """
    findings: list[str] = []
    if flat_minutes >= _FLAT_PRICE_ALERT_MINUTES:
        span = f"/{m1.rows}봉" if m1 is not None and m1.rows else ""
        findings.append(
            f"가격 고정 {flat_minutes}분{span}(o=h=l=c) — 상한/하한 고착 또는 일방시장 의심, "
            "이날의 스톨·CB·NaN 초과는 이 상태의 결과일 수 있음"
        )
    return findings


def _first_session_start(day: date, session_starts: Mapping[str, Sequence[str]]) -> datetime | None:
    """그날 MESSIAH 프로세스가 처음 기동한 시각(KST 벽시계) — 크래시 집계 창의 시작점.

    로그의 `SessionStart` ts에서 `HH:MM:SS`만 잘라 쓴다(`analyze_logs`가 그렇게 모은다).
    하나도 없으면 `None` — 그날 아예 안 돌았거나 로그가 없는 경우라 창을 좁힐 근거가 없다.

    반환은 **의도적으로 naive**다 — 이 값의 유일한 소비처가 `Get-WinEvent`의 `StartTime`
    문자열이고(`_collect_native_crashes`), Windows 이벤트 로그의 시각은 이 PC의 로컬 시각
    (= KST)이다. tz를 붙이면 오히려 오프셋 변환이 끼어들 여지가 생긴다.
    """
    stamps = [s for starts in session_starts.values() for s in starts if s]
    if not stamps:
        return None
    try:
        # noqa DTZ007: 로그가 남긴 KST 벽시계 문자열이라 tz를 붙일 대상이 아니다(위 docstring).
        earliest = min(datetime.strptime(s, "%H:%M:%S").time() for s in stamps)  # noqa: DTZ007
    except ValueError:
        return None
    return datetime.combine(day, earliest)


# ---------------------------------------------------------------- 네이티브 크래시


def _collect_native_crashes(
    day: date,
    *,
    runner=subprocess.run,
    since: datetime | None = None,
    python_version_prefix: str | None = None,
) -> NativeCrashes:
    """`Application Error`(이벤트 ID 1000) 중 해당 날짜의 python.exe 크래시만 센다.

    2026-07-30 사고의 **유일한** 흔적이 여기였다 — 네이티브 크래시는 프로세스를 즉사시켜
    애플리케이션 로그에 traceback은커녕 한 줄도 안 남긴다.

    ## 남의 크래시를 세지 않는다 (2026-07-31 수정)

    2026-07-31 리포트는 "네이티브 크래시 8건"으로 임계를 초과했는데, 실측해 보니 그중 2건은
    MESSIAH가 아니었다: 06:34:53·06:36:19의 두 건은 python.exe **3.10**(MESSIAH의 .venv는
    3.12) + `KERNELBASE.dll` + 예외코드 `0xc06d007f`로, MESSIAH가 기동하기(08:35) 두 시간
    전에 이 PC의 다른 프로젝트가 낸 것이었다. 임계 초과 목록이 남의 사고로 오염되면 매일
    늑대소년이 된다.

    두 가지로 좁힌다:

    - `since`: 그날 첫 `SessionStart` 시각 이후만 본다. 호출측(`build_report`)이 실제 로그에서
      읽은 값을 넘긴다 — "MESSIAH가 돌지도 않던 시간"을 아예 창에서 뺀다.
    - `python_version_prefix`: 이벤트에 찍힌 결함 프로세스 버전이 이 접두사로 시작하는 것만
      센다(기본값은 지금 이 인터프리터의 major.minor). 같은 PC에 여러 파이썬이 있는 환경에서
      가장 싸게 구분되는 축이다.

    좁힌 뒤에도 **못 세는 것과 0건은 계속 구분한다**(`available=False`, L18) — 이 사고의
    유일한 흔적이 이 집계였으므로 "못 셌다"는 사실 자체가 중요하다.

    ## 크래시가 0건인 날에만 집계가 실패했다 (2026-08-05 수정)

    종전 스크립트는 `-ErrorAction SilentlyContinue`를 걸고 파이썬이 `returncode != 0`을
    실패로 판정했다. 그런데 **`-ErrorAction SilentlyContinue`는 오류 출력만 막고 종료 코드는
    못 막는다** — 창 안에 `Application Error` 이벤트가 하나도 없으면 `Get-WinEvent`가
    "일치하는 이벤트 없음"이라는 비종료 오류를 내고 powershell.exe가 1로 끝난다.

    결과는 정확히 거꾸로였다: 크래시가 난 날(07-29·30·31·08-03)은 집계가 성공했고,
    **크래시가 0건인 첫 날(08-04)이 "집계 실패"로 보고됐다.** UI 크래시 격리 수정이 처음
    성공한 그 날 성공을 증명할 수치가 사라졌고, "3거래일 연속 `native_crashes` 0건"을
    조건으로 건 등록부는 그 상태로는 **영원히 판정을 못 채운다**.

    이제 스크립트가 **항상 0으로 끝나고** 첫 줄에 `OK <건수>` 또는 `ERR <예외형>`을 찍는다.
    "이벤트 없음"은 로케일 문자열이 아니라 `FullyQualifiedErrorId`(`NoMatchingEventsFound*`,
    번역되지 않는다)로 식별한다 — 한글 Windows에서 메시지 매칭은 안 통한다.
    """
    if sys.platform != "win32":
        return NativeCrashes(
            available=False, count=0, details=["Windows 전용 집계 — 건너뜀"], supported=False
        )

    start = datetime.combine(day, datetime.min.time())
    if since is not None and since > start:
        start = since
    end = datetime.combine(day, datetime.min.time()) + timedelta(days=1)
    if python_version_prefix is None:
        python_version_prefix = f"{sys.version_info.major}.{sys.version_info.minor}."
    # 출력은 **ASCII만** 뽑는다. 이벤트 로그의 `Message`는 시스템 로캘 언어라(한글 Windows면
    # 한국어) 그대로 받으면 PowerShell이 CP949로 내보내는데, 파이썬이 utf-8로 디코딩하다
    # UnicodeDecodeError가 난다(2026-07-30 실측).
    #
    # 예전엔 그 `Message`에 정규식을 걸어 모듈명을 뽑았는데, 2026-07-31부터 `Properties`
    # 배열을 직접 읽는다 — 로캘 문자열을 아예 안 건드리고(근본 해법), 필요한 값이 전부
    # 구조화된 필드로 있다: [0] 프로세스명 · [1] 프로세스 버전 · [3] 결함 모듈 · [6] 예외코드 ·
    # [7] 결함 오프셋. 특히 **[1] 버전**이 있어야 같은 PC의 다른 파이썬(2026-07-31의 3.10 2건)을
    # 걸러낼 수 있고, **[7] 오프셋**을 남겨야 "같은 주소에서 또 죽었다"를 사후에 대조할 수 있다
    # (07-29·07-30·07-31 UI 크래시 10건이 전부 +0x083973c7 동일이었다).
    script = (
        "$ErrorActionPreference='Stop'; $events=@(); "
        "try { $events=@(Get-WinEvent -FilterHashtable @{LogName='Application';"
        "ProviderName='Application Error';"
        f"StartTime='{start:%Y-%m-%d %H:%M:%S}';EndTime='{end:%Y-%m-%d %H:%M:%S}'"
        "} -ErrorAction Stop) } "
        # 로케일 문자열이 아니라 번역되지 않는 오류 ID로 "이벤트 없음"을 식별한다.
        "catch { if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { $events=@() } "
        "else { Write-Output ('ERR ' + $_.Exception.GetType().Name); exit 0 } } "
        "$f=@($events | Where-Object { $_.Properties.Count -ge 8 -and "
        "$_.Properties[0].Value -like 'python.exe*' -and "
        f"$_.Properties[1].Value -like '{python_version_prefix}*'"
        " }); "
        "Write-Output ('OK ' + $f.Count); "
        "$f | ForEach-Object { $_.TimeCreated.ToString('HH:mm:ss') + ' ' + "
        "$_.Properties[3].Value + ' ' + $_.Properties[6].Value + ' +0x' "
        "+ $_.Properties[7].Value }; "
        "exit 0"
    )
    try:
        result = runner(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",  # 그래도 로캘 바이트가 섞여 오면 리포트를 죽이지 말고 흘린다
            timeout=60,
        )
    except Exception as exc:  # noqa: BLE001 — 못 세는 것과 0건은 다르다
        return NativeCrashes(available=False, count=0, details=[f"집계 실패: {exc}"])

    lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    # 종료 코드가 아니라 **센티널 첫 줄**로 판정한다 — 종료 코드는 "이벤트 0건"과
    # "질의 실패"를 구분하지 못했다(위 docstring).
    if result.returncode != 0 or not lines or not lines[0].startswith("OK "):
        reason = lines[0] if lines else f"출력 없음(exit={result.returncode})"
        return NativeCrashes(available=False, count=0, details=[f"Get-WinEvent 실패: {reason}"])

    return NativeCrashes(available=True, count=int(lines[0][3:].strip()), details=lines[1:])


# ---------------------------------------------------------------- 체결틱 적재량


def count_tick_rows(tick_dir: Path | None, symbol: str, day: date) -> int:
    """그날 적재된 체결틱 행 수 — 조각 파일을 세지 않고 **실제 행**을 센다.

    파일 개수로 대신하면 "파일은 있는데 0행"을 못 잡는다. 그게 이 지표가 막으려는 상황이다
    (`IntegrityReport.tick_rows` 주석 — 마흐디의 감마플립 넉 달 사고와 같은 형태).

    실패해도 0을 돌려주고 리포트를 막지 않는다 — 관측 수단이 운영을 멈추면 안 된다. 다만
    0은 **"안 쌓였다"와 구분되지 않으므로** 임계 판정이 그 자체로 breach를 낸다.
    """
    if tick_dir is None:
        tick_dir = DEFAULT_TICK_DIR
    try:
        frame = tick_archiver.read_day(tick_dir, symbol, day)
    except Exception:  # noqa: BLE001 — 관측이 운영을 막지 않는다
        return 0
    return 0 if frame is None else int(frame.height)


# ---------------------------------------------------------------- 조립


def load_volume_check(day: date, log_dir: Path) -> dict[str, Any] | None:
    """`scripts/verify_archive_volume.py`가 남긴 외부 대조 결과 — 없으면 None(미측정).

    이 파일이 리포트의 **외부 대조 축**이다(2026-08-05 고도화 1). REST 호출을 장후 종료
    절차(15:35~15:40)에 넣지 않는다는 판단은 유지하되, 그렇다고 "그날 외부 대조를 아예
    안 했다"가 조용히 지나가서는 안 된다 — 없으면 `unmeasured`로 올라간다.

    2026-08-04가 이 축이 없어서 생긴 사고다: 리포트는 "결손 0분"으로 깨끗했는데 아카이브
    거래량은 공식값의 55%였다. 내부 정합성(Horizon 항등식)은 수집값끼리의 일치라 절반
    유실이 양쪽에 똑같이 반영돼 통과한다 — 외부 기준이 있어야만 잡힌다.
    """
    return load_json_artifact(day, log_dir, "volume_check")


def load_json_artifact(day: date, log_dir: Path, prefix: str) -> dict[str, Any] | None:
    """`logs/{prefix}_YYYYMMDD.json`을 읽는다 — 없거나 깨졌으면 None(= 미측정).

    장후에 사람이 따로 돌리는 도구들의 산출물을 리포트가 **1급 축**으로 삼는 통로다
    (2026-08-05 고도화 1·4). 이 파일들은 REST 호출이나 무거운 재계산을 요구해 15:35~15:40
    종료 예산에 넣을 수 없지만, 그렇다고 "안 돌린 날"이 조용히 지나가서도 안 된다 —
    없으면 `unmeasured`에 올라간다.
    """
    path = log_dir / f"{prefix}_{day.strftime('%Y%m%d')}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def build_report(
    *,
    day: date,
    symbol: str,
    instance_id: str,
    bar_dir: Path,
    log_paths: Mapping[str, Sequence[Path]],
    thresholds: dict[str, float] | None = None,
    crash_collector=_collect_native_crashes,
    log_dir: Path | None = None,
    tick_dir: Path | None = None,
    host_collector=None,
    flow_dir: Path | None = None,
    option_chain_dir: Path | None = None,
    host_event_collector=observation_gaps.collect_host_events,
) -> IntegrityReport:
    """`log_paths`는 프로세스 이름 → 로그 파일 목록이다.

    프로세스별로 나눠 받는 이유: 재기동 횟수를 통째로 합치면 "L1이 6번 + G2가 5번"이
    "11번"으로 뭉뚱그려져 **어느 프로세스가 불안정한지**가 사라진다(2026-07-29가 정확히 그
    형태였다 — 원인은 워치독이 L1을 죽인 것이었고 G2 재시작은 그 여파였다).
    """
    limits = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    continuity = analyze_bar_continuity(bar_dir, symbol, day)

    per_process = {name: analyze_logs(paths) for name, paths in log_paths.items()}
    logs = analyze_logs([path for paths in log_paths.values() for path in paths])
    starts_by_process = {
        name: len(result["session_starts"]) for name, result in per_process.items()
    }
    # 첫 기동은 **예정된 것**이라 재기동이 아니다 — 이 한 줄이 정상일 리포트에서 "재기동 1회"
    # 라는 헛경고를 없앤다(2026-08-03 정정).
    restarts_by_process = {name: max(0, count - 1) for name, count in starts_by_process.items()}
    session_starts = {name: result["session_starts"] for name, result in per_process.items()}

    # 크래시 집계 창을 "MESSIAH가 실제로 돌던 시간"으로 좁힌다(2026-07-31 — 그날 08:35 기동
    # 전인 06:34·06:36의 남의 프로세스 크래시 2건이 리포트에 섞여 임계를 초과시켰다).
    crashes = crash_collector(day, since=_first_session_start(day, session_starts))

    m1 = next((c for c in continuity if c.horizon == Horizon.M1.value), None)
    restarts = max(restarts_by_process.values(), default=0)
    critical_lines = logs["level_counts"].get("CRITICAL", 0)
    flat_minutes, pre_open_minutes = _bar_shape_metrics(bar_dir, symbol, day)
    tick_rows = count_tick_rows(tick_dir, symbol, day)

    breaches: list[str] = []
    # 봉이 정상인 날에도 틱만 조용히 안 쌓일 수 있다 — 수집 경로가 다르기 때문이다. 그리고
    # 틱은 백필이 없어 그 하루가 영구히 빈다(`IntegrityReport.tick_rows` 주석).
    if tick_rows < limits["min_tick_rows"]:
        breaches.append(
            f"체결틱 적재 {tick_rows}행 < 최소 {limits['min_tick_rows']:.0f}행 — "
            "결선 확인 필요(틱은 백필 경로가 없어 오늘치는 소급 불가)"
        )
    if m1 is not None and m1.missing_minutes > limits["missing_minutes"]:
        breaches.append(
            f"1분봉 결손 {m1.missing_minutes}분 > 임계 {limits['missing_minutes']:.0f}분"
        )
    if m1 is not None and m1.longest_gap_minutes > limits["longest_gap_minutes"]:
        breaches.append(
            f"최장 공백 {m1.longest_gap_minutes}분 > 임계 {limits['longest_gap_minutes']:.0f}분"
        )
    for name, count in sorted(restarts_by_process.items()):
        if count > limits["restarts"]:
            breaches.append(f"{name} 재기동 {count}회 > 임계 {limits['restarts']:.0f}회")
    if crashes.available and crashes.count > limits["native_crashes"]:
        breaches.append(f"네이티브 크래시 {crashes.count}건")
    if critical_lines > limits["critical_log_lines"]:
        breaches.append(f"CRITICAL 로그 {critical_lines}건")
    # UI가 죽어서 다시 떴다는 건 그 사이에 **화면이 없었다**는 뜻이다 — 재기동에 성공했어도
    # 관측 공백은 실재한다(2026-08-03: 11:25:18~11:25:55 37초, 14:20:18~14:20:33 15초).
    # 그 전엔 이 사실이 Windows 전용 `native_crashes`로만 드러났다(위 임계 주석 참고).
    ui_restarts = logs["tag_counts"].get("CommandCenterUIRestarted", 0)
    if ui_restarts > limits["ui_restarts"]:
        breaches.append(f"Command Center UI 자동 재기동 {ui_restarts}회 — 그 사이 관측 공백")
    # 화면이 통째로 사라진 날은 "관측 공백"이라 그 자체가 임계 초과다 — 2026-07-31엔
    # 12:35~15:35 3시간 무화면이었는데 리포트엔 아무 표시도 안 났다.
    ui_gave_up = logs["tag_counts"].get("CommandCenterUIRestartGaveUp", 0)
    if ui_gave_up:
        breaches.append(f"Command Center UI 자동 재기동 포기 {ui_gave_up}회 — 관측 공백 발생")

    data_flow_findings = analyze_data_flow_ownership(logs["tag_counts"])
    breaches.extend(data_flow_findings)
    market_findings = analyze_market_state(flat_minutes, m1)
    breaches.extend(market_findings)

    # 상위 Horizon은 1분봉의 합이라는 정의상의 항등식 — 외부 기준이 필요 없다.
    horizon_findings = analyze_horizon_consistency(bar_dir, symbol, day)
    breaches.extend(horizon_findings)

    # 같은 손상을 **로그 쪽**에서 센다(2026-08-05 장중). 위 항등식은 재합성으로 지워지지만
    # 이건 안 지워진다 — 그날 라이브 수집이 실제로 잘렸다는 사실의 기록이다.
    late_bar_drops = logs["tag_counts"].get("ComposerLateBarDropped", 0) + logs["tag_counts"].get(
        "ComposerFlushedIncomplete", 0
    )
    if late_bar_drops > limits["late_bar_drops"]:
        breaches.append(
            f"상위 Horizon 버킷에서 1분봉 {late_bar_drops}개 유실 — 합성봉이 그만큼 짧게 "
            "확정됐다(장 종료 후 `run_recompose.py`로 재합성 필요, 원인은 수집 경로에 있다)"
        )

    # **측정 불능은 0건이 아니다** (2026-08-05). 종전에는 `available=False`가 조용히
    # 지나가서, 크래시 0건인 날에만 집계가 실패하는 결함이 드러나지 않았고 그 상태로는
    # "3거래일 연속 크래시 0건" 등록부가 영원히 판정을 못 채웠다.
    if crashes.supported and not crashes.available:
        breaches.append(
            "네이티브 크래시 집계 불가 — 측정 불능은 0건이 아니다"
            + (f" ({crashes.details[0]})" if crashes.details else "")
        )

    # 장후 도구들의 산출물이 있는 곳. UI 로그까지 포함하는 디렉터리라 `_infer_log_dir()`가
    # 호출측이 넘긴 로그 경로에서 역추론한다(`_infer_log_dir` docstring).
    resolved_log_dir = log_dir or _infer_log_dir(log_paths)
    vol_axis = load_json_artifact(day, resolved_log_dir, "vol_scorecard") or {}

    # ---- 고도화 3: 세션 내내 죽어 있던 피처 ----
    degenerate_features = logs["degenerate_features"]
    for horizon, entry in sorted(degenerate_features.items()):
        dead = list(entry.get("always_nan") or []) + list(entry.get("constant") or [])
        if dead:
            breaches.append(
                f"{horizon} 피처 {len(dead)}개가 세션 내내 죽어 있었다({', '.join(dead[:5])}"
                f"{' 외' if len(dead) > 5 else ''}) — 모델에 죽은 입력이 들어간다"
            )

    # ---- 고도화 1: 외부 대조 ----
    volume_check = load_volume_check(day, resolved_log_dir)
    if volume_check is not None and not volume_check.get("ok"):
        ratio = volume_check.get("ratio")
        breaches.append(
            f"공식 분봉 대비 아카이브 거래량 비율 "
            f"{'측정 불가' if ratio is None else f'{ratio:.3f}'} < "
            f"{volume_check.get('warn_ratio', 0.95)} — 수집 당시 파서를 의심할 것"
        )

    # ---- 고도화 5: 호스트 위생 ----
    from messiah.ops import host_health as host_health_module

    host = (host_collector or host_health_module.collect)()
    for finding in host.degraded:
        breaches.append(f"호스트 위생: {finding}")

    clock_skew = logs["clock_skew_seconds"]
    if clock_skew is not None and abs(clock_skew) > limits["clock_skew_seconds"]:
        breaches.append(
            f"거래소 시각 − 로컬 시계 {clock_skew:+.2f}초 > 임계 "
            f"{limits['clock_skew_seconds']:.1f}초 — 완성봉 유예 500ms가 무의미하고, "
            "부호가 뒤집히면 상위 Horizon 합성봉이 매 버킷 한 봉씩 잘린다(w32time 확인)"
        )

    # 이벤트로그 집계와 로그 속 faulthandler 덤프를 대조한다(2026-08-03 고도화 D). 덤프 자체는
    # 사고가 아니지만 **"크래시가 났는데 덤프가 없다"는 사고**다 — 그 상태로 5거래일을 보냈다.
    # 로그 디렉터리는 `log_paths`에서 역추론한다(호출측이 기본 경로를 그대로 쓰는 게 보통).
    forensics = collect_crash_forensics(
        day,
        log_dir=resolved_log_dir,
        native_crash_count=crashes.count,
        native_crashes_available=crashes.available,
    )
    breaches.extend(forensics.findings)

    # ---- 고도화 2: 못 잰 것을 한자리에 ----
    #
    # 2026-08-04에 크래시 집계가 정확히 이 형태로 사라졌다 — `available=False`가 리포트
    # 어딘가에 조용히 남았을 뿐이고, 그 결과 등록부가 매일 "판정 불가"라 영원히 안 끝났다.
    # "오늘 무엇을 모르는가"가 한 줄로 보여야 사람이 그걸 조치 대상으로 인식한다.
    unmeasured: list[str] = []
    if crashes.supported and not crashes.available:
        unmeasured.append("네이티브 크래시 집계")
    if clock_skew is None:
        unmeasured.append("시계 스큐(수집 세션의 ClockSkew 로그 없음)")
    if logs["delivery_latency"] is None:
        unmeasured.append(
            "회선 수신 지연 분포(TickDeliveryLatency 로그 없음 — 1분봉 시각 확정 승격 근거)"
        )
    if volume_check is None:
        unmeasured.append("공식 분봉 대비 거래량 대조(verify_archive_volume.py 미실행)")
    if not (vol_axis.get("horizons") if vol_axis else None):
        unmeasured.append("변동성 축 채점(run_vol_scorecard.py 미실행)")
    if not degenerate_features:
        unmeasured.append("피처 건강도(장 마감 FeatureHealth 로그 없음)")
    unmeasured.extend(f"호스트 위생 — {item}" for item in host.unmeasured)

    # ---- 고도화 2(2026-08-06): 적재 계열 전수 커버리지 ----
    #
    # 봉·틱 말고 **나머지 전부**를 본다. 판정 창의 시작을 첫 프로세스 기동으로 잡는 이유는
    # `series_coverage.session_window()` docstring 참고 — 재기동으로 늦게 뜬 공백을
    # `restarts`와 두 번 세지 않기 위해서다.
    # 계열 경로는 **`bar_dir`의 부모에서 파생**한다. 다섯 계열이 전부 같은 `data/` 아래
    # 형제라서(`data/bars` · `data/ticks` · `data/flow_intraday` · `data/option_chain`)
    # 한 인자로 묶이는 것이 실제 배치와 맞고, 무엇보다 **테스트가 자동으로 격리된다** —
    # 모듈 기본값을 쓰면 tmp 디렉터리로 만든 리포트가 저장소의 진짜 `data/`를 집어 든다.
    data_root = Path(bar_dir).parent
    coverage_window = series_coverage.session_window(
        day, start=_first_session_start(day, session_starts)
    )
    coverages = series_coverage.collect(
        day,
        symbol,
        window=coverage_window,
        flow_dir=flow_dir or data_root / "flow_intraday",
        option_chain_dir=option_chain_dir or data_root / "option_chain",
        tick_dir=tick_dir or data_root / "ticks",
    )
    series_findings = [f for item in coverages for f in series_coverage.findings_for(item)]
    breaches.extend(series_findings)

    # ---- P1-1·P1-2(2026-08-06): 관측 공백과 그 원인 ----
    #
    # UI는 구조화 로그를 안 내므로 기동 시각을 자기 로그에서 따로 뽑는다 — **관측 공백을
    # 재려면 UI야말로 봐야 하는 프로세스다**(사람이 장중에 보는 화면이 그것이고,
    # 2026-08-06에 21분간 사라졌는데 `ui_restarts`는 0이었다).
    starts_for_gaps = dict(session_starts)
    ui_starts = _read_ui_starts(day, resolved_log_dir)
    if len(ui_starts) > 1:
        starts_for_gaps["ui"] = ui_starts
    observation = observation_gaps.ObservationReport()
    observation.events, observation.events_available, observation.events_detail = (
        host_event_collector(day)
    )
    observation.gaps = observation_gaps.find_gaps(
        day,
        starts_by_process=starts_for_gaps,
        activity_by_process={name: result["activity_kst"] for name, result in per_process.items()},
        events=observation.events,
    )
    for gap in observation.gaps:
        if gap.minutes > limits["observation_gap_minutes"]:
            breaches.append(gap.describe())
    if not observation.events_available:
        unmeasured.append(f"관측 공백 원인(호스트 이벤트 — {observation.events_detail})")

    return IntegrityReport(
        date=day.isoformat(),
        symbol=symbol,
        instance_id=instance_id,
        bar_continuity=continuity,
        restarts=restarts,
        starts_by_process=starts_by_process,
        restarts_by_process=restarts_by_process,
        ui_restarts=ui_restarts,
        session_starts_kst=session_starts,
        nan_ratio_by_horizon=logs["nan_ratio_by_horizon"],
        log_level_counts=logs["level_counts"],
        tag_counts=logs["tag_counts"],
        circuit_breaker_events=logs["circuit_breaker_events"],
        data_flow_findings=data_flow_findings,
        horizon_findings=horizon_findings,
        late_bar_drops=late_bar_drops,
        clock_skew_seconds=clock_skew,
        delivery_latency=logs["delivery_latency"],
        session_git_shas=logs["session_git_shas"],
        degenerate_features=degenerate_features,
        volume_check=volume_check,
        vol_axis=vol_axis,
        host_health=host.to_dict(),
        unmeasured=unmeasured,
        flat_price_minutes=flat_minutes,
        pre_open_minutes=pre_open_minutes,
        market_findings=market_findings,
        native_crashes=crashes,
        crash_forensics=forensics,
        tick_rows=tick_rows,
        series_coverage=[item.to_dict() for item in coverages],
        series_findings=series_findings,
        observation_gaps=[gap.to_dict() for gap in observation.gaps],
        host_events=[event.to_dict() for event in observation.events],
        breaches=breaches,
    )


def _read_ui_starts(day: date, log_dir: Path) -> list[str]:
    """Streamlit UI 로그에서 기동 시각을 뽑는다 — 없으면 빈 목록.

    UI 로그는 구조화 로그가 아니라 `analyze_logs()`의 시야 밖이다(`ops/crash_dumps.py`가
    같은 이유로 UI 로그를 따로 읽는다). 실패해도 리포트를 막지 않는다.
    """
    path = log_dir / f"ui_{day:%Y%m%d}.log"
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return []
    return observation_gaps.parse_ui_starts(text)


def _infer_log_dir(log_paths: Mapping[str, Sequence[Path]]) -> Path:
    """포렌식이 볼 로그 디렉터리 — 호출측이 넘긴 로그 경로의 부모를 쓴다.

    `log_paths`에는 UI 로그가 없다(구조화 로그가 아니라서). 그런데 5거래일 크래시가 전부 UI
    프로세스였으므로 포렌식은 그 파일을 반드시 봐야 한다 — 그래서 파일 목록이 아니라
    **디렉터리**를 알아내 `ops/crash_dumps.py`가 직접 UI 로그를 찾게 한다."""
    for paths in log_paths.values():
        for path in paths:
            return path.parent
    return DEFAULT_LOG_DIR


def format_summary(report: IntegrityReport) -> str:
    """사람이 장 마감 후 30초 안에 훑을 수 있는 요약 — 상세는 JSON에 있다."""
    lines = [f"=== 일일 무결성 리포트 {report.date} ({report.symbol}) ==="]
    for continuity in report.bar_continuity:
        span = (
            f"{continuity.first_bar_kst}~{continuity.last_bar_kst}"
            if continuity.first_bar_kst
            else "데이터 없음"
        )
        lines.append(
            f"  봉 {continuity.horizon}: {continuity.rows}개 {span} · "
            f"결손 {continuity.missing_minutes}분(최장 {continuity.longest_gap_minutes}분)"
        )
    for name, starts in sorted(report.starts_by_process.items()):
        # 기동과 재기동을 나눠 적는다 — 예전엔 "재기동 1회"가 정상일에도 찍혀서, 사람이
        # 매일 그 줄을 무시하는 법을 배우고 있었다(진짜 재기동이 묻힌다).
        restarts = report.restarts_by_process.get(name, 0)
        lines.append(
            f"  {name} 기동: {starts}회 · 재기동 {restarts}회 "
            f"{report.session_starts_kst.get(name, [])}"
        )
    lines.append(f"  Command Center UI 자동 재기동: {report.ui_restarts}회")

    if report.nan_ratio_by_horizon:
        parts = [
            f"{horizon} 중앙 {stat['median']:.2f}/최종 {stat['last']:.2f}"
            for horizon, stat in report.nan_ratio_by_horizon.items()
        ]
        lines.append("  피처 NaN 비율: " + " · ".join(parts))

    if report.clock_skew_seconds is not None:
        lines.append(f"  시계 스큐(거래소−로컬): {report.clock_skew_seconds:+.2f}초")
    else:
        lines.append("  시계 스큐(거래소−로컬): 미측정")

    crashes = report.native_crashes
    if crashes.available:
        lines.append(f"  네이티브 크래시: {crashes.count}건")
    elif not crashes.supported:
        lines.append("  네이티브 크래시: 집계 불가(Windows 전용)")
    else:
        # 이 갈래가 2026-08-04에 조용히 지나간 자리다 — 이제 임계 초과로도 함께 뜬다.
        reason = crashes.details[0] if crashes.details else ""
        lines.append(f"  네이티브 크래시: 집계 실패 — {reason}")
    # 덤프가 있으면 죽은 지점의 파이썬 프레임을 바로 보여준다 — 이 줄이 없어서 5거래일 동안
    # 정황만으로 원인을 추정했다(`ops/crash_dumps.py`).
    lines.extend(format_dump_lines(report.crash_forensics))

    levels = report.log_level_counts
    lines.append(
        f"  로그: CRITICAL {levels.get('CRITICAL', 0)} · ERROR {levels.get('ERROR', 0)} · "
        f"WARNING {levels.get('WARNING', 0)}"
    )
    if report.circuit_breaker_events:
        lines.append(f"  CB 이벤트: {report.circuit_breaker_events}")
    if report.flat_price_minutes or report.pre_open_minutes:
        lines.append(
            f"  가격 고정(o=h=l=c): {report.flat_price_minutes}분 · "
            f"장전 봉: {report.pre_open_minutes}개"
        )
    for finding in report.market_findings:
        lines.append(f"  ⚠ 시장 상태: {finding}")
    for finding in report.data_flow_findings:
        lines.append(f"  ⚠ 탐지·복구 불일치: {finding}")
    for finding in report.horizon_findings:
        lines.append(f"  ⚠ Horizon 정합: {finding}")
    # 0건도 찍는다 — 이 줄이 없으면 "검사했는데 0건"과 "이 리포트에 그 축이 없다"가
    # 구분되지 않는다(L18, 고도화 2가 `unmeasured`로 세운 것과 같은 원칙).
    lines.append(
        f"  버킷 유실(늦은 봉·미완 확정): {report.late_bar_drops}건"
        + (" ✅" if report.late_bar_drops == 0 else " ⚠")
    )
    # 적재 계열 커버리지 (2026-08-06 고도화 2) — **정상인 계열도 전부 찍는다.**
    # 2026-08-06에 리포트가 조용했던 이유는 이 계열들을 "정상"으로 판정해서가 아니라
    # 아예 안 봐서였다. 목록 자체가 "무엇을 보고 있는가"의 증거다.
    if report.series_coverage:
        lines.append(f"  적재 계열 커버리지 ({len(report.series_coverage)}개):")
        lines.extend(
            series_coverage.summarize(
                [series_coverage.SeriesCoverage(**entry) for entry in report.series_coverage]
            )
        )
    for finding in report.series_findings:
        lines.append(f"  ⚠ 적재 공백: {finding}")
    # 관측 공백 (2026-08-06 P1-1·P1-2) — **공백이 없는 날도 한 줄 남긴다.**
    lines.extend(
        observation_gaps.summarize(
            observation_gaps.ObservationReport(
                events=[observation_gaps.HostEvent(**e) for e in report.host_events],
                gaps=[observation_gaps.ObservationGap(**g) for g in report.observation_gaps],
                events_available=True,
            )
        )
    )
    if report.host_events:
        lines.append("  호스트 생명주기:")
        lines.extend(
            f"    {observation_gaps.describe_event(observation_gaps.HostEvent(**e))}"
            for e in report.host_events
        )
    if report.delivery_latency is not None:
        latency = report.delivery_latency
        lines.append(
            f"  회선 수신 지연 초과분: p50 {latency.get('p50', 0):.3f}s · "
            f"p90 {latency.get('p90', 0):.3f}s · p99 {latency.get('p99', 0):.3f}s · "
            f"최대 {latency.get('max', 0):.3f}s (표본 {int(latency.get('samples', 0)):,}건)"
        )
    if report.session_git_shas:
        lines.append(f"  수집 커밋: {', '.join(report.session_git_shas)}")

    if report.volume_check is not None:
        ratio = report.volume_check.get("ratio")
        lines.append(
            "  공식 분봉 대비 거래량: "
            + ("측정 불가" if ratio is None else f"{ratio:.3f}")
            + (" ✅" if report.volume_check.get("ok") else " ⚠")
        )
    for horizon, entry in sorted((report.vol_axis.get("horizons") or {}).items()):
        beats = entry.get("beats_baseline") or []
        baseline_ic = entry.get("baseline_ic")
        lines.append(
            f"  변동성 축 {horizon}: 표본 {entry.get('samples')} · 기준선 IC "
            + ("미측정" if baseline_ic is None else f"{baseline_ic:+.3f}")
            + f" · 기준선 초과 {len(beats)}개"
            + (f" {beats}" if beats else "")
        )
    host = report.host_health.get("checks") or []
    if host:
        lines.append("  호스트: " + " · ".join(f"{c['name']}={c['detail']}" for c in host))

    # **못 잰 것을 임계 초과 바로 앞에 둔다** — 사람이 "깨끗한 날"이라고 읽기 전에
    # "무엇을 모르는 날인지"를 먼저 보게 하는 것이 이 블록의 목적이다(고도화 2).
    if report.unmeasured:
        lines.append("  ❓ 미측정:")
        lines.extend(f"    - {item}" for item in report.unmeasured)

    if report.breaches:
        lines.append("  ⚠ 임계 초과:")
        lines.extend(f"    - {breach}" for breach in report.breaches)
    else:
        lines.append("  ✅ 임계 초과 없음")
    return "\n".join(lines)


# ---------------------------------------------------------------- 산출 (CLI·장후 절차 공용)

DEFAULT_LOG_DIR = Path("logs")
DEFAULT_BAR_DIR = Path("data") / "bars"
DEFAULT_TICK_DIR = Path("data") / "ticks"  # `scripts/run_l1_daily.py`의 `_TICK_DIR`와 같은 값


def log_paths_for(day: date, log_dir: Path = DEFAULT_LOG_DIR) -> dict[str, list[Path]]:
    """프로세스 이름 → 그날의 로그 파일. 실제 로그는 `.bat`가 stdout을 날짜별 파일로 tee한
    것뿐이다(`scripts/agenda.py`가 2026-07-27에 정정한 것과 같은 사실)."""
    stamp = day.strftime("%Y%m%d")
    return {
        "l1_daily": [log_dir / f"l1_daily_{stamp}.log"],
        "g2_paper": [log_dir / f"g2_daily_{stamp}.log"],
    }


def generate_and_write(
    *,
    day: date,
    symbol: str,
    instance_id: str,
    bar_dir: Path = DEFAULT_BAR_DIR,
    log_dir: Path = DEFAULT_LOG_DIR,
) -> IntegrityReport:
    """리포트를 만들어 `logs/daily_integrity_YYYYMMDD.json`에 쓰고 로그에 남긴다.

    CLI(`scripts/daily_integrity_report.py`)와 장후 종료 절차(`run_l1_daily.py`)가 같은
    함수를 쓴다 — 두 경로가 갈리면 "손으로 돌린 리포트와 자동 리포트가 다르다"는 최악의
    형태가 된다.
    """
    from messiah.core import logging as mlog  # 순환 방지용 지역 임포트 아님 — 로깅만 필요

    report = build_report(
        day=day,
        symbol=symbol,
        instance_id=instance_id,
        bar_dir=bar_dir,
        log_paths=log_paths_for(day, log_dir),
    )
    out_path = log_dir / f"daily_integrity_{day.strftime('%Y%m%d')}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(format_summary(report), flush=True)
    mlog.log(
        "IntegrityReportGenerated",
        "일일 무결성 리포트 산출",
        date=report.date,
        symbol=symbol,
        restarts_by_process=report.restarts_by_process,
        breaches=len(report.breaches),
        path=str(out_path),
    )
    for breach in report.breaches:
        mlog.log("IntegrityThresholdBreached", breach, date=report.date, symbol=symbol)

    _report_fix_verifications(day, log_dir)
    return report


def _report_fix_verifications(day: date, log_dir: Path) -> None:
    """등록된 수정들이 실제로 들었는지 채점한다 (고도화 B, 2026-08-03).

    **오늘 리포트를 쓴 뒤에** 부른다 — 채점은 오늘 것을 포함한 이력 전체를 읽으므로
    `build_report()` 안에서 하면 자기 자신을 읽어야 하는 순환이 된다
    (`ops/fix_verification.py` 모듈 docstring).

    채점 실패가 장후 절차를 막지 않는다 — 등록부 오타로 그날 종료가 멈추면 본말전도다.
    다만 조용히 넘기지는 않는다(L18): 실패 사실을 화면과 로그에 남긴다.
    """
    from messiah.core import logging as mlog
    from messiah.ops import fix_verification as fv

    try:
        verdicts = fv.run(today=day, log_dir=log_dir)
    except Exception as exc:  # noqa: BLE001 — 장후 절차를 막지 않는다
        print(f"수정 유효성 검증 실패(장후 절차는 계속): {exc}", flush=True)
        return

    print("=== 수정 유효성 검증 ===", flush=True)
    for line in fv.format_verdicts(verdicts):
        print(line, flush=True)

    tags = {
        fv.VerificationStatus.VERIFIED: "FixVerificationPassed",
        fv.VerificationStatus.RECURRED: "FixVerificationRecurred",
        fv.VerificationStatus.OVERDUE: "FixVerificationOverdue",
        fv.VerificationStatus.STALLED: "FixVerificationStalled",
        fv.VerificationStatus.PREMISE_BROKEN: "FixVerificationPremiseBroken",
    }
    for verdict in verdicts:
        tag = tags.get(verdict.status)
        if tag is None:
            continue  # 검증 대기는 정상 진행 상태 — 매일 로그를 채울 이유가 없다
        mlog.log(
            tag,
            f"{verdict.id}: {verdict.detail}",
            date=day.isoformat(),
            fix_id=verdict.id,
            status=verdict.status,
        )
