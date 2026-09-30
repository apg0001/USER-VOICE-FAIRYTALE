from pathlib import Path


class AudioValidationError(ValueError):
    def __init__(self, code: str, user_message: str) -> None:
        self.code = code
        self.user_message = user_message
        super().__init__(code)


ALLOWED_MEDIA: dict[str, tuple[str, ...]] = {
    ".wav": ("audio/wav", "audio/x-wav", "audio/wave"),
    ".mp3": ("audio/mpeg", "audio/mp3"),
    ".m4a": ("audio/mp4", "audio/x-m4a", "video/mp4"),
    ".flac": ("audio/flac", "audio/x-flac"),
}


def detect_container(header: bytes) -> str | None:
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WAVE":
        return ".wav"
    if header.startswith(b"fLaC"):
        return ".flac"
    if header.startswith(b"ID3") or (
        len(header) >= 2 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0
    ):
        return ".mp3"
    if len(header) >= 12 and header[4:8] == b"ftyp":
        return ".m4a"
    return None


def validate_upload_identity(path: Path, original_filename: str, content_type: str) -> str:
    extension = Path(original_filename).suffix.lower()
    if extension not in ALLOWED_MEDIA:
        raise AudioValidationError(
            "UNSUPPORTED_EXTENSION",
            "WAV, MP3, M4A 또는 FLAC 파일을 업로드해 주세요.",
        )
    normalized_mime = content_type.partition(";")[0].strip().lower()
    if normalized_mime not in ALLOWED_MEDIA[extension]:
        raise AudioValidationError(
            "MIME_MISMATCH",
            "파일 형식과 Content-Type이 일치하지 않습니다.",
        )
    with path.open("rb") as stream:
        detected = detect_container(stream.read(64))
    if detected != extension:
        raise AudioValidationError(
            "CONTENT_MISMATCH",
            "파일 확장자와 실제 미디어 형식이 일치하지 않습니다.",
        )
    return extension

