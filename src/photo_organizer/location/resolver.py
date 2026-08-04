"""Daily dominant location resolver.

Groups photos by capture date, clusters their GPS coordinates by physical
distance (so the same spot is geocoded once), names each cluster with the
configured :class:`LocationMode`, and picks the dominant location per day
with a confidence level. Only *reads* metadata and geocoding results — it
never creates directories or moves files.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date

from photo_organizer.domain.models import PhotoRecord
from photo_organizer.location.cache import GeocodingCache
from photo_organizer.location.clustering import GPSClusterer
from photo_organizer.location.geocoder import GeocodingError, ReverseGeocoder
from photo_organizer.location.models import DailyLocationResult, LocationInfo, LocationMode
from photo_organizer.location.normalizer import LocationNameNormalizer, sanitize_filename

_CONFIDENCE_NONE = "none"
_CONFIDENCE_LOW = "low"
_CONFIDENCE_MEDIUM = "medium"
_CONFIDENCE_HIGH = "high"
_CONFIDENCE_MANUAL = "manual"

# Why a day came out ``Unknown_Location`` (surfaced in previews/logs).
_UNKNOWN_NO_GPS = "no GPS data for this day"
_UNKNOWN_GEOCODER_FAILED = "geocoder failed"
_UNKNOWN_NO_SUITABLE_NAME = "no suitable location name for the resolved place"


class DailyLocationResolver:
    """Resolves one dominant location name per date for a batch of photos."""

    def __init__(
        self,
        geocoder: ReverseGeocoder,
        cache: GeocodingCache | None = None,
        cluster_radius_m: float = 200.0,
    ) -> None:
        """Create a resolver bound to a geocoder and (optionally) a cache.

        Args:
            geocoder: reverse geocoder; its ``provider``/``language`` are
                part of the cache key.
            cache: coordinate -> LocationInfo store.
            cluster_radius_m: GPS points closer than this (in meters) are
                treated as the same place and geocoded once.
        """
        self._geocoder = geocoder
        self._cache = cache or GeocodingCache()
        self._clusterer = GPSClusterer(radius_m=cluster_radius_m)
        self._provider = geocoder.provider
        self._language = geocoder.language

    def resolve(
        self,
        records: list[PhotoRecord],
        date_overrides: dict[str, str] | None = None,
        cli_location_name: str | None = None,
        location_mode: LocationMode = LocationMode.ARCHIVE,
    ) -> dict[date, DailyLocationResult]:
        """Resolve per-day dominant locations for *records*.

        Manual names take priority over GPS inference:
        ``date_overrides`` > ``cli_location_name`` > GPS > Unknown_Location.

        Args:
            date_overrides: {ISO date "YYYY-MM-DD": location name}.
            cli_location_name: applies to every date.
            location_mode: naming granularity for folder names.
        """
        overrides = date_overrides or {}
        normalizer = LocationNameNormalizer(location_mode)
        detail_normalizer = LocationNameNormalizer(LocationMode.DETAIL)

        groups: dict[date, list[PhotoRecord]] = defaultdict(list)
        for record in records:
            if record.captured_at is None:
                continue  # no usable timestamp
            groups[record.captured_at.date()].append(record)

        results: dict[date, DailyLocationResult] = {}
        try:
            for day in sorted(groups):
                results[day] = self._resolve_day(
                    day,
                    groups[day],
                    normalizer,
                    detail_normalizer,
                    overrides,
                    cli_location_name,
                )
        finally:
            self._cache.flush()  # batch geocoding done — persist once
        return results

    def _resolve_day(
        self,
        day: date,
        records: list[PhotoRecord],
        normalizer: LocationNameNormalizer,
        detail_normalizer: LocationNameNormalizer,
        overrides: dict[str, str],
        cli_location_name: str | None,
    ) -> DailyLocationResult:
        total = len(records)
        gps_pairs = [(record, record.gps) for record in records if record.gps is not None]

        if day.isoformat() in overrides:
            return _manual_result(
                day, total, len(gps_pairs), overrides[day.isoformat()], "date override"
            )
        if cli_location_name:
            return _manual_result(
                day, total, len(gps_pairs), cli_location_name, "CLI --location-name override"
            )

        if not gps_pairs:
            return DailyLocationResult(
                date=day,
                location_name="Unknown_Location",
                total_photos=total,
                photos_with_gps=0,
                dominant_count=0,
                dominant_ratio=0.0,
                confidence=_CONFIDENCE_NONE,
                reason=_UNKNOWN_NO_GPS,
                detailed_places=[],
            )

        # Cluster coordinates by physical distance so a spot is geocoded once.
        clusters = self._clusterer.cluster(pt for _, pt in gps_pairs)
        point_to_cluster: dict[tuple[float, float], int] = {}
        for index, cluster in enumerate(clusters):
            for point in cluster.points:
                point_to_cluster[point] = index

        cluster_names: dict[int, str] = {}
        cluster_details: dict[int, str] = {}
        cluster_geocoded: dict[int, bool] = {}  # True when the lookup succeeded
        for index, cluster in enumerate(clusters):
            info = self._location_info(*cluster.centroid)
            if info is None:
                cluster_geocoded[index] = False
                cluster_names[index] = "Unknown_Location"
                cluster_details[index] = "Unknown_Location"
            else:
                cluster_geocoded[index] = True
                cluster_names[index] = normalizer.normalize(info)
                cluster_details[index] = detail_normalizer.normalize(info)

        counts: Counter[str] = Counter()
        cluster_photo_counts: Counter[int] = Counter()
        for _, point in gps_pairs:
            index = point_to_cluster[point]
            cluster_photo_counts[index] += 1
            counts[cluster_names[index]] += 1

        top = counts.most_common()
        first_name, first_count = top[0]
        gps_count = len(gps_pairs)
        ratio = first_count / gps_count

        if first_name == "Unknown_Location":
            location_name, confidence = "Unknown_Location", _CONFIDENCE_NONE
            reason = _unknown_reason(cluster_names, cluster_geocoded, cluster_photo_counts)
        elif ratio >= 0.6:
            location_name, confidence = first_name, _CONFIDENCE_HIGH
            reason = f"{first_name} covers {ratio:.0%} of the GPS photos"
        elif ratio >= 0.4 and (len(top) < 2 or (first_count - top[1][1]) / gps_count >= 0.15):
            location_name, confidence = first_name, _CONFIDENCE_MEDIUM
            lead = (first_count - top[1][1]) / gps_count
            reason = f"{first_name} covers {ratio:.0%}, leading the next place by {lead:.0%}"
        else:
            location_name, confidence = "Multi_Location", _CONFIDENCE_LOW
            reason = f"photos spread across {len(top)} locations, no clear dominance"

        detail_counts: Counter[str] = Counter()
        for _, point in gps_pairs:
            detail = cluster_details[point_to_cluster[point]]
            if detail and detail != "Unknown_Location":
                detail_counts[detail] += 1
        detailed_places = [name for name, _ in detail_counts.most_common(10)]

        return DailyLocationResult(
            date=day,
            location_name=location_name,
            total_photos=total,
            photos_with_gps=gps_count,
            dominant_count=first_count,
            dominant_ratio=round(ratio, 4),
            confidence=confidence,
            reason=reason,
            detailed_places=detailed_places,
        )

    def _location_info(self, latitude: float, longitude: float) -> LocationInfo | None:
        """Geocode a coordinate, consulting the cache first; None on failure."""
        cached = self._cache.get(latitude, longitude, self._provider, self._language)
        if cached is not None:
            return cached
        try:
            info = self._geocoder.reverse(latitude, longitude)
        except GeocodingError:
            return None
        except Exception:
            return None  # never let a lookup crash the batch
        if info is None:
            return None
        self._cache.put(latitude, longitude, self._provider, self._language, info)
        return info


def _manual_result(
    day: date,
    total: int,
    gps_count: int,
    name: str,
    why: str,
) -> DailyLocationResult:
    """Build a result for a manually supplied location name."""
    clean = sanitize_filename(name) or "Unknown_Location"
    return DailyLocationResult(
        date=day,
        location_name=clean,
        total_photos=total,
        photos_with_gps=gps_count,
        dominant_count=gps_count,
        dominant_ratio=1.0 if gps_count else 0.0,
        confidence=_CONFIDENCE_MANUAL,
        reason=why,
        detailed_places=[],
    )


def _unknown_reason(
    cluster_names: dict[int, str],
    cluster_geocoded: dict[int, bool],
    cluster_photo_counts: Counter[int],
) -> str:
    """Explain why the dominant location came out ``Unknown_Location``.

    The dominant unknown cluster decides the category: one that never
    geocoded means the lookup failed; one that geocoded but yielded no
    usable name means the place had no suitable name.
    """
    dominant = max(
        (index for index, name in cluster_names.items() if name == "Unknown_Location"),
        key=cluster_photo_counts.__getitem__,
        default=None,
    )
    if dominant is None:
        return _UNKNOWN_NO_SUITABLE_NAME
    if not cluster_geocoded[dominant]:
        return _UNKNOWN_GEOCODER_FAILED
    return _UNKNOWN_NO_SUITABLE_NAME
