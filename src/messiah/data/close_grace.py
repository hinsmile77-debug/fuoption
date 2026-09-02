"""완성봉 「늦은 틱 유예」의 단일 정본 — Horizon → 유예(ms) (2026-09-01 F-82).

## 왜 별도 모듈인가

이 정책은 2026-08-25 F-43 이후 `features/engine._grace_ms()` 안에 살았고, 거기서
`data/bar_composer`를 **함수 안에서** 들여왔다 — 그 모듈이 `ParquetArchiver`(polars)를
끌고 오기 때문이다. 그 회피는 엔진에서는 통했지만, 2026-09-01 F-82가 **화면**에도 같은
값을 요구하면서 깨졌다: `ui/app.py`는 polars를 이 프로세스에 들이지 않기로 한 자리다
(모듈 docstring — 2026-08-03 네이티브 크래시 5거래일 연속). 무거운 쪽을 안 들이면서
같은 값을 쓰려면 **정책이 무거운 모듈 밖에** 있어야 한다.

숫자를 화면에 다시 적는 선택지는 없다. 유예는 「넘으면 자료가 빠질 수 있는 경계」라
두 곳이 갈라지면 한쪽이 조용히 틀린 시각을 말하게 된다(L18).

## 값의 근거는 여기가 아니라 상류에 있다

1분봉은 정규화기가 늦은 틱을 받아 주는 창(`MINUTE_CLOSE_GRACE_SECONDS`), 상위 Horizon은
합성기가 마지막 구성 1분봉을 기다리는 상한(`MAX_CONSTITUENT_WAIT_SECONDS`)이 경계다.
후자는 2026-09-01에 `bar_composer`에서 이 모듈로 **옮겨 왔다**(값·근거 그대로) — 옮긴
이유는 위 무게 문제 하나뿐이고, `bar_composer`는 여기서 도로 읽는다.
"""

from __future__ import annotations

from messiah.core.messages import Horizon
from messiah.data.normalizer import MINUTE_CLOSE_GRACE_SECONDS

# 겹④ — 그 버킷의 마지막 1분봉이 도착하기를 기다리는 상한.
#
# 2026-08-05 실측 1분봉 발행 지연: 중앙값 0.655초 · p75 0.966초 · p90 1.62초 · 최대 7.96초
# (`data/bar_composer` 모듈 docstring "스큐를 고쳤더니 드러난 것"). 5초면 그날 표본의
# p99 위쪽을 덮는다.
#
# 상한을 이 크기로 둬도 안전한 이유: 가장 짧은 합성 Horizon이 3분(180초)이라 5초는 그 2.8%다.
# 그리고 대기의 최악은 "합성봉이 몇 초 늦게 나간다"인 반면, 안 기다린 최악은 **조용한 데이터
# 손상**이다 — 2026-08-05에 실제로 상위 봉의 3~17%가 그렇게 사라졌다. 비대칭이 명확하다.
MAX_CONSTITUENT_WAIT_SECONDS = 5.0


def close_grace_ms(horizon: Horizon) -> float:
    """그 Horizon의 완성봉 유예(ms) — **「넘으면 손실인가」의 경계**다.

    `features/engine._PUBLISH_SLA_MS`(1,000ms)와 다른 질문이다. 저쪽은 「느린가」를 묻는
    채점 기준이고 이쪽은 경계다 — 2026-08-25에 1분봉 발행 오프셋 최대가 3,119.7ms였는데
    그날 자료 손실은 0건이었다. 두 값이 한 축에 섞여 있으면 그 사실을 말할 수 없다.
    """
    if horizon is Horizon.M1:
        return MINUTE_CLOSE_GRACE_SECONDS * 1000.0
    return MAX_CONSTITUENT_WAIT_SECONDS * 1000.0
