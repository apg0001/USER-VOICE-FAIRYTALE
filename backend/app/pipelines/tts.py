import asyncio
import os
import re
import tempfile
import wave
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.models.tts import TTSModel
from app.storage import ObjectStorage

CheckpointCallback = Callable[[int, int], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class TTSPipelineResult:
    storage_key: str
    duration_seconds: float
    sample_rate: int
    chunk_count: int


def chunk_text(text: str, *, max_chars: int = 280) -> list[str]:
    normalized = " ".join(text.split())
    if not normalized:
        raise ValueError("TTS text must not be empty")
    sentences = [item.strip() for item in re.split(r"(?<=[.!?。！？])\s*", normalized)]
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        while len(sentence) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            split_at = sentence.rfind(" ", 0, max_chars + 1)
            if split_at < 1:
                split_at = max_chars
            chunks.append(sentence[:split_at].strip())
            sentence = sentence[split_at:].strip()
        candidate = f"{current} {sentence}".strip()
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


class TTSPipeline:
    def __init__(self, storage: ObjectStorage, *, pause_ms: int = 180) -> None:
        self.storage = storage
        self.pause_ms = pause_ms

    async def run(
        self,
        *,
        job_id: str,
        text: str,
        model: TTSModel,
        voice_profile: dict[str, Any],
        max_chunk_chars: int,
        on_checkpoint: CheckpointCallback | None = None,
    ) -> TTSPipelineResult:
        chunks = chunk_text(text, max_chars=max_chunk_chars)
        rendered = []
        sample_rate: int | None = None
        for index, chunk in enumerate(chunks, start=1):
            audio = await asyncio.to_thread(model.synthesize, chunk, voice_profile)
            if sample_rate is not None and audio.sample_rate != sample_rate:
                raise ValueError("TTS chunks must use one sample rate")
            sample_rate = audio.sample_rate
            rendered.append(audio.pcm_s16le)
            if on_checkpoint is not None:
                await on_checkpoint(index, len(chunks))

        assert sample_rate is not None
        silence = b"\x00\x00" * round(sample_rate * self.pause_ms / 1000)
        pcm = silence.join(rendered)
        descriptor, temp_name = tempfile.mkstemp(prefix="tts-result-", suffix=".wav")
        os.close(descriptor)
        temp_path = Path(temp_name)
        try:
            await asyncio.to_thread(self._write_wav, temp_path, pcm, sample_rate)
            storage_key = await self.storage.put(
                temp_path, namespace=f"jobs/{job_id}/outputs", suffix="wav"
            )
        finally:
            await asyncio.to_thread(temp_path.unlink, missing_ok=True)
        duration = len(pcm) / 2 / sample_rate
        return TTSPipelineResult(storage_key, duration, sample_rate, len(chunks))

    @staticmethod
    def _write_wav(path: Path, pcm: bytes, sample_rate: int) -> None:
        with wave.open(str(path), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(sample_rate)
            stream.writeframes(pcm)
