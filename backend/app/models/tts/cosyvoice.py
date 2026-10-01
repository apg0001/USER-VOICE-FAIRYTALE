import gc
import importlib
import json
import sys
from array import array
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Protocol, cast

from app.core.config import Settings
from app.models.base import ModelCapability, ModelDescriptor
from app.models.tts.base import TTSAudio

COSYVOICE_RUNTIME_REVISION = "074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc"
COSYVOICE_MODEL_REVISION = "29e01c4e8d000f4bcd70751be16fa94bf3d85a18"
COSYVOICE_MODEL_ID = "FunAudioLLM/Fun-CosyVoice3-0.5B-2512"
COSYVOICE_SYSTEM_PROMPT = "You are a helpful assistant."

COSYVOICE3_DESCRIPTOR = ModelDescriptor(
    key="cosyvoice3-0.5b-2512",
    display_name="Fun-CosyVoice3 0.5B (2512)",
    version=COSYVOICE_MODEL_REVISION[:12],
    capabilities=(ModelCapability.GENERAL_TTS, ModelCapability.LONG_FORM_TTS),
    requires_gpu=True,
    metadata={
        "runtime": "QwenAudio/CosyVoice",
        "runtime_revision": COSYVOICE_RUNTIME_REVISION,
        "model_id": COSYVOICE_MODEL_ID,
        "model_revision": COSYVOICE_MODEL_REVISION,
        "code_license": "Apache-2.0",
        "weights_license": "Apache-2.0",
        "languages": ["ko", "en", "zh", "ja", "de", "es", "fr", "it", "ru"],
        "sample_rate": 24_000,
        "estimated_vram_mb": 6_708,
    },
)

REQUIRED_MODEL_FILES = (
    "CosyVoice-BlankEN/config.json",
    "CosyVoice-BlankEN/generation_config.json",
    "CosyVoice-BlankEN/merges.txt",
    "CosyVoice-BlankEN/model.safetensors",
    "CosyVoice-BlankEN/tokenizer_config.json",
    "CosyVoice-BlankEN/vocab.json",
    "campplus.onnx",
    "cosyvoice3.yaml",
    "flow.pt",
    "hift.pt",
    "llm.pt",
    "speech_tokenizer_v3.onnx",
    "voice-model-manifest.json",
)


class CosyVoiceRuntime(Protocol):
    sample_rate: int

    def inference_zero_shot(
        self,
        text: str,
        prompt_text: str,
        prompt_wav: str,
        *,
        stream: bool,
    ) -> Iterable[dict[str, Any]]: ...

    def inference_cross_lingual(
        self,
        text: str,
        prompt_wav: str,
        *,
        stream: bool,
    ) -> Iterable[dict[str, Any]]: ...


RuntimeFactory = Callable[[Path, Path, bool], CosyVoiceRuntime]


class CosyVoiceRuntimeError(RuntimeError):
    pass


def _load_runtime(runtime_path: Path, model_path: Path, fp16: bool) -> CosyVoiceRuntime:
    matcha_path = runtime_path / "third_party" / "Matcha-TTS"
    for path in (runtime_path, matcha_path):
        path_value = str(path)
        if path_value not in sys.path:
            sys.path.insert(0, path_value)
    try:
        module = importlib.import_module("cosyvoice.cli.cosyvoice")
    except ImportError as error:
        raise CosyVoiceRuntimeError("CosyVoice worker dependencies are not installed") from error
    return cast(
        CosyVoiceRuntime,
        module.CosyVoice3(
            model_dir=str(model_path),
            fp16=fp16,
            load_trt=False,
            load_vllm=False,
        ),
    )


