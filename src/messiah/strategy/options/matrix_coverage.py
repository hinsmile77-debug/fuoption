"""매트릭스 정합 진단 — **매트릭스가 배정하는 구조를 평가기가 만들 수 있는가**
(2026-09-02 신설, `models/label_geometry.py`와 같은 계열의 자기판정 도구).

## 왜 필요했나

`label_geometry.py`가 잡는 결함(레이블 정의와 판단 게이트가 서로를 모른 채 정해져 어긋난 것)
과 **같은 형태의 결함이 옵션 쪽에도 있었다.** `matrix._MATRIX`의 (중립·저IV) 셀은
`CALENDAR`를 배정하는데, `evaluator._leg_templates()`는 그 구조의 다리 템플릿을 갖고 있지
않아 항상 빈 목록을 돌려준다 — 그 함수 마지막 줄이 `return []  # CALENDAR 등 미지원 구조`
라고 **이미 적고 있었다.**

두 사실 다 문서화돼 있었는데 **그 둘을 곱한 결과는 아무 데도 없었다**: 그 셀에 걸린 사이클은
후보가 하나도 안 나온 채 지나가고, 서비스는 그것을 "생성된 후보가 전부 안전규칙에서 기각됨"
이라고 잘못 말했다(안전규칙까지 가지도 못했다). 2026-09-02 아카이브 재생에서 **191 사이클 중
142건(74%)** 이 그 형태였다.

## 이 도구가 하는 것과 안 하는 것

**한다**: 매트릭스 어휘 × 평가기 지원 여부를 맞대어 "구멍 난 셀"을 센다(정적 — 데이터 불필요),
그리고 (score, IV Rank) 표본을 주면 그 구멍에 실제로 몇 %가 걸리는지 센다(경험적).

**안 한다**: 고치지 않는다. 구멍을 메우는 방법은 셋이고(㉠ CALENDAR를 구현한다, ㉡ 그 셀에
다른 구조를 배정한다, ㉢ 그 셀은 관망이 맞다고 확정해 빈 셀로 만든다) 그 선택은 위험 성향을
바꾸는 결정이라 사람 몫이다(`label_geometry`가 flat 비율을 "고쳐야 할 값"으로 판정하지 않는
것과 같은 규율).

## 그 결정은 내려졌다 — 그리고 이 도구는 계속 필요하다 (2026-09-02 O-4)

사람이 **㉢(관망 확정)** 을 골랐고 `matrix._MATRIX[(NEUTRAL, LOW)]`는 이제 빈 셀이다(근거는
그 자리 주석). 그래서 지금 이 도구를 라이브 매트릭스에 돌리면 **정합**이 나온다.

정합이 나온다고 도구를 지우지 않는 이유는 둘이다. ⑴ 매트릭스는 앞으로도 바뀐다 — CALENDAR를
다시 열거나 새 구조를 넣는 날, 평가기를 같이 안 고치면 같은 침묵이 재발한다. ⑵ **정합할 때만
돌리는 검사는 자기가 죽은 것을 모른다** — 그래서 `check_matrix_coverage()`에 `cells_override`/
`buildable_override` 주입점을 뒀고, 테스트가 합성 구멍으로 탐지 능력 자체를 계속 검증한다
(`tests/test_zero_is_measured.py` 계열의 negative control과 같은 규율).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from messiah.strategy.options import matrix
from messiah.strategy.options.config import OptionsConfig
from messiah.strategy.options.evaluator import buildable_structures
from messiah.strategy.options.matrix import Direction, IVState


@dataclass(frozen=True, slots=True)
class CellCoverage:
    """매트릭스 셀 1개 — 배정된 구조 중 평가기가 만들 수 있는 것과 없는 것."""

    direction: Direction
    iv_state: IVState
    structures: tuple[str, ...]
    unbuildable: tuple[str, ...]

    @property
    def is_intentionally_empty(self) -> bool:
        """빈 셀은 결함이 아니다 — (중립·중IV)는 §4 "논지 없음"으로 **의도된 관망**이다."""
        return not self.structures

    @property
    def is_hollow(self) -> bool:
        """구조를 배정했는데 **하나도 못 만드는** 셀 — 조용한 무후보의 원인."""
        return bool(self.structures) and len(self.unbuildable) == len(self.structures)

    @property
    def is_partial(self) -> bool:
        return bool(self.unbuildable) and not self.is_hollow

    @property
    def label(self) -> str:
        return f"{self.direction.value}·{self.iv_state.value}"


@dataclass(frozen=True, slots=True)
class MatrixCoverage:
    cells: tuple[CellCoverage, ...]
    buildable: frozenset[str]
    unbuildable: frozenset[str]
    # 셀별 관측 횟수(경험적 입력을 준 경우) — 없으면 빈 dict.
    hits: Mapping[str, int]

    @property
    def hollow_cells(self) -> tuple[CellCoverage, ...]:
        return tuple(cell for cell in self.cells if cell.is_hollow)

    @property
    def partial_cells(self) -> tuple[CellCoverage, ...]:
        return tuple(cell for cell in self.cells if cell.is_partial)

    @property
    def is_healthy(self) -> bool:
        return not self.hollow_cells and not self.partial_cells

    @property
    def hollow_hit_share(self) -> float | None:
        """관측 표본 중 구멍 난 셀에 걸린 비율 — 표본이 없으면 None(0으로 위장하지 않는다)."""
        total = sum(self.hits.values())
        if not total:
            return None
        hollow = {cell.label for cell in self.hollow_cells}
        return sum(count for label, count in self.hits.items() if label in hollow) / total

    @property
    def verdict(self) -> str:
        if self.is_healthy:
            return (
                "매트릭스 전 셀이 평가기와 정합 — 단, 이건 '후보가 나온다'가 아니라 "
                "'구조를 만들 수는 있다'이다"
            )
        parts: list[str] = []
        for cell in self.hollow_cells:
            share = ""
            if self.hits:
                count = self.hits.get(cell.label, 0)
                total = sum(self.hits.values()) or 1
                share = f" — 관측 {count}/{total}회({count / total:.0%})"
            parts.append(
                f"**{cell.label} 셀이 비어 있다**: {', '.join(cell.structures)}를 배정하는데 "
                f"평가기가 하나도 못 만든다{share}. 이 셀에 걸린 사이클은 후보 0으로 조용히 "
                f"지나간다"
            )
        for cell in self.partial_cells:
            parts.append(
                f"{cell.label} 셀 일부 결손: {', '.join(cell.unbuildable)} 미지원 "
                f"(배정 {len(cell.structures)}개 중)"
            )
        return " / ".join(parts)

    def format_lines(self) -> list[str]:
        vocabulary = len(self.buildable) + len(self.unbuildable)
        missing = f" ({', '.join(sorted(self.unbuildable))})" if self.unbuildable else ""
        lines = [
            f"매트릭스 {len(self.cells)}셀 · 어휘 {vocabulary}구조 — "
            f"만들 수 있음 {len(self.buildable)} / 없음 {len(self.unbuildable)}{missing}",
        ]
        for cell in self.cells:
            mark = "비었음" if cell.is_hollow else ("일부결손" if cell.is_partial else "정합")
            if cell.is_intentionally_empty:
                mark = "관망(의도)"
            hit = f" · 관측 {self.hits.get(cell.label, 0)}회" if self.hits else ""
            structures = ", ".join(cell.structures) if cell.structures else "—"
            lines.append(f"  [{cell.label:<14}] {mark:<9} {structures}{hit}")
        share = self.hollow_hit_share
        if share is not None:
            lines.append(f"  구멍 난 셀에 걸린 관측 비율: {share:.1%}")
        lines.append(f"  판정: {self.verdict}")
        return lines


def check_matrix_coverage(
    *,
    config: OptionsConfig = OptionsConfig(),
    samples: Iterable[tuple[float, float | None]] = (),
    cells_override: Mapping[tuple[Direction, IVState], tuple[str, ...]] | None = None,
    buildable_override: frozenset[str] | None = None,
) -> MatrixCoverage:
    """
    입력: `samples`는 `(score, iv_rank)` 쌍 — 실제 사이클에서 뽑은 값이면 경험적 비중까지
         나오고, 안 주면 정적 판정만 한다(도구가 데이터 없이도 답할 수 있어야 한다).
         `*_override`는 **검사 기계 자체를 검증하기 위한 주입점**이다(2026-09-02 O-4).
         라이브 매트릭스가 정합해진 뒤에도 "구멍을 잡아내는가"를 계속 테스트해야 하는데,
         라이브 값만 보면 그 테스트가 상시 통과라 **탐지 능력이 죽은 것을 못 본다** —
         `zero_is_measured` 계열의 negative control과 같은 규율이다.
    실패 조건: 없다. 매트릭스가 비어 있으면 셀 0개로 "판정 불가"가 아니라 정합으로 나오는데,
         그 경우는 `matrix.ALL_STRUCTURES`가 비는 것이라 import 단계에서 이미 드러난다.
    """
    buildable = (
        buildable_override if buildable_override is not None else buildable_structures(config)
    )
    source_cells = cells_override if cells_override is not None else matrix.matrix_cells()
    cells: list[CellCoverage] = []
    for (direction, iv_state), structures in source_cells.items():
        cells.append(
            CellCoverage(
                direction=direction,
                iv_state=iv_state,
                structures=structures,
                unbuildable=tuple(s for s in structures if s not in buildable),
            )
        )
    cells.sort(key=lambda cell: (cell.direction.value, cell.iv_state.value))

    hits: dict[str, int] = {}
    for score, iv_rank in samples:
        iv_state = matrix.classify_iv_state(iv_rank, config)
        if iv_state is None:
            hits["IV Rank 미판정"] = hits.get("IV Rank 미판정", 0) + 1
            continue
        label = f"{matrix.classify_direction(score, config).value}·{iv_state.value}"
        hits[label] = hits.get(label, 0) + 1

    vocabulary = frozenset(
        structure for structures in source_cells.values() for structure in structures
    )
    return MatrixCoverage(
        cells=tuple(cells),
        buildable=buildable,
        # 어휘는 **그 매트릭스가 실제로 배정하는 구조**다 — `ALL_STRUCTURES`를 쓰면 주입된
        # 셀로 검사할 때 라이브 어휘가 섞인다.
        unbuildable=vocabulary - buildable,
        hits=hits,
    )


def unbuildable_reason(structures: Sequence[str], buildable: frozenset[str]) -> str | None:
    """서비스가 `NO_OPTION` 사유를 지을 때 쓰는 한 줄 — 전부 미지원이면 그 사실을 말한다.

    이게 없어서 `strategy/options/service.py`가 "생성된 후보가 전부 안전규칙에서 기각됨"이라고
    **안전규칙까지 가지도 못한 사이클**에 대해 말했다. 사유가 틀리면 사람이 엉뚱한 데를 고친다.
    """
    missing = [s for s in structures if s not in buildable]
    if not missing or len(missing) < len(structures):
        return None
    return f"평가기가 못 만드는 구조만 배정됨({', '.join(missing)}) — 매트릭스 셀과 평가기 불일치"
