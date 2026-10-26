import wave
from array import array
from pathlib import Path

import pytest

from app.models.voice_conversion.mock import MockVoiceConversionModel
from app.pipelines import VoiceConversionPipeline
from app.storage import LocalObjectStorage


def write_wav(path: Path, *, seconds: int = 21, sample_rate: int = 8_000) -> None:
    frames = array("h", (1_000 if index % 2 else -1_000 for index in range(seconds * sample_rate)))
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        stream.writeframes(frames.tobytes())


@pytest.mark.asyncio
async def test_voice_conversion_preserves_timing_and_checkpoints(tmp_path: Path) -> None:
    storage = LocalObjectStorage(tmp_path / "storage")
    source = tmp_path / "source.wav"
    write_wav(source)
    input_key = await storage.put(source, namespace="jobs/input", suffix="wav")
    model = MockVoiceConversionModel()
    model.load()
    checkpoints: list[tuple[int, int]] = []

    async def checkpoint(completed: int, total: int) -> None:
        checkpoints.append((completed, total))

    result = await VoiceConversionPipeline(storage, chunk_seconds=10).run(
        job_id="speech-job",
        input_storage_key=input_key,
        model=model,
        voice_profile={"source_sha256": "profile-fingerprint"},
        on_checkpoint=checkpoint,
    )

    output_path = storage._safe_path(result.storage_key)
    with wave.open(str(output_path), "rb") as stream:
        assert stream.getframerate() == 8_000
        assert stream.getnframes() == 21 * 8_000
    assert result.duration_seconds == 21
    assert result.chunk_count == 3
    assert checkpoints == [(1, 3), (2, 3), (3, 3)]


@pytest.mark.asyncio
async def test_voice_conversion_rejects_non_pcm_wav(tmp_path: Path) -> None:
    storage = LocalObjectStorage(tmp_path / "storage")
    source = tmp_path / "invalid.wav"
    source.write_bytes(b"not-a-wave")
    input_key = await storage.put(source, namespace="jobs/input", suffix="wav")
    model = MockVoiceConversionModel()
    model.load()

    with pytest.raises(ValueError, match="invalid WAV"):
        await VoiceConversionPipeline(storage).run(
            job_id="speech-job",
            input_storage_key=input_key,
            model=model,
            voice_profile={},
        )
