import asyncio
import io
import os
import tempfile
import wave
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.models.voice_conversion import VoiceConversionModel
from app.storage import ObjectStorage

ConversionCheckpoint = Callable[[int, int], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class VoiceConversionResult:
    storage_key: str
    duration_seconds: float
    sample_rate: int
    chunk_count: int


class VoiceConversionPipeline:
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
        model: VoiceConversionModel,
        voice_profile: dict[str, Any],
        on_checkpoint: ConversionCheckpoint | None = None,
    ) -> VoiceConversionResult:
        source = bytearray()
        async for chunk in self.storage.open(input_storage_key):
            source.extend(chunk)
        sample_rate, frames = await asyncio.to_thread(self._read_wav, bytes(source))
        duration = len(frames) / 2 / sample_rate
        if duration <= 0 or duration > self.max_duration_seconds:
            raise ValueError("voice conversion input duration is invalid")

        chunk_bytes = sample_rate * self.chunk_seconds * 2
        source_chunks = [
            frames[offset : offset + chunk_bytes] for offset in range(0, len(frames), chunk_bytes)
        ]
        converted: list[bytes] = []
        for index, chunk in enumerate(source_chunks, start=1):
            converted.append(
                await asyncio.to_thread(model.convert, chunk, sample_rate, voice_profile)
            )
            if len(converted[-1]) != len(chunk):
                raise ValueError("voice conversion must preserve frame count")
            if on_checkpoint is not None:
                await on_checkpoint(index, len(source_chunks))

        descriptor, temp_name = tempfile.mkstemp(prefix="speech-vc-", suffix=".wav")
        os.close(descriptor)
        temp_path = Path(temp_name)
        try:
            await asyncio.to_thread(self._write_wav, temp_path, b"".join(converted), sample_rate)
            key = await self.storage.put(
                temp_path, namespace=f"jobs/{job_id}/outputs", suffix="wav"
            )
        finally:
            await asyncio.to_thread(temp_path.unlink, missing_ok=True)
        return VoiceConversionResult(key, duration, sample_rate, len(source_chunks))

    @staticmethod
    def _read_wav(payload: bytes) -> tuple[int, bytes]:
        try:
            with wave.open(io.BytesIO(payload), "rb") as stream:
                if stream.getnchannels() != 1 or stream.getsampwidth() != 2:
                    raise ValueError("voice conversion input must be mono PCM S16LE WAV")
                if stream.getcomptype() != "NONE":
                    raise ValueError("compressed WAV is not supported")
                return stream.getframerate(), stream.readframes(stream.getnframes())
        except (EOFError, wave.Error) as error:
            raise ValueError("invalid WAV input") from error

    @staticmethod
    def _write_wav(path: Path, frames: bytes, sample_rate: int) -> None:
        with wave.open(str(path), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(sample_rate)
            stream.writeframes(frames)
