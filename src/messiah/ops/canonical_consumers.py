"""정본을 **안 쓰는 소비자**를 찾는 검사 (2026-08-07 고도화 2).

## 왜 생겼나

2026-08-07에 목위클리 옵션체인이 하루 종일 비었다. 조사에 반나절이 들었고, 결론은
"KRX 규정상 미상장 — 정상"이었다. 그런데 **그 규칙은 이미 이 저장소에 있었다**:

    core/event_calendar.py  has_thursday_weekly()   ← 2026-07-10 마흐디 실측 이식, 7월부터 존재
    tests/features/test_ev_core.py:233              ← `2026-08-13 → False`를 못박아 둔 테스트

소비자가 `features/ev_core.py` 하나뿐이었다. 옵션체인 폴러도, 수집 계획도, 무결성 리포트도
그 함수의 존재를 몰랐다. 그래서 폴러는 22번 틀린 처방("마스터파일 갱신 필요")을 찍었고,
조사자는 로그·마스터파일·아카이브를 다 뒤지면서 `event_calendar.py`는 열지 않았다 —
**수집 경로가 그 모듈을 참조하지 않으니 추적선에 안 걸렸다.**

## 이 프로젝트가 반복한 실패의 네 번째 형태

    InvestorFlowPoller (7개월)  — 만들었는데 결선을 안 했다
    OptionChainPoller  (수개월)  — 만들었는데 결선을 안 했다
    FL 피처                      — 만들었는데 모델에 안 닿았다
    has_thursday_weekly (1개월)  — **알고 있는데 안 물어봤다**   ← 2026-08-07

앞의 셋은 "만들었으니 되고 있겠지"였고, 넷째는 "알고 있으니 쓰고 있겠지"였다. 같은 병이다.
앞의 셋은 `ops/series_coverage.py`(적재가 실제로 되는가)가 잡게 됐다. 넷째를 잡는 것이
이 모듈이다.

## 어떻게 검사하나 — import 그래프가 아니라 **이름 사용**

`from messiah.core import event_calendar` 뒤에 `cal.thursday_weekly_listed(...)`를 부르는
형태가 흔해서, import 문만 봐서는 "무엇을 쓰는가"를 알 수 없다. 그래서 소스 텍스트에서
**심볼 이름의 등장**을 센다. 거칠지만 이 검사가 답해야 하는 질문("이 파일이 그 정본을
언급이라도 하는가")에는 정확히 맞고, AST를 돌리는 것보다 훨씬 덜 깨진다.

## 등록은 손으로 한다 — 그게 이 검사의 값이다

정본과 그 기대 소비자를 자동으로 알아낼 방법은 없다. 사람이 "이 규칙은 여기서도 물어야
한다"고 적는 행위 자체가 설계 판단이고, 그 판단을 적어 두는 자리가 없어서 2026-08-07이
났다. `configs/pending_verifications.yaml`이 "고쳤다"는 판단을 사람 기억에서 꺼내
파일로 옮긴 것과 같은 규율이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# 저장소 루트 — 이 파일이 `src/messiah/ops/`에 있으므로 3단계 위.
_REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Canon:
    """정본 심볼 하나와 그것을 물어야 하는 곳들."""

    symbol: str
    home: str
    expected_consumers: tuple[str, ...]
    why: str

    def missing(self, root: Path | None = None) -> list[str]:
        """기대 소비자 중 이 심볼을 언급조차 안 하는 파일 목록."""
        base = root or _REPO_ROOT
        out: list[str] = []
        for rel in self.expected_consumers:
            path = base / rel
            if not path.exists():
                out.append(f"{rel} (파일 없음)")
                continue
            if self.symbol not in path.read_text(encoding="utf-8", errors="replace"):
                out.append(rel)
        return out


CANONICAL: tuple[Canon, ...] = (
    Canon(
        symbol="thursday_weekly_listed",
        home="src/messiah/core/event_calendar.py",
        expected_consumers=(
            # 그날 계약을 만드는 자리 — 여기서 안 물으면 나머지 전부가 못 묻는다.
            "src/messiah/ops/series_expectation.py",
        ),
        why=(
            "목위클리 미상장(먼슬리 만기 주) 판정. 2026-08-07에 이 정본이 있는데도 "
            "수집 경로가 안 물어서 오탐 22건 + 사고 오판이 났다."
        ),
    ),
    Canon(
        symbol="series_expectation",
        home="src/messiah/ops/series_expectation.py",
        expected_consumers=(
            # 장중 — 폴러에 술어를 주입하고 기동 로그에 계약을 찍는다.
            "scripts/run_l1_daily.py",
            # 장후 — 커버리지 판정에 계약을 실어 오탐/양방향 단언을 가른다.
            "src/messiah/ops/integrity_report.py",
        ),
        why=(
            "그날 적재 계열 계약. 장중(수집)과 장후(리포트)가 **같은 계약**을 봐야 한다 — "
            "한쪽만 알면 화면과 리포트가 서로 다른 말을 한다."
        ),
    ),
    Canon(
        symbol="monthly_expiry",
        home="src/messiah/core/event_calendar.py",
        expected_consumers=(
            # 2026-08-04에 사본 두 벌을 여기로 합쳤다 — 되돌아가지 않는지 지킨다.
            "src/messiah/data/backfill.py",
            # 만기 라벨 → 날짜 파싱도 규칙을 따로 만들지 않고 이 정본을 부른다.
            "src/messiah/broker/kis/symbol_master.py",
        ),
        why=(
            "정규월물 만기(둘째 목요일, 휴장 보정). 2026-08-04에 사본 두 벌이 "
            "어긋나 있던 것을 하나로 합쳤다 — 세 번째 사본이 생기지 않게 한다."
        ),
    ),
)


def findings(root: Path | None = None) -> list[str]:
    """정본을 안 쓰는 기대 소비자 목록 — 빈 목록이면 정상.

    반환 문장에 **왜 그 소비자가 물어야 하는지**를 같이 담는다. 파일 이름만 나열하면
    받는 사람이 "이걸 왜 여기서 써야 하지"부터 다시 조사해야 하고, 그 조사가 귀찮으면
    검사를 지우는 쪽으로 간다.
    """
    out: list[str] = []
    for canon in CANONICAL:
        gaps = canon.missing(root)
        if gaps:
            out.append(
                f"{canon.symbol}({canon.home}): {', '.join(gaps)}에서 안 쓰인다 — {canon.why}"
            )
    return out
