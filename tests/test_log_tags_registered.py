"""모든 `mlog.log()` 태그가 `TAG_LEVELS`에 등록돼 있는가 (2026-09-02 신설).

## 왜 생겼나 — 실제로 세션을 내릴 뻔했다

2026-09-02에 `strategy/options/chain_smile.py`를 결선하면서 태그 셋(`OptionSmileProviderStarted`
·`OptionSmileResidualHigh`·`OptionsCandidateUnbuildable`)의 **등록을 빠뜨린 채 커밋했다.**
`mlog.log()`는 미등록 태그에 `ValueError`를 던진다(SYSTEM.md R6). 그런데
`ChainSmileProvider.run_forever()`는 **기동 첫 줄에서** 그 태그를 쓴다:

    run_g2_paper_trading._run_regular_session()
      └ asyncio.gather(..., provider.run_forever(), ...)
          └ mlog.log("OptionSmileProviderStarted", ...)  → ValueError
              → gather가 예외를 전파 → 정규 세션 전체 중단

전 테스트(2,580건)가 통과했는데도 남아 있었다 — **그 로그 줄을 실행하는 테스트가 하나도
없었기 때문**이다. 단위 테스트는 "그 코드가 불렸는가"만 볼 수 있고, 안 불린 코드의 태그
등록 여부는 못 본다. 그래서 소스를 **정적으로** 훑는 검사가 필요하다.

## 한계 — 문자열 리터럴만 본다

`mlog.log(tag_variable, ...)`처럼 변수로 넘기는 호출은 여기서 못 잡는다. 지금 저장소에는
그런 호출이 없고, 생긴다면 그 자체가 리뷰 대상이다(태그는 상수여야 집계가 선다).
"""

from __future__ import annotations

import re
from pathlib import Path

from messiah.core.logging import TAG_LEVELS

_SRC = Path(__file__).resolve().parent.parent / "src"
# `mlog.log("Tag", ...)` / `log("Tag", ...)` — 여는 괄호 뒤 첫 인자가 문자열 리터럴인 경우.
_CALL = re.compile(r"\bmlog\.log\(\s*\n?\s*[\"']([A-Za-z][A-Za-z0-9_]*)[\"']")


def _used_tags() -> dict[str, list[str]]:
    used: dict[str, list[str]] = {}
    for path in _SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for tag in _CALL.findall(text):
            used.setdefault(tag, []).append(str(path.relative_to(_SRC)))
    return used


def test_every_logged_tag_is_registered():
    used = _used_tags()
    assert used, "태그를 하나도 못 찾았다 — 정규식이 호출 형태를 놓쳤을 수 있다"

    missing = {tag: sorted(set(files)) for tag, files in used.items() if tag not in TAG_LEVELS}

    assert not missing, (
        "미등록 태그 — `mlog.log()`가 ValueError를 던져 그 코드 경로가 죽는다(R6). "
        f"core/logging.py TAG_LEVELS에 추가할 것: {missing}"
    )


def test_the_options_wiring_tags_are_registered():
    """2026-09-02에 실제로 빠졌던 셋 — 회귀 고정."""
    for tag in (
        "OptionSmileProviderStarted",
        "OptionSmileResidualHigh",
        "OptionsCandidateUnbuildable",
    ):
        assert tag in TAG_LEVELS, f"{tag} 미등록 — 기동 첫 줄에서 세션이 죽는다"
