import gc
import importlib
import importlib.util
import json
import sys
import types
from array import array
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.core.config import Settings
from app.models.base import ModelCapability, ModelDescriptor

SEED_VC_RUNTIME_REVISION = "51383efd921027683c89e5348211d93ff12ac2a8"
SEED_VC_SAMPLE_RATE = 44_100
SEED_VC_DIFFUSION_STEPS = 30
SEED_VC_CFG_RATE = 0.7
SEED_VC_MAX_REFERENCE_SECONDS = 25


@dataclass(frozen=True, slots=True)
class SeedVCComponent:
    directory: str
    repo_id: str
    revision: str
    license: str
    files: tuple[str, ...]


SEED_VC_COMPONENTS = (
    SeedVCComponent(
        "seed-vc",
        "Plachta/Seed-VC",
        "257283f9f41585055e8f858fba4fd044e5caed6e",
        "GPL-3.0",
        (
            "DiT_seed_v2_uvit_whisper_base_f0_44k_bigvgan_pruned_ft_ema_v2.pth",
            "config_dit_mel_seed_uvit_whisper_base_f0_44k.yml",
        ),
    ),
    SeedVCComponent(
        "rmvpe",
        "lj1995/VoiceConversionWebUI",
        "e6d0c1a17da07c33557852f9dfa2bd44cc75737d",
        "MIT",
        ("rmvpe.pt",),
    ),
    SeedVCComponent(
        "campplus",
        "funasr/campplus",
        "e4b6ede7ce16997aff4ae69fbca1f0175e2afede",
        "Apache-2.0",
        ("campplus_cn_common.bin",),
    ),
    SeedVCComponent(
        "bigvgan",
        "nvidia/bigvgan_v2_44khz_128band_512x",
        "95a9d1dcb12906c03edd938d77b9333d6ded7dfb",
        "MIT",
        ("bigvgan_generator.pt", "config.json"),
    ),
    SeedVCComponent(
        "whisper-small",
        "openai/whisper-small",
        "973afd24965f72e36ca33b3055d56a652f456b4d",
        "Apache-2.0",
        ("config.json", "model.safetensors", "preprocessor_config.json"),
    ),
)

SEED_VC_DESCRIPTOR = ModelDescriptor(
    key="seed-vc-svc-f0-44k-v2",
    display_name="Seed-VC Singing (F0, 44.1 kHz)",
    version=SEED_VC_COMPONENTS[0].revision[:12],
    capabilities=(ModelCapability.SINGING_VOICE_CONVERSION,),
    requires_gpu=True,
    metadata={
        "runtime": "Plachtaa/seed-vc",
        "runtime_revision": SEED_VC_RUNTIME_REVISION,
        "code_license": "GPL-3.0",
        "weights_license": "GPL-3.0 (DiT); MIT/Apache-2.0 auxiliary models",
        "usage": "research/non-commercial; GPL-3.0 obligations apply if the worker is distributed",
        "upstream_status": "archived 2025-11-21",
        "sample_rate": SEED_VC_SAMPLE_RATE,
        "diffusion_steps": SEED_VC_DIFFUSION_STEPS,
        "preserves": ["duration", "melody (source F0, no auto shift)"],
        "estimated_vram_mb": 4_096,
    },
)


class SeedVCRuntime(Protocol):
    def convert(self, mono_pcm_s16le: bytes, reference_wav: str) -> bytes: ...


RuntimeFactory = Callable[[Path, Path, str], SeedVCRuntime]


class SeedVCRuntimeError(RuntimeError):
    pass


def _install_vector_quantize_stub() -> None:
    """The pinned f0 config never quantizes, so the heavy `dac` codec stack is not installed."""
    if "dac.nn.quantize" in sys.modules or importlib.util.find_spec("dac") is not None:
        return

    class VectorQuantize:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise SeedVCRuntimeError("vector quantization is not part of the pinned f0 model")

    package, nn, quantize = (types.ModuleType(n) for n in ("dac", "dac.nn", "dac.nn.quantize"))
    quantize.VectorQuantize = VectorQuantize  # type: ignore[attr-defined]
    package.nn = nn  # type: ignore[attr-defined]
    nn.quantize = quantize  # type: ignore[attr-defined]
    sys.modules.update({"dac": package, "dac.nn": nn, "dac.nn.quantize": quantize})


