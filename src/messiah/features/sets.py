"""등록된 `feature_set` **이름 목록만** — 계산기 모듈을 안 끌어온다 (2026-08-27 신설).

## 왜 `spec.py`에서 떼어냈나

`features/spec.py`는 계산기 모듈(`px_core`·`vl_core`·`fl_core`·`ev_core`)을 모듈 최상단에서
임포트한다. 그건 그 파일의 존재 이유 자체다 — 이름 목록을 손으로 복사하지 않고 계산기를
**참조**하려면 계산기를 알아야 한다(그 모듈 docstring "이름 목록을 손으로 적지 않는다").

그런데 `fl_core`는 `data/investor_flow_history`를 끌어오고, 그 모듈은 `import polars as pl`을
한다. 즉 **`spec`을 임포트하면 polars 네이티브 런타임이 그 프로세스에 올라온다.**

Command Center UI는 그러면 안 되는 프로세스다. 2026-07-29~08-03 사이 UI는 20번 즉사했고
(`_polars_runtime.pyd` +0x083973c7, 0xc0000005 — Windows 이벤트 로그 실측: 07-29 2건·07-30
10건·07-31 6건·08-03 2건), 그래서 봉 파싱을 자식 프로세스로 밀어내 크래시를 가뒀다
(`ui/bar_reader.py` · `data/bar_export.py`). 그 격리는 실제로 통했다 — 08-03 14:20 이후
같은 폴트는 0건이다.

**그런데 격리가 우회되고 있었다.** 2026-08-27 실측:

    ui/app.py:1460      st.sidebar.text_input("Redis URL", _default_redis_url())
    ui/app.py:481       load_instance().redis_url
    core/config.py:145  InstanceConfig.model_validate(raw)
    core/config.py:108  _registered_feature_set_only()   ← pydantic 검증기
                          from messiah.features import spec
    features/spec.py:45   from messiah.features import ev_core, fl_core, px_core, vl_core
    features/fl_core.py:35  from messiah.data.investor_flow_history import FlowHistory
    data/investor_flow_history.py:29  import polars as pl   ← 여기서 부모에 로드

UI는 **Redis URL 문자열 하나**가 필요했을 뿐인데, 설정 검증기가 피처 계산기 전체를 끌어오고
그 끝에 polars가 딸려 왔다. `config.py`의 검증기는 이 위험을 알고 지연 임포트로 적어 뒀지만
(*"spec.py는 계산기 모듈(→ polars)을 끌어온다"*), **지연 임포트는 시점만 미룰 뿐 여부를
바꾸지 않는다** — UI는 첫 렌더 1초 안에 `load_instance()`를 부른다.

## 그래서 이 모듈이 지켜야 하는 성질

**임포트가 없다.** 표준 라이브러리조차 필요 없다. 여기에 임포트를 한 줄 넣는 순간 그 줄이
무엇을 딸고 오는지가 이 파일의 새 계약이 된다. `tests/ui/test_bar_reader.py`의
`test_ui_process_never_loads_polars`가 **렌더 시점까지** 이것을 못박는다.

값 자체(이름 → 카테고리)는 문자열뿐이라 계산기를 몰라도 된다. 카테고리 키가 실재하는지는
`spec.validate_registry()`가 계속 검증한다 — 이 분리가 그 검증을 옮기거나 약화시키지 않는다.
"""

# 이름 → 카테고리 키. 카테고리 키의 해석은 `spec.CATEGORIES`가 한다(계산기를 아는 쪽).
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


def registered_names() -> tuple[str, ...]:
    return tuple(sorted(FEATURE_SETS))
