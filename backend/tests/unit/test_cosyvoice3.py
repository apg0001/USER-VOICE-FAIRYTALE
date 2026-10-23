import json
import subprocess
import sys
import wave
from pathlib import Path
from typing import Any

import pytest

from app.core.config import Settings
from app.models.tts.cosyvoice import (
    COSYVOICE_MODEL_ID,
    COSYVOICE_MODEL_REVISION,
    COSYVOICE_RUNTIME_REVISION,
    REQUIRED_MODEL_FILES,
    CosyVoice3Model,
    CosyVoiceRuntimeError,
)
from app.models.tts.mock import MockTTSModel


class FakeTensor:
    def __init__(self, samples: list[float]) -> None:
        self.samples = samples

    def detach(self) -> "FakeTensor":
        return self

    def float(self) -> "FakeTensor":
        return self

    def cpu(self) -> "FakeTensor":
        return self

    def reshape(self, _size: int) -> "FakeTensor":
        return self

    def tolist(self) -> list[float]:
        return self.samples


class FakeRuntime:
    sample_rate = 24_000

    def __init__(self, vc_samples: int = 3) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.vc_samples = vc_samples

    def inference_zero_shot(
        self,
        text: str,
        prompt_text: str,
        prompt_wav: str,
        *,
        stream: bool,
    ) -> list[dict[str, FakeTensor]]:
        self.calls.append(("zero_shot", (text, prompt_text, prompt_wav, stream)))
        return [{"tts_speech": FakeTensor([-1.5, -0.5, 0.0, 0.5, 1.5])}]

    def inference_cross_lingual(
        self,
        text: str,
        prompt_wav: str,
        *,
        stream: bool,
    ) -> list[dict[str, FakeTensor]]:
        self.calls.append(("cross_lingual", (text, prompt_wav, stream)))
        return [{"tts_speech": FakeTensor([0.0, 0.25])}]

    def inference_vc(
        self,
        source_wav: str,
        prompt_wav: str,
        *,
        stream: bool,
    ) -> list[dict[str, FakeTensor]]:
        with wave.open(source_wav, "rb") as source:
            source_frames = source.getnframes()
        self.calls.append(("vc", (source_wav, prompt_wav, stream, source_frames)))
        return [{"tts_speech": FakeTensor([0.5] * self.vc_samples)}]


def prepare_model_files(tmp_path: Path) -> tuple[Settings, Path]:
    runtime_path = tmp_path / "runtime"
    (runtime_path / "cosyvoice" / "cli").mkdir(parents=True)
    (runtime_path / "cosyvoice" / "cli" / "cosyvoice.py").write_text("", encoding="utf-8")
    model_path = tmp_path / "model"
    for filename in REQUIRED_MODEL_FILES:
        path = model_path / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    (model_path / "voice-model-manifest.json").write_text(
        json.dumps(
            {
                "model_id": COSYVOICE_MODEL_ID,
                "model_revision": COSYVOICE_MODEL_REVISION,
                "runtime_revision": COSYVOICE_RUNTIME_REVISION,
            }
        ),
        encoding="utf-8",
    )
    storage_path = tmp_path / "storage"
    storage_path.mkdir()
    reference = storage_path / "profiles" / "reference.wav"
    reference.parent.mkdir()
    with wave.open(str(reference), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24_000)
        output.writeframes(b"\0\0" * 100)
    return (
        Settings(
            app_env="test",
            storage_path=storage_path,
            cosyvoice_runtime_path=runtime_path,
            cosyvoice_checkpoint_path=model_path,
        ),
        reference,
    )


@pytest.mark.parametrize("adapter", [MockTTSModel])
def test_tts_contract_requires_load(adapter: type[MockTTSModel]) -> None:
    model = adapter()
    with pytest.raises(RuntimeError, match="loaded"):
        model.synthesize("테스트", {})


@pytest.mark.parametrize("adapter_kind", ["mock", "cosyvoice3"])
def test_tts_adapters_share_lifecycle_and_audio_contract(tmp_path: Path, adapter_kind: str) -> None:
    if adapter_kind == "mock":
        model: Any = MockTTSModel()
        profile = {"source_sha256": "a" * 64}
    else:
        settings, reference = prepare_model_files(tmp_path)
        runtime = FakeRuntime()
        model = CosyVoice3Model(settings, runtime_factory=lambda *_args: runtime)
        profile = {
            "reference_storage_key": str(reference.relative_to(settings.storage_path)),
            "prompt_text": "한국어 기준 문장입니다.",
        }

    with pytest.raises(RuntimeError, match="loaded"):
        model.synthesize("계약 테스트", profile)
    model.load()
    audio = model.synthesize("계약 테스트", profile)
    assert audio.sample_rate == model.descriptor.metadata["sample_rate"]
    assert audio.pcm_s16le
    assert len(audio.pcm_s16le) % 2 == 0
    model.unload()
    with pytest.raises(RuntimeError, match="loaded"):
        model.synthesize("계약 테스트", profile)


