from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class OutputProvenance:
    model_key: str
    model_version: str
    synthetic_audio: bool = True
    schema_version: int = 1

    def to_dict(self) -> dict[str, object]:
        return {
            "synthetic_audio": self.synthetic_audio,
            "schema_version": self.schema_version,
            "model_key": self.model_key,
            "model_version": self.model_version,
        }


class WatermarkProvider(ABC):
    """Worker-only extension point; implementations must run before object publication."""

    @abstractmethod
    async def apply(self, source: Path, destination: Path) -> dict[str, object]:
        pass


@dataclass(frozen=True, slots=True)
class AbuseReport:
    output_id: str
    reporter_subject_hash: str
    category: str
    details: str | None = None


class AbuseReportSink(ABC):
    """Boundary for a moderated abuse queue; no default sink silently accepts reports."""

    @abstractmethod
    async def submit(self, report: AbuseReport) -> str:
        pass
