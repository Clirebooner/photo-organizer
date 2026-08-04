"""Domain models — the contract passed between pipeline stages.

These dataclasses carry no behavior and depend on nothing outside the
standard library, so every module can import them without creating
dependency cycles. See README §7.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path


class MediaKind(StrEnum):
    """Broad category of a media file, decided by extension/signature."""

    RAW = "raw"  # NEF / DNG
    IMAGE = "image"  # JPEG / TIFF
    VIDEO = "video"  # MOV / MP4
    SIDECAR = "sidecar"  # XMP / THM and friends


class ActionKind(StrEnum):
    """What the executor should do with a planned file."""

    COPY = "copy"
    MOVE = "move"
    SYMLINK = "symlink"
    SKIP = "skip"


@dataclass(frozen=True)
class PhotoRecord:
    """Normalized photographic metadata for one file.

    This is the *lingua franca* of the pipeline: everything downstream
    of the metadata reader consumes only this structure, never raw
    EXIF, so swapping the underlying reader (exifread -> exiftool)
    changes nothing downstream.
    """

    source_path: Path
    media_kind: MediaKind
    captured_at: datetime | None = None
    camera_make: str | None = None  # e.g. "NIKON CORPORATION"
    camera_model: str | None = None  # e.g. "NIKON D850"
    lens: str | None = None
    iso: int | None = None
    exposure: str | None = None  # e.g. "1/250s"
    aperture: str | None = None  # e.g. "f/4"
    focal_length: str | None = None  # e.g. "50mm"
    gps: tuple[float, float] | None = None  # (lat, lon)


@dataclass(frozen=True)
class PlannedAction:
    """One planned filesystem operation: source -> destination."""

    kind: ActionKind
    source: Path
    dest: Path
    reason: str = ""  # human-readable justification, for logs/audit
