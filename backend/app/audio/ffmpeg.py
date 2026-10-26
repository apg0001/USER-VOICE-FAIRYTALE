import asyncio
import json
import re
from pathlib import Path
from typing import Any, Protocol

from app.audio.types import AudioProbe, AudioQuality, NoiseReduction, PreprocessingConfig


class MediaToolError(RuntimeError):
    pass


class MediaTool(Protocol):
    async def probe(self, source: Path) -> AudioProbe: ...

    async def analyze(self, source: Path, duration_seconds: float) -> AudioQuality: ...

    async def transcode(
        self, source: Path, destination: Path, config: PreprocessingConfig
    ) -> None: ...


class FFmpegMediaTool(MediaTool):
    def __init__(
        self,
        *,
        ffmpeg_path: str = "ffmpeg",
        ffprobe_path: str = "ffprobe",
        timeout_seconds: int = 300,
    ) -> None:
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self.timeout_seconds = timeout_seconds

    async def _run(self, *command: str) -> tuple[bytes, bytes]:
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as error:
            raise MediaToolError("ffmpeg executable is unavailable") from error
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.timeout_seconds
            )
        except TimeoutError as error:
            process.kill()
            await process.wait()
            raise MediaToolError("media command timed out") from error
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace")[-1000:]
            raise MediaToolError(f"media command failed: {detail}")
        return stdout, stderr

    async def probe(self, source: Path) -> AudioProbe:
        stdout, _ = await self._run(
            self.ffprobe_path,
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "format=duration,format_name,size:stream=codec_name,sample_rate,channels",
            "-of",
            "json",
            str(source),
        )
        try:
            payload: dict[str, Any] = json.loads(stdout)
            stream = payload["streams"][0]
            media_format = payload["format"]
            return AudioProbe(
                format_name=str(media_format["format_name"]),
                codec_name=str(stream["codec_name"]),
                duration_seconds=float(media_format["duration"]),
                sample_rate=int(stream["sample_rate"]),
                channels=int(stream["channels"]),
                size_bytes=int(media_format["size"]),
            )
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise MediaToolError("ffprobe returned malformed metadata") from error

    async def analyze(self, source: Path, duration_seconds: float) -> AudioQuality:
        _, stderr = await self._run(
            self.ffmpeg_path,
            "-hide_banner",
            "-nostdin",
            "-i",
            str(source),
            "-af",
            "silencedetect=noise=-40dB:d=0.3,astats=metadata=1:reset=0",
            "-f",
            "null",
            "-",
        )
        report = stderr.decode("utf-8", errors="replace")
        silence = sum(float(value) for value in re.findall(r"silence_duration: ([0-9.]+)", report))
        peak_values = re.findall(r"Peak level dB: (-?(?:inf|[0-9.]+))", report)
        finite_peaks = [float(value) for value in peak_values if value != "-inf"]
        peak_db = max(finite_peaks) if finite_peaks else None
        noise_values = re.findall(r"Noise floor dB: (-?(?:inf|[0-9.]+))", report)
        finite_noise = [float(value) for value in noise_values if value != "-inf"]
        noise_floor_db = max(finite_noise) if finite_noise else None
        silence = min(duration_seconds, silence)
        speech = max(0.0, duration_seconds - silence)
        ratio = silence / duration_seconds if duration_seconds else 1.0
        if noise_floor_db is None:
            noise_level = "unknown"
        elif noise_floor_db > -35:
            noise_level = "high"
        elif noise_floor_db > -50:
            noise_level = "normal"
        else:
            noise_level = "low"
        return AudioQuality(
            speech_seconds=round(speech, 3),
            silence_seconds=round(silence, 3),
            silence_ratio=round(ratio, 4),
            peak_db=peak_db,
            clipping=peak_db is not None and peak_db >= -0.1,
            noise_floor_db=noise_floor_db,
            noise_level=noise_level,
        )

    async def transcode(self, source: Path, destination: Path, config: PreprocessingConfig) -> None:
        filters: list[str] = []
        if config.trim_silence:
            filters.append(
                "silenceremove=start_periods=1:start_duration=0.1:start_threshold=-50dB:"
                "stop_periods=-1:stop_duration=0.2:stop_threshold=-50dB"
            )
        if config.noise_reduction == NoiseReduction.NORMAL:
            filters.append("afftdn=nf=-25")
        elif config.noise_reduction == NoiseReduction.STRONG:
            filters.append("afftdn=nf=-35:tn=1")
        if config.normalize_loudness:
            filters.append("loudnorm=I=-16:LRA=11:TP=-1.5")

        command = [
            self.ffmpeg_path,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-i",
            str(source),
            "-vn",
            "-ac",
            str(config.target_channels),
            "-ar",
            str(config.target_sample_rate),
        ]
        if filters:
            command.extend(["-af", ",".join(filters)])
        command.extend(["-c:a", "pcm_s16le", str(destination)])
        await self._run(*command)
