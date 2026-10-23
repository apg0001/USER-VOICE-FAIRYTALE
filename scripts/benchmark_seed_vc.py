"""Run the MUSDB18 sung-vocal acceptance against the Seed-VC adapter and the full pipeline."""

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

from benchmark_cosyvoice3 import VRAMSampler  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.models.separation.demucs import HTDemucsSeparationModel  # noqa: E402
from app.models.singing.seed_vc import (  # noqa: E402
    SEED_VC_COMPONENTS,
    SEED_VC_DIFFUSION_STEPS,
    SEED_VC_RUNTIME_REVISION,
    SEED_VC_SAMPLE_RATE,
    SeedVCSingingModel,
)
from app.pipelines import SingingPipeline  # noqa: E402
from app.storage import LocalObjectStorage  # noqa: E402

FIXTURE = REPOSITORY_ROOT / "backend/tests/model/fixtures/singing_vc_acceptance.json"


def read_pcm(path: Path) -> tuple[bytes, int, int]:
    with wave.open(str(path), "rb") as source:
        return source.readframes(source.getnframes()), source.getframerate(), source.getnchannels()


def mono_float(pcm: bytes, channels: int) -> Any:
    import numpy as np

    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float64) / 32_768
    return samples.reshape(-1, channels).mean(axis=1)


def harvest(samples: Any, sample_rate: int) -> Any:
    import pyworld

    f0, _ = pyworld.harvest(samples, sample_rate, frame_period=10.0)
    return f0


