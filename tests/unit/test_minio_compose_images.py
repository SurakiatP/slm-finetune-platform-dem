from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
MAIN_COMPOSE = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
TEST_COMPOSE = yaml.safe_load(
    (REPO_ROOT / "docker" / "compose.template-tests.yml").read_text()
)


def test_minio_images_use_pinned_official_quay_releases() -> None:
    assert MAIN_COMPOSE["services"]["minio"]["image"] == (
        "quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z"
    )
    assert MAIN_COMPOSE["services"]["minio-init"]["image"] == (
        "quay.io/minio/mc:RELEASE.2025-08-13T08-35-41Z"
    )
    assert TEST_COMPOSE["services"]["minio"]["image"] == (
        "quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z"
    )