class _TorchSeedVCRuntime:
    """Mirrors the pinned seed_vc_wrapper f0 path with local, offline checkpoints."""

    def __init__(self, runtime_path: Path, model_path: Path, device: str) -> None:
        if str(runtime_path) not in sys.path:
            sys.path.insert(0, str(runtime_path))
        _install_vector_quantize_stub()
        try:
            self.np = importlib.import_module("numpy")
            self.torch = importlib.import_module("torch")
            self.torchaudio = importlib.import_module("torchaudio")
            self.librosa = importlib.import_module("librosa")
            yaml = importlib.import_module("yaml")
            commons = importlib.import_module("modules.commons")
            audio = importlib.import_module("modules.audio")
            campplus = importlib.import_module("modules.campplus.DTDNN")
            bigvgan = importlib.import_module("modules.bigvgan.bigvgan")
            rmvpe = importlib.import_module("modules.rmvpe")
            transformers = importlib.import_module("transformers")
        except ImportError as error:
            raise SeedVCRuntimeError("Seed-VC worker dependencies are not installed") from error
        torch = self.torch
        self.device = torch.device(device)
        dit = model_path / "seed-vc"
        config = yaml.safe_load(
            (dit / "config_dit_mel_seed_uvit_whisper_base_f0_44k.yml").read_text(encoding="utf-8")
        )
        model = commons.build_model(commons.recursive_munch(config["model_params"]), stage="DiT")
        model, *_ = commons.load_checkpoint(
            model,
            None,
            str(dit / "DiT_seed_v2_uvit_whisper_base_f0_44k_bigvgan_pruned_ft_ema_v2.pth"),
            load_only_params=True,
            ignore_modules=[],
            is_distributed=False,
        )
        for key in model:
            model[key].eval()
            model[key].to(self.device)
        model.cfm.estimator.setup_caches(max_batch_size=1, max_seq_length=8192)
        self.model = model
        spect = config["preprocess_params"]["spect_params"]
        self.hop_length = spect["hop_length"]
        self.mel_args = {
            "n_fft": spect["n_fft"],
            "win_size": spect["win_length"],
            "hop_size": spect["hop_length"],
            "num_mels": spect["n_mels"],
            "sampling_rate": SEED_VC_SAMPLE_RATE,
            "fmin": 0,
            "fmax": None,
            "center": False,
        }
        self.mel_spectrogram = audio.mel_spectrogram
        self.campplus = campplus.CAMPPlus(feat_dim=80, embedding_size=192)
        self.campplus.load_state_dict(
            torch.load(model_path / "campplus" / "campplus_cn_common.bin", map_location="cpu")
        )
        self.campplus.eval().to(self.device)
        vocoder = bigvgan.BigVGAN.from_pretrained(
            str(model_path / "bigvgan"), use_cuda_kernel=False
        )
        vocoder.remove_weight_norm()
        self.vocoder = vocoder.eval().to(self.device)
        whisper_path = str(model_path / "whisper-small")
        whisper = transformers.WhisperModel.from_pretrained(whisper_path, torch_dtype=torch.float16)
        del whisper.decoder
        self.whisper = whisper.to(self.device)
        self.feature_extractor = transformers.AutoFeatureExtractor.from_pretrained(whisper_path)
        self.rmvpe = rmvpe.RMVPE(
            str(model_path / "rmvpe" / "rmvpe.pt"), is_half=False, device=self.device
        )

    def _whisper_features(self, waves_16k: Any) -> Any:
        inputs = self.feature_extractor(
            [waves_16k.squeeze(0).cpu().numpy()],
            return_tensors="pt",
            return_attention_mask=True,
            sampling_rate=16_000,
        )
        features = self.whisper._mask_input_features(
            inputs.input_features, attention_mask=inputs.attention_mask
        ).to(self.device)
        hidden = self.whisper.encoder(
            features.to(self.whisper.encoder.dtype), return_dict=True
        ).last_hidden_state
        return hidden.to(self.torch.float32)[:, : waves_16k.size(-1) // 320 + 1]

    def convert(self, mono_pcm_s16le: bytes, reference_wav: str) -> bytes:
        np, torch, torchaudio = self.np, self.torch, self.torchaudio
        samples = np.frombuffer(mono_pcm_s16le, dtype="<i2").astype(np.float32) / 32_768
        reference = self.librosa.load(reference_wav, sr=SEED_VC_SAMPLE_RATE)[0]
        reference = reference[: SEED_VC_SAMPLE_RATE * SEED_VC_MAX_REFERENCE_SECONDS]
        with torch.inference_mode():
            source = torch.from_numpy(samples)[None].to(self.device)
            prompt = torch.from_numpy(reference)[None].float().to(self.device)
            source_16k = torchaudio.functional.resample(source, SEED_VC_SAMPLE_RATE, 16_000)
            prompt_16k = torchaudio.functional.resample(prompt, SEED_VC_SAMPLE_RATE, 16_000)
            source_features = self._whisper_features(source_16k)
            prompt_features = self._whisper_features(prompt_16k)
            mel = self.mel_spectrogram(source, **self.mel_args)
            prompt_mel = self.mel_spectrogram(prompt, **self.mel_args)
            context = SEED_VC_SAMPLE_RATE // self.hop_length * 30
            if mel.size(2) + prompt_mel.size(2) > context:
                raise SeedVCRuntimeError("source chunk and reference exceed the 30 s context")
            fbank = torchaudio.compliance.kaldi.fbank(
                prompt_16k, num_mel_bins=80, dither=0, sample_frequency=16_000
            )
            style = self.campplus((fbank - fbank.mean(dim=0, keepdim=True)).unsqueeze(0))
            prompt_f0 = torch.from_numpy(self.rmvpe.infer_from_audio(prompt_16k[0], thred=0.03))
            source_f0 = torch.from_numpy(self.rmvpe.infer_from_audio(source_16k[0], thred=0.03))
            regulator = self.model.length_regulator
            condition = regulator(
                source_features,
                ylens=torch.LongTensor([mel.size(2)]).to(self.device),
                n_quantizers=3,
                f0=source_f0.to(self.device)[None],
            )[0]
            prompt_condition = regulator(
                prompt_features,
                ylens=torch.LongTensor([prompt_mel.size(2)]).to(self.device),
                n_quantizers=3,
                f0=prompt_f0.to(self.device)[None],
            )[0]
            joined = torch.cat([prompt_condition, condition], dim=1)
            with torch.autocast(device_type=self.device.type, dtype=torch.float16):
                generated = self.model.cfm.inference(
                    joined,
                    torch.LongTensor([joined.size(1)]).to(self.device),
                    prompt_mel,
                    style,
                    None,
                    SEED_VC_DIFFUSION_STEPS,
                    inference_cfg_rate=SEED_VC_CFG_RATE,
                )[:, :, prompt_mel.size(-1) :]
            wave = self.vocoder(generated.float())[0, 0].clamp(-1.0, 1.0)
        values = wave.mul(32_767).round().to(torch.int16).cpu().numpy()
        return bytes(values.astype("<i2").tobytes())


def _load_runtime(runtime_path: Path, model_path: Path, device: str) -> SeedVCRuntime:
    return _TorchSeedVCRuntime(runtime_path, model_path, device)


class SeedVCSingingModel:
    descriptor = SEED_VC_DESCRIPTOR

    def __init__(
        self,
        settings: Settings,
        *,
        runtime_factory: RuntimeFactory = _load_runtime,
    ) -> None:
        self.settings = settings
        self.runtime_factory = runtime_factory
        self.runtime: SeedVCRuntime | None = None

    def load(self) -> None:
        runtime_path = self.settings.seed_vc_runtime_path.resolve()
        model_path = self.settings.seed_vc_checkpoint_path.resolve()
        if not (runtime_path / "modules" / "commons.py").is_file():
            raise SeedVCRuntimeError(f"pinned Seed-VC runtime is missing: {runtime_path}")
        missing = [
            f"{component.directory}/{name}"
            for component in SEED_VC_COMPONENTS
            for name in component.files
            if not (model_path / component.directory / name).is_file()
        ]
        if missing:
            raise SeedVCRuntimeError(f"Seed-VC checkpoint is incomplete ({len(missing)} missing)")
        self._validate_manifest(model_path / "voice-model-manifest.json")
        self.runtime = self.runtime_factory(runtime_path, model_path, self.settings.cuda_device)

    def convert(
        self,
        vocal_pcm_s16le: bytes,
        sample_rate: int,
        channels: int,
        voice_profile: dict[str, Any],
    ) -> bytes:
        if self.runtime is None:
            raise RuntimeError("model must be loaded before inference")
        if sample_rate != SEED_VC_SAMPLE_RATE:
            raise SeedVCRuntimeError(f"singing conversion input must be {SEED_VC_SAMPLE_RATE} Hz")
        if channels not in {1, 2}:
            raise SeedVCRuntimeError("singing conversion input must be mono or stereo")
        reference_key = voice_profile.get("reference_storage_key")
        if not isinstance(reference_key, str) or not reference_key:
            raise SeedVCRuntimeError("voice profile has no Seed-VC reference")
        reference = self._reference_path(reference_key)
        mono = self._downmix(vocal_pcm_s16le, channels)
        converted = self.runtime.convert(mono, str(reference))
        # Mel frames are hop-aligned, so the vocoder output can differ by under one hop.
        converted = converted[: len(mono)].ljust(len(mono), b"\0")
        return self._upmix(converted, channels)

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
            raise SeedVCRuntimeError("invalid or missing voice reference")
        return candidate

    @staticmethod
    def _downmix(pcm: bytes, channels: int) -> bytes:
        if channels == 1:
            return pcm
        samples = array("h")
        samples.frombytes(pcm)
        left, right = samples[0::2], samples[1::2]
        return array("h", ((a + b) // 2 for a, b in zip(left, right, strict=True))).tobytes()

    @staticmethod
    def _upmix(pcm: bytes, channels: int) -> bytes:
        if channels == 1:
            return pcm
        mono = array("h")
        mono.frombytes(pcm)
        stereo = array("h", bytes(len(pcm) * 2))
        stereo[0::2] = mono
        stereo[1::2] = mono
        return stereo.tobytes()

    @staticmethod
    def _validate_manifest(path: Path) -> None:
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SeedVCRuntimeError("Seed-VC checkpoint manifest is invalid") from error
        pinned = {(c.repo_id, c.revision) for c in SEED_VC_COMPONENTS}
        recorded = {
            (item.get("repo_id"), item.get("revision")) for item in manifest.get("files", [])
        }
        if manifest.get("runtime_revision") != SEED_VC_RUNTIME_REVISION or recorded != pinned:
            raise SeedVCRuntimeError("Seed-VC checkpoint provenance does not match")
