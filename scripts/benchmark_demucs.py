"""Run the MUSDB18 7 s test-excerpt separation acceptance against the htdemucs adapter."""

from __future__ import annotations

import argparse
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
from app.models.separation.demucs import (  # noqa: E402
    DEMUCS_PACKAGE_VERSION,
    HTDEMUCS_SAMPLE_RATE,
    HTDEMUCS_WEIGHTS_FILE,
    HTDEMUCS_WEIGHTS_SHA256,
    HTDemucsSeparationModel,
)

FIXTURE = REPOSITORY_ROOT / "backend/tests/model/fixtures/separation_acceptance.json"


def read_stereo(path: Path) -> Any:
    import numpy as np

    with wave.open(str(path), "rb") as source:
        if source.getsampwidth() != 2 or source.getframerate() != HTDEMUCS_SAMPLE_RATE:
            raise ValueError(f"Expected 44.1 kHz PCM16 WAV: {path}")
        channels = source.getnchannels()
        frames = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2")
    frames = frames.reshape(-1, channels).astype(np.int32)
    return frames if channels == 2 else np.repeat(frames, 2, axis=1)


def to_pcm(samples: Any) -> bytes:
    import numpy as np

    return bytes(np.clip(samples, -32_768, 32_767).astype("<i2").tobytes())


def from_pcm(payload: bytes) -> Any:
    import numpy as np

    return np.frombuffer(payload, dtype="<i2").reshape(-1, 2).astype(np.float64)


def sdr(reference: Any, estimate: Any) -> float:
    import numpy as np

    reference = np.asarray(reference, dtype=np.float64)
    error = reference - np.asarray(estimate, dtype=np.float64)
    return float(10 * np.log10((np.sum(reference**2) + 1e-8) / (np.sum(error**2) + 1e-8)))


