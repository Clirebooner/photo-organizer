"""Reverse geocoding.

Only the :class:`ReverseGeocoder` protocol matters to the resolver;
:class:`NominatimGeocoder` is one implementation (geopy + OpenStreetMap
Nominatim). It favors *structured address fields* over parsing the full
display_name, throttles requests (Nominatim usage policy), and never
lets a failed lookup crash a batch — failures raise
:class:`GeocodingError`, which the resolver turns into an unknown
location.
"""

from __future__ import annotations

import time
from typing import Any, Protocol

from geopy.exc import GeocoderServiceError, GeocoderTimedOut, GeocoderUnavailable
from geopy.geocoders import Nominatim

from photo_organizer.location.models import LocationCandidate, LocationInfo

# Nominatim address keys, most specific first, with their candidate kinds.
_CANDIDATE_ORDER: tuple[tuple[str, str], ...] = (
    ("tourism", "poi"),
    ("attraction", "poi"),
    ("amenity", "poi"),
    ("park", "park"),
    ("leisure", "park"),
    ("natural", "park"),
    ("neighbourhood", "neighborhood"),
    ("suburb", "neighborhood"),
    ("locality", "locality"),
    ("municipality", "municipality"),
    ("village", "village"),
    ("town", "town"),
    ("city", "city"),
    ("county", "county"),
    ("state", "admin1"),
    ("country", "country"),
)


class GeocodingError(Exception):
    """Raised when a reverse-geocoding lookup fails (network, no result, ...)."""


class ReverseGeocoder(Protocol):
    """Anything that turns (latitude, longitude) into a LocationInfo."""

    @property
    def provider(self) -> str:
        """Identifier of the geocoding provider (part of the cache key)."""
        ...

    @property
    def language(self) -> str | None:
        """Preferred result language (e.g. 'zh'), if any."""
        ...

    def reverse(self, latitude: float, longitude: float) -> LocationInfo:
        """Reverse geocode the coordinate into a :class:`LocationInfo`.

        Raises:
            GeocodingError: if the lookup fails.
        """
        ...


class NominatimGeocoder:
    """Reverse geocoder backed by OpenStreetMap Nominatim via geopy."""

    def __init__(
        self,
        user_agent: str = "photo-organizer/0.1 (personal photo organizer)",
        min_interval: float = 1.0,
        language: str | None = None,
    ) -> None:
        """Create a throttled Nominatim reverse geocoder.

        Args:
            user_agent: identifies this client to the service (required
                by Nominatim's usage policy).
            min_interval: minimum seconds between two requests.
            language: prefer result names in this language, e.g. "zh".
        """
        self._geocoder = Nominatim(user_agent=user_agent)
        self._min_interval = min_interval
        self._language = language
        self._last_request = 0.0

    @property
    def provider(self) -> str:
        return "nominatim"

    @property
    def language(self) -> str | None:
        return self._language

    def reverse(self, latitude: float, longitude: float) -> LocationInfo:
        """Reverse geocode a coordinate; raises :class:`GeocodingError` on failure."""
        self._throttle()
        try:
            kwargs: dict[str, Any] = {"timeout": 15}
            if self._language:
                kwargs["language"] = self._language
            location = self._geocoder.reverse((latitude, longitude), **kwargs)
        except (GeocoderTimedOut, GeocoderUnavailable, GeocoderServiceError) as exc:
            raise GeocodingError(
                f"geocoding failed for ({latitude:.4f}, {longitude:.4f}): {exc}"
            ) from exc
        if location is None:
            raise GeocodingError(f"no geocoding result for ({latitude:.4f}, {longitude:.4f})")
        return self._to_info(location)

    def _throttle(self) -> None:
        """Enforce the minimum interval between requests."""
        elapsed = time.monotonic() - self._last_request
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request = time.monotonic()

    @staticmethod
    def _to_info(location: Any) -> LocationInfo:
        """Map a geopy Location onto a structured LocationInfo."""
        raw = location.raw or {}
        address = raw.get("address") or {}
        display_name = str(raw.get("display_name", "") or "")
        info = LocationInfo(
            display_name=display_name,
            country_code=_upper_or_none(_addr(address, "country_code")),
            country=_addr(address, "country"),
            admin1=_addr(address, "state"),
            admin2=_addr(address, "county"),
            archive_city=_archive_city(address, display_name),
            city=_addr(address, "city"),
            town=_addr(address, "town"),
            village=_addr(address, "village"),
            municipality=_addr(address, "municipality"),
            locality=_addr(address, "locality"),
            neighborhood=_first_addr(address, "suburb", "neighbourhood"),
            poi_name=_first_addr(address, "tourism", "amenity", "attraction"),
            park_name=_first_addr(address, "park", "leisure", "natural"),
            raw={key: str(value) for key, value in address.items()},
            candidates=[
                LocationCandidate(
                    name=str(address[key]),
                    kind=kind,
                    score=len(_CANDIDATE_ORDER) - index,
                    source="nominatim",
                )
                for index, (key, kind) in enumerate(_CANDIDATE_ORDER)
                if address.get(key)
            ],
        )
        return info


def _addr(address: dict[str, Any], key: str) -> str | None:
    value = address.get(key)
    return str(value).strip() if value else None


def _first_addr(address: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = address.get(key)
        if value:
            return str(value).strip()
    return None


def _upper_or_none(value: str | None) -> str | None:
    return value.upper() if value else None


def _archive_city(address: dict[str, Any], display_name: str) -> str | None:
    """Parent city of a district (e.g. 深圳市 for 南山区), best-effort.

    Nominatim often puts the district into ``city`` and the parent city
    only in ``display_name``; ``state_district`` covers the common case
    where it *is* a structured field.
    """
    value = _addr(address, "state_district")
    if value:
        return value
    return _parent_city_from_display(display_name)


def _parent_city_from_display(display_name: str) -> str | None:
    """Extract a 市/自治州-suffixed parent city from a CJK display_name.

    E.g. "科技园, 南山区, 深圳市, 广东省, 中国" -> "深圳市".
    """
    parts = [part.strip() for part in display_name.split(",") if part.strip()]
    for part in reversed(parts):
        if part.endswith("市") or "自治州" in part:
            return part
    return None
