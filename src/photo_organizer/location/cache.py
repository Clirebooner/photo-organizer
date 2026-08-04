"""Geocoding cache.

Caches reverse-geocoding results keyed by the coordinate cluster (rounded
to 4 decimal places), the provider and the language — so different
providers/languages never collide. :meth:`GeocodingCache.put` only mutates
memory; the file is rewritten once by :meth:`GeocodingCache.flush` at the
end of a batch. Cache read/write failures must never break the main flow,
so every file operation is best-effort.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from photo_organizer.location.models import LocationCandidate, LocationInfo

DEFAULT_CACHE_PATH = Path.home() / ".cache" / "photo-organizer" / "geocoding.json"


class GeocodingCache:
    """A tiny JSON-backed cache for :class:`LocationInfo` results."""

    def __init__(self, path: Path | str | None = None) -> None:
        """Use *path* for the cache file (default: ``~/.cache/photo-organizer/geocoding.json``)."""
        self._path = Path(path) if path is not None else DEFAULT_CACHE_PATH
        self._data: dict[str, dict[str, Any]] = {}
        self._dirty = False
        self._load()

    @staticmethod
    def cache_key(
        latitude: float,
        longitude: float,
        provider: str,
        language: str | None,
    ) -> str:
        """Stable key: provider, language, and the rounded coordinate cluster."""
        lang = language or "default"
        return f"{provider}|{lang}|{latitude:.4f},{longitude:.4f}"

    def get(
        self,
        latitude: float,
        longitude: float,
        provider: str,
        language: str | None,
    ) -> LocationInfo | None:
        """Return the cached LocationInfo for a coordinate, or None."""
        entry = self._data.get(self.cache_key(latitude, longitude, provider, language))
        if entry is None:
            return None
        return _from_dict(entry)

    def put(
        self,
        latitude: float,
        longitude: float,
        provider: str,
        language: str | None,
        info: LocationInfo,
    ) -> None:
        """Store *info* in memory; persisted by :meth:`flush`."""
        key = self.cache_key(latitude, longitude, provider, language)
        self._data[key] = _to_dict(info)
        self._dirty = True

    def flush(self) -> None:
        """Write pending entries to disk once; a no-op when nothing changed."""
        if not self._dirty:
            return
        self._save()
        self._dirty = False

    def _load(self) -> None:
        try:
            if self._path.is_file():
                with self._path.open("r", encoding="utf-8") as stream:
                    self._data = json.load(stream)
        except (OSError, ValueError):
            self._data = {}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("w", encoding="utf-8") as stream:
                json.dump(self._data, stream, ensure_ascii=False, indent=2)
        except OSError:
            pass  # cache write failure must not interrupt the pipeline


def _to_dict(info: LocationInfo) -> dict[str, Any]:
    """Serialize a LocationInfo (nested dataclasses included) to JSON-safe dicts."""
    return asdict(info)


def _from_dict(data: dict[str, Any]) -> LocationInfo:
    """Rebuild a LocationInfo from a cached dict, restoring candidate objects."""
    data = dict(data)
    candidates = [LocationCandidate(**c) for c in data.pop("candidates", [])]
    return LocationInfo(**data, candidates=candidates)
