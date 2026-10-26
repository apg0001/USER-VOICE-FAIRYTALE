"""Run the fixed two-direction Korean speech VC acceptance against the CosyVoice3 adapter."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import platform
import statistics
import sys
import time
import wave
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))

from benchmark_cosyvoice3 import (  # noqa: E402
    VRAMSampler,
    cosine_similarity,
    edit_distance,
    load_whisper_audio,
    normalize_korean,
    write_wav,
)

from app.core.config import Settings  # noqa: E402
from app.models.tts.cosyvoice import (  # noqa: E402
    COSYVOICE_MODEL_ID,
    COSYVOICE_MODEL_REVISION,
    COSYVOICE_RUNTIME_REVISION,
    CosyVoice3Model,
)
from app.pipelines import VoiceConversionPipeline  # noqa: E402
from app.storage import LocalObjectStorage  # noqa: E402

FIXTURES = REPOSITORY_ROOT / "backend/tests/model/fixtures"


def read_pcm(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as source:
        if source.getnchannels() != 1 or source.getsampwidth() != 2:
            raise ValueError(f"Expected mono PCM16 WAV: {path}")
        return source.readframes(source.getnframes()), source.getframerate()


def pitch_shift(source: Path, destination: Path, semitones: int) -> None:
    import numpy as np
    import torch
    import torchaudio.functional

    pcm, sample_rate = read_pcm(source)
    waveform = torch.from_numpy(np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32_768)
    shifted = torchaudio.functional.pitch_shift(waveform, sample_rate, semitones)
    clipped = shifted.clamp(-1.0, 1.0).mul(32_767).round().to(torch.int16).numpy()
    write_wav(destination, clipped.astype("<i2").tobytes(), sample_rate)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--acceptance-root", type=Path, default=REPOSITORY_ROOT / "models/acceptance"
    )
    parser.add_argument(
        "--tts-outputs",
        type=Path,
        default=REPOSITORY_ROOT / "models/acceptance/cosyvoice3",
        help="Directory with the accepted Phase 11 TTS WAVs named by case id",
    )
    parser.add_argument(
        "--model", type=Path, default=REPOSITORY_ROOT / "models/cosyvoice3-0.5b-2512"
    )
    parser.add_argument(
        "--runtime", type=Path, default=REPOSITORY_ROOT / "models/cosyvoice-runtime"
    )
    parser.add_argument("--asr-model", default="small")
    args = parser.parse_args()

    import torch

    corpus = json.loads((FIXTURES / "korean_vc_acceptance.json").read_text(encoding="utf-8"))
    tts_corpus = json.loads((FIXTURES / corpus["source_corpus"]).read_text(encoding="utf-8"))
    root = args.acceptance_root.resolve()
    work = root / "vc"
    work.mkdir(parents=True, exist_ok=True)
    targets = {target["id"]: target for target in corpus["targets"]}
    for target_id in targets:
        reference = root / f"{target_id}.wav"
        if not reference.is_file():
            reference = work / f"{target_id}.wav"
        if not reference.is_file():
            raise SystemExit(f"Target reference WAV does not exist: {target_id}.wav")
        targets[target_id]["path"] = reference

    sources: dict[tuple[str, str], Path] = {}
    for direction in corpus["directions"]:
        for case in tts_corpus["cases"]:
            source = args.tts_outputs.resolve() / f"{case['id']}.wav"
            if not source.is_file():
                raise SystemExit(f"Phase 11 source WAV does not exist: {source}")
            if direction["source_pitch_semitones"]:
                shifted = (
                    work / "sources" / f"{case['id']}_{direction['source_pitch_semitones']}st.wav"
                )
                shifted.parent.mkdir(exist_ok=True)
                pitch_shift(source, shifted, direction["source_pitch_semitones"])
                source = shifted
            sources[(direction["id"], case["id"])] = source

    settings = Settings(
        app_env="test",
        storage_path=root,
        enable_cosyvoice3=True,
        cosyvoice_runtime_path=args.runtime.resolve(),
        cosyvoice_checkpoint_path=args.model.resolve(),
        cosyvoice_fp16=True,
    )
    storage = LocalObjectStorage(root)
    pipeline = VoiceConversionPipeline(storage)
    model = CosyVoice3Model(settings)
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    vram_sampler = VRAMSampler()
    vram_sampler.start()
    load_started = time.perf_counter()
    model.load()
    load_seconds = time.perf_counter() - load_started
    runtime = model.runtime
    assert runtime is not None
    extract_embedding = runtime.frontend._extract_spk_embedding

    def embedding(path: Path) -> Any:
        return extract_embedding(str(path))

    target_embeddings = {key: embedding(value["path"]) for key, value in targets.items()}
    cases = []
    for direction in corpus["directions"]:
        target = targets[direction["target"]]
        profile = {"reference_storage_key": str(target["path"].relative_to(root).as_posix())}
        for case in tts_corpus["cases"]:
            source = sources[(direction["id"], case["id"])]
            started = time.perf_counter()
            result = asyncio.run(
                pipeline.run(
                    job_id=f"vc-acceptance-{direction['id']}-{case['id']}",
                    input_storage_key=str(source.relative_to(root).as_posix()),
                    model=model,
                    voice_profile=profile,
                )
            )
            inference_seconds = time.perf_counter() - started
            output = root / result.storage_key
            pcm, _ = read_pcm(output)
            samples = memoryview(pcm).cast("h")
            target_embedding = target_embeddings[direction["target"]]
            source_cosine = cosine_similarity(target_embedding, embedding(source))
            output_cosine = cosine_similarity(target_embedding, embedding(output))
            cases.append(
                {
                    "direction": direction["id"],
                    "id": case["id"],
                    "category": case["category"],
                    "text": case["text"],
                    "output": output.relative_to(root).as_posix(),
                    "audio_seconds": result.duration_seconds,
                    "chunk_count": result.chunk_count,
                    "inference_seconds": inference_seconds,
                    "rtf": inference_seconds / result.duration_seconds,
                    "source_target_cosine": source_cosine,
                    "target_cosine": output_cosine,
                    "target_cosine_gain": output_cosine - source_cosine,
                    "clipped_sample_ratio": sum(abs(value) >= 32_767 for value in samples)
                    / max(1, len(samples)),
                }
            )

    torch_peak_vram_mb = torch.cuda.max_memory_allocated() / (1024 * 1024)
    device_peak_vram_mb = vram_sampler.stop()
    peak_vram_mb = max(torch_peak_vram_mb, device_peak_vram_mb)
    model.unload()
    del runtime, model, target_embeddings
    torch.cuda.empty_cache()

    import whisper

    recognizer = whisper.load_model(args.asr_model, device="cpu")
    for case in cases:
        transcript = recognizer.transcribe(
            load_whisper_audio(root / case["output"]), language="ko", fp16=False
        )["text"]
        expected = normalize_korean(case["text"])
        case["asr_transcript"] = transcript.strip()
        case["cer"] = edit_distance(expected, normalize_korean(transcript)) / max(1, len(expected))

    metric_keys = ("rtf", "cer", "target_cosine", "target_cosine_gain", "clipped_sample_ratio")
    metrics = {
        "load_seconds": load_seconds,
        "peak_vram_mb": peak_vram_mb,
        "rtf_median": statistics.median(case["rtf"] for case in cases),
        "cer_mean": statistics.mean(case["cer"] for case in cases),
        "target_cosine_mean": statistics.mean(case["target_cosine"] for case in cases),
        "target_cosine_gain_mean": statistics.mean(case["target_cosine_gain"] for case in cases),
        "clipped_sample_ratio_max": max(case["clipped_sample_ratio"] for case in cases),
        "by_direction": {
            direction["id"]: {
                key: statistics.mean(
                    case[key] for case in cases if case["direction"] == direction["id"]
                )
                for key in metric_keys
            }
            for direction in corpus["directions"]
        },
        "non_finite_metrics": any(
            not math.isfinite(float(case[key])) for case in cases for key in metric_keys
        ),
    }
    thresholds = corpus["thresholds"]
    checks = {
        "load_seconds": metrics["load_seconds"] <= thresholds["load_seconds_max"],
        "peak_vram_mb": metrics["peak_vram_mb"] <= thresholds["peak_vram_mb_max"],
        "rtf_median": metrics["rtf_median"] <= thresholds["rtf_median_max"],
        "cer_mean": metrics["cer_mean"] <= thresholds["cer_mean_max"],
        "target_cosine_mean": metrics["target_cosine_mean"] >= thresholds["target_cosine_mean_min"],
        "target_cosine_gain_mean": metrics["target_cosine_gain_mean"]
        >= thresholds["target_cosine_gain_mean_min"],
        "clipping": metrics["clipped_sample_ratio_max"] <= thresholds["clipped_sample_ratio_max"],
        "finite": not metrics["non_finite_metrics"],
    }
    report = {
        "schema_version": 1,
        "adapter": "cosyvoice3-0.5b-2512",
        "capability": "speech_voice_conversion",
        "model_id": COSYVOICE_MODEL_ID,
        "model_revision": COSYVOICE_MODEL_REVISION,
        "runtime_revision": COSYVOICE_RUNTIME_REVISION,
        "process_id": os.getpid(),
        "platform": platform.platform(),
        "asr_model": args.asr_model,
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
    report_path = work / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
