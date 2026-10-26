import asyncio
import hashlib
import tempfile
import wave
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.audio.mixing import MixingMetrics, mix_pcm_s16le
from app.models.separation import SeparationModel
from app.models.singing import SingingVoiceModel
from app.storage import ObjectStorage

SingingCheckpoint = Callable[[int, int], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class SingingPipelineResult:
    storage_key: str
    duration_seconds: float
    sample_rate: int
    channels: int
    chunk_count: int
    manifest: dict[str, Any]


class SingingPipeline:
    def __init__(
        self,
        storage: ObjectStorage,
        *,
        max_duration_seconds: float = 3_600,
        chunk_seconds: int = 10,
    ) -> None:
        self.storage = storage
        self.max_duration_seconds = max_duration_seconds
        self.chunk_seconds = chunk_seconds

    async def run(
        self,
        *,
        job_id: str,
        input_storage_key: str,
        separator: SeparationModel,
        singing_model: SingingVoiceModel,
        voice_profile: dict[str, Any],
        on_checkpoint: SingingCheckpoint | None = None,
    ) -> SingingPipelineResult:
        with tempfile.TemporaryDirectory(prefix="singing-pipeline-") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "source.wav"
            final_path = root / "final.wav"
            await self._stage_input(input_storage_key, source_path)
            vocal_digest = hashlib.sha256()
            instrumental_digest = hashlib.sha256()
            converted_digest = hashlib.sha256()
            mix_metrics: list[dict[str, Any]] = []
            with wave.open(str(source_path), "rb") as source:
                if source.getsampwidth() != 2 or source.getcomptype() != "NONE":
                    raise ValueError("singing input must be PCM S16LE WAV")
                channels = source.getnchannels()
                if channels not in {1, 2}:
                    raise ValueError("singing input must be mono or stereo")
                sample_rate = source.getframerate()
                frame_count = source.getnframes()
                duration = frame_count / sample_rate
                if duration <= 0 or duration > self.max_duration_seconds:
                    raise ValueError("singing input duration is invalid")

                frames_per_chunk = sample_rate * self.chunk_seconds
                chunk_count = max(1, (frame_count + frames_per_chunk - 1) // frames_per_chunk)
                written_frames = 0
                with wave.open(str(final_path), "wb") as output:
                    output.setnchannels(channels)
                    output.setsampwidth(2)
                    output.setframerate(sample_rate)
                    completed = 0
                    while pcm := source.readframes(frames_per_chunk):
                        mixed, vocal, instrumental, converted, metrics = await asyncio.to_thread(
                            self._process_chunk,
                            pcm,
                            sample_rate,
                            channels,
                            separator,
                            singing_model,
                            voice_profile,
                        )
                        output.writeframes(mixed)
                        vocal_digest.update(vocal)
                        instrumental_digest.update(instrumental)
                        converted_digest.update(converted)
                        mix_metrics.append(asdict(metrics))
                        written_frames += len(mixed) // (2 * channels)
                        completed += 1
                        if on_checkpoint is not None:
                            await on_checkpoint(completed, chunk_count)
                if written_frames != frame_count:
                    raise ValueError("mixed output must preserve input frame count")

            manifest = {
                "source": {
                    "frame_count": frame_count,
                    "sha256": await asyncio.to_thread(self._sha256, source_path),
                },
                "vocal": {"sha256": vocal_digest.hexdigest(), "retained": False},
                "instrumental": {
                    "sha256": instrumental_digest.hexdigest(),
                    "retained": False,
                },
                "converted_vocal": {
                    "sha256": converted_digest.hexdigest(),
                    "retained": False,
                },
                "final": {
                    "frame_count": frame_count,
                    "sha256": await asyncio.to_thread(self._sha256, final_path),
                    "retained": True,
                },
                "mixing": {
                    "target_peak_db": -1.0,
                    "chunks": mix_metrics,
                    "intermediates_cleaned": True,
                },
            }
            storage_key = await self.storage.put(
                final_path, namespace=f"jobs/{job_id}/outputs", suffix="wav"
            )
            return SingingPipelineResult(
                storage_key=storage_key,
                duration_seconds=duration,
                sample_rate=sample_rate,
                channels=channels,
                chunk_count=chunk_count,
                manifest=manifest,
            )

    async def _stage_input(self, key: str, destination: Path) -> None:
        with destination.open("wb") as stream:
            async for chunk in self.storage.open(key):
                await asyncio.to_thread(stream.write, chunk)

    @staticmethod
    def _process_chunk(
        pcm: bytes,
        sample_rate: int,
        channels: int,
        separator: SeparationModel,
        singing_model: SingingVoiceModel,
        voice_profile: dict[str, Any],
    ) -> tuple[bytes, bytes, bytes, bytes, MixingMetrics]:
        separated = separator.separate(pcm, sample_rate, channels)
        if not (
            len(separated.vocal_pcm_s16le) == len(separated.instrumental_pcm_s16le) == len(pcm)
        ):
            raise ValueError("separator must preserve frame count")
        converted = singing_model.convert(
            separated.vocal_pcm_s16le,
            sample_rate,
            channels,
            voice_profile,
        )
        if len(converted) != len(pcm):
            raise ValueError("singing conversion must preserve frame count")
        mixed, metrics = mix_pcm_s16le(
            separated.vocal_pcm_s16le,
            converted,
            separated.instrumental_pcm_s16le,
        )
        return (
            mixed,
            separated.vocal_pcm_s16le,
            separated.instrumental_pcm_s16le,
            converted,
            metrics,
        )

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()
