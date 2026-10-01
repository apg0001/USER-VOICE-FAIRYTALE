"""Run the fixed Korean acceptance corpus against the pinned CosyVoice3 adapter."""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))

from app.core.config import Settings  # noqa: E402
from app.models.tts.cosyvoice import (  # noqa: E402
    COSYVOICE_MODEL_ID,
    COSYVOICE_MODEL_REVISION,
    COSYVOICE_RUNTIME_REVISION,
    CosyVoice3TTSModel,
)


class VRAMSampler:
    def __init__(self, interval_seconds: float = 0.25) -> None:
        self.interval_seconds = interval_seconds
        self.baseline_mb = self._read_total_used_mb()
        self.peak_total_mb = self.baseline_mb
        self._stopped = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    @staticmethod
    def _read_total_used_mb() -> float:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return float(result.stdout.splitlines()[0].strip())

    def _run(self) -> None:
        while not self._stopped.wait(self.interval_seconds):
            try:
                self.peak_total_mb = max(self.peak_total_mb, self._read_total_used_mb())
            except (OSError, subprocess.SubprocessError, ValueError):
                continue

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> float:
        self._stopped.set()
        self._thread.join()
        return max(0.0, self.peak_total_mb - self.baseline_mb)


def write_wav(path: Path, pcm: bytes, sample_rate: int) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm)


def load_whisper_audio(path: Path) -> Any:
    import numpy as np
    import torch
    import torchaudio.functional

    with wave.open(str(path), "rb") as source:
        if source.getnchannels() != 1 or source.getsampwidth() != 2:
            raise ValueError(f"Expected mono PCM16 WAV: {path}")
        sample_rate = source.getframerate()
        samples = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2")
    waveform = torch.from_numpy(samples.astype(np.float32) / 32_768)
    if sample_rate != 16_000:
        waveform = torchaudio.functional.resample(waveform, sample_rate, 16_000)
    return waveform.numpy()


def normalize_korean(text: str) -> str:
    return "".join(character for character in text if "가" <= character <= "힣")


def edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for row, left_value in enumerate(left, start=1):
        current = [row]
        for column, right_value in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (left_value != right_value),
                )
            )
        previous = current
    return previous[-1]


