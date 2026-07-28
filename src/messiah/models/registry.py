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
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import yaml

from messiah.core import logging as mlog
from messiah.core.messages import BundleStatus, Horizon, RegistryStatusChanged
from messiah.models.trainer import ExpertTrainingResult
from messiah.models.validator import ValidationReport
from messiah.strategy.futures.expert import HorizonExpert
from messiah.strategy.futures.meta_labeler import MetaLabeler

_VALID_TRANSITIONS: dict[BundleStatus, frozenset[BundleStatus]] = {
    BundleStatus.CANDIDATE: frozenset({BundleStatus.SHADOW, BundleStatus.RETIRED}),
    BundleStatus.SHADOW: frozenset({BundleStatus.LIVE, BundleStatus.RETIRED}),
    BundleStatus.LIVE: frozenset({BundleStatus.RETIRED}),
    BundleStatus.RETIRED: frozenset(),
}


class RegistryError(Exception):
    """상태기계 위반(잘못된 전이)·중복 등록·미등록 조회 등 Registry 계약 위반."""


@dataclass(frozen=True)
class BundleManifest:
    bundle_id: str
    horizon: Horizon
    trained_range: tuple[str, str]
    run_id: str
    feature_set: str
    validation_report: str
    gates_passed: dict[str, float]
    status: BundleStatus = BundleStatus.CANDIDATE

    def to_yaml_dict(self) -> dict:
        return {
            "bundle_id": self.bundle_id,
            "horizon": self.horizon.value,
            "trained_range": list(self.trained_range),
            "run_id": self.run_id,
            "feature_set": self.feature_set,
            "validation_report": self.validation_report,
            "gates_passed": self.gates_passed,
            "status": self.status.value,
        }

    @classmethod
    def from_yaml_dict(cls, data: dict) -> BundleManifest:
        return cls(
            bundle_id=data["bundle_id"],
            horizon=Horizon(data["horizon"]),
            trained_range=tuple(data["trained_range"]),
            run_id=data["run_id"],
            feature_set=data["feature_set"],
            validation_report=data["validation_report"],
            gates_passed=dict(data["gates_passed"]),
            status=BundleStatus(data["status"]),
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
    (bundle_dir / "thresholds.yaml").write_text(
        yaml.safe_dump(
            {"meta_labeler_threshold": training_result.meta_labeler.threshold},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    gates_passed = {gate.name: gate.value for gate in validation_report.gates if gate.passed}
    manifest = BundleManifest(
        bundle_id=bundle_id,
        horizon=horizon,
        trained_range=trained_range,
        run_id=run_id,
        feature_set=feature_set,
        validation_report="validation_report.json",
        gates_passed=gates_passed,
        status=BundleStatus.CANDIDATE,
    )
    (bundle_dir / "manifest.yaml").write_text(
        yaml.safe_dump(manifest.to_yaml_dict(), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    (bundle_dir / "validation_report.json").write_text(
        json.dumps(
            [gate.__dict__ for gate in validation_report.gates],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return manifest


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
        §7.1 [6]). 이 메서드 자체는 `validation_report.passed`를 재확인하지 않는다 —
        `manifest.gates_passed`가 이미 `pack_bundle()`에서 통과 관문만 추려 담았다."""
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

    def promote_to_live(self, bundle_id: str, *, operator: str, reason: str = "") -> None:
        """사람 승인 전제(Ver 1.1 §6-4) — `operator`로 승인자를 남긴다(감사 추적). 같은
        Horizon의 기존 `live`는 자동 `retired`(Ver 1.6 §9.2 "승격 시 이전 live는 자동
        retired, 롤백 가능하게 보존" — 레코드·파일 모두 지우지 않는다)."""
        _, horizon = self._status_of(bundle_id)
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
