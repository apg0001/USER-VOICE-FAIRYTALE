from pathlib import Path

from app.audio.ffmpeg import MediaTool
from app.audio.types import AudioProbe, AudioQuality, PreprocessingConfig


class AudioPreprocessingPipeline:
    def __init__(self, media_tool: MediaTool) -> None:
        self.media_tool = media_tool

    async def inspect(self, source: Path) -> tuple[AudioProbe, AudioQuality]:
        probe = await self.media_tool.probe(source)
        quality = await self.media_tool.analyze(source, probe.duration_seconds)
        return probe, quality

    async def process(
        self,
        source: Path,
        destination: Path,
        config: PreprocessingConfig,
    ) -> None:
        await self.media_tool.transcode(source, destination, config)

