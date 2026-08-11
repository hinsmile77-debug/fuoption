"""feature_set 정본 — 이름 하나가 벡터 모양 하나를 정한다 (2026-08-04 신설, F0-1).

## 왜 필요했나

FL(수급)이 붙으면서 `FeatureEngine`이 `if self._flow is not None`으로 **벡터 모양을 런타임에
바꾸기** 시작했다. 카테고리가 MS·OP·RG·EV까지 늘면 그 분기는 2^5 조합이 되고, `feature_set`
문자열 하나로는 지금 어느 조합인지 알 방법이 없다 — 금지 6계명("피처 불일치 침묵 금지")이
그 구조에서는 못 버틴다. 학습 때 130개로 배운 모델이 추론 때 121개를 받아도 이름만 같으면
아무도 못 알아챈다.

그래서 **이름 → 카테고리 → 정확한 피처 이름 목록**을 이 모듈 한 곳에만 적는다. 학습·추론·
백테스트·라이브가 전부 여기를 통해 같은 답을 얻는다.

## 이름 목록을 손으로 적지 않는다

`CATEGORIES`는 계산기 모듈을 **참조**할 뿐 목록을 복사하지 않는다. 복사하면 계산기에 피처를
추가한 사람이 이 파일을 안 고쳐 목록이 조용히 어긋나고, 그게 정확히 `px_ema_cross_60`이
프로덕션에서 죽은 채 학습되던 사고의 형태다(2026-08-04).

참조는 **호출 시점에** 읽는다(import 시점에 스냅샷하지 않는다). 그래야 계산기 목록을
갈아끼우는 테스트가 스펙과 엔진에서 같은 것을 본다 — 스냅샷하면 둘이 갈려 테스트가
검증하려던 것과 다른 것을 검증하게 된다.

## 사이드카를 요구하는 카테고리

FL처럼 **봉 밖의 상태**가 필요한 카테고리는 `sidecar` 키를 선언한다(`features/sidecar.py`).
엔진은 스펙이 요구하는 사이드카가 없으면 **생성 시점에 거부**한다. 종전에는 주입을 잊으면
FL 9개가 조용히 사라졌고, 실제로 `FeatureEngine` 생성처 7곳 전부가 그 상태였다.

## 미등록 이름

운영 설정(`configs/instance.yaml`)의 `feature_set`은 `core/config.py` 검증기가 **기동 시점에**
등록 여부를 확인한다 — 오타가 조용히 기저 벡터로 떨어지지 않게. 여기 `resolve()`가 미등록
이름에 기저 카테고리를 주는 것은 테스트·스모크가 임의 라벨("v-test")을 쓰기 때문이고, 그
경우 `FeatureSetUnregistered`를 남긴다(조용한 폴백 금지, L18).
"""

from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType
from typing import Callable, Iterable, Sequence

from messiah.core import logging as mlog
from messiah.features import ev_core, fl_core, px_core, vl_core

# 계산기 모듈이 노출하는 레지스트리 속성 이름 — 카테고리마다 같은 규약을 쓴다.
_WINDOWED_ATTR = "WINDOWED_FEATURES"
_STATEFUL_ATTR = "STATEFUL_FEATURES"


@dataclass(frozen=True, slots=True)
class CategorySpec:
    """카테고리 1개 = 계산기 모듈 1개 + (필요하면) 사이드카 1개.

    `module`의 레지스트리를 **속성 이름으로** 들고 있다가 필요할 때 읽는다 — 값을 복사해
    두면 계산기 쪽 변경이 여기 반영되지 않는다(모듈 docstring "호출 시점에 읽는다").
    """

    key: str
    module: ModuleType
    # 봉 밖 상태가 필요한 카테고리만 채운다. `sidecar`는 엔진에 넘기는 키 이름,
    # `sidecar_attr`는 그 모듈에서 (이름, 계산기) 목록을 들고 있는 속성 이름.
    sidecar: str | None = None
    sidecar_attr: str | None = None

    @property
    def windowed(self) -> Sequence[tuple[str, Callable, tuple[int, ...]]]:
        return getattr(self.module, _WINDOWED_ATTR, ())

    @property
    def stateful(self) -> Sequence[tuple[str, Callable]]:
        return getattr(self.module, _STATEFUL_ATTR, ())

    @property
    def sidecar_features(self) -> Sequence[tuple[str, Callable]]:
        if self.sidecar is None or self.sidecar_attr is None:
            return ()
        return getattr(self.module, self.sidecar_attr, ())

    @property
    def feature_names(self) -> tuple[str, ...]:
        """이 카테고리가 내는 **정확한** 피처 이름 — 엔진이 만드는 키와 같은 규칙으로 전개."""
        names: list[str] = []
        for name, _fn, windows in self.windowed:
            names.extend(f"{name}_{window}" for window in windows)
        names.extend(name for name, _fn in self.stateful)
        names.extend(name for name, _fn in self.sidecar_features)
        return tuple(names)


