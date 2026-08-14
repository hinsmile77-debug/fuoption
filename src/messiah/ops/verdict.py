"""**오늘 판단이 가능한가** — 한 줄로 답한다 (2026-08-14 G-3 · G-6 · G-8).

## 왜 생겼나

2026-08-14 10:51의 화면들은 이렇게 말했다:

    status_snapshot.json   컴포넌트 4종 중 3종 `state:"OK"`
    self_check             `PASS — 기동 허용` (3회 전부)
    Command Center UI      상단 배지 초록·앰버 혼재

**세 화면이 각자 정상을 말하는 동안 시스템은 종일 판단 불능이었다.** 국면은 100% UNKNOWN,
판단 14/14가 첫 관문에서 접혔고, 1m NaN은 84.7%로 개장했다. 사람이 그 사실을 알아내는 데
로그 3개 + 아카이브 디렉터리 + 소스 4개 대조로 15분이 걸렸다.

12:30에는 **반대 방향의 같은 병**이 드러났다 — `status_snapshot`은 피처엔진을
`level=WARN "NaN 비율 임계 초과"`로 말했는데 같은 시각 `l1_daily` 로그의 관련 태그는 0건.
한 화면은 이상을 말했고 다른 화면은 침묵했다. 사람이 둘을 나란히 열어야만 보였다.

## 하나의 키에 담는다

**별도 `readiness` 키를 신설하지 않는다.** 화면이 또 나뉘면 L18의 반대편 실수다 — 지금
문제가 "표면이 많아서 아무도 전체를 못 본다"인데 표면을 하나 더 만들 수는 없다.
기존 `verdict`에 사유를 싣는다.

## 사유마다 **출처 표면**을 적는다 (G-6)

    {"code": "feature_nan_ratio_exceeded", "since_kst": "09:00:00",
     "sources": ["status_snapshot"], "missing_from": ["l1_daily.log"]}

`missing_from`이 비어 있지 않으면 **그 자체가 관측 결함**이다. 어떤 사실이 한 표면에만
나타난다는 것은, 다른 표면을 보는 사람은 그 사실을 영영 못 본다는 뜻이다.

## 축이 갈리면 중재한다 (G-8)

2026-08-14 리포트의 `breaches` 마지막 줄이 스스로 말했다 — *"아침 잘림 판정이 축마다
다르다: 잘렸다(계열 머리 구멍 410분) / 아니다(기동 지연 +0.6분 · 거래량 아침 미수집 0분)"*.
모순을 **말했지만 풀지는 않았다.** 사람이 `data/bars/`를 직접 `ls` 해서야 답이 나왔다.

중재 규칙은 세 단계다: ① 각 축이 무엇을 봤는지 `sources`에 적고 ② **경로가 서로 다르면
그 자체를 원인 후보로 승격**하고 ③ 소수파 축의 경로에 데이터가 있는지 되물어 답을 싣는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

# 판단 가용성을 깎는 사유 코드 — **문자열을 파싱하지 않는다**(`decision/meta_decision.py`의
# `DECISION_GATES`와 같은 규율: 문구를 다듬는 순간 조용히 0이 되는 축을 만들지 않는다).
REASON_WARM_START_SHORT = "warm_start_short"
REASON_NAN_RATIO_EXCEEDED = "feature_nan_ratio_exceeded"
REASON_REGIME_UNKNOWN = "regime_unknown"
REASON_SYMBOL_MISMATCH = "symbol_mismatch_suspected"
REASON_NO_EXPERT_CONTRIBUTION = "no_expert_contribution"
REASON_AXIS_CONFLICT = "observation_axis_conflict"


@dataclass(frozen=True)
class Reason:
    """판단 가용성을 깎는 사실 하나 — **어디서 봤는지까지** 적는다."""

    code: str
    detail: str
    since_kst: str | None = None
    sources: tuple[str, ...] = field(default_factory=tuple)
    # 이 사실이 **있어야 하는데 없는** 표면. 비어 있지 않으면 그 자체가 관측 결함이다(G-6).
    missing_from: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "detail": self.detail,
            "since_kst": self.since_kst,
            "sources": list(self.sources),
            "missing_from": list(self.missing_from),
        }


@dataclass(frozen=True)
class Verdict:
    """`ok=False`면 그날 판단을 믿을 수 없다 — 그리고 왜인지가 `reasons`에 있다."""

    ok: bool
    reasons: tuple[Reason, ...] = field(default_factory=tuple)

    @property
    def observation_gaps(self) -> tuple[Reason, ...]:
        """한 표면에만 나타난 사유들 — 관측 자체의 결함(G-6)."""
        return tuple(r for r in self.reasons if r.missing_from)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "reasons": [r.to_dict() for r in self.reasons],
            # 사람이 첫 줄만 읽어도 알 수 있게 — 화면은 목록을 다 안 읽는다.
            "summary": (
                "판단 가용" if self.ok else " · ".join(dict.fromkeys(r.code for r in self.reasons))
            ),
            "observation_gap_count": len(self.observation_gaps),
        }


def arbitrate_axes(
    claims: dict[str, tuple[bool, str]], *, evidence: dict[str, bool] | None = None
) -> Reason | None:
    """같은 사실에 대해 축이 갈리면 **원인 후보까지** 만든다 (G-8).

    입력: `claims`는 `{축 이름: (그 축의 주장(True=이상), 그 축이 본 경로)}`.
         `evidence`는 `{경로: 그 경로에 데이터가 있는가}` — 있으면 ③단계를 수행한다.
    산출: 모순이 없으면 None. 있으면 다수파·소수파와 각자가 본 경로를 담은 `Reason`.

    **경로가 서로 다르면 그 자체가 원인 후보다.** 2026-08-14에 "아침이 잘렸다"고 말한 축과
    "아니다"라고 말한 축은 서로 다른 심볼 디렉터리를 보고 있었고, 그 사실만 나란히 적혔어도
    사람이 `ls`를 할 필요가 없었다.
    """
    if len(claims) < 2:
        return None
    positives = {name for name, (flag, _path) in claims.items() if flag}
    if not positives or len(positives) == len(claims):
        return None  # 전원 일치 — 모순 없음

    negatives = set(claims) - positives
    minority, majority = (
        (positives, negatives) if len(positives) < len(negatives) else (negatives, positives)
    )
    paths = {name: path for name, (_flag, path) in claims.items()}
    distinct_paths = set(paths.values())

    parts = [
        f"축 모순 — 이상: {', '.join(sorted(positives))} / 아님: {', '.join(sorted(negatives))}",
        "경로: " + " · ".join(f"{n}={paths[n]}" for n in sorted(paths)),
    ]
    if len(distinct_paths) > 1:
        parts.append("**경로가 다르다 — 이것이 원인 후보다**")
    if evidence:
        # ③ 소수파가 본 경로에 데이터가 있는가를 되묻고 답을 싣는다.
        answers = [
            f"{paths[n]}={'데이터 있음' if evidence.get(paths[n]) else '데이터 없음'}"
            for n in sorted(minority)
            if paths[n] in evidence
        ]
        if answers:
            parts.append("소수파 경로 확인: " + " · ".join(answers))
            parts.append(f"→ 다수파({', '.join(sorted(majority))}) 쪽을 믿는다")
    return Reason(
        code=REASON_AXIS_CONFLICT,
        detail=" | ".join(parts),
        sources=tuple(sorted(claims)),
    )


def build(reasons: Iterable[Reason | None]) -> Verdict:
    """None을 걸러 `Verdict`를 만든다 — 호출부가 조건마다 if를 쓰지 않게."""
    kept = tuple(r for r in reasons if r is not None)
    return Verdict(ok=not kept, reasons=kept)
