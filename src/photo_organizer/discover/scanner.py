"""File discovery — recursively scan an input directory for media files."""

from __future__ import annotations

import os
from pathlib import Path

from photo_organizer.discover.models import DiscoveredFile, MediaKind

# Extension sets, normalized to lowercase with the leading dot.
_IMAGE_SUFFIXES = {".nef", ".jpg", ".jpeg", ".arw", ".cr2", ".cr3", ".dng"}
_VIDEO_SUFFIXES = {".mov", ".mp4"}
_TEMP_SUFFIXES = {".tmp", ".partial", ".cache"}


class PhotoScanner:
    """Recursively scans a root directory for supported media files."""

    def scan(self, root: Path) -> list[DiscoveredFile]:
        """Return every supported media file under *root*, recursively.

        Hidden entries (dot-prefixed) and temporary files (``.tmp``,
        ``.partial``, ``.cache``) are skipped, along with their
        directories. A missing root, or a root that is a file, yields an
        empty list. The result is sorted by path for determinism.
        """
        root = Path(root)
        if not root.is_dir():
            return []

        discovered: list[DiscoveredFile] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [name for name in dirnames if not _is_ignored_name(name)]
            for filename in filenames:
                if _is_ignored_name(filename):
                    continue
                suffix = Path(filename).suffix.lower()
                media_kind = _classify(suffix)
                if media_kind is MediaKind.UNKNOWN:
                    continue
                path = Path(dirpath) / filename
                try:
                    size = path.stat().st_size
                except OSError:
                    continue  # unreadable file — best-effort skip
                discovered.append(
                    DiscoveredFile(path=path, size=size, suffix=suffix, media_kind=media_kind)
                )
        return sorted(discovered, key=lambda file: file.path)


def _is_ignored_name(name: str) -> bool:
    """True for hidden (dot-prefixed) or temporary-suffixed names."""
    if name.startswith("."):
        return True
    return Path(name).suffix.lower() in _TEMP_SUFFIXES


def _classify(suffix: str) -> MediaKind:
    """Classify a normalized (lowercased, dotted) file suffix."""
    if suffix in _IMAGE_SUFFIXES:
        return MediaKind.IMAGE
    if suffix in _VIDEO_SUFFIXES:
        return MediaKind.VIDEO
    return MediaKind.UNKNOWN