class CosyVoice3TTSModel:
    descriptor = COSYVOICE3_DESCRIPTOR

    def __init__(
        self,
        settings: Settings,
        *,
        runtime_factory: RuntimeFactory = _load_runtime,
    ) -> None:
        self.settings = settings
        self.runtime_factory = runtime_factory
        self.runtime: CosyVoiceRuntime | None = None

    def load(self) -> None:
        runtime_path = self.settings.cosyvoice_runtime_path.resolve()
        model_path = self.settings.cosyvoice_checkpoint_path.resolve()
        if not (runtime_path / "cosyvoice" / "cli" / "cosyvoice.py").is_file():
            raise CosyVoiceRuntimeError(f"pinned CosyVoice runtime is missing: {runtime_path}")
        missing = [name for name in REQUIRED_MODEL_FILES if not (model_path / name).is_file()]
        if missing:
            raise CosyVoiceRuntimeError(
                f"CosyVoice checkpoint is incomplete ({len(missing)} files missing)"
            )
        self._validate_manifest(model_path / "voice-model-manifest.json")
        self.runtime = self.runtime_factory(
            runtime_path,
            model_path,
            self.settings.cosyvoice_fp16,
        )

    def synthesize(self, text: str, voice_profile: dict[str, Any]) -> TTSAudio:
        if self.runtime is None:
            raise RuntimeError("model must be loaded before inference")
        reference_key = voice_profile.get("reference_storage_key")
        if not isinstance(reference_key, str) or not reference_key:
            raise CosyVoiceRuntimeError("voice profile has no CosyVoice reference")
        reference_path = self._reference_path(reference_key)
        prompt_text = voice_profile.get("prompt_text")
        safe_text = self._sanitize_user_text(text)
        if isinstance(prompt_text, str) and prompt_text.strip():
            outputs = self.runtime.inference_zero_shot(
                safe_text,
                (
                    f"{COSYVOICE_SYSTEM_PROMPT}<|endofprompt|>"
                    f"{self._sanitize_user_text(prompt_text.strip())}"
                ),
                str(reference_path),
                stream=False,
            )
        else:
            outputs = self.runtime.inference_cross_lingual(
                f"{COSYVOICE_SYSTEM_PROMPT}<|endofprompt|>{safe_text}",
                str(reference_path),
                stream=False,
            )
        return TTSAudio(
            pcm_s16le=self._to_pcm(outputs),
            sample_rate=self.runtime.sample_rate,
        )

    def unload(self) -> None:
        self.runtime = None
        gc.collect()
        torch_module = sys.modules.get("torch")
        if torch_module is not None and torch_module.cuda.is_available():
            torch_module.cuda.empty_cache()

    def _reference_path(self, key: str) -> Path:
        root = self.settings.storage_path.resolve()
        candidate = (root / key).resolve()
        if root not in candidate.parents or not candidate.is_file():
            raise CosyVoiceRuntimeError("invalid or missing voice reference")
        return candidate

    @staticmethod
    def _sanitize_user_text(value: str) -> str:
        return value.replace("<|", "< |").replace("|>", "| >")

    @staticmethod
    def _to_pcm(outputs: Iterable[dict[str, Any]]) -> bytes:
        pcm = array("h")
        for output in outputs:
            speech = output.get("tts_speech")
            if speech is None:
                raise CosyVoiceRuntimeError("runtime output has no tts_speech")
            values = speech.detach().float().cpu().reshape(-1).tolist()
            pcm.extend(round(max(-1.0, min(1.0, float(value))) * 32_767) for value in values)
        if not pcm:
            raise CosyVoiceRuntimeError("runtime produced no audio")
        if sys.byteorder != "little":
            pcm.byteswap()
        return pcm.tobytes()

    @staticmethod
    def _validate_manifest(path: Path) -> None:
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise CosyVoiceRuntimeError("CosyVoice checkpoint manifest is invalid") from error
        expected = {
            "model_id": COSYVOICE_MODEL_ID,
            "model_revision": COSYVOICE_MODEL_REVISION,
            "runtime_revision": COSYVOICE_RUNTIME_REVISION,
        }
        if any(manifest.get(key) != value for key, value in expected.items()):
            raise CosyVoiceRuntimeError("CosyVoice checkpoint provenance does not match")
