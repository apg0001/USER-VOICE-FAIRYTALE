"""Download the pinned Seed-VC singing (f0, 44.1 kHz) components and record provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))

from app.models.singing.seed_vc import SEED_VC_COMPONENTS, SEED_VC_RUNTIME_REVISION  # noqa: E402


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=REPOSITORY_ROOT / "models/seed-vc-svc")
    args = parser.parse_args()
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as error:
        raise SystemExit(
            "Install the downloader first: python -m pip install huggingface-hub"
        ) from error

    output = args.output.resolve()
    files = []
    for component in SEED_VC_COMPONENTS:
        directory = output / component.directory
        for filename in component.files:
            path = Path(
                hf_hub_download(
                    repo_id=component.repo_id,
                    filename=filename,
                    revision=component.revision,
                    local_dir=directory,
                )
            )
            files.append(
                {
                    "component": component.directory,
                    "repo_id": component.repo_id,
                    "revision": component.revision,
                    "license": component.license,
                    "path": path.relative_to(output).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": sha256(path),
                }
            )
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "runtime_repository": "https://github.com/Plachtaa/seed-vc",
        "runtime_revision": SEED_VC_RUNTIME_REVISION,
        "files": files,
        "total_bytes": sum(item["bytes"] for item in files),
    }
    (output / "voice-model-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Downloaded {len(files)} files ({manifest['total_bytes']} bytes) to {output}")


if __name__ == "__main__":
    main()