def cosine_similarity(left: Any, right: Any) -> float:
    import torch

    return float(torch.nn.functional.cosine_similarity(left.flatten(), right.flatten(), dim=0))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument(
        "--corpus",
        type=Path,
        default=REPOSITORY_ROOT / "backend/tests/model/fixtures/korean_tts_acceptance.json",
    )
    parser.add_argument(
        "--model", type=Path, default=REPOSITORY_ROOT / "models/cosyvoice3-0.5b-2512"
    )
    parser.add_argument(
        "--runtime", type=Path, default=REPOSITORY_ROOT / "models/cosyvoice-runtime"
    )
    parser.add_argument(
        "--output", type=Path, default=REPOSITORY_ROOT / "models/acceptance/cosyvoice3"
    )
    parser.add_argument("--asr-model", default="small")
    args = parser.parse_args()

    import torch

    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    reference = args.reference.resolve()
    if not reference.is_file():
        raise SystemExit(f"Reference WAV does not exist: {reference}")
    storage_root = reference.parent
    settings = Settings(
        app_env="test",
        storage_path=storage_root,
        enable_cosyvoice3=True,
        cosyvoice_runtime_path=args.runtime.resolve(),
        cosyvoice_checkpoint_path=args.model.resolve(),
        cosyvoice_fp16=True,
    )
    profile = {
        "reference_storage_key": reference.name,
        "prompt_text": corpus["reference"]["transcript"],
    }
    model = CosyVoice3TTSModel(settings)
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    vram_sampler = VRAMSampler()
    vram_sampler.start()
    load_started = time.perf_counter()
    model.load()
    load_seconds = time.perf_counter() - load_started
    runtime = model.runtime
    assert runtime is not None
    reference_embedding = runtime.frontend._extract_spk_embedding(str(reference))

    cases = []
    for case in corpus["cases"]:
        started = time.perf_counter()
        audio = model.synthesize(case["text"], profile)
        inference_seconds = time.perf_counter() - started
        audio_seconds = len(audio.pcm_s16le) / 2 / audio.sample_rate
        path = output / f"{case['id']}.wav"
        write_wav(path, audio.pcm_s16le, audio.sample_rate)
        generated_embedding = runtime.frontend._extract_spk_embedding(str(path))
        samples = memoryview(audio.pcm_s16le).cast("h")
        clipped = sum(abs(value) >= 32_767 for value in samples) / max(1, len(samples))
        cases.append(
            {
                **case,
                "output": path.name,
                "audio_seconds": audio_seconds,
                "inference_seconds": inference_seconds,
                "rtf": inference_seconds / audio_seconds,
                "speaker_cosine": cosine_similarity(reference_embedding, generated_embedding),
                "clipped_sample_ratio": clipped,
            }
        )

    torch_peak_vram_mb = torch.cuda.max_memory_allocated() / (1024 * 1024)
    device_peak_vram_mb = vram_sampler.stop()
    peak_vram_mb = max(torch_peak_vram_mb, device_peak_vram_mb)
    model.unload()
    del runtime, model, reference_embedding
    torch.cuda.empty_cache()

    import whisper

    recognizer = whisper.load_model(args.asr_model, device="cpu")
    for case in cases:
        transcript = recognizer.transcribe(
            load_whisper_audio(output / case["output"]), language="ko", fp16=False
        )["text"]
        expected = normalize_korean(case["text"])
        recognized = normalize_korean(transcript)
        case["asr_transcript"] = transcript.strip()
        case["cer"] = edit_distance(expected, recognized) / max(1, len(expected))

    metrics = {
        "load_seconds": load_seconds,
        "peak_vram_mb": peak_vram_mb,
        "rtf_median": statistics.median(case["rtf"] for case in cases),
        "cer_mean": statistics.mean(case["cer"] for case in cases),
        "speaker_cosine_mean": statistics.mean(case["speaker_cosine"] for case in cases),
        "clipped_sample_ratio_max": max(case["clipped_sample_ratio"] for case in cases),
        "non_finite_metrics": any(
            not math.isfinite(float(value))
            for case in cases
            for value in (
                case["rtf"],
                case["cer"],
                case["speaker_cosine"],
                case["clipped_sample_ratio"],
            )
        ),
    }
    thresholds = corpus["thresholds"]
    checks = {
        "load_seconds": metrics["load_seconds"] <= thresholds["load_seconds_max"],
        "peak_vram_mb": metrics["peak_vram_mb"] <= thresholds["peak_vram_mb_max"],
        "rtf_median": metrics["rtf_median"] <= thresholds["rtf_median_max"],
        "cer_mean": metrics["cer_mean"] <= thresholds["cer_mean_max"],
        "speaker_cosine_mean": metrics["speaker_cosine_mean"]
        >= thresholds["speaker_cosine_mean_min"],
        "clipping": metrics["clipped_sample_ratio_max"] <= thresholds["clipped_sample_ratio_max"],
        "finite": not metrics["non_finite_metrics"],
    }
    report = {
        "schema_version": 1,
        "adapter": "cosyvoice3-0.5b-2512",
        "model_id": COSYVOICE_MODEL_ID,
        "model_revision": COSYVOICE_MODEL_REVISION,
        "runtime_revision": COSYVOICE_RUNTIME_REVISION,
        "process_id": os.getpid(),
        "platform": platform.platform(),
        "asr_model": args.asr_model,
        "reference_id": corpus["reference"]["id"],
        "device": torch.cuda.get_device_name(0),
        "torch": torch.__version__,
        "cuda": torch.version.cuda,
        "thresholds": thresholds,
        "metrics": metrics,
        "vram_measurement": {
            "baseline_total_used_mb": vram_sampler.baseline_mb,
            "peak_total_used_mb": vram_sampler.peak_total_mb,
            "device_delta_mb": device_peak_vram_mb,
            "torch_peak_allocated_mb": torch_peak_vram_mb,
            "poll_interval_seconds": vram_sampler.interval_seconds,
        },
        "checks": checks,
        "passed": all(checks.values()),
        "cases": cases,
    }
    report_path = output / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
