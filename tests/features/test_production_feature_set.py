"""운영 `feature_set`이 실제로 기동 가능한가 (2026-08-10 B-4).

2026-08-04에 EV 계산기 12종과 `v2026.08-ev` 정의가 함께 들어왔다. 그런데 여섯 거래일간
**한 번도 계산되지 않았다** — `configs/instance.yaml`이 `v2026.07`이었고, 그것을 올리려 하면
수집기가 `사이드카 ['calendar']가 주입되지 않았다`로 기동을 거부했기 때문이다.

거부 자체는 옳은 동작이다(`FeatureEngine._assert_sidecars_match_spec` — 카테고리가 통째로
빠진 벡터가 같은 이름을 달고 나가는 것보다 낫다). 문제는 **진입점이 사이드카 정본을 안
불렀다**는 것이고, 그 사실이 설정을 바꿔 보기 전까지 드러나지 않았다.

이 파일은 그 함정을 고정한다: 운영 설정의 `feature_set`은 정본 조립기만으로 **항상**
엔진을 만들 수 있어야 한다. 다음에 `v2026.08-fl`처럼 새 사이드카를 요구하는 이름으로
올릴 때, 이 테스트가 배선 누락을 설정 변경 **전에** 잡는다.
"""

from __future__ import annotations

import pytest

from messiah.core.config import load_instance
from messiah.features import sidecar
from messiah.features import spec as feature_spec
from messiah.features.engine import FeatureEngine
from messiah.ops import canonical_consumers
from messiah.simulator.inprocess_bus import InProcessBus


def test_the_configured_feature_set_is_registered():
    """미등록 이름은 기저 카테고리로 조용히 떨어진다 — 운영 설정에선 그게 사고다."""
    spec = feature_spec.resolve(load_instance("configs").feature_set)

    assert (
        spec.registered
    ), f"'{spec.name}'이 FEATURE_SETS에 없다 — 등록된 이름: {feature_spec.registered_names()}"


def test_an_engine_can_be_built_for_the_configured_feature_set():
    """**이 테스트가 이 파일의 요점이다.**

    정본 조립기(`sidecar.build`)만으로 엔진이 서야 한다. 안 서면 그 설정으로는 수집기가
    기동하지 못하고, 그 사실은 지금까지 아침에 프로세스가 안 뜨는 것으로만 드러났다.
    """
    cfg = load_instance("configs")
    spec = feature_spec.resolve(cfg.feature_set)

    engine = FeatureEngine(
        "A05608",
        InProcessBus(instance_id="test"),
        feature_set=cfg.feature_set,
        sidecars=sidecar.build(spec),
    )

    assert engine.spec.name == cfg.feature_set


def test_ev_features_are_actually_in_the_configured_vector():
    """2026-08-10에 켠 것이 실제로 켜져 있는가 — 되돌리면 이 테스트가 먼저 운다.

    되돌림 자체는 정당할 수 있다(그때는 이 단언도 같이 고친다). 막으려는 것은 **아무도
    모르는 사이에 꺼지는 것**이다 — 여섯 거래일이 그렇게 지나갔다.
    """
    spec = feature_spec.resolve(load_instance("configs").feature_set)

    ev = [name for name in spec.feature_names if name.startswith("ev_")]

    assert "EV" in spec.categories
    assert {"ev_tod_cos", "ev_close_remain"} <= set(
        ev
    ), "변동성 축 관심 피처 둘이 벡터에 있어야 등록부 `ev-features-measured`가 채점된다"


@pytest.mark.parametrize("name", sorted(feature_spec.registered_names()))
def test_every_registered_feature_set_can_be_assembled(name: str):
    """등록된 이름 전부가 조립 가능해야 한다 — 못 하는 이름을 등록해 두면 그건 함정이다.

    `flow` 사이드카는 원천 파일 경로가 필요해 `build()`가 거부한다(그쪽 docstring). 그건
    **설계된 거부**이므로 여기서 성공을 요구하지 않고, 요구 사이드카 목록이 조립기가 아는
    이름인지만 본다.
    """
    spec = feature_spec.resolve(name)

    for required in spec.required_sidecars:
        assert required in (
            sidecar.CALENDAR,
            sidecar.FLOW,
        ), f"'{name}'이 조립기가 모르는 사이드카 '{required}'를 요구한다"


def test_the_startup_line_states_the_shape_the_checklist_asks_for():
    """**채점 항목과 그 항목을 출력하는 코드는 같은 자리에 있어야 한다** (2026-08-11 F-1).

    2026-08-10 커밋 ④-a는 다음 날 아침의 확인 항목을 "기동 로그에 피처 수(137)가 찍히는지"로
    적었는데 그 줄을 찍는 코드가 없었다 — 확인할 수 없는 채점 항목이 하루를 통째로 흘렸다.
    이 테스트는 그 줄이 개수·카테고리·사이드카를 실제로 말하는지 고정한다.
    """
    cfg = load_instance("configs")
    spec = feature_spec.resolve(cfg.feature_set)

    line = spec.describe()

    assert cfg.feature_set in line
    assert f"{len(spec)}개" in line
    for category in spec.categories:
        assert category in line
    for required in spec.required_sidecars:
        assert required in line


def test_the_startup_line_flags_an_unregistered_name():
    """오타난 운영 설정이 "PX·VL 121개로 정상 기동"처럼 보이면 안 된다 — `resolve()`가
    조용히 기저 카테고리로 떨어뜨리기 때문에 그 사실을 이 줄이 말해야 한다."""
    line = feature_spec.resolve("v2026.08-ev-오타").describe()

    assert "미등록" in line


def test_every_expected_consumer_calls_the_canonical_builder():
    """진입점 하나가 정본을 빠뜨리면 그 자리만 조용히 다른 벡터를 쓴다.

    2026-08-10에 여섯 곳 중 셋이 그랬고, 그중 하나(`run_vol_scorecard.py`)는 하필
    **"관심 피처가 측정되는가"를 채점하는 자리 자신**이었다.
    """
    findings = [line for line in canonical_consumers.findings() if "sidecar.build" in line]

    assert findings == []
