import gc
import hashlib
import importlib
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from app.core.config import Settings
from app.models.base import ModelCapability, ModelDescriptor
from app.models.separation.base import SeparatedAudio

DEMUCS_PACKAGE_VERSION = "4.1.0"
HTDEMUCS_SIGNATURE = "955717e8"
HTDEMUCS_WEIGHTS_FILE = "955717e8-8726e21a.th"
HTDEMUCS_WEIGHTS_SHA256 = "8726e21a993978c7ba086d3872e7608d7d5bfca646ca4aca459ffda844faa8b4"
HTDEMUCS_SAMPLE_RATE = 44_100

HTDEMUCS_DESCRIPTOR = ModelDescriptor(
    key="demucs-htdemucs-4.1.0",
    display_name="Demucs v4 Hybrid Transformer (htdemucs)",
    version=HTDEMUCS_SIGNATURE,
    capabilities=(ModelCapability.SINGING_VOICE_CONVERSION,),
    requires_gpu=True,
    metadata={
        "role": "vocal-separation",
        "runtime": "demucs",
        "runtime_version": DEMUCS_PACKAGE_VERSION,
        "weights_file": HTDEMUCS_WEIGHTS_FILE,
        "weights_sha256": HTDEMUCS_WEIGHTS_SHA256,
        "code_license": "MIT",
        "weights_license": "unspecified; maintainer described the weights as research-only",
        "usage": "research/non-commercial; re-review before commercial release",
        "stems": ["vocal", "instrumental"],
        "sample_rate": HTDEMUCS_SAMPLE_RATE,
        "estimated_vram_mb": 875,
    },
)


class DemucsRuntime(Protocol):
    def separate(self, pcm_s16le: bytes, channels: int) -> tuple[bytes, bytes]: ...


RuntimeFactory = Callable[[Path, str], DemucsRuntime]


class DemucsRuntimeError(RuntimeError):
    pass


class _TorchDemucsRuntime:
    """Runs the pinned bag of models; only constructed inside the inference worker."""

    def __init__(self, model_path: Path, device: str) -> None:
        try:
            self._np = importlib.import_module("numpy")
            self._torch = importlib.import_module("torch")
            apply = importlib.import_module("demucs.apply")
            pretrained = importlib.import_module("demucs.pretrained")
        except ImportError as error:
            raise DemucsRuntimeError("Demucs worker dependencies are not installed") from error
        self._apply_model = apply.apply_model
        self.device = device
        self.model = pretrained.get_model("htdemucs", repo=model_path)
        self.model.to(device)
        self.model.eval()
        self.vocal_index = list(self.model.sources).index("vocals")

    def separate(self, pcm_s16le: bytes, channels: int) -> tuple[bytes, bytes]:
        np, torch = self._np, self._torch
        frames = np.frombuffer(pcm_s16le, dtype="<i2").reshape(-1, channels).T
        mix = torch.from_numpy(frames.astype(np.float32) / 32_768)
        if channels == 1:
            mix = mix.expand(2, -1)
        reference = mix.mean(0)
        mean, std = reference.mean(), reference.std() + 1e-8
        with torch.inference_mode():
            stems = self._apply_model(
                self.model,
                ((mix - mean) / std)[None],
                shifts=0,
                split=True,
                overlap=0.25,
                device=self.device,
            )[0]
        stems = stems * std + mean
        vocal = stems[self.vocal_index]
        instrumental = stems.sum(0) - vocal
        return self._to_pcm(vocal, channels), self._to_pcm(instrumental, channels)

    def _to_pcm(self, stem: object, channels: int) -> bytes:
        np, torch = self._np, self._torch
        tensor = torch.as_tensor(stem).float().cpu()
        if channels == 1:
            tensor = tensor.mean(0, keepdim=True)
        values = tensor.clamp(-1.0, 1.0).mul(32_767).round().to(torch.int16).numpy()
        return bytes(np.ascontiguousarray(values.T).astype("<i2").tobytes())


def _load_runtime(model_path: Path, device: str) -> DemucsRuntime:
    return _TorchDemucsRuntime(model_path, device)


class HTDemucsSeparationModel:
    descriptor = HTDEMUCS_DESCRIPTOR

    def __init__(
        self,
        settings: Settings,
        *,
        runtime_factory: RuntimeFactory = _load_runtime,
    ) -> None:
        self.settings = settings
        self.runtime_factory = runtime_factory
        self.runtime: DemucsRuntime | None = None

    def load(self) -> None:
        model_path = self.settings.demucs_model_path.resolve()
        weights = model_path / HTDEMUCS_WEIGHTS_FILE
        if not (model_path / "htdemucs.yaml").is_file() or not weights.is_file():
            raise DemucsRuntimeError(f"pinned htdemucs checkpoint is missing: {model_path}")
        if self._sha256(weights) != HTDEMUCS_WEIGHTS_SHA256:
            raise DemucsRuntimeError("htdemucs checkpoint checksum does not match the pin")
        self.runtime = self.runtime_factory(model_path, self.settings.cuda_device)

    def separate(self, pcm_s16le: bytes, sample_rate: int, channels: int) -> SeparatedAudio:
        if self.runtime is None:
            raise RuntimeError("separator must be loaded before inference")
        if sample_rate != HTDEMUCS_SAMPLE_RATE:
            raise DemucsRuntimeError(f"separation input must be {HTDEMUCS_SAMPLE_RATE} Hz")
        if channels not in {1, 2}:
            raise DemucsRuntimeError("separation input must be mono or stereo")
        vocal, instrumental = self.runtime.separate(pcm_s16le, channels)
        if not len(vocal) == len(instrumental) == len(pcm_s16le):
            raise DemucsRuntimeError("separator must preserve frame count")
        return SeparatedAudio(vocal, instrumental)

    def unload(self) -> None:
        self.runtime = None
        gc.collect()
        torch_module = sys.modules.get("torch")
        if torch_module is not None and torch_module.cuda.is_available():
            torch_module.cuda.empty_cache()

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()
