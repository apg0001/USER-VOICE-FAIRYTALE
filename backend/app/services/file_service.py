import asyncio
import hashlib
import tempfile
from pathlib import Path

from app.audio.preprocessing import AudioPreprocessingPipeline
from app.audio.types import AudioArtifact, PreprocessingConfig
from app.audio.validation import AudioValidationError, validate_upload_identity
from app.storage import ObjectStorage


class FileService:
    def __init__(
        self,
        storage: ObjectStorage,
        pipeline: AudioPreprocessingPipeline,
        *,
        max_upload_size: int,
        min_duration_seconds: float = 1.0,
        max_duration_seconds: float = 3600.0,
    ) -> None:
        self.storage = storage
        self.pipeline = pipeline
        self.max_upload_size = max_upload_size
        self.min_duration_seconds = min_duration_seconds
        self.max_duration_seconds = max_duration_seconds

    async def ingest_audio(
        self,
        source: Path,
        *,
        original_filename: str,
        content_type: str,
        namespace: str,
        config: PreprocessingConfig,
    ) -> AudioArtifact:
        size = (await asyncio.to_thread(source.stat)).st_size
        if size <= 0 or size > self.max_upload_size:
            raise AudioValidationError("INVALID_SIZE", "허용된 파일 크기를 확인해 주세요.")
        extension = await asyncio.to_thread(
            validate_upload_identity, source, original_filename, content_type
        )
        probe, quality = await self.pipeline.inspect(source)
        if not self.min_duration_seconds <= probe.duration_seconds <= self.max_duration_seconds:
            raise AudioValidationError(
                "INVALID_DURATION",
                f"{self.min_duration_seconds:g}초 이상 "
                f"{self.max_duration_seconds:g}초 이하의 음성을 업로드해 주세요.",
            )
        if quality.speech_seconds < self.min_duration_seconds:
            raise AudioValidationError(
                "INSUFFICIENT_SPEECH",
                "충분한 음성을 찾지 못했습니다. 조용한 환경에서 다시 녹음해 주세요.",
            )
        digest = await asyncio.to_thread(self._sha256, source)
        original_key = await self.storage.put(
            source,
            namespace=f"{namespace}/original",
            suffix=extension,
        )
        try:
            with tempfile.TemporaryDirectory(prefix="voice-preprocess-") as temp_dir:
                cleaned = Path(temp_dir) / "cleaned.wav"
                await self.pipeline.process(source, cleaned, config)
                cleaned_key = await self.storage.put(
                    cleaned, namespace=f"{namespace}/cleaned", suffix="wav"
                )
        except Exception:
            await self.storage.delete(original_key)
            raise
        return AudioArtifact(
            original_storage_key=original_key,
            cleaned_storage_key=cleaned_key,
            sha256=digest,
            probe=probe,
            quality=quality,
            preprocessing=config,
        )

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

