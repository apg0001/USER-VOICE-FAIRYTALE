"""Download the pinned htdemucs checkpoint into a local Demucs model repository."""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import urllib.request
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))

from app.models.separation.demucs import (  # noqa: E402
    HTDEMUCS_SIGNATURE,
    HTDEMUCS_WEIGHTS_FILE,
    HTDEMUCS_WEIGHTS_SHA256,
)

WEIGHTS_URL = f"https://dl.fbaipublicfiles.com/demucs/hybrid_transformer/{HTDEMUCS_WEIGHTS_FILE}"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=REPOSITORY_ROOT / "models/demucs-htdemucs")
    args = parser.parse_args()

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    weights = output / HTDEMUCS_WEIGHTS_FILE
    if not weights.is_file() or sha256(weights) != HTDEMUCS_WEIGHTS_SHA256:
        partial = weights.with_suffix(".partial")
        with urllib.request.urlopen(WEIGHTS_URL, timeout=60) as response, partial.open("wb") as out:
            shutil.copyfileobj(response, out)
        if sha256(partial) != HTDEMUCS_WEIGHTS_SHA256:
            partial.unlink()
            raise SystemExit("Downloaded htdemucs weights do not match the pinned SHA-256")
        partial.replace(weights)
    # Demucs resolves the "htdemucs" bag name from this file inside a local repo.
    (output / "htdemucs.yaml").write_text(f"models: ['{HTDEMUCS_SIGNATURE}']\n", encoding="utf-8")
    print(f"Verified {weights.name} ({weights.stat().st_size} bytes) in {output}")


if __name__ == "__main__":
    main()
