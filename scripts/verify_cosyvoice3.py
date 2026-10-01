"""Verify every file in a downloaded CosyVoice3 provenance manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

MODEL_ID = "FunAudioLLM/Fun-CosyVoice3-0.5B-2512"
MODEL_REVISION = "29e01c4e8d000f4bcd70751be16fa94bf3d85a18"
RUNTIME_REVISION = "074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc"
REQUIRED_FILES = {
    "CosyVoice-BlankEN/config.json",
    "CosyVoice-BlankEN/generation_config.json",
    "CosyVoice-BlankEN/merges.txt",
    "CosyVoice-BlankEN/model.safetensors",
    "CosyVoice-BlankEN/tokenizer_config.json",
    "CosyVoice-BlankEN/vocab.json",
    "campplus.onnx",
    "cosyvoice3.yaml",
    "flow.pt",
    "hift.pt",
    "llm.pt",
    "speech_tokenizer_v3.onnx",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("model", nargs="?", type=Path, default=Path("models/cosyvoice3-0.5b-2512"))
    args = parser.parse_args()
    root = args.model.resolve()
    manifest_path = root / "voice-model-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = {
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "runtime_revision": RUNTIME_REVISION,
    }
    mismatches = [
        f"{key}: expected {value}, got {manifest.get(key)}"
        for key, value in expected.items()
        if manifest.get(key) != value
    ]
    total_bytes = 0
    files = manifest.get("files", [])
    recorded_paths = {entry.get("path") for entry in files}
    if recorded_paths != REQUIRED_FILES:
        mismatches.append("required file set")
    for entry in files:
        path = (root / entry["path"]).resolve()
        if root not in path.parents:
            mismatches.append(f"unsafe path: {entry['path']}")
            continue
        if not path.is_file():
            mismatches.append(f"missing: {entry['path']}")
            continue
        size = path.stat().st_size
        total_bytes += size
        if size != entry["bytes"]:
            mismatches.append(f"size: {entry['path']}")
        elif sha256(path) != entry["sha256"]:
            mismatches.append(f"sha256: {entry['path']}")
    if total_bytes != manifest.get("total_bytes"):
        mismatches.append("total_bytes")
    if mismatches:
        raise SystemExit("Checkpoint verification failed:\n- " + "\n- ".join(mismatches))
    print(
        f"Verified {len(manifest['files'])} files ({total_bytes} bytes) "
        f"at model revision {MODEL_REVISION}"
    )


if __name__ == "__main__":
    main()
