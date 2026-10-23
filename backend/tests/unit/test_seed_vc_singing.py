import json
import subprocess
import sys
import wave
from array import array
from pathlib import Path

import pytest

from app.core.config import Settings
from app.models import build_model_registry
from app.models.base import ModelCapability
from app.models.singing.registry import build_singing_registry
from app.models.singing.seed_vc import (
    SEED_VC_COMPONENTS,
    SEED_VC_DESCRIPTOR,
    SEED_VC_RUNTIME_REVISION,
    SeedVCRuntimeError,
    SeedVCSingingModel,
)


class FakeRuntime:
    def __init__(self, *, extra_samples: int = 0) -> None:
        self.calls: list[tuple[bytes, str]] = []
        self.extra_samples = extra_samples

    def convert(self, mono_pcm_s16le: bytes, reference_wav: str) -> bytes:
        self.calls.append((mono_pcm_s16le, reference_wav))
        samples = array("h")
        samples.frombytes(mono_pcm_s16le)
        return array("h", [-value for value in samples] + [7] * self.extra_samples).tobytes()


def prepare(tmp_path: Path) -> tuple[Settings, str]:
    runtime = tmp_path / "runtime"
    (runtime / "modules").mkdir(parents=True)
    (runtime / "modules" / "commons.py").write_text("", encoding="utf-8")
    checkpoint = tmp_path / "checkpoint"
    for component in SEED_VC_COMPONENTS:
        for name in component.files:
            path = checkpoint / component.directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
    (checkpoint / "voice-model-manifest.json").write_text(
        json.dumps(
            {
                "runtime_revision": SEED_VC_RUNTIME_REVISION,
                "files": [
                    {"repo_id": component.repo_id, "revision": component.revision}
                    for component in SEED_VC_COMPONENTS
                ],
            }
        ),
        encoding="utf-8",
    )
    storage = tmp_path / "storage"
    reference = storage / "profiles" / "reference.wav"
    reference.parent.mkdir(parents=True)
    with wave.open(str(reference), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24_000)
        output.writeframes(b"\0\0" * 10)
    settings = Settings(
        app_env="test",
        storage_path=storage,
        enable_seed_vc=True,
        enable_demucs=True,
        seed_vc_runtime_path=runtime,
        seed_vc_checkpoint_path=checkpoint,
    )
    return settings, "profiles/reference.wav"


def test_seed_vc_converts_mono_and_restores_stereo_layout(tmp_path: Path) -> None:
    settings, reference_key = prepare(tmp_path)
    runtime = FakeRuntime(extra_samples=3)
    model = SeedVCSingingModel(settings, runtime_factory=lambda *_args: runtime)
    profile = {"reference_storage_key": reference_key}
    stereo = array("h", [100, 300, -50, -150]).tobytes()

    with pytest.raises(RuntimeError, match="loaded"):
        model.convert(stereo, 44_100, 2, profile)
    model.load()
    converted = model.convert(stereo, 44_100, 2, profile)

    mono_sent, reference_path = runtime.calls[0]
    assert array("h", mono_sent).tolist() == [200, -100]
    assert reference_path == str((settings.storage_path / reference_key).resolve())
    # Converted mono is trimmed to the source length and duplicated per channel.
    assert array("h", converted).tolist() == [-200, -200, 100, 100]
    model.unload()
    with pytest.raises(RuntimeError, match="loaded"):
        model.convert(stereo, 44_100, 2, profile)


def test_seed_vc_pads_short_output_and_validates_inputs(tmp_path: Path) -> None:
    settings, reference_key = prepare(tmp_path)

    class ShortRuntime:
        def convert(self, mono_pcm_s16le: bytes, reference_wav: str) -> bytes:
            return mono_pcm_s16le[:2]

    model = SeedVCSingingModel(settings, runtime_factory=lambda *_args: ShortRuntime())
    model.load()
    profile = {"reference_storage_key": reference_key}

    assert (
        model.convert(array("h", [5, 6, 7]).tobytes(), 44_100, 1, profile)
        == array("h", [5, 0, 0]).tobytes()
    )
    with pytest.raises(SeedVCRuntimeError, match="44100 Hz"):
        model.convert(b"\0\0", 24_000, 1, profile)
    with pytest.raises(SeedVCRuntimeError, match="mono or stereo"):
        model.convert(b"\0\0" * 6, 44_100, 6, profile)
    with pytest.raises(SeedVCRuntimeError, match="no Seed-VC reference"):
        model.convert(b"\0\0", 44_100, 1, {})
    with pytest.raises(SeedVCRuntimeError, match="invalid or missing"):
        model.convert(b"\0\0", 44_100, 1, {"reference_storage_key": "../outside.wav"})


def test_seed_vc_rejects_incomplete_or_unpinned_checkpoint(tmp_path: Path) -> None:
    settings, _ = prepare(tmp_path)
    manifest = settings.seed_vc_checkpoint_path / "voice-model-manifest.json"
    manifest.write_text(json.dumps({"runtime_revision": "main", "files": []}), encoding="utf-8")
    with pytest.raises(SeedVCRuntimeError, match="provenance"):
        SeedVCSingingModel(settings, runtime_factory=lambda *_args: FakeRuntime()).load()

    first = SEED_VC_COMPONENTS[0]
    (settings.seed_vc_checkpoint_path / first.directory / first.files[0]).unlink()
    with pytest.raises(SeedVCRuntimeError, match="incomplete"):
        SeedVCSingingModel(settings, runtime_factory=lambda *_args: FakeRuntime()).load()


def test_seed_vc_requires_real_separation_and_is_labelled() -> None:
    without_demucs = Settings(app_env="test", enable_seed_vc=True)
    with_demucs = Settings(app_env="test", enable_seed_vc=True, enable_demucs=True)

    assert not without_demucs.seed_vc_available
    with pytest.raises(KeyError):
        build_singing_registry(include_mock=False, settings=without_demucs).create(
            SEED_VC_DESCRIPTOR.key
        )
    created = build_singing_registry(include_mock=False, settings=with_demucs).create(
        SEED_VC_DESCRIPTOR.key
    )
    assert isinstance(created, SeedVCSingingModel)
    published = build_model_registry(include_mock=False, include_seed_vc=True)
    assert tuple(published.descriptors(ModelCapability.SINGING_VOICE_CONVERSION)) == (
        SEED_VC_DESCRIPTOR,
    )
    assert SEED_VC_DESCRIPTOR.metadata["code_license"] == "GPL-3.0"
    assert "research" in SEED_VC_DESCRIPTOR.metadata["usage"]


def test_api_import_does_not_load_seed_vc_runtime() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import app.main; "
                "assert 'modules.commons' not in sys.modules; "
                "assert 'transformers' not in sys.modules; "
                "assert 'dac' not in sys.modules"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
