"""Location domain models.

The Apple-Photos-inspired idea: instead of just the administrative
division, a reverse geocode yields *multiple* place candidates with
different granularity, and the naming layer picks the most appropriate
one depending on the use (folder name vs. preview vs. pure admin).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum


class LocationMode(StrEnum):
    """Naming mode — decides which granularity a location name targets."""

    ARCHIVE = "archive"  # folder names: broad, archive-friendly
    DETAIL = "detail"  # logs/preview: specific place
    ADMIN = "admin"  # pure administrative division


@dataclass(frozen=True)
class LocationCandidate:
    """One place candidate extracted from a geocoding result."""

    name: str
    kind: str  # poi | park | neighborhood | locality | city | town | village
    #          # | municipality | county | admin1 | country | unknown
    score: int = 0
    source: str | None = None  # e.g. "nominatim"


@dataclass(frozen=True)
class LocationInfo:
    """Normalized reverse-geocoding result for one coordinate.

    ``raw`` keeps the provider's original address dict for debugging and
    future use; ``candidates`` holds the granularity-sorted candidates.
    """

    display_name: str = ""
    country_code: str | None = None  # e.g. "CN", "JP"
    country: str | None = None
    admin1: str | None = None  # state / province / prefecture
    admin2: str | None = None  # county
    archive_city: str | None = None  # parent city a district belongs to, e.g. 深圳市 for 南山区
    city: str | None = None
    town: str | None = None
    village: str | None = None
    municipality: str | None = None
    locality: str | None = None
    neighborhood: str | None = None
    poi_name: str | None = None
    park_name: str | None = None
    raw: dict[str, str] | None = None
    candidates: list[LocationCandidate] = field(default_factory=list)


@dataclass(frozen=True)
class DailyLocationResult:
    """The dominant location for one day's photos."""

    date: date
    location_name: str  # already filename-safe
    total_photos: int
    photos_with_gps: int
    dominant_count: int
    dominant_ratio: float  # 0..1, dominant_count / photos_with_gps
    confidence: str  # "manual" | "high" | "medium" | "low" | "none"
    reason: str
    detailed_places: list[str] = field(default_factory=list)  # ≤ 10, deduped