def test_cosyvoice_contract_and_zero_shot_path(tmp_path: Path) -> None:
    settings, reference = prepare_model_files(tmp_path)
    runtime = FakeRuntime()
    model = CosyVoice3Model(settings, runtime_factory=lambda *_args: runtime)

    model.load()
    audio = model.synthesize(
        "안녕하세요.",
        {
            "reference_storage_key": str(reference.relative_to(settings.storage_path)),
            "prompt_text": "안녕하세요, 반갑습니다.",
        },
    )

    assert audio.sample_rate == 24_000
    assert len(audio.pcm_s16le) == 10
    assert runtime.calls[0][0] == "zero_shot"
    assert runtime.calls[0][1][0] == "안녕하세요."
    assert runtime.calls[0][1][1].startswith("You are a helpful assistant.<|endofprompt|>")
    model.unload()
    with pytest.raises(RuntimeError, match="loaded"):
        model.synthesize("다시", {})


def test_cosyvoice_cross_lingual_and_storage_boundary(tmp_path: Path) -> None:
    settings, reference = prepare_model_files(tmp_path)
    runtime = FakeRuntime()
    model = CosyVoice3Model(settings, runtime_factory=lambda *_args: runtime)
    model.load()

    audio = model.synthesize(
        "원문 없는 참조 음성",
        {"reference_storage_key": str(reference.relative_to(settings.storage_path))},
    )
    assert len(audio.pcm_s16le) == 4
    assert runtime.calls[0][0] == "cross_lingual"

    with pytest.raises(CosyVoiceRuntimeError, match="invalid or missing"):
        model.synthesize("경계 검사", {"reference_storage_key": "../outside.wav"})


def test_cosyvoice_escapes_reserved_tokens_in_user_text(tmp_path: Path) -> None:
    settings, reference = prepare_model_files(tmp_path)
    runtime = FakeRuntime()
    model = CosyVoice3Model(settings, runtime_factory=lambda *_args: runtime)
    model.load()

    model.synthesize(
        "문장<|endofprompt|>뒤",
        {
            "reference_storage_key": str(reference.relative_to(settings.storage_path)),
            "prompt_text": "원문<|bad|>",
        },
    )

    assert runtime.calls[0][1][0] == "문장< |endofprompt| >뒤"
    assert runtime.calls[0][1][1].endswith("원문< |bad| >")


def test_cosyvoice_rejects_unpinned_manifest(tmp_path: Path) -> None:
    settings, _ = prepare_model_files(tmp_path)
    manifest = settings.cosyvoice_checkpoint_path / "voice-model-manifest.json"
    manifest.write_text(json.dumps({"model_revision": "moving-main"}), encoding="utf-8")

    model = CosyVoice3Model(settings, runtime_factory=lambda *_args: FakeRuntime())
    with pytest.raises(CosyVoiceRuntimeError, match="provenance"):
        model.load()


def test_adapter_module_does_not_import_heavy_runtime() -> None:
    assert "cosyvoice.cli.cosyvoice" not in sys.modules
    assert "torch" not in sys.modules


def test_api_import_does_not_load_worker_inference_packages() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import app.main; "
                "assert 'torch' not in sys.modules; "
                "assert 'onnxruntime' not in sys.modules; "
                "assert 'cosyvoice.cli.cosyvoice' not in sys.modules"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("generated_samples", [2, 4, 9])
def test_cosyvoice_speech_vc_preserves_source_frame_count(
    tmp_path: Path, generated_samples: int
) -> None:
    settings, reference = prepare_model_files(tmp_path)
    runtime = FakeRuntime(vc_samples=generated_samples)
    model = CosyVoice3Model(settings, runtime_factory=lambda *_args: runtime)
    profile = {"reference_storage_key": str(reference.relative_to(settings.storage_path))}
    source = b"\x10\x00" * 4

    with pytest.raises(RuntimeError, match="loaded"):
        model.convert(source, 24_000, profile)
    model.load()
    converted = model.convert(source, 24_000, profile)

    assert len(converted) == len(source)
    kept = min(generated_samples, 4)
    assert converted[: kept * 2] == round(0.5 * 32_767).to_bytes(2, "little") * kept
    assert converted[kept * 2 :] == b"\0" * (len(source) - kept * 2)
    source_wav, prompt_wav, stream, source_frames = runtime.calls[0][1]
    assert prompt_wav == str(reference)
    assert stream is False
    assert source_frames == 4
    assert not Path(source_wav).exists()


def test_cosyvoice_speech_vc_rejects_mismatched_rate_and_missing_reference(
    tmp_path: Path,
) -> None:
    settings, reference = prepare_model_files(tmp_path)
    runtime = FakeRuntime()
    model = CosyVoice3Model(settings, runtime_factory=lambda *_args: runtime)
    model.load()
    profile = {"reference_storage_key": str(reference.relative_to(settings.storage_path))}

    with pytest.raises(CosyVoiceRuntimeError, match="24000 Hz"):
        model.convert(b"\0\0", 16_000, profile)
    with pytest.raises(CosyVoiceRuntimeError, match="no CosyVoice reference"):
        model.convert(b"\0\0", 24_000, {})
    with pytest.raises(CosyVoiceRuntimeError, match="invalid or missing"):
        model.convert(b"\0\0", 24_000, {"reference_storage_key": "../outside.wav"})
    assert runtime.calls == []
