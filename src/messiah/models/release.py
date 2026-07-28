"""Release packaging — Ver 1.1 §7.3 "릴리스 = git tag + 모델 번들" (Ver 2.0 §9 W37~38).

`models/registry.py`의 `ModelRegistry`는 **Horizon 하나짜리** 모델 번들(`candidate → shadow
→ live → retired`)을 추적한다. `configs/instance.yaml`의 `model_bundle` 필드(그리고
`scripts/self_check.py`의 `check_bundle()`)가 가리키는 대상은 그보다 한 단계 위 — **여러
Horizon의 `live` 번들을 한 데 묶어 PC에 배포하는 단위**다(Ver 1.1 §7.2 예시
`model_bundle: "release-2026.07.21"`). 이 모듈이 그 상위 계층이다.

## 왜 두 계층으로 나누는가

Horizon마다 승격 시점이 다르다(어떤 Horizon은 이번 주 승격, 다른 Horizon은 다음 주) — 이걸
릴리스 시점에 "그 순간 각 Horizon의 live가 무엇인지" 스냅샷으로 고정해야 "코드는 전 PC 동일
바이너리, 차이는 configs/instance.yaml 하나뿐"(Ver 1.1 §7.2) 원칙이 실제로 성립한다. 릴리스
이후 어느 Horizon이 재승격되어도 이미 배포된 릴리스는 그 시점 스냅샷을 그대로 가리킨다
(각 PC는 번들 교체+재시작으로만 업그레이드, Ver 1.1 §7.3 항목 5).

## 부분 릴리스는 허용한다 (명시적 갭)

이 시점 어떤 Horizon도 `live` 상태 번들을 하나도 갖고 있지 않다(G1 백테스트 관문을 실제
데이터로 통과한 모델이 아직 없음 — capability_matrix.md 반복 기록). `pack_release()`는 이를
호출 거부 사유로 삼지 않는다 — 빈 릴리스(또는 일부 Horizon만 채워진 릴리스)를 만들 수 있게
허용하고, 그 사실을 `manifest.yaml`의 `missing_horizons`에 정직하게 남긴다. `self_check.py`의
`check_bundle()`은 "manifest.yaml이 존재하는지"만 보므로 이 상태로도 `mode: paper`/`replay`
기동은 통과한다 — `mode: live`에서 실제로 거래에 쓸 Horizon이 비어 있으면 Meta Decision
Engine이 그 Horizon 없이 판단하게 되므로(다른 Horizon만으로 Aggregator가 계산), 실거래
투입 전 반드시 `missing_horizons`를 사람이 검토할 것.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

from messiah.core.messages import Horizon
from messiah.models.registry import ModelRegistry

ALL_HORIZONS: tuple[Horizon, ...] = (
    Horizon.M1,
    Horizon.M3,
    Horizon.M5,
    Horizon.M10,
    Horizon.M15,
    Horizon.M30,
)


@dataclass(frozen=True)
class ReleaseManifest:
    release_id: str
    git_sha: str
    bundles: dict[str, str]  # horizon.value -> bundle_id
    missing_horizons: list[str]

    def to_yaml_dict(self) -> dict:
        return {
            "release_id": self.release_id,
            "git_sha": self.git_sha,
            "bundles": self.bundles,
            "missing_horizons": self.missing_horizons,
        }

    @classmethod
    def from_yaml_dict(cls, data: dict) -> ReleaseManifest:
        return cls(
            release_id=data["release_id"],
            git_sha=data["git_sha"],
            bundles=dict(data["bundles"]),
            missing_horizons=list(data["missing_horizons"]),
        )


def pack_release(
    registry: ModelRegistry,
    release_id: str,
    *,
    out_dir: Path,
    horizons: tuple[Horizon, ...] = ALL_HORIZONS,
) -> ReleaseManifest:
    """`{out_dir}/{release_id}/manifest.yaml`을 만든다 — `configs/instance.yaml`의
    `model_bundle: "{release_id}"`가 이 디렉터리를 가리키게 된다(`self_check.py`
    `check_bundle()`이 검사하는 바로 그 경로)."""
    bundles: dict[str, str] = {}
    missing: list[str] = []
    for horizon in horizons:
        live = registry.get_live(horizon)
        if live is None:
            missing.append(horizon.value)
        else:
            bundles[horizon.value] = live.bundle_id

    manifest = ReleaseManifest(
        release_id=release_id, git_sha=_git_sha(), bundles=bundles, missing_horizons=missing
    )
    release_dir = Path(out_dir) / release_id
    release_dir.mkdir(parents=True, exist_ok=True)
    (release_dir / "manifest.yaml").write_text(
        yaml.safe_dump(manifest.to_yaml_dict(), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return manifest


def load_release_manifest(release_dir: Path) -> ReleaseManifest:
    data = yaml.safe_load((Path(release_dir) / "manifest.yaml").read_text(encoding="utf-8"))
    return ReleaseManifest.from_yaml_dict(data)


def verify_release(registry: ModelRegistry, manifest: ReleaseManifest) -> list[str]:
    """릴리스가 가리키는 각 bundle_id가 **지금도** Registry에서 `live` 상태인지 검증한다.

    반환: 문제 목록(빈 리스트 = 이상 없음). "번들 손상 배포"(Ver 1.6 §12 실패모드표)를
    막는 자가 점검용 — 릴리스 발행 이후 해당 Horizon이 강등/재승격되면 릴리스 스냅샷과
    Registry 현재 상태가 어긋날 수 있음을 감지한다(자동 복구는 하지 않는다 — 사람이 재판단할
    신호일 뿐)."""
    problems: list[str] = []
    for horizon_value, bundle_id in manifest.bundles.items():
        record = registry.get(bundle_id)
        if record is None:
            problems.append(f"{horizon_value}: bundle {bundle_id} — Registry에 없음")
            continue
        live = registry.get_live(Horizon(horizon_value))
        if live is None or live.bundle_id != bundle_id:
            problems.append(f"{horizon_value}: 릴리스가 가리키는 {bundle_id}가 더 이상 live가 아님")
    return problems


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return "nogit"
