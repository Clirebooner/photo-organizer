"""Location — GPS place candidates, naming normalization, and per-day
dominant-location resolution.

This module only *reads* metadata and produces location names; it never
creates directories or moves files.
"""

from photo_organizer.location.cache import GeocodingCache
from photo_organizer.location.clustering import GpsCluster, GPSClusterer
from photo_organizer.location.geocoder import (
    GeocodingError,
    NominatimGeocoder,
    ReverseGeocoder,
)
from photo_organizer.location.models import (
    DailyLocationResult,
    LocationCandidate,
    LocationInfo,
    LocationMode,
)
from photo_organizer.location.normalizer import LocationNameNormalizer, sanitize_filename
from photo_organizer.location.resolver import DailyLocationResolver

__all__ = [
    "DailyLocationResolver",
    "DailyLocationResult",
    "GPSClusterer",
    "GeocodingCache",
    "GeocodingError",
    "GpsCluster",
    "LocationCandidate",
    "LocationInfo",
    "LocationMode",
    "LocationNameNormalizer",
    "NominatimGeocoder",
    "ReverseGeocoder",
    "sanitize_filename",
]
