import wave
from pathlib import Path

import pytest

from app.models.tts.mock import MockTTSModel
from app.pipelines import TTSPipeline, chunk_text
from app.storage import LocalObjectStorage


def test_chunk_text_preserves_order_and_limits_chunks() -> None:
    text = "첫 번째 문장입니다. 두 번째 문장은 조금 더 깁니다! 마지막 문장입니다?"
    chunks = chunk_text(text, max_chars=24)

    assert all(0 < len(chunk) <= 24 for chunk in chunks)
    assert " ".join(chunks).replace(" ", "") == text.replace(" ", "")


@pytest.mark.asyncio
async def test_tts_pipeline_writes_valid_wav_and_checkpoints(tmp_path: Path) -> None:
    storage = LocalObjectStorage(tmp_path / "storage")
    model = MockTTSModel()
    model.load()
    checkpoints: list[tuple[int, int]] = []

    async def checkpoint(completed: int, total: int) -> None:
        checkpoints.append((completed, total))

    result = await TTSPipeline(storage, pause_ms=50).run(
        job_id="job-1",
        text="하나의 문장입니다. 또 다른 문장입니다. 마지막 문장입니다.",
        model=model,
        voice_profile={"source_sha256": "abc123"},
        max_chunk_chars=18,
        on_checkpoint=checkpoint,
    )

    output_path = storage._safe_path(result.storage_key)
    with wave.open(str(output_path), "rb") as stream:
        assert stream.getnchannels() == 1
        assert stream.getsampwidth() == 2
        assert stream.getframerate() == 24_000
        assert stream.getnframes() > 0
    assert result.chunk_count > 1
    assert checkpoints[-1] == (result.chunk_count, result.chunk_count)