# 등록된 카테고리 — 신규 카테고리(MS/OP/RG/EV)는 여기 한 줄만 늘어난다.
CATEGORIES: dict[str, CategorySpec] = {
    "PX": CategorySpec("PX", px_core),
    "VL": CategorySpec("VL", vl_core),
    "FL": CategorySpec("FL", fl_core, sidecar="flow", sidecar_attr="FLOW_FEATURES"),
    # EV의 사이드카는 **참조** 데이터(KRX 캘린더)라 FL과 미래참조 규율이 다르다 —
    # `features/sidecar.py`의 "두 종류" 참고.
    "EV": CategorySpec("EV", ev_core, sidecar="calendar", sidecar_attr="CALENDAR_FEATURES"),
}

# 사이드카가 없어도 되는 기저 카테고리 — 미등록 `feature_set`이 떨어지는 자리이기도 하다.
BASE_CATEGORIES: tuple[str, ...] = ("PX", "VL")

# feature_set 이름 → 활성 카테고리. **이름은 한 번 정하면 바꾸지 않는다** — 저장된 모델
# 번들이 이 문자열로 자기 입력 모양을 주장하고 있고, 의미를 바꾸면 그 주장이 거짓이 된다.
FEATURE_SETS: dict[str, tuple[str, ...]] = {
    # 현행 프로덕션(121개). PX 82 + VL 39 — 전부 완성봉 OHLCV 파생.
    "v2026.07": ("PX", "VL"),
    # FL 결선판(130개). `flow` 사이드카(`data/investor_flow_history.FlowHistory`) 필수.
    "v2026.08-fl": ("PX", "VL", "FL"),
    # EV 결선판(137개, F1). `calendar` 사이드카(`core/event_calendar.EventCalendar`) 필수.
    # **이력 전체에 소급 계산되는 유일한 카테고리**라 지금 있는 163거래일로 바로 A/B가 된다.
    "v2026.08-ev": ("PX", "VL", "EV"),
    # 둘 다. FL 사이드카가 일별 KOSPI 현물 수급뿐이라(파생 장중은 2026-08-05부터 누적) 지금은
    # 실익이 작지만, 조합을 이름으로 못 부르면 A/B 자체를 못 돌린다.
    "v2026.08-fl-ev": ("PX", "VL", "FL", "EV"),
}


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    """`feature_set` 이름 1개의 해석 결과. 이름 목록은 **파생값**이라 속성으로 계산한다
    (튜플로 얼려 두면 계산기 변경과 갈린다 — 모듈 docstring)."""

    name: str
    categories: tuple[str, ...]
    registered: bool

    @property
    def category_specs(self) -> tuple[CategorySpec, ...]:
        return tuple(CATEGORIES[key] for key in self.categories)

    @property
    def feature_names(self) -> tuple[str, ...]:
        """전 카테고리의 피처 이름을 **정렬해서** — `models/trainer.build_training_data()`가
        `sorted(vector.values)`로 학습 열 순서를 정하므로 같은 순서여야 한다.

        같은 이름이 두 카테고리에서 나오면 그건 설계 오류다(뒤엣것이 앞엣것을 덮어써
        조용히 하나가 사라진다) — `validate_registry()`가 잡는다.
        """
        names: list[str] = []
        for spec in self.category_specs:
            names.extend(spec.feature_names)
        return tuple(sorted(names))

    @property
    def required_sidecars(self) -> tuple[str, ...]:
        return tuple(s.sidecar for s in self.category_specs if s.sidecar is not None)

    def __len__(self) -> int:
        return len(self.feature_names)

    def describe(self) -> str:
        """기동 로그 한 줄 — **"무슨 모양으로 떴는가"를 첫 발행 전에 말한다** (2026-08-11 F-1).

        2026-08-10 커밋 ④-a가 운영 `feature_set`을 `v2026.08-ev`(137개)로 올리면서 다음 날
        아침의 채점 항목을 "기동 로그에 피처 수 137이 찍히는지 확인"으로 적었는데, **그 줄을
        찍는 코드가 어디에도 없었다.** 기동 로그가 말하던 것은 웜스타트 봉 수뿐이었고
        `feature_set` 이름은 첫 `FeaturePublish`(DEBUG)까지 기다려야 나왔다 — 08-11 아침에
        그 항목을 확인할 방법이 실제로 없었다.

        여기 두는 이유는 카테고리·개수·사이드카가 **전부 이 객체의 파생값**이기 때문이다.
        호출처가 각자 문자열을 조립하면 그게 곧 두 번째 사본이고, `sidecar.build()`가 여섯
        소비자 중 셋에서 빠져 엿새를 잃은 것과 같은 형태로 어긋난다(커밋 ④-a).

        미등록 이름이면 그 사실을 함께 말한다 — `resolve()`가 조용히 기저 카테고리로
        떨어뜨리므로(모듈 docstring "미등록 이름"), 오타난 운영 설정이 "PX·VL 121개로 정상
        기동"처럼 보이는 것을 막는다.
        """
        breakdown = " · ".join(f"{s.key} {len(s.feature_names)}" for s in self.category_specs)
        sidecars = list(self.required_sidecars)
        tail = "" if self.registered else "  ⚠ 미등록 이름 — 기저 카테고리로 해석됨"
        return (
            f"피처셋 {self.name} — {len(self)}개 ({breakdown}) · "
            f"사이드카 {sidecars if sidecars else '없음'}{tail}"
        )


