from pathlib import Path

import pytest

from app.audio.validation import AudioValidationError, detect_container, validate_upload_identity


def test_magic_detection_supports_allowed_containers() -> None:
    assert detect_container(b"RIFF\x00\x00\x00\x00WAVEfmt ") == ".wav"
    assert detect_container(b"fLaC\x00\x00") == ".flac"
    assert detect_container(b"ID3\x04\x00") == ".mp3"
    assert detect_container(b"\x00\x00\x00\x18ftypM4A ") == ".m4a"


def test_disguised_upload_is_rejected_before_media_tool(tmp_path: Path) -> None:
    source = tmp_path / "malware.wav"
    source.write_bytes(b"MZ-not-a-wave")

    with pytest.raises(AudioValidationError) as captured:
        validate_upload_identity(source, "voice.wav", "audio/wav")

    assert captured.value.code == "CONTENT_MISMATCH"


def test_mime_must_match_extension(tmp_path: Path) -> None:
    source = tmp_path / "voice.wav"
    source.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")

    with pytest.raises(AudioValidationError) as captured:
        validate_upload_identity(source, "voice.wav", "application/octet-stream")

    assert captured.value.code == "MIME_MISMATCH"
