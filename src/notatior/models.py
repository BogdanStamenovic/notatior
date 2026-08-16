from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class StageName(StrEnum):
    INGEST = "ingest"
    CALIBRATION = "calibration"
    DETECTION = "detection"
    RHYTHM = "rhythm"
    SCORE = "score"
    VALIDATION = "validation"
    DYNAMICS = "dynamics"
    EXPORT = "export"


STAGE_ORDER = list(StageName)


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    REVIEW = "review"
    COMPLETE = "complete"
    STALE = "stale"
    FAILED = "failed"


@dataclass(slots=True)
class KeyRegion:
    index: int
    midi: int
    kind: str
    polygon: list[list[float]]
    active_lab: list[float] | None = None
    hand: str | None = None


@dataclass(slots=True)
class RawNote:
    id: str
    midi: int
    onset: float
    offset: float
    confidence: float = 1.0
    color_cluster: int | None = None
    hand: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ScoreNote:
    id: str
    midi: int
    onset_quarters: float
    duration_quarters: float
    hand: str
    voice: int = 1
    accidental: str | None = None
    velocity: int = 72
    confidence: float = 1.0


@dataclass(slots=True)
class StageState:
    name: str
    status: str = StageStatus.PENDING
    progress: float = 0.0
    message: str = ""
    input_hash: str | None = None
    approved: bool = False
    error: str | None = None


@dataclass(slots=True)
class Project:
    id: str
    title: str
    source: str
    created_at: str
    updated_at: str
    settings: dict[str, Any] = field(default_factory=dict)
    stages: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
