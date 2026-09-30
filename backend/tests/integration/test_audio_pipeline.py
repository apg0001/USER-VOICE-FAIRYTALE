import asyncio
import shutil
import wave
from pathlib import Path

import pytest

from app.audio.ffmpeg import FFmpegMediaTool
from app.audio.preprocessing import AudioPreprocessingPipeline
from app.audio.types import (
    AudioProbe,
    AudioQuality,
    NoiseReduction,
    PreprocessingConfig,
)
from app.services.file_service import FileService
from app.storage import LocalObjectStorage


class FakeMediaTool:
    def __init__(self) -> None:
        self.transcode_config: PreprocessingConfig | None = None

    async def probe(self, source: Path) -> AudioProbe:
        size = (await asyncio.to_thread(source.stat)).st_size
        return AudioProbe("wav", "pcm_s16le", 12.0, 48_000, 2, size)

    async def analyze(self, source: Path, duration_seconds: float) -> AudioQuality:
        return AudioQuality(10.0, 2.0, 0.1667, -1.0, False, -48.0, "normal")

    async def transcode(
        self, source: Path, destination: Path, config: PreprocessingConfig
    ) -> None:
        self.transcode_config = config
        await asyncio.to_thread(
            destination.write_bytes, b"RIFF\x00\x00\x00\x00WAVEcleaned"
        )


@pytest.mark.asyncio
async def test_ingest_preserves_original_and_cleaned_artifacts(tmp_path: Path) -> None:
    source = tmp_path / "sample.wav"
    source.write_bytes(b"RIFF\x00\x00\x00\x00WAVE" + b"audio" * 100)
    media_tool = FakeMediaTool()
    storage = LocalObjectStorage(tmp_path / "objects")
    service = FileService(
        storage,
        AudioPreprocessingPipeline(media_tool),
        max_upload_size=1024 * 1024,
    )
    config = PreprocessingConfig(noise_reduction=NoiseReduction.NORMAL)

    artifact = await service.ingest_audio(
        source,
        original_filename="sample.wav",
        content_type="audio/wav",
        namespace="user/test",
        config=config,
    )

    assert artifact.original_storage_key != artifact.cleaned_storage_key
    assert artifact.sha256
    assert artifact.quality.speech_seconds == 10.0
    assert media_tool.transcode_config == config
    assert b"".join([chunk async for chunk in storage.open(artifact.original_storage_key)])
    assert b"".join([chunk async for chunk in storage.open(artifact.cleaned_storage_key)])


@pytest.mark.asyncio
@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg binaries are not installed",
)
async def test_real_ffmpeg_normalizes_to_model_wav(tmp_path: Path) -> None:
    source = tmp_path / "fixture.wav"
    with wave.open(str(source), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(44_100)
        wav.writeframes(b"\x01\x00\x01\x00" * 44_100)
    destination = tmp_path / "normalized.wav"
    tool = FFmpegMediaTool(timeout_seconds=30)

    await tool.transcode(
        source,
        destination,
        PreprocessingConfig(
            target_sample_rate=24_000,
            trim_silence=False,
            normalize_loudness=False,
            noise_reduction=NoiseReduction.OFF,
        ),
    )
    probe = await tool.probe(destination)

    assert probe.sample_rate == 24_000
    assert probe.channels == 1
    assert probe.codec_name == "pcm_s16le"


@pytest.mark.asyncio
@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg binaries are not installed",
)
async def test_real_ffmpeg_preserves_stereo_for_singing(tmp_path: Path) -> None:
    source = tmp_path / "music.wav"
    with wave.open(str(source), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(44_100)
        wav.writeframes(b"\x01\x00\x02\x00" * 44_100)
    destination = tmp_path / "singing.wav"
    tool = FFmpegMediaTool(timeout_seconds=30)

    await tool.transcode(
        source,
        destination,
        PreprocessingConfig(
            target_sample_rate=44_100,
            target_channels=2,
            trim_silence=False,
            normalize_loudness=False,
            noise_reduction=NoiseReduction.OFF,
        ),
    )
    probe = await tool.probe(destination)

    assert probe.sample_rate == 44_100
    assert probe.channels == 2
    assert probe.codec_name == "pcm_s16le"

