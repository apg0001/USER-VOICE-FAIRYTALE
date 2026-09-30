import wave
from array import array
from pathlib import Path

import pytest

from app.audio.mixing import mix_pcm_s16le
from app.models.separation.mock import MockSeparationModel
from app.models.singing.mock import MockSingingVoiceModel
from app.pipelines import SingingPipeline
from app.storage import LocalObjectStorage


def write_stereo_wav(
    path: Path, *, seconds: int = 21, sample_rate: int = 8_000
) -> None:
    samples = array("h")
    for index in range(seconds * sample_rate):
        sample = 7_000 if index % 2 else -7_000
        samples.extend((sample, -sample))
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        stream.writeframes(samples.tobytes())


@pytest.mark.asyncio
async def test_singing_pipeline_preserves_timing_and_cleans_stems(tmp_path: Path) -> None:
    storage = LocalObjectStorage(tmp_path / "storage")
    source = tmp_path / "source.wav"
    write_stereo_wav(source)
    input_key = await storage.put(source, namespace="jobs/input", suffix="wav")
    separator = MockSeparationModel()
    singing_model = MockSingingVoiceModel()
    separator.load()
    singing_model.load()
    checkpoints: list[tuple[int, int]] = []

    async def checkpoint(completed: int, total: int) -> None:
        checkpoints.append((completed, total))

    result = await SingingPipeline(storage, chunk_seconds=10).run(
        job_id="singing-job",
        input_storage_key=input_key,
        separator=separator,
        singing_model=singing_model,
        voice_profile={"source_sha256": "profile-fingerprint"},
        on_checkpoint=checkpoint,
    )

    output_path = storage._safe_path(result.storage_key)
    with wave.open(str(output_path), "rb") as stream:
        assert stream.getframerate() == 8_000
        assert stream.getnchannels() == 2
        assert stream.getnframes() == 21 * 8_000
    assert result.duration_seconds == 21
    assert result.chunk_count == 3
    assert checkpoints == [(1, 3), (2, 3), (3, 3)]
    assert result.manifest["source"]["frame_count"] == 21 * 8_000
    assert result.manifest["vocal"]["retained"] is False
    assert result.manifest["instrumental"]["retained"] is False
    assert result.manifest["converted_vocal"]["retained"] is False
    assert result.manifest["final"]["retained"] is True
    assert result.manifest["final"]["frame_count"] == 21 * 8_000
    assert len(result.manifest["final"]["sha256"]) == 64
    assert result.manifest["mixing"]["intermediates_cleaned"] is True
    assert len(result.manifest["mixing"]["chunks"]) == 3


def test_mixing_limiter_prevents_clipping() -> None:
    loud = array("h", [30_000, -30_000]).tobytes()

    mixed, metrics = mix_pcm_s16le(loud, loud, loud)

    samples = array("h")
    samples.frombytes(mixed)
    assert max(abs(sample) for sample in samples) <= round(32_767 * 10 ** (-1 / 20))
    assert metrics.peak_before_limit == 60_000
    assert metrics.limiter_gain < 1
    assert metrics.peak_after_limit == max(abs(sample) for sample in samples)


def test_singing_adapters_require_loading() -> None:
    with pytest.raises(RuntimeError, match="separator must be loaded"):
        MockSeparationModel().separate(b"\x00\x00", 44_100, 1)
    with pytest.raises(RuntimeError, match="singing model must be loaded"):
        MockSingingVoiceModel().convert(b"\x00\x00", 44_100, 1, {})


def test_mock_singing_conversion_preserves_frame_and_pitch_direction() -> None:
    model = MockSingingVoiceModel()
    model.load()
    source = array("h", [-8_000, -2_000, 0, 2_000, 8_000])

    converted_bytes = model.convert(
        source.tobytes(), 44_100, 1, {"source_sha256": "fingerprint"}
    )

    converted = array("h")
    converted.frombytes(converted_bytes)
    assert len(converted) == len(source)
    assert [sample == 0 for sample in converted] == [sample == 0 for sample in source]
    assert [sample > 0 for sample in converted] == [sample > 0 for sample in source]


@pytest.mark.asyncio
async def test_failed_singing_pipeline_does_not_publish_partial_output(
    tmp_path: Path,
) -> None:
    storage = LocalObjectStorage(tmp_path / "storage")
    invalid = tmp_path / "invalid.wav"
    invalid.write_bytes(b"not-a-wave")
    input_key = await storage.put(invalid, namespace="jobs/input", suffix="wav")
    separator = MockSeparationModel()
    singing_model = MockSingingVoiceModel()
    separator.load()
    singing_model.load()

    with pytest.raises((EOFError, wave.Error)):
        await SingingPipeline(storage).run(
            job_id="failed-singing-job",
            input_storage_key=input_key,
            separator=separator,
            singing_model=singing_model,
            voice_profile={},
        )

    assert not (tmp_path / "storage" / "jobs" / "failed-singing-job").exists()
