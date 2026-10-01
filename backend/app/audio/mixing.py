import math
from array import array
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MixingMetrics:
    vocal_gain: float
    limiter_gain: float
    peak_before_limit: int
    peak_after_limit: int


def _rms(samples: array[int]) -> float:
    if not samples:
        return 0.0
    return math.sqrt(sum(sample * sample for sample in samples) / len(samples))


def mix_pcm_s16le(
    original_vocal_pcm: bytes,
    converted_vocal_pcm: bytes,
    instrumental_pcm: bytes,
    *,
    target_peak_db: float = -1.0,
) -> tuple[bytes, MixingMetrics]:
    original_vocal = array("h")
    original_vocal.frombytes(original_vocal_pcm)
    converted_vocal = array("h")
    converted_vocal.frombytes(converted_vocal_pcm)
    instrumental = array("h")
    instrumental.frombytes(instrumental_pcm)
    if not (len(original_vocal) == len(converted_vocal) == len(instrumental)):
        raise ValueError("mix inputs must have matching frame counts")

    converted_rms = _rms(converted_vocal)
    original_rms = _rms(original_vocal)
    vocal_gain = 1.0 if converted_rms == 0 else original_rms / converted_rms
    vocal_gain = min(4.0, max(0.25, vocal_gain))
    mixed = [
        instrumental[index] + round(converted_vocal[index] * vocal_gain)
        for index in range(len(instrumental))
    ]
    peak_before = max((abs(sample) for sample in mixed), default=0)
    target_peak = round(32_767 * (10 ** (target_peak_db / 20)))
    limiter_gain = 1.0 if peak_before <= target_peak else target_peak / peak_before
    limited = array(
        "h",
        (max(-32_768, min(32_767, round(sample * limiter_gain))) for sample in mixed),
    )
    peak_after = max((abs(sample) for sample in limited), default=0)
    return limited.tobytes(), MixingMetrics(
        vocal_gain=round(vocal_gain, 6),
        limiter_gain=round(limiter_gain, 6),
        peak_before_limit=peak_before,
        peak_after_limit=peak_after,
    )
