from pathlib import Path

import pytest

from app.storage.local import LocalObjectStorage


@pytest.mark.asyncio
async def test_storage_uses_generated_key_and_blocks_traversal(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    source.write_bytes(b"RIFF-test")
    storage = LocalObjectStorage(tmp_path / "objects")

    key = await storage.put(source, namespace="voice-samples", suffix="wav")

    assert key.startswith("voice-samples/")
    assert b"".join([chunk async for chunk in storage.open(key)]) == b"RIFF-test"
    with pytest.raises(ValueError, match="invalid storage key"):
        _ = storage._safe_path("../../secret")