def resolve(feature_set: str) -> FeatureSpec:
    """`feature_set` 이름 → `FeatureSpec`.

    미등록 이름은 기저 카테고리(`BASE_CATEGORIES`)로 해석하고 `FeatureSetUnregistered`를
    남긴다 — 테스트·스모크가 임의 라벨을 쓰기 때문이며, 운영 설정은 `core/config.py`
    검증기가 기동 시점에 먼저 거부한다(모듈 docstring "미등록 이름").
    """
    categories = FEATURE_SETS.get(feature_set)
    if categories is not None:
        return FeatureSpec(feature_set, categories, registered=True)

    mlog.log(
        "FeatureSetUnregistered",
        f"미등록 feature_set '{feature_set}' — 기저 카테고리 {BASE_CATEGORIES}로 해석"
        "(운영 설정이면 configs/instance.yaml의 오타를 의심할 것)",
        feature_set=feature_set,
        categories=list(BASE_CATEGORIES),
    )
    return FeatureSpec(feature_set, BASE_CATEGORIES, registered=False)


def registered_names() -> tuple[str, ...]:
    return tuple(sorted(FEATURE_SETS))


def validate_registry() -> list[str]:
    """레지스트리 자체의 정합성 — 문제 목록을 돌려준다(비어 있으면 정상).

    테스트가 매번 부른다. 조립 시점이 아니라 여기서 잡는 이유는, 이름 충돌·미등록 카테고리
    같은 오류가 런타임에 드러날 때는 이미 **값 하나가 조용히 사라진 뒤**이기 때문이다.
    """
    problems: list[str] = []

    for name, categories in FEATURE_SETS.items():
        unknown = [c for c in categories if c not in CATEGORIES]
        if unknown:
            problems.append(f"feature_set '{name}'이 미등록 카테고리를 참조: {unknown}")

    for key, spec in CATEGORIES.items():
        if spec.key != key:
            problems.append(f"CATEGORIES 키 '{key}'와 CategorySpec.key '{spec.key}' 불일치")
        if (spec.sidecar is None) != (spec.sidecar_attr is None):
            problems.append(f"카테고리 '{key}'의 sidecar/sidecar_attr가 한쪽만 설정됨")

    seen: dict[str, str] = {}
    for key, spec in CATEGORIES.items():
        for feature_name in spec.feature_names:
            if feature_name in seen:
                problems.append(
                    f"피처 이름 '{feature_name}'이 카테고리 '{seen[feature_name]}'와 "
                    f"'{key}' 양쪽에 있다 — 한쪽이 조용히 덮어써진다"
                )
            seen[feature_name] = key

    return problems


def missing_sidecars(spec: FeatureSpec, provided: Iterable[str]) -> tuple[str, ...]:
    have = set(provided)
    return tuple(s for s in spec.required_sidecars if s not in have)


def unexpected_sidecars(spec: FeatureSpec, provided: Iterable[str]) -> tuple[str, ...]:
    """스펙이 안 쓰는 사이드카를 주입한 경우 — 주입한 쪽은 그 피처가 나온다고 믿고 있다.

    조용히 무시하면 "FL을 붙였는데 왜 성능이 그대로지"로 몇 주를 쓴다. 이건 실제로
    2026-08-04에 일어난 일의 거울상이다(그때는 반대로 주입을 잊었다).
    """
    required = set(spec.required_sidecars)
    return tuple(s for s in sorted(set(provided)) if s not in required)
