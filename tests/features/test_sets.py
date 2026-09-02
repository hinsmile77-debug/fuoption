"""`features/sets.py` — 이름 목록만 들고 계산기를 안 끌어오는 모듈 (2026-08-27 신설분, F-75③).

이 파일이 지키는 계약은 둘이고, **(b)가 이 분리의 목적 그 자체다.**

  (a) 등록된 이름 4개의 **문자열이 그대로**여야 한다 — 저장된 모델 번들이 이 문자열로 자기
      입력 모양을 주장한다. 의미를 바꾸면 그 주장이 거짓이 된다(`sets.py` 주석).
  (b) `import messiah.features.sets`가 **polars를 끌어오지 않아야** 한다. 없으면 누군가
      임포트 한 줄로 되돌리고, 그 한 줄이 Command Center UI에 polars 네이티브 런타임을
      다시 올린다(2026-07-29~08-03 UI 즉사 20건의 유입 경로 — `sets.py` docstring).
"""

from __future__ import annotations

import subprocess
import sys

from messiah.features import sets
from messiah.features import spec as feature_spec

# 2026-08-27 시점의 등록 이름. **이 목록을 바꾸는 것은 저장된 번들과의 계약을 바꾸는 것**이라
# 여기서 문자열 그대로 못박는다(추가는 가능, 이름 변경·삭제는 이 줄이 먼저 깨진다).
_REGISTERED = ("v2026.07", "v2026.08-ev", "v2026.08-fl", "v2026.08-fl-ev")


def test_the_four_registered_names_are_exactly_these_strings():
    assert sets.registered_names() == _REGISTERED
    assert set(sets.FEATURE_SETS) == set(_REGISTERED)


def test_categories_are_plain_strings_not_calculator_objects():
    """값이 문자열 키뿐이라 계산기를 몰라도 된다 — 그게 이 모듈이 가벼운 이유다."""
    for categories in sets.FEATURE_SETS.values():
        assert isinstance(categories, tuple)
        assert all(isinstance(key, str) for key in categories)
    assert sets.FEATURE_SETS["v2026.07"] == ("PX", "VL")


def test_spec_reexports_the_same_objects_not_a_copy():
    """`feature_spec.FEATURE_SETS`를 쓰던 호출부가 그대로 돌아야 한다 — 재수출이지 복제가
    아니다(복제면 한쪽만 고쳐지는 날이 온다)."""
    assert feature_spec.FEATURE_SETS is sets.FEATURE_SETS
    assert feature_spec.registered_names is sets.registered_names


def test_importing_sets_does_not_load_polars():
    """**이 분리의 목적 그 자체** (F-75③(b)).

    깨끗한 인터프리터에서 `messiah.features.sets`만 임포트하고 `sys.modules`를 본다.
    `spec`을 임포트하면 계산기 → `fl_core` → `data/investor_flow_history` → `import polars`가
    딸려 오는데, 설정 검증기(`core/config._registered_feature_set_only`)는 이름 확인만
    필요했다. 그 한 줄이 UI 프로세스에 polars를 올렸다.
    """
    probe = (
        "import sys, messiah.features.sets;"
        "print('polars' in sys.modules, 'messiah.features.spec' in sys.modules)"
    )
    result = subprocess.run(  # noqa: S603 — 인자는 전부 이 파일이 만든 고정 문자열
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=True,
    )

    assert result.stdout.strip() == "False False"


def test_config_validation_does_not_load_polars_either():
    """검증기가 `spec`이 아니라 `sets`를 보는지 — 실제 경로로 확인한다.

    `sets.py`가 가벼워도 `config.py`가 `spec`을 계속 임포트하면 아무것도 안 바뀐다. 2026-08-27
    실측이 정확히 그 형태였다(고친 유도식이 엿새 동안 0회 사용된 채였다는 F-A′와 같은 병).
    """
    probe = (
        "import sys;"
        "from messiah.core.config import InstanceConfig;"
        "InstanceConfig.model_validate({'instance_id': 'test', 'feature_set': 'v2026.07'});"
        "print('polars' in sys.modules)"
    )
    result = subprocess.run(  # noqa: S603 — 상동
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=True,
    )

    assert result.stdout.strip() == "False"