def rpa50(source_f0: Any, converted_f0: Any) -> float:
    import numpy as np

    frames = min(len(source_f0), len(converted_f0))
    source_f0, converted_f0 = source_f0[:frames], converted_f0[:frames]
    voiced = source_f0 > 0
    if not voiced.any():
        return float("nan")
    both = voiced & (converted_f0 > 0)
    cents = np.zeros(frames)
    cents[both] = np.abs(1200 * np.log2(converted_f0[both] / source_f0[both]))
    return float(np.sum(both & (cents <= 50)) / np.sum(voiced))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--musdb-root", type=Path, required=True)
    parser.add_argument(
        "--acceptance-root", type=Path, default=REPOSITORY_ROOT / "models/acceptance"
    )
    parser.add_argument(
        "--seed-vc-runtime", type=Path, default=REPOSITORY_ROOT / "models/seed-vc-runtime"
    )
    parser.add_argument(
        "--seed-vc-model", type=Path, default=REPOSITORY_ROOT / "models/seed-vc-svc"
    )
    parser.add_argument(
        "--demucs-model", type=Path, default=REPOSITORY_ROOT / "models/demucs-htdemucs"
    )
    args = parser.parse_args()

    import numpy as np
    import torch
    from resemblyzer import VoiceEncoder, preprocess_wav

    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    thresholds = fixture["thresholds"]
    root = args.acceptance_root.resolve()
    output_dir = root / "singing"
    output_dir.mkdir(parents=True, exist_ok=True)
    references = {
        "synthetic-heami-ko-kr-v1": root / "synthetic-heami-ko-kr-v1.wav",
        "synthetic-zira-en-us-v1": root / "vc" / "synthetic-zira-en-us-v1.wav",
    }
    for target, path in references.items():
        if not path.is_file():
            raise SystemExit(f"Target reference WAV does not exist: {target}")

    encoder = VoiceEncoder(device="cpu", verbose=False)

    def embed(samples: Any, sample_rate: int) -> Any:
        return encoder.embed_utterance(preprocess_wav(samples.astype(np.float32), sample_rate))

    target_embeddings = {}
    for target, path in references.items():
        pcm, rate, channels = read_pcm(path)
        target_embeddings[target] = embed(mono_float(pcm, channels), rate)

    tracks = []
    for track in sorted(p for p in (args.musdb_root / "test").iterdir() if p.is_dir()):
        pcm, rate, channels = read_pcm(track / "vocals.wav")
        f0 = harvest(mono_float(pcm, channels), rate)
        if float(np.mean(f0 > 0)) >= fixture["source"]["min_voiced_ratio"]:
            tracks.append((track, pcm, channels, f0))
    if not tracks:
        raise SystemExit("No MUSDB test vocals pass the voiced-ratio filter")

    settings = Settings(
        app_env="test",
        storage_path=root,
        enable_seed_vc=True,
        enable_demucs=True,
        seed_vc_runtime_path=args.seed_vc_runtime.resolve(),
        seed_vc_checkpoint_path=args.seed_vc_model.resolve(),
        demucs_model_path=args.demucs_model.resolve(),
    )
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    vram_sampler = VRAMSampler()
    vram_sampler.start()
    model = SeedVCSingingModel(settings)
    load_started = time.perf_counter()
    model.load()
    load_seconds = time.perf_counter() - load_started

    cases = []
    for target in references:
        profile = {"reference_storage_key": references[target].relative_to(root).as_posix()}
        for track, pcm, channels, source_f0 in tracks:
            torch.cuda.synchronize()
            started = time.perf_counter()
            converted = model.convert(pcm, SEED_VC_SAMPLE_RATE, channels, profile)
            torch.cuda.synchronize()
            seconds = time.perf_counter() - started
            audio_seconds = len(pcm) / 2 / channels / SEED_VC_SAMPLE_RATE
            source = mono_float(pcm, channels)
            output = mono_float(converted, channels)
            source_cosine = float(
                np.dot(target_embeddings[target], embed(source, SEED_VC_SAMPLE_RATE))
            )
            output_cosine = float(
                np.dot(target_embeddings[target], embed(output, SEED_VC_SAMPLE_RATE))
            )
            cases.append(
                {
                    "target": target,
                    "track": track.name,
                    "audio_seconds": audio_seconds,
                    "rtf": seconds / audio_seconds,
                    "source_target_cosine": source_cosine,
                    "target_cosine": output_cosine,
                    "target_cosine_gain": output_cosine - source_cosine,
                    "rpa50": rpa50(source_f0, harvest(output, SEED_VC_SAMPLE_RATE)),
                    "clipped_sample_ratio": float(
                        np.mean(np.abs(np.frombuffer(converted, dtype="<i2")) >= 32_767)
                    ),
                }
            )

    # Full pipeline with real separation: both models resident, 10 s chunks across track joins.
    separator = HTDemucsSeparationModel(settings)
    separator.load()
    mixtures = [
        read_pcm(track / "mixture.wav")[0] for track, *_ in tracks[: fixture["pipeline_tracks"]]
    ]
    source_key = "singing/pipeline-source.wav"
    with wave.open(str(root / source_key), "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(SEED_VC_SAMPLE_RATE)
        stream.writeframes(b"".join(mixtures))
    pipeline_started = time.perf_counter()
    result = asyncio.run(
        SingingPipeline(LocalObjectStorage(root)).run(
            job_id="singing-acceptance",
            input_storage_key=source_key,
            separator=separator,
            singing_model=model,
            voice_profile={
                "reference_storage_key": references["synthetic-heami-ko-kr-v1"]
                .relative_to(root)
                .as_posix()
            },
        )
    )
    pipeline_seconds = time.perf_counter() - pipeline_started
    pipeline = {
        "audio_seconds": result.duration_seconds,
        "chunk_count": result.chunk_count,
        "rtf": pipeline_seconds / result.duration_seconds,
        "frame_count_preserved": result.manifest["final"]["frame_count"]
        == result.manifest["source"]["frame_count"],
        "output": result.storage_key,
    }

    torch_peak_vram_mb = torch.cuda.max_memory_allocated() / (1024 * 1024)
    device_peak_vram_mb = vram_sampler.stop()
    peak_vram_mb = max(torch_peak_vram_mb, device_peak_vram_mb)
    separator.unload()
    model.unload()

    keys = ("rtf", "target_cosine", "target_cosine_gain", "rpa50", "clipped_sample_ratio")
    metrics = {
        "load_seconds": load_seconds,
        "pipeline_peak_vram_mb": peak_vram_mb,
        "source_tracks": len(tracks),
        "case_count": len(cases),
        "rtf_median": statistics.median(c["rtf"] for c in cases),
        "target_cosine_mean": statistics.mean(c["target_cosine"] for c in cases),
        "target_cosine_gain_mean": statistics.mean(c["target_cosine_gain"] for c in cases),
        "rpa50_mean": statistics.mean(c["rpa50"] for c in cases),
        "rpa50_min": min(c["rpa50"] for c in cases),
        "clipped_sample_ratio_max": max(c["clipped_sample_ratio"] for c in cases),
        "by_target": {
            target: {
                key: statistics.mean(c[key] for c in cases if c["target"] == target) for key in keys
            }
            for target in references
        },
        "pipeline": pipeline,
    }
    finite = all(math.isfinite(float(c[key])) for c in cases for key in keys)
    checks = {
        "target_cosine_gain_mean": metrics["target_cosine_gain_mean"]
        >= thresholds["target_cosine_gain_mean_min"],
        "rpa50_mean": metrics["rpa50_mean"] >= thresholds["rpa50_mean_min"],
        "rtf_median": metrics["rtf_median"] <= thresholds["rtf_median_max"],
        "pipeline_rtf": pipeline["rtf"] <= thresholds["pipeline_rtf_max"],
        "pipeline_frames": pipeline["frame_count_preserved"],
        "load_seconds": load_seconds <= thresholds["load_seconds_max"],
        "pipeline_peak_vram_mb": peak_vram_mb <= thresholds["pipeline_peak_vram_mb_max"],
        "clipping": metrics["clipped_sample_ratio_max"] <= thresholds["clipped_sample_ratio_max"],
        "finite": finite,
    }
    report = {
        "schema_version": 1,
        "adapter": "seed-vc-svc-f0-44k-v2",
        "runtime_revision": SEED_VC_RUNTIME_REVISION,
        "components": [
            {"repo_id": c.repo_id, "revision": c.revision, "license": c.license}
            for c in SEED_VC_COMPONENTS
        ],
        "diffusion_steps": SEED_VC_DIFFUSION_STEPS,
        "process_id": os.getpid(),
        "platform": platform.platform(),
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
        },
        "checks": checks,
        "passed": all(checks.values()),
        "cases": cases,
    }
    (output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps({k: v for k, v in report.items() if k != "cases"}, ensure_ascii=False, indent=2)
    )
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
