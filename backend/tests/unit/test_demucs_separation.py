import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from app.core.config import Settings
from app.models.separation import demucs
from app.models.separation.demucs import (
    HTDEMUCS_DESCRIPTOR,
    HTDEMUCS_WEIGHTS_FILE,
    DemucsRuntimeError,
    HTDemucsSeparationModel,
)
from app.models.separation.mock import MockSeparationModel
from app.models.separation.registry import build_separation_registry, separator_key


class FakeRuntime:
    def __init__(self, *, drop_bytes: int = 0) -> None:
        self.calls: list[tuple[int, int]] = []
        self.drop_bytes = drop_bytes

    def separate(self, pcm_s16le: bytes, channels: int) -> tuple[bytes, bytes]:
        self.calls.append((len(pcm_s16le), channels))
        kept = pcm_s16le[: len(pcm_s16le) - self.drop_bytes]
        return kept, bytes(len(kept))


def prepare_checkpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    model_path = tmp_path / "demucs"
    model_path.mkdir()
    (model_path / "htdemucs.yaml").write_text("models: ['955717e8']\n", encoding="utf-8")
    weights = model_path / HTDEMUCS_WEIGHTS_FILE
    weights.write_bytes(b"pinned weights")
    monkeypatch.setattr(
        demucs, "HTDEMUCS_WEIGHTS_SHA256", hashlib.sha256(b"pinned weights").hexdigest()
    )
    return Settings(app_env="test", enable_demucs=True, demucs_model_path=model_path)


def test_htdemucs_lifecycle_and_frame_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = prepare_checkpoint(tmp_path, monkeypatch)
    runtime = FakeRuntime()
    loaded_with: list[tuple[Path, str]] = []

    def factory(path: Path, device: str) -> FakeRuntime:
        loaded_with.append((path, device))
        return runtime

    model = HTDemucsSeparationModel(settings, runtime_factory=factory)
    stereo = b"\x01\x00\x02\x00" * 8

    with pytest.raises(RuntimeError, match="loaded"):
        model.separate(stereo, 44_100, 2)
    model.load()
    separated = model.separate(stereo, 44_100, 2)

    assert loaded_with == [(settings.demucs_model_path.resolve(), "cuda:0")]
    assert separated.vocal_pcm_s16le == stereo
    assert len(separated.instrumental_pcm_s16le) == len(stereo)
    assert runtime.calls == [(len(stereo), 2)]
    model.unload()
    with pytest.raises(RuntimeError, match="loaded"):
        model.separate(stereo, 44_100, 2)


def test_htdemucs_rejects_wrong_rate_channels_and_frame_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = prepare_checkpoint(tmp_path, monkeypatch)
    model = HTDemucsSeparationModel(
        settings, runtime_factory=lambda *_args: FakeRuntime(drop_bytes=4)
    )
    model.load()

    with pytest.raises(DemucsRuntimeError, match="44100 Hz"):
        model.separate(b"\0\0", 24_000, 1)
    with pytest.raises(DemucsRuntimeError, match="mono or stereo"):
        model.separate(b"\0\0" * 6, 44_100, 6)
    with pytest.raises(DemucsRuntimeError, match="frame count"):
        model.separate(b"\0\0" * 8, 44_100, 2)


def test_htdemucs_rejects_missing_or_unpinned_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = Settings(app_env="test", demucs_model_path=tmp_path / "absent")
    with pytest.raises(DemucsRuntimeError, match="missing"):
        HTDemucsSeparationModel(missing, runtime_factory=lambda *_args: FakeRuntime()).load()

    settings = prepare_checkpoint(tmp_path, monkeypatch)
    (settings.demucs_model_path / HTDEMUCS_WEIGHTS_FILE).write_bytes(b"moved weights")
    model = HTDemucsSeparationModel(settings, runtime_factory=lambda *_args: FakeRuntime())
    with pytest.raises(DemucsRuntimeError, match="checksum"):
        model.load()
    assert model.runtime is None


def test_real_separator_is_opt_in_and_labelled_research_only() -> None:
    default = Settings(app_env="test")
    enabled = Settings(app_env="test", enable_demucs=True)

    assert separator_key(default) == MockSeparationModel.descriptor.key
    assert separator_key(enabled) == HTDEMUCS_DESCRIPTOR.key
    with pytest.raises(KeyError):
        build_separation_registry(include_mock=True, settings=default).create(
            HTDEMUCS_DESCRIPTOR.key
        )
    created = build_separation_registry(include_mock=False, settings=enabled).create(
        HTDEMUCS_DESCRIPTOR.key
    )
    assert isinstance(created, HTDemucsSeparationModel)
    assert "research" in HTDEMUCS_DESCRIPTOR.metadata["usage"]


def test_api_import_does_not_load_demucs() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import app.main; assert 'demucs' not in sys.modules",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
