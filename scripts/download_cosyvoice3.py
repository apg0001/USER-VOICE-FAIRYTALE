"""Download the reviewed CosyVoice3 checkpoint subset and record its provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

MODEL_ID = "FunAudioLLM/Fun-CosyVoice3-0.5B-2512"
MODEL_REVISION = "29e01c4e8d000f4bcd70751be16fa94bf3d85a18"
RUNTIME_REPOSITORY = "https://github.com/QwenAudio/CosyVoice.git"
RUNTIME_REVISION = "074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc"
ALLOW_PATTERNS = (
    "CosyVoice-BlankEN/*",
    "campplus.onnx",
    "cosyvoice3.yaml",
    "flow.pt",
    "hift.pt",
    "llm.pt",
    "speech_tokenizer_v3.onnx",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("models/cosyvoice3-0.5b-2512"))
    args = parser.parse_args()
    try:
        from huggingface_hub import snapshot_download
    except ImportError as error:
        raise SystemExit(
            "Install the downloader first: python -m pip install huggingface-hub==0.36.0"
        ) from error

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=MODEL_ID,
        revision=MODEL_REVISION,
        local_dir=output,
        allow_patterns=list(ALLOW_PATTERNS),
    )
    files = []
    for path in sorted(item for item in output.rglob("*") if item.is_file()):
        if ".cache" in path.parts or path.name == "voice-model-manifest.json":
            continue
        files.append(
            {
                "path": path.relative_to(output).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "runtime_repository": RUNTIME_REPOSITORY,
        "runtime_revision": RUNTIME_REVISION,
        "files": files,
        "total_bytes": sum(item["bytes"] for item in files),
    }
    (output / "voice-model-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Downloaded {len(files)} files ({manifest['total_bytes']} bytes) to {output}")


if __name__ == "__main__":
    main()
