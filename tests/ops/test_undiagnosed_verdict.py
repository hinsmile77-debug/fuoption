"""고친 적이 없는 것을 「수정이 듣지 않았다」고 말하면 ERROR가 닳는다 — 2026-08-20 G-H.

`no-degenerate-features`는 2026-08-13 최초 위반 후 엿새 동안 **3회 재발**하는 내내
*"수정이 듣지 않았다"* 라는 같은 문장(ERROR)을 냈다. 그런데 **애초에 수정이 없었다** —
`DECISION_LOG.md`가 *"창 길이인지 버그인지 미확정 · 조사 먼저(W-11)"* 로 적어 뒀고,
등록부는 그 사실을 모른 채 매일 ERROR를 냈다.

2026-08-20 장후 ERROR는 정확히 1건이었고 그것이 이 항목이었다.
**"고친 게 안 듣는다"와 "아직 안 고쳤다"는 대응이 완전히 다르다** — 전자는 원인 재조사,
후자는 그냥 착수다. 같은 ERROR로 묶으면 ERROR 한 건의 의미가 닳는다.

## 왜 「값이 비었나」가 아니라 「키를 적었나」인가

등록부의 기존 20여 항목은 이 필드를 모른다. "값이 비면 미착수"로 정하면 **그 전부가
하루아침에 WARNING으로 내려앉아 경보가 통째로 둔해진다** — 장후 리포트 G-H가 정확히 그
위험을 지적했고, 실제로 첫 구현에서 기존 테스트 7건이 그 이유로 깨졌다.
"""

from __future__ import annotations

from datetime import date

import pytest

from messiah.ops import fix_verification as fv


def _item(**kwargs) -> fv.PendingVerification:
    base = dict(
        id="sample",
        summary="샘플",
        registered=date(2026, 8, 13),
        metric="degenerate_feature_count",
        consecutive_days=3,
        max_value=0.0,
    )
    base.update(kwargs)
    return fv.PendingVerification(**base)


def _report(degenerate: int) -> dict:
    """`_degenerate_feature_count`가 읽는 실제 리포트 모양 — 판정된 Horizon만 센다."""
    return {
        "degenerate_features": {
            "1m": {
                "always_nan": [],
                "constant": [f"px_f{i}" for i in range(degenerate)],
                "judged": True,
            }
        }
    }


def test_key_absent_keeps_the_legacy_meaning() -> None:
    """등록부 기존 항목을 소급해서 뒤집지 않는다."""
    assert _item().fix_is_pending is False


def test_declared_but_empty_means_not_yet_fixed() -> None:
    assert _item(fix_state_declared=True).fix_is_pending is True


def test_declared_with_a_sha_means_fixed() -> None:
    assert _item(fix_state_declared=True, fix_committed="db7bcc8").fix_is_pending is False


@pytest.mark.parametrize(
    ("declared", "sha", "expected"),
    [
        (False, None, fv.VerificationStatus.RECURRED),  # 종전 그대로
        (True, "db7bcc8", fv.VerificationStatus.RECURRED),  # 고쳤는데 안 듣는다
        (True, None, fv.VerificationStatus.UNDIAGNOSED),  # 아직 안 고쳤다
    ],
)
def test_verdict_splits_on_whether_a_fix_exists(declared, sha, expected, tmp_path) -> None:
    """같은 위반 이력이 세 갈래로 갈린다 — 그 갈래가 곧 처방이다."""
    item = _item(fix_state_declared=declared, fix_committed=sha)
    reports = {
        date(2026, 8, 18): _report(1),
        date(2026, 8, 19): _report(1),
        date(2026, 8, 20): _report(1),
    }
    verdicts = fv.evaluate([item], reports, today=date(2026, 8, 20))
    assert verdicts[0].status == expected


def test_undeclared_entries_are_listed_not_blocked() -> None:
    """막지 않는다 — 20여 항목을 한꺼번에 강제하면 급하게 채운 틀린 값이 들어온다.

    대신 매일 센다. 조용히 두면 영원히 안 채워지고, 그러면 이 축이 하루짜리 장식이 된다.
    """
    items = [_item(id="a"), _item(id="b", fix_state_declared=True), _item(id="c")]
    assert fv.undeclared_fix_state(items) == ["a", "c"]


def test_the_real_registry_declares_the_degenerate_entry() -> None:
    """실제 등록부가 이 항목의 수정 상태를 **선언**하고 있어야 한다.

    선언이 없으면 종전 의미(무조건 `재발`)로 돌아가고, 이 축은 만들어만 두고 아무도 안 쓰는
    상태가 된다 — 이 저장소가 반복해서 배운 실패 형태다.

    값은 2026-08-20 저녁에 `f15aa58`(세션 경계 전환 + 번들 재학습)로 채워졌다. 그래서 이제
    이 항목의 위반은 `기전 미상`(WARNING)이 아니라 `재발`(ERROR)이다 — 고친 것이 안 듣는
    상황이 되기 때문이고, 그때는 원인을 다시 봐야 한다.
    """
    items = {item.id: item for item in fv.load_registry()}
    entry = items.get("no-degenerate-features")
    assert entry is not None
    assert entry.fix_state_declared, "`fix_committed:` 키가 등록부에 있어야 한다"
    assert (
        entry.fix_committed
    ), "2026-08-20 F-G 2단계로 값 전환과 재학습이 끝났다 — 그 sha가 적혀 있어야 한다"
    assert not entry.fix_is_pending


def test_undiagnosed_needs_attention_but_is_not_an_error() -> None:
    """사람 몫이되 ERROR는 아니다 — 처방이 "다시 봐라"가 아니라 "착수해라"다.

    `needs_attention`에는 들어가야 한다(안 그러면 아무도 착수를 안 한다). 그러나 로그
    태그는 `FixVerificationUndiagnosed`(WARNING)이지 `FixVerificationRecurred`(ERROR)가 아니다.
    """
    import logging as _logging

    from messiah.core.logging import TAG_LEVELS

    assert fv.VerificationStatus.UNDIAGNOSED != fv.VerificationStatus.RECURRED
    assert TAG_LEVELS["FixVerificationUndiagnosed"] == _logging.WARNING
    assert TAG_LEVELS["FixVerificationRecurred"] == _logging.ERROR

    item = _item(fix_state_declared=True)
    reports = {date(2026, 8, 19): _report(1), date(2026, 8, 20): _report(1)}
    verdict = fv.evaluate([item], reports, today=date(2026, 8, 20))[0]
    assert verdict.status == fv.VerificationStatus.UNDIAGNOSED
    assert verdict.needs_attention, "착수를 안 하면 영원히 미착수로 남는다"
