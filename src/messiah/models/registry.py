"""Model Registry — Ver 1.6 §9 번들 포맷·상태기계, Ver 1.1 §6-3 (Ver 2.0 §9 W35~36, Phase 5).

책임: `HorizonExpert`/`MetaLabeler`의 자유 문자열 버전(`model_version`)을 감사 가능한
카탈로그로 승격한다 — 번들 하나(Horizon 하나)마다 `candidate → shadow → live → retired`
상태를 추적하고(Ver 1.6 §9.2), live는 Horizon당 정확히 1개만 존재하게 강제한다.

## 저장소 선택: 파일 + SQLite

Ver 1.1 §5 "모델 아티팩트: 파일 + Registry(SQLite→PostgreSQL)"를 그대로 따른다 — 이
프로젝트엔 SQLAlchemy 등 DB 라이브러리가 없고(신규 의존성 추가 없이) `sqlite3`는
표준 라이브러리라 그대로 §5가 명시한 1단계("SQLite")에 정확히 대응한다.

## 번들 디렉터리 레이아웃 — 원안과 파일명이 다르다

Ver 1.6 §9.1 원안(`experts/e1.lgb`..)과 달리, 이미 존재하는 `HorizonExpert.save()`/
`MetaLabeler.save()`의 stem 기반 멀티파일 직렬화를 그대로 재사용한다(중복 구현 방지) —
`expert_e0.lgb`..`expert_eN.lgb` + `expert.json` + `expert_calibrator.pkl`(선택),
`meta_labeler.lgb` + `meta_labeler.json`. `manifest.yaml`이 실제 파일명의 진실 원천이므로
이름 규칙 자체는 안전하게 바뀔 수 있다.

## Registry ≠ Release

이 모듈은 "Horizon 하나의 모델 번들" 상태기계다. 여러 Horizon의 `live` 번들을 묶어 PC에
배포하는 "릴리스"(Ver 1.1 §7.3 "release = git tag + model bundle", `configs/instance.yaml`의
`model_bundle` 필드가 가리키는 대상)는 별도 계층 — `models/release.py` 참고.

## 승격은 사람이 한다 (Ver 1.1 §6-4)

`promote_to_shadow()`/`promote_to_live()`는 즉시 실행되는 상태 전이 API다 — 이 모듈은
"자동 제안"(`models/shadow_manager.py`의 `PromotionProposal`)과 "사람 승인"(이 메서드 호출)을
분리하지 않는다. 그 분리는 호출자(운영 절차)의 책임이다: 자동화가 실제로 `promote_to_live()`를
호출하는 것은 사람이 그 파이프라인을 그렇게 만들었을 때만 일어난다.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

import yaml

from messiah.core import logging as mlog
from messiah.core.messages import BundleStatus, Horizon, RegistryStatusChanged
from messiah.models.trainer import ExpertTrainingResult
from messiah.models.validator import ValidationReport
from messiah.strategy.futures.expert import HorizonExpert
from messiah.strategy.futures.meta_labeler import MetaLabeler

#: R18 — live 승격 전 섀도 병행 관측이 필요한 거래일수. 지금은 **계측 기준**일 뿐
#: 강제 차단선이 아니다(F-14 ④ — 차단은 별도 결정).
SHADOW_TRADING_DAYS_REQUIRED = 20

_VALID_TRANSITIONS: dict[BundleStatus, frozenset[BundleStatus]] = {
    BundleStatus.CANDIDATE: frozenset({BundleStatus.SHADOW, BundleStatus.RETIRED}),
    BundleStatus.SHADOW: frozenset({BundleStatus.LIVE, BundleStatus.RETIRED}),
    BundleStatus.LIVE: frozenset({BundleStatus.RETIRED}),
    BundleStatus.RETIRED: frozenset(),
}


class RegistryError(Exception):
    """상태기계 위반(잘못된 전이)·중복 등록·미등록 조회 등 Registry 계약 위반."""


@dataclass(frozen=True)
class ManifestGate:
    """매니페스트에 적히는 관문 한 줄 — 통과·미달·미측정 셋을 **전부** 적는다 (F-14).

    종전 `gates_passed: dict[name, value]`는 **통과분만** 담았다. 그래서 미달과 미측정이
    매니페스트에서 통째로 사라졌고, "관문 넷 통과"처럼 읽혔다(실제로는 일곱 중 넷이고
    셋은 아무도 안 쟀다). 없는 것과 통과한 것을 같은 모양으로 두지 않는다(마흐디 L18).
    """

    name: str
    passed: bool
    measured: bool = True
    value: float | None = None
    threshold: float | None = None

    def to_yaml_dict(self) -> dict:
        return {
            "name": self.name,
            "passed": bool(self.passed),
            "measured": bool(self.measured),
            "value": self.value,
            "threshold": self.threshold,
        }

    @classmethod
    def from_yaml_dict(cls, data: Mapping) -> ManifestGate:
        return cls(
            name=str(data["name"]),
            passed=bool(data["passed"]),
            measured=bool(data.get("measured", True)),
            value=_opt_float(data.get("value")),
            threshold=_opt_float(data.get("threshold")),
        )


def _opt_float(x: object) -> float | None:
    if x is None:
        return None
    try:
        value = float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if math.isnan(value) or math.isinf(value) else value


@dataclass(frozen=True)
class BundleManifest:
    """번들 매니페스트 — **상태의 정본이 아니다** (2026-08-21 F-14).

    `initial_status`는 패킹 시점의 상태를 남기는 기록일 뿐이고, 그 번들이 **지금**
    candidate인지 live인지는 `data/models/registry.db`의 `bundles.status`가 정본이다.
    종전엔 필드 이름이 `status`라 두 곳이 같은 질문에 다른 답을 낼 수 있었다 — 실제로
    2026-08-21 장후 점검에서 매니페스트는 `candidate`, Registry는 `live`인 번들이
    나왔다. 이름을 바꿔 **매니페스트가 현재 상태를 주장할 수 없게** 했다
    (불변원칙 2 "스키마는 단일 정의"의 자료 판본).
    """

    bundle_id: str
    horizon: Horizon
    trained_range: tuple[str, str]
    run_id: str
    feature_set: str
    validation_report: str
    gates: tuple[ManifestGate, ...]
    initial_status: BundleStatus = BundleStatus.CANDIDATE
    #: 옛 스키마(`gates_passed` dict)에서 읽어 온 매니페스트인가. 옛 번들은 미달·미측정
    #: 관문이 기록에 남아 있지 않으므로 승격 관문이 판정할 재료 자체가 없다 —
    #: 거부가 아니라 **유예(grandfather)** 하되 그 사실을 반드시 소리 내어 남긴다.
    legacy_gates: bool = False

    @property
    def gates_passed(self) -> dict[str, float | None]:
        """통과 관문만 추린 읽기 전용 뷰 — 옛 `gates_passed` 필드의 자리를 대신한다.
        **판정에 쓰지 말 것**(통과분만 보이므로 미달·미측정이 안 보인다). 사람이 읽는
        요약과 옛 호출부 호환용이다."""
        return {gate.name: gate.value for gate in self.gates if gate.passed}

    def blocking_gates(self) -> tuple[ManifestGate, ...]:
        """승격을 막는 관문 — 미달과 미측정을 **함께** 돌려준다."""
        return tuple(gate for gate in self.gates if not gate.passed)

    def to_yaml_dict(self) -> dict:
        return {
            "bundle_id": self.bundle_id,
            "horizon": self.horizon.value,
            "trained_range": list(self.trained_range),
            "run_id": self.run_id,
            "feature_set": self.feature_set,
            "validation_report": self.validation_report,
            "gates": [gate.to_yaml_dict() for gate in self.gates],
            "initial_status": self.initial_status.value,
        }

    @classmethod
    def from_yaml_dict(cls, data: dict) -> BundleManifest:
        """신·구 스키마를 모두 읽는다 (F-14 회귀 위험 ㉡ 마이그레이션).

        옛 스키마: `gates_passed: {name: value}` + `status:`.
        새 스키마: `gates: [{name, passed, measured, value, threshold}]` + `initial_status:`.
        """
        raw_gates = data.get("gates")
        legacy = raw_gates is None
        if legacy:
            # 옛 매니페스트는 통과분만 담았다 — 담긴 것은 전부 통과·측정된 관문이다.
            # 담기지 **않은** 관문이 무엇이었는지는 매니페스트만으로 알 수 없다.
            gates = tuple(
                ManifestGate(name=str(name), passed=True, measured=True, value=_opt_float(value))
                for name, value in dict(data.get("gates_passed") or {}).items()
            )
        else:
            gates = tuple(ManifestGate.from_yaml_dict(item) for item in raw_gates)
        status_value = data.get("initial_status", data.get("status"))
        return cls(
            bundle_id=data["bundle_id"],
            horizon=Horizon(data["horizon"]),
            trained_range=tuple(data["trained_range"]),
            run_id=data["run_id"],
            feature_set=data["feature_set"],
            validation_report=data["validation_report"],
            gates=gates,
            initial_status=BundleStatus(status_value),
            legacy_gates=legacy,
        )


@dataclass(frozen=True)
class BundleRecord:
    """Registry 조회 결과 — 카탈로그 메타데이터 + 실제 아티팩트 로더."""

    bundle_id: str
    bundle_dir: Path

    def manifest(self) -> BundleManifest:
        return load_manifest(self.bundle_dir)

    def load_expert(self) -> HorizonExpert:
        return load_expert(self.bundle_dir)

    def load_meta_labeler(self) -> MetaLabeler:
        return load_meta_labeler(self.bundle_dir)


def pack_bundle(
    *,
    bundle_id: str,
    horizon: Horizon,
    training_result: ExpertTrainingResult,
    validation_report: ValidationReport,
    trained_range: tuple[str, str],
    feature_set: str,
    run_id: str,
    out_dir: Path,
) -> BundleManifest:
    """`{out_dir}/{bundle_id}/`에 번들 디렉터리를 만들고 `BundleManifest`를 반환한다.

    이 함수는 Registry에 등록하지 않는다 — 패킹과 등록을 분리해 "패킹은 됐는데 등록은 안
    된" 상태를 호출자가 명시적으로 다룰 수 있게 한다(등록은 `ModelRegistry.register()`)."""
    bundle_dir = Path(out_dir) / bundle_id
    bundle_dir.mkdir(parents=True, exist_ok=True)

    training_result.expert.save(bundle_dir / "expert")
    training_result.meta_labeler.save(bundle_dir / "meta_labeler.lgb")

    (bundle_dir / "feature_set.yaml").write_text(
        yaml.safe_dump(
            {
                "feature_set": feature_set,
                "feature_names": training_result.expert.feature_names,
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    # **임계값의 출처를 함께 적는다** (2026-08-21 F-6).
    #
    # 종전엔 숫자 하나뿐이었다. 그래서 `meta_labeler_threshold: 0.0`이 "학습이 0을
    # 최적이라 판단했다"인지 "지지도 하한을 채우는 후보가 없어 격자 첫 칸으로 떨어졌다"
    # 인지 저장 상태만 보고는 알 수 없었다 — 그리고 임계 0은 메타 게이트가 통째로
    # 무력이라는 뜻이다(`p >= 0`은 언제나 참).
    selection = training_result.threshold_selection
    thresholds: dict[str, object] = {
        "meta_labeler_threshold": training_result.meta_labeler.threshold
    }
    if selection is not None:
        thresholds.update(
            {
                "meta_labeler_threshold_source": selection.source,
                "meta_labeler_threshold_support": selection.support,
                "meta_labeler_threshold_total": selection.total,
                "meta_labeler_threshold_min_support": selection.min_support,
            }
        )
    (bundle_dir / "thresholds.yaml").write_text(
        yaml.safe_dump(thresholds, allow_unicode=True),
        encoding="utf-8",
    )

    # **통과분만 추리지 않는다** (F-14). 미달·미측정도 그대로 싣는다 — 매니페스트가
    # "관문 넷 통과"라고 읽히던 것이 2026-08-21 P0의 절반이었다.
    gates = tuple(
        ManifestGate(
            name=gate.name,
            passed=bool(gate.passed),
            measured=bool(gate.measured),
            value=_opt_float(gate.value),
            threshold=_opt_float(gate.threshold),
        )
        for gate in validation_report.gates
    )
    manifest = BundleManifest(
        bundle_id=bundle_id,
        horizon=horizon,
        trained_range=trained_range,
        run_id=run_id,
        feature_set=feature_set,
        validation_report="validation_report.json",
        gates=gates,
        initial_status=BundleStatus.CANDIDATE,
    )
    (bundle_dir / "manifest.yaml").write_text(
        yaml.safe_dump(manifest.to_yaml_dict(), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    # `gate.to_dict()`가 `NaN`/`inf`를 `null`로 눕힌다 — jq·브라우저가 거부하지 않는
    # **엄밀한 JSON**이어야 한다(F-14 검증 ㉢). `allow_nan=False`가 그 보증이다:
    # 어딘가에서 NaN이 새어 들어오면 조용히 나가지 않고 여기서 죽는다.
    (bundle_dir / "validation_report.json").write_text(
        json.dumps(
            [gate.to_dict() for gate in validation_report.gates],
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ),
        encoding="utf-8",
    )
    return manifest


#: 옛 번들 두 개에는 출처 키가 없다 — "최적화였다"고 가정하지 않는다(L18).
THRESHOLD_SOURCE_UNKNOWN = "unknown"


def load_threshold_selection(bundle_dir: Path) -> dict[str, object]:
    """번들의 `thresholds.yaml`을 읽어 임계값과 **출처**를 돌려준다 (2026-08-21 F-6).

    출처 키가 없는 옛 번들은 `source="unknown"`이다 — `absent`가 아니다. 키가 없다는
    사실 자체가 "그 시절엔 아무도 안 적었다"는 정보이고, 그것은 폴백과도 최적화와도
    다른 세 번째 상태다.
    """
    path = Path(bundle_dir) / "thresholds.yaml"
    if not path.exists():
        return {"threshold": None, "source": THRESHOLD_SOURCE_UNKNOWN, "measured": False}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {
        "threshold": _opt_float(data.get("meta_labeler_threshold")),
        "source": str(data.get("meta_labeler_threshold_source", THRESHOLD_SOURCE_UNKNOWN)),
        "support": data.get("meta_labeler_threshold_support"),
        "total": data.get("meta_labeler_threshold_total"),
        "min_support": data.get("meta_labeler_threshold_min_support"),
        "measured": "meta_labeler_threshold_source" in data,
    }


def load_manifest(bundle_dir: Path) -> BundleManifest:
    data = yaml.safe_load((Path(bundle_dir) / "manifest.yaml").read_text(encoding="utf-8"))
    return BundleManifest.from_yaml_dict(data)


def load_expert(bundle_dir: Path) -> HorizonExpert:
    return HorizonExpert.load(Path(bundle_dir) / "expert")


def load_meta_labeler(bundle_dir: Path) -> MetaLabeler:
    return MetaLabeler.load(Path(bundle_dir) / "meta_labeler.lgb")


def save_conformal_state(bundle_dir: Path, scores: Sequence[float]) -> None:
    """번들의 "매일 갱신되는 유일한 부분"(Ver 1.6 §9.1) — Self Evaluation(Phase 5)이
    매일 장 마감 후 그날의 (예측확률,실제결과) 이력을 추가해 갱신한다."""
    (Path(bundle_dir) / "conformal_state.json").write_text(
        json.dumps({"scores": list(scores)}), encoding="utf-8"
    )


def load_conformal_state(bundle_dir: Path) -> list[float]:
    path = Path(bundle_dir) / "conformal_state.json"
    if not path.exists():
        return []  # 운영 이력 없음 — ConformalCalibrator.quantile_width([])가 보수적 1.0 반환
    return list(json.loads(path.read_text(encoding="utf-8"))["scores"])


class ModelRegistry:
    """번들 카탈로그 — SQLite 단일 테이블(모듈 docstring 저장소 선택 참고).

    버스 발행은 이 클래스가 직접 하지 않는다(모든 메서드가 동기 — Trainer/Validator와
    같은 야간 배치 성격, 이벤트 루프 의존 없음). 대신 상태 전이마다 `RegistryStatusChanged`를
    내부 큐에 쌓아 두고, 호출자(비동기 컨텍스트)가 `drain_events()`로 꺼내 원하는 시점에
    `bus.publish(TOPIC_REGISTRY, ...)`하면 된다 — sync/async 경계를 이 클래스 안에 숨기지
    않고 명시적으로 호출자에게 넘긴다."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bundles (
                bundle_id TEXT PRIMARY KEY,
                horizon TEXT NOT NULL,
                status TEXT NOT NULL,
                bundle_dir TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()
        self._pending_events: list[RegistryStatusChanged] = []

    def close(self) -> None:
        self._conn.close()

    def drain_events(self) -> list[RegistryStatusChanged]:
        events, self._pending_events = self._pending_events, []
        return events

    def register(self, manifest: BundleManifest, bundle_dir: Path) -> None:
        """신규 candidate 등록 — Validator 통과 산출물만 넘길 것(호출자 책임, Ver 1.6
        §7.1 [6]). 이 메서드 자체는 관문을 판정하지 않는다 — candidate는 "아직 아무것도
        주장하지 않는 상태"이기 때문이다. **관문 판정은 `promote_to_live()`가 한다**
        (2026-08-21 F-14).

        종전 주석은 "`manifest.gates_passed`가 이미 통과 관문만 추려 담았으니 안전하다"고
        적혀 있었다. 그 문장이 정확히 결함이었다 — 통과분만 담기 때문에 미달·미측정이
        기록에서 사라졌고, 그래서 매니페스트만 보면 언제나 전원 통과였다."""
        existing = self._conn.execute(
            "SELECT 1 FROM bundles WHERE bundle_id = ?", (manifest.bundle_id,)
        ).fetchone()
        if existing:
            raise RegistryError(f"이미 등록된 bundle_id: {manifest.bundle_id}")
        self._conn.execute(
            "INSERT INTO bundles (bundle_id, horizon, status, bundle_dir, created_at) "
            "VALUES (?, ?, ?, ?, datetime('now'))",
            (
                manifest.bundle_id,
                manifest.horizon.value,
                BundleStatus.CANDIDATE.value,
                str(bundle_dir),
            ),
        )
        self._conn.commit()
        mlog.log(
            "RegistryBundleRegistered",
            "candidate 등록",
            bundle_id=manifest.bundle_id,
            horizon=manifest.horizon.value,
        )
        self._queue_event(
            manifest.bundle_id, manifest.horizon, None, BundleStatus.CANDIDATE, "신규 등록"
        )

    def promote_to_shadow(self, bundle_id: str, reason: str = "") -> None:
        self._transition(bundle_id, BundleStatus.SHADOW, reason)

    def promote_to_live(
        self,
        bundle_id: str,
        *,
        operator: str,
        reason: str = "",
        shadow_trading_days: int | None = None,
    ) -> None:
        """사람 승인 전제(Ver 1.1 §6-4) — `operator`로 승인자를 남긴다(감사 추적). 같은
        Horizon의 기존 `live`는 자동 `retired`(Ver 1.6 §9.2 "승격 시 이전 live는 자동
        retired, 롤백 가능하게 보존" — 레코드·파일 모두 지우지 않는다).

        **관문을 여기서 다시 본다** (2026-08-21 F-14). 종전엔 이 메서드가 관문을 전혀
        묻지 않았고, `manifest.gates_passed`가 "통과분만 담았으니 안전하다"는 주석에
        기대고 있었다 — 그 주석이 틀렸다. 통과분만 담기 때문에 **미달·미측정이 사라져서**
        매니페스트만 보면 언제나 전원 통과였다. 지금은 미달과 미측정 둘 다 승격을 막는다.

        `shadow_trading_days`는 R18의 20거래일 섀도 요건을 **계측**하기 위한 것이다.
        20 미만이면 `BundlePromotedWithoutShadow`(WARNING)를 내되 **막지는 않는다** —
        강제 차단은 별도 결정이 필요하고, 이번 범위는 계측과 경보까지다.
        """
        _, horizon = self._status_of(bundle_id)
        self._enforce_promotion_gates(bundle_id, operator=operator)
        self._record_shadow_requirement(bundle_id, shadow_trading_days)
        previous_live = self.get_live(horizon)
        self._transition(bundle_id, BundleStatus.LIVE, f"승인자={operator}; {reason}".strip("; "))
        if previous_live is not None and previous_live.bundle_id != bundle_id:
            self._conn.execute(
                "UPDATE bundles SET status = ? WHERE bundle_id = ?",
                (BundleStatus.RETIRED.value, previous_live.bundle_id),
            )
            self._conn.commit()
            mlog.log(
                "RegistryLiveRetired",
                "신규 live 승격에 따른 자동 강등",
                bundle_id=previous_live.bundle_id,
                superseded_by=bundle_id,
            )
            self._queue_event(
                previous_live.bundle_id,
                horizon,
                BundleStatus.LIVE,
                BundleStatus.RETIRED,
                f"{bundle_id}로 대체",
            )

    def _enforce_promotion_gates(self, bundle_id: str, *, operator: str) -> None:
        """live 승격 직전 매니페스트의 관문을 다시 읽어 미달·미측정을 막는다 (F-14).

        옛 스키마 번들(`gates_passed` dict만 있는 것)은 **판정할 재료가 없다** — 미달·
        미측정이 애초에 기록되지 않았기 때문이다. 그래서 거부가 아니라 유예하되,
        유예했다는 사실을 WARNING으로 남기고 자가점검이 매 기동 그것을 읽는다
        (`scripts/self_check.py check_bundle`). 조용한 통과는 만들지 않는다(금지계명 12).
        """
        record = self.get(bundle_id)
        if record is None:
            raise RegistryError(f"미등록 bundle_id: {bundle_id}")
        try:
            manifest = load_manifest(record.bundle_dir)
        except (OSError, KeyError, ValueError) as exc:
            raise RegistryError(
                f"{bundle_id}: 매니페스트를 읽을 수 없어 승격 관문을 판정할 수 없다 — {exc!r}"
            ) from exc

        if manifest.legacy_gates:
            mlog.log(
                "BundlePromotedWithLegacyGates",
                f"{bundle_id}: 옛 매니페스트 스키마 — 미달·미측정 기록이 없어 승격 관문을 "
                "유예(grandfather)했다. 재학습·재패킹 전까지 이 번들의 관문은 미판정이다",
                bundle_id=bundle_id,
                operator=operator,
                level=logging.WARNING,
            )
            return

        blocking = manifest.blocking_gates()
        if blocking:
            unmeasured = [g.name for g in blocking if not g.measured]
            failed = [g.name for g in blocking if g.measured]
            mlog.log(
                "BundlePromotionRejected",
                f"{bundle_id}: 승격 거부 — 미달 {failed or '없음'} · 미측정 {unmeasured or '없음'}",
                bundle_id=bundle_id,
                operator=operator,
                failed_gates=failed,
                unmeasured_gates=unmeasured,
                level=logging.ERROR,
            )
            raise RegistryError(
                f"{bundle_id}: live 승격 거부 — 미달 관문 {failed or '없음'} · "
                f"미측정 관문 {unmeasured or '없음'}. "
                "미측정은 통과가 아니다(2026-08-21 F-14)"
            )

    def _record_shadow_requirement(self, bundle_id: str, shadow_trading_days: int | None) -> None:
        """R18 섀도 20거래일 요건 — 계측과 경보까지만 (F-14 ④)."""
        if shadow_trading_days is None:
            mlog.log(
                "BundlePromotedWithoutShadow",
                f"{bundle_id}: 섀도 거래일수 미측정 — R18(20거래일)을 채웠는지 알 수 없다",
                bundle_id=bundle_id,
                shadow_trading_days=None,
                required=SHADOW_TRADING_DAYS_REQUIRED,
                measured=False,
                level=logging.WARNING,
            )
            return
        if shadow_trading_days < SHADOW_TRADING_DAYS_REQUIRED:
            mlog.log(
                "BundlePromotedWithoutShadow",
                f"{bundle_id}: 섀도 {shadow_trading_days}거래일 — "
                f"R18 요건 {SHADOW_TRADING_DAYS_REQUIRED}거래일 미달",
                bundle_id=bundle_id,
                shadow_trading_days=shadow_trading_days,
                required=SHADOW_TRADING_DAYS_REQUIRED,
                measured=True,
                level=logging.WARNING,
            )

    def retire(self, bundle_id: str, reason: str) -> None:
        self._transition(bundle_id, BundleStatus.RETIRED, reason)

    def get_live(self, horizon: Horizon) -> BundleRecord | None:
        return self._one_by_horizon_status(horizon, BundleStatus.LIVE)

    def list_by_status(self, status: BundleStatus) -> list[BundleRecord]:
        rows = self._conn.execute(
            "SELECT bundle_id, bundle_dir FROM bundles WHERE status = ? ORDER BY created_at",
            (status.value,),
        ).fetchall()
        return [BundleRecord(bundle_id=r[0], bundle_dir=Path(r[1])) for r in rows]

    def get(self, bundle_id: str) -> BundleRecord | None:
        row = self._conn.execute(
            "SELECT bundle_id, bundle_dir FROM bundles WHERE bundle_id = ?", (bundle_id,)
        ).fetchone()
        return None if row is None else BundleRecord(bundle_id=row[0], bundle_dir=Path(row[1]))

    # ---------------------------------------------------------------- 내부

    def _one_by_horizon_status(self, horizon: Horizon, status: BundleStatus) -> BundleRecord | None:
        row = self._conn.execute(
            "SELECT bundle_id, bundle_dir FROM bundles WHERE horizon = ? AND status = ?",
            (horizon.value, status.value),
        ).fetchone()
        return None if row is None else BundleRecord(bundle_id=row[0], bundle_dir=Path(row[1]))

    def _status_of(self, bundle_id: str) -> tuple[BundleStatus, Horizon]:
        row = self._conn.execute(
            "SELECT status, horizon FROM bundles WHERE bundle_id = ?", (bundle_id,)
        ).fetchone()
        if row is None:
            raise RegistryError(f"미등록 bundle_id: {bundle_id}")
        return BundleStatus(row[0]), Horizon(row[1])

    def _transition(self, bundle_id: str, new_status: BundleStatus, reason: str) -> None:
        old_status, horizon = self._status_of(bundle_id)
        if new_status not in _VALID_TRANSITIONS[old_status]:
            mlog.log(
                "RegistryTransitionRejected",
                f"{old_status.value} -> {new_status.value} 거부",
                bundle_id=bundle_id,
            )
            raise RegistryError(
                f"{bundle_id}: {old_status.value} -> {new_status.value}는 허용되지 않는 전이"
            )
        self._conn.execute(
            "UPDATE bundles SET status = ? WHERE bundle_id = ?", (new_status.value, bundle_id)
        )
        self._conn.commit()
        self._queue_event(bundle_id, horizon, old_status, new_status, reason)

    def _queue_event(
        self,
        bundle_id: str,
        horizon: Horizon,
        old_status: BundleStatus | None,
        new_status: BundleStatus,
        reason: str,
    ) -> None:
        self._pending_events.append(
            RegistryStatusChanged(
                bundle_id=bundle_id,
                horizon=horizon,
                old_status=old_status,
                new_status=new_status,
                reason=reason,
            )
        )
