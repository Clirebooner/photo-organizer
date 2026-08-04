"""Discover models — file-level results of scanning an input directory."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class MediaKind(StrEnum):
    """Broad file category, decided by extension at scan time.

    Coarser than the domain's
    :class:`~photo_organizer.domain.models.MediaKind`: the scanner only
    needs IMAGE / VIDEO / UNKNOWN.
    """

    IMAGE = "image"
    VIDEO = "video"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DiscoveredFile:
    """One media file found by the scanner."""

    path: Path
    size: int
    suffix: str  # normalized, e.g. ".nef"
    media_kind: MediaKind

    def file_id(self) -> str:
        """Stable identifier, reserved for future hash-based dedup.

        Placeholder: returns the path for now; will become a content
        hash (path-independent) when dedup is implemented.
        """
        return str(self.path)
