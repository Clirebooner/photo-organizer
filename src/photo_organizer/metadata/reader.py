"""Metadata reading — from file bytes to a normalized PhotoRecord.

The pipeline only ever talks to a :class:`MetadataReader`. This module
implements the exifread backend (:class:`ExifReader`); a richer
``exiftool`` backend can be added later as another implementation of the
same protocol, without touching any other module.
"""

from __future__ import annotations

from datetime import datetime
from fractions import Fraction
from pathlib import Path
from typing import Any, Protocol

import exifread

from photo_organizer.domain.models import MediaKind, PhotoRecord


class MetadataError(Exception):
    """Raised when metadata for a file cannot be read or mapped."""


class MetadataReader(Protocol):
    """Anything that can turn a file path into a PhotoRecord."""

    def read(self, path: Path) -> PhotoRecord:
        """Return a normalized PhotoRecord for *path*.

        Raises:
            MetadataError: if the file cannot be read or its metadata
                cannot be mapped onto a PhotoRecord.
        """
        ...


# ---------------------------------------------------------------------------
# Low-level tag helpers (exifread IfdTag -> Python values)
# ---------------------------------------------------------------------------


def _suffix_kind(path: Path) -> MediaKind:
    """Map a file extension onto a MediaKind; raise for unknown types."""
    suffix = path.suffix.lower()
    if suffix in {".nef", ".dng", ".cr2", ".arw"}:
        return MediaKind.RAW
    if suffix in {".jpg", ".jpeg", ".tif", ".tiff"}:
        return MediaKind.IMAGE
    if suffix in {".mov", ".mp4", ".avi", ".mts"}:
        return MediaKind.VIDEO
    if suffix in {".xmp", ".thm"}:
        return MediaKind.SIDECAR
    raise MetadataError(f"unsupported file type: {path.name!r}")


def _video_record(path: Path) -> PhotoRecord:
    """PhotoRecord for a video: no EXIF, file mtime as the capture time.

    exifread cannot parse MOV/MP4 (it raises ``ExifNotFound`` internally),
    so videos bypass EXIF parsing entirely and use the file modification
    time — the same fallback non-EXIF photos use.
    """
    return PhotoRecord(
        source_path=path,
        media_kind=MediaKind.VIDEO,
        captured_at=datetime.fromtimestamp(path.stat().st_mtime),
    )


def _tag(tags: dict[str, Any], *names: str) -> str | None:
    """Printable string of the first matching tag, stripped; else None."""
    for name in names:
        tag = tags.get(name)
        if tag is not None:
            value = str(tag).strip()
            if value:
                return value
    return None


def _int_tag(tags: dict[str, Any], *names: str) -> int | None:
    """Integer value of the first matching tag, else None."""
    for name in names:
        tag = tags.get(name)
        if tag is None or not tag.values:
            continue
        try:
            return int(tag.values[0])
        except (TypeError, ValueError):
            continue
    return None


def _exposure(tags: dict[str, Any]) -> str | None:
    """Exposure time as a fraction string, e.g. "1/30"."""
    tag = tags.get("EXIF ExposureTime")
    if tag is None or not tag.values:
        return None
    value = tag.values[0]
    if isinstance(value, Fraction):
        return f"{value.numerator}/{value.denominator}"
    return str(value)


def _decimal_str(tags: dict[str, Any], name: str) -> str | None:
    """Single rational tag as a clean decimal string, e.g. "21.5"."""
    tag = tags.get(name)
    if tag is None or not tag.values:
        return None
    try:
        value = float(tag.values[0])
    except (TypeError, ValueError):
        return None
    if value == int(value):
        return str(int(value))
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _exif_datetime(tags: dict[str, Any], *names: str) -> datetime | None:
    """Parse an EXIF date string ("YYYY:MM:DD HH:MM:SS") from the first tag."""
    for name in names:
        value = _tag(tags, name)
        if not value:
            continue
        for fmt in ("%Y:%m:%d %H:%M:%S", "%Y:%m:%d %H:%M", "%Y:%m:%d"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
    return None


def _dms_to_decimal(ifd_tag: Any, ref: str | None) -> float | None:
    """Convert a DMS tag (degrees, minutes, seconds) + N/S/E/W ref to decimal."""
    if ifd_tag is None or not ifd_tag.values:
        return None
    try:
        deg, minutes, seconds = (float(v) for v in ifd_tag.values[:3])
    except (TypeError, ValueError):
        return None
    decimal = deg + minutes / 60.0 + seconds / 3600.0
    if ref and ref.strip().upper() in ("S", "W"):
        decimal = -decimal
    return decimal


def _gps(tags: dict[str, Any]) -> tuple[float, float] | None:
    """Combine GPS latitude/longitude (with refs) into a (lat, lon) tuple."""
    lat = _dms_to_decimal(
        tags.get("GPS GPSLatitude"), _tag(tags, "GPS GPSLatitudeRef")
    )
    lon = _dms_to_decimal(
        tags.get("GPS GPSLongitude"), _tag(tags, "GPS GPSLongitudeRef")
    )
    if lat is None or lon is None:
        return None
    return (lat, lon)


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------


class ExifReader:
    """Reads metadata via the pure-Python ``exifread`` library."""

    def __init__(self) -> None:
        """Set up the reader (stateless)."""
        pass

    def read(self, path: Path) -> PhotoRecord:
        """Extract metadata from *path* using ``exifread``.

        Videos (per :func:`_suffix_kind`) skip EXIF parsing entirely —
        exifread cannot read MOV/MP4 and would warn — and use the file
        modification time as ``captured_at`` (see :func:`_video_record`).
        Every other type goes through ``exifread``: ``captured_at`` comes
        from EXIF DateTimeOriginal, falling back to DateTimeDigitized,
        then to the file mtime (NOT creation time, for cross-platform
        consistency). Camera make/model, lens, ISO, exposure, aperture,
        focal length and GPS are read best-effort and left ``None`` when
        absent.

        Raises:
            MetadataError: if the file is missing, of an unsupported
                type, or cannot be parsed.
        """
        path = Path(path)
        if not path.is_file():
            raise MetadataError(f"file not found: {path}")

        media_kind = _suffix_kind(path)
        if media_kind is MediaKind.VIDEO:
            return _video_record(path)

        try:
            with path.open("rb") as stream:
                tags = exifread.process_file(stream)
        except (OSError, TypeError, ValueError) as exc:
            raise MetadataError(f"could not parse metadata: {path}: {exc}") from exc

        captured_at = _exif_datetime(
            tags, "EXIF DateTimeOriginal", "EXIF DateTimeDigitized"
        )
        if captured_at is None:
            captured_at = datetime.fromtimestamp(path.stat().st_mtime)

        return PhotoRecord(
            source_path=path,
            media_kind=media_kind,
            captured_at=captured_at,
            camera_make=_tag(tags, "Image Make"),
            camera_model=_tag(tags, "Image Model"),
            lens=_tag(tags, "EXIF LensModel"),
            iso=_int_tag(tags, "EXIF ISOSpeedRatings", "EXIF PhotographicSensitivity"),
            exposure=_exposure(tags),
            aperture=_decimal_str(tags, "EXIF FNumber"),
            focal_length=_decimal_str(tags, "EXIF FocalLength"),
            gps=_gps(tags),
        )
