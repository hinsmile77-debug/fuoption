"""같은 사실이 같은 말로 반복될 때 로그를 접는다 (2026-08-13 G-2 · 2026-09-10 반입).

## 무슨 일이 있었나

2026-08-13과 2026-09-10, 옵션체인 폴러가 `OptionChainCalendarViolation`(ERROR)을 하루
84건 냈다. 5분마다 도는 폴러가 **같은 사유**를 매 사이클 다시 적은 것이다. 원인은 하나인데
줄은 84개라, 장후 점검의 증거 다이제스트에서 ERROR 집계가 사건 수가 아니라 폴링 횟수를
센다 — 그날 실제로 무엇이 몇 건이었는지 사람이 로그를 직접 세어야 했다.

## 접는 것이지 낮추는 것이 아니다

**첫 1건은 원래 레벨 그대로 나간다.** 그 뒤 같은 `(태그, 페이로드)`가 다시 오면 창
(`SUMMARY_INTERVAL_SECONDS`) 동안 삼키고, 창이 끝날 때 「n회 반복」 요약 1건을 **같은
태그·같은 레벨로** 낸다. 사실이 사라지는 구간이 없다 — 삼킨 건수는 반드시 다음 요약에
실려 나온다(금지계명 12「조용한 폴백 금지」의 취지).

페이로드가 바뀌면(예: `nearest` 라벨이 바뀌면) 그 즉시 새 사실로 보고 원래 레벨로 복귀한다.

## 왜 태그를 새로 파지 않는가

`OptionChainCalendarViolationRepeated` 같은 새 태그를 만들면 같은 사실이 두 태그로 갈려
집계가 어긋난다 — `logging._LEVEL_ESCALATABLE` 주석이 적어 둔 그 함정이고, R6(태그 1개 =
심각도 1개)의 취지이기도 하다. 요약은 같은 태그로 내고 `folded`·`repeat_count`·`first_at`
필드로 구분한다.

## 왜 허용 태그를 명시하는가

접기는 **조용해지는 방향**의 변경이라 전 태그에 자동 적용하면 어느 계기가 언제 조용해졌는지
아무도 모른다. 그래서 이 목록에 적힌 태그만 접는다 — 늘릴 때마다 근거를 여기 남긴다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

#: 접기를 적용하는 태그. 늘릴 때는 「같은 사유가 주기적으로 재발행되는가」를 먼저 확인한다.
#: - `OptionChainCalendarViolation` (2026-08-13 G-2): 5분 폴링마다 같은 사유 재발행, 하루 84건.
REPEAT_FOLDED_TAGS: frozenset[str] = frozenset({"OptionChainCalendarViolation"})

#: 요약 주기. 폴링 간격(5분)의 배수여야 「한 창에 여러 건」이 성립한다.
SUMMARY_INTERVAL_SECONDS = 900.0


def _payload_key(fields: dict[str, object]) -> str:
    """페이로드의 동일성 키 — 값이 하나라도 다르면 다른 사실로 본다.

    직렬화 불가한 값이 섞여도 접기가 예외를 내면 안 된다(계기가 발행을 막으면 본말전도다).
    그럴 때는 `repr`로 내려간다 — 키의 용도는 동일성 비교뿐이라 그것으로 충분하다.
    """
    try:
        return json.dumps(fields, sort_keys=True, default=repr, ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(sorted((k, repr(v)) for k, v in fields.items()))


@dataclass
class _Run:
    """한 `(태그, 페이로드)`의 진행 중인 반복 구간."""

    first_at: datetime
    window_opened_at: datetime
    suppressed: int = 0


@dataclass(frozen=True)
class Folded:
    """삼킨 건수를 실어 내보내는 요약 1건 — 호출측이 메시지에 덧붙인다."""

    repeat_count: int
    first_at: datetime


class RepeatFolder:
    """`(태그, 페이로드)`별 반복 접기. 상태는 프로세스 안에만 있다(R12 무상태 규율 유지)."""

    def __init__(self, *, interval_seconds: float = SUMMARY_INTERVAL_SECONDS) -> None:
        self._interval = interval_seconds
        self._runs: dict[tuple[str, str], _Run] = {}

    def reset(self) -> None:
        """세션 경계에서 비운다 — 어제의 반복이 오늘 첫 건을 삼키면 안 된다."""
        self._runs.clear()

    def admit(
        self, tag: str, fields: dict[str, object], moment: datetime
    ) -> tuple[bool, Folded | None]:
        """`(내보낼까, 요약)`.

        - 접지 않는 태그 · 첫 건 · 페이로드가 바뀐 건 → `(True, None)` (원래 레벨 그대로)
        - 창 안의 반복 → `(False, None)` (삼킨다. 건수는 다음 요약에 실린다)
        - 창을 넘긴 반복 → `(True, Folded(...))` (같은 태그·같은 레벨로 요약 1건)
        """
        if tag not in REPEAT_FOLDED_TAGS:
            return True, None

        key = (tag, _payload_key(fields))
        run = self._runs.get(key)
        if run is None:
            # 페이로드가 바뀌었으면 이전 구간은 버린다 — 새 사실이므로 즉시 원래 레벨이다.
            for stale in [k for k in self._runs if k[0] == tag]:
                del self._runs[stale]
            self._runs[key] = _Run(first_at=moment, window_opened_at=moment)
            return True, None

        elapsed = (moment - run.window_opened_at).total_seconds()
        if elapsed < self._interval:
            run.suppressed += 1
            return False, None

        folded = Folded(repeat_count=run.suppressed + 1, first_at=run.first_at)
        run.window_opened_at = moment
        run.suppressed = 0
        return True, folded