def rms_dbfs(samples: Any) -> float:
    import numpy as np

    rms = float(np.sqrt(np.mean((np.asarray(samples, dtype=np.float64) / 32_768) ** 2)))
    return 20 * math.log10(max(rms, 1e-12))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--musdb-root", type=Path, required=True, help="MUSDB18-7-WAV root")
    parser.add_argument("--model", type=Path, default=REPOSITORY_ROOT / "models/demucs-htdemucs")
    parser.add_argument(
        "--output", type=Path, default=REPOSITORY_ROOT / "models/acceptance/separation"
    )
    args = parser.parse_args()

    import numpy as np
    import torch

    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    thresholds = fixture["thresholds"]
    tracks = sorted(path for path in (args.musdb_root / "test").iterdir() if path.is_dir())
    if not tracks:
        raise SystemExit(f"No MUSDB test tracks under {args.musdb_root}")

    settings = Settings(app_env="test", enable_demucs=True, demucs_model_path=args.model)
    model = HTDemucsSeparationModel(settings)
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    vram_sampler = VRAMSampler()
    vram_sampler.start()
    load_started = time.perf_counter()
    model.load()
    load_seconds = time.perf_counter() - load_started

    cases = []
    boundary_sources = []
    for track in tracks:
        mixture = read_stereo(track / "mixture.wav")
        vocal = read_stereo(track / "vocals.wav")
        instrumental = sum(
            read_stereo(track / f"{stem}.wav") for stem in ("drums", "bass", "other")
        )
        torch.cuda.synchronize()
        started = time.perf_counter()
        separated = model.separate(to_pcm(mixture), HTDEMUCS_SAMPLE_RATE, 2)
        torch.cuda.synchronize()
        seconds = time.perf_counter() - started
        audio_seconds = len(mixture) / HTDEMUCS_SAMPLE_RATE
        vocal_estimate = from_pcm(separated.vocal_pcm_s16le)
        instrumental_estimate = from_pcm(separated.instrumental_pcm_s16le)
        outputs = np.concatenate([vocal_estimate, instrumental_estimate])
        has_vocal = rms_dbfs(vocal) > fixture["silent_vocal_rms_dbfs"]
        cases.append(
            {
                "track": track.name,
                "audio_seconds": audio_seconds,
                "rtf": seconds / audio_seconds,
                "has_vocal": has_vocal,
                "vocal_sdr_db": sdr(vocal, vocal_estimate) if has_vocal else None,
                "instrumental_sdr_db": sdr(instrumental, instrumental_estimate),
                "clipped_sample_ratio": float(np.mean(np.abs(outputs) >= 32_767)),
            }
        )
        if has_vocal and len(boundary_sources) < fixture["boundary_tracks"]:
            boundary_sources.append((mixture, vocal))

    # Same signal separated whole vs. in the worker's fixed 10 s chunks.
    long_mixture = np.concatenate([item[0] for item in boundary_sources])
    long_vocal = np.concatenate([item[1] for item in boundary_sources])
    whole = from_pcm(model.separate(to_pcm(long_mixture), HTDEMUCS_SAMPLE_RATE, 2).vocal_pcm_s16le)
    step = HTDEMUCS_SAMPLE_RATE * fixture["chunk_seconds"]
    chunked = np.concatenate(
        [
            from_pcm(
                model.separate(
                    to_pcm(long_mixture[offset : offset + step]), HTDEMUCS_SAMPLE_RATE, 2
                ).vocal_pcm_s16le
            )
            for offset in range(0, len(long_mixture), step)
        ]
    )
    boundary = {
        "audio_seconds": len(long_mixture) / HTDEMUCS_SAMPLE_RATE,
        "chunk_count": math.ceil(len(long_mixture) / step),
        "whole_vocal_sdr_db": sdr(long_vocal, whole),
        "chunked_vocal_sdr_db": sdr(long_vocal, chunked),
    }
    boundary["vocal_sdr_drop_db"] = (
        boundary["whole_vocal_sdr_db"] - boundary["chunked_vocal_sdr_db"]
    )

    torch_peak_vram_mb = torch.cuda.max_memory_allocated() / (1024 * 1024)
    device_peak_vram_mb = vram_sampler.stop()
    peak_vram_mb = max(torch_peak_vram_mb, device_peak_vram_mb)
    model.unload()

    vocal_sdrs = [case["vocal_sdr_db"] for case in cases if case["vocal_sdr_db"] is not None]
    metrics = {
        "load_seconds": load_seconds,
        "peak_vram_mb": peak_vram_mb,
        "track_count": len(cases),
        "vocal_track_count": len(vocal_sdrs),
        "vocal_sdr_median_db": statistics.median(vocal_sdrs),
        "vocal_sdr_mean_db": statistics.mean(vocal_sdrs),
        "instrumental_sdr_median_db": statistics.median(
            case["instrumental_sdr_db"] for case in cases
        ),
        "rtf_median": statistics.median(case["rtf"] for case in cases),
        "clipped_sample_ratio_max": max(case["clipped_sample_ratio"] for case in cases),
        "chunk_boundary": boundary,
    }
    finite = all(
        math.isfinite(value)
        for value in (
            *vocal_sdrs,
            *(case["instrumental_sdr_db"] for case in cases),
            *(case["rtf"] for case in cases),
            boundary["vocal_sdr_drop_db"],
        )
    )
    checks = {
        "vocal_sdr_median": metrics["vocal_sdr_median_db"] >= thresholds["vocal_sdr_median_min_db"],
        "instrumental_sdr_median": metrics["instrumental_sdr_median_db"]
        >= thresholds["instrumental_sdr_median_min_db"],
        "chunk_boundary": boundary["vocal_sdr_drop_db"]
        <= thresholds["chunk_boundary_vocal_sdr_drop_max_db"],
        "rtf_median": metrics["rtf_median"] <= thresholds["rtf_median_max"],
        "load_seconds": metrics["load_seconds"] <= thresholds["load_seconds_max"],
        "peak_vram_mb": metrics["peak_vram_mb"] <= thresholds["peak_vram_mb_max"],
        "clipping": metrics["clipped_sample_ratio_max"] <= thresholds["clipped_sample_ratio_max"],
        "finite": finite,
    }
    report = {
        "schema_version": 1,
        "adapter": "demucs-htdemucs-4.1.0",
        "runtime_version": DEMUCS_PACKAGE_VERSION,
        "weights_file": HTDEMUCS_WEIGHTS_FILE,
        "weights_sha256": HTDEMUCS_WEIGHTS_SHA256,
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
            "poll_interval_seconds": vram_sampler.interval_seconds,
        },
        "checks": checks,
        "passed": all(checks.values()),
        "cases": cases,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in report.items() if k != "cases"}, indent=2))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
