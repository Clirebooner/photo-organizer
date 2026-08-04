"""Location name normalization.

Turns a :class:`LocationInfo` into a short, filename-safe location name
for a given :class:`LocationMode`:

- ``archive`` — folder names; broad, archive-friendly (scenic area /
  attraction over a specific POI; non-CJK municipality type words such
  as "Resort Municipality" are stripped).
- ``detail`` — logs/preview; the most specific place (POI first).
- ``admin`` — pure administrative division.

CJK countries (CN/HK/MO/TW/JP/KR) get special handling: short Chinese
names, common suffixes stripped, and romanized names mapped to Chinese.
"""

from __future__ import annotations

import re

from photo_organizer.location.models import LocationInfo, LocationMode

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_INVALID_FILENAME_CHARS = set('<>:"/\\|?*')

CJK_COUNTRIES = {"CN", "HK", "MO", "TW", "JP", "KR"}

_CJK_COUNTRY_KEYWORDS = (
    "China", "Hong Kong", "Macau", "Macao", "Taiwan", "Japan", "Korea",
    "中国", "香港", "澳门", "台湾", "日本", "韩国", "대한민국",
)

# Suffixes stripped from admin names (longer ones first so e.g.
# "서울특별시" loses "특별시" and not just "시").
_CN_SUFFIXES = ("市", "地区", "盟")
_TW_SUFFIXES = ("市", "县")
_JP_SUFFIXES = ("市", "区", "町", "村", "郡", "府", "県", "县", "都", "道")
_KR_SUFFIXES = ("특별시", "광역시", "시", "군", "구", "市", "郡", "区")

_MUNICIPALITIES = {"北京", "上海", "天津", "重庆"}  # direct-use municipalities

_JP_ALIASES = {
    "Tokyo": "東京",
    "Kyoto": "京都",
    "Osaka": "大阪",
    "Sapporo": "札幌",
    "Nara": "奈良",
    "Hakone": "箱根",
    "Fujikawaguchiko": "富士河口湖",
    "Yokohama": "横浜",
    "Nagoya": "名古屋",
    "Hiroshima": "広島",
    "Fukuoka": "福岡",
    "Okinawa": "沖縄",
    "Hakodate": "函館",
}

_KR_ALIASES = {
    "Seoul": "首尔",
    "서울": "首尔",
    "Busan": "釜山",
    "부산": "釜山",
    "Jeju": "济州",
    "제주": "济州",
    "Incheon": "仁川",
    "인천": "仁川",
    "Daegu": "大邱",
    "대구": "大邱",
    "Daejeon": "大田",
    "대전": "大田",
    "Gwangju": "光州",
    "광주": "光州",
    "Gyeongju": "庆州",
    "경주": "庆州",
    "Suwon": "水原",
    "수원": "水原",
}

# Administrative type words stripped from non-CJK municipality names in
# *archive* output (longest first). OSM often carries the full legal type
# — "Whistler Resort Municipality", "Halifax Regional Municipality" — which
# is too bureaucratic for a folder name; a plain name like "Central Saanich"
# has no suffix and is left untouched.
_MUNICIPALITY_SUFFIXES = (
    "Regional Municipality",
    "Resort Municipality",
    "District Municipality",
    "Municipality",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def sanitize_filename(name: str | None) -> str:
    """Make *name* safe as a folder name; never returns an empty string.

    Spaces and Windows-illegal characters become underscores, consecutive
    underscores collapse, and leading/trailing space/underscore/dot are
    trimmed. An empty result becomes ``Unknown_Location``.
    """
    if not name:
        return "Unknown_Location"
    name = name.strip()
    for ch in _INVALID_FILENAME_CHARS:
        name = name.replace(ch, "_")
    name = name.replace(" ", "_")
    name = re.sub(r"_+", "_", name)
    name = name.strip("_.")
    return name or "Unknown_Location"


def is_cjk(info: LocationInfo) -> bool:
    """True when a LocationInfo belongs to a CJK country (CN/HK/MO/TW/JP/KR)."""
    if (info.country_code or "").upper() in CJK_COUNTRIES:
        return True
    text = f"{info.country or ''} {info.display_name or ''}"
    lower = text.lower()
    return any(keyword.lower() in lower for keyword in _CJK_COUNTRY_KEYWORDS)


def _first(*values: str | None) -> str | None:
    for value in values:
        if value:
            return value
    return None


def _strip_suffixes(name: str, suffixes: tuple[str, ...]) -> str:
    """Strip a single trailing admin suffix (longest match first).

    Only one suffix is removed: e.g. ``京都市`` -> ``京都``, never ``京``.
    """
    for suffix in sorted(suffixes, key=len, reverse=True):
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return name


def _strip_municipality_suffix(name: str) -> str:
    """Drop a trailing administrative type from a municipality name.

    Only the type words are removed — "Whistler Resort Municipality" ->
    "Whistler" — and a plain place name such as "Central Saanich" is left
    untouched.
    """
    for suffix in _MUNICIPALITY_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            stripped = name[: -len(suffix)].strip()
            if stripped:
                return stripped
    return name


def _is_cn_district(name: str) -> bool:
    """True for a district-level Mainland China name (市辖区), e.g. 南山区."""
    return name.endswith("区")


# ---------------------------------------------------------------------------
# Normalizer
# ---------------------------------------------------------------------------


class LocationNameNormalizer:
    """Normalizes a LocationInfo into a location name for one mode."""

    def __init__(self, mode: LocationMode = LocationMode.ARCHIVE) -> None:
        self.mode = mode

    def normalize(self, info: LocationInfo | None) -> str:
        """Return a filename-safe location name for *info* (or ``Unknown_Location``)."""
        if info is None:
            return "Unknown_Location"
        if self.mode == LocationMode.DETAIL:
            name = self._detail(info)
        else:
            name = self._area_or_admin(info)
        return sanitize_filename(name or "Unknown_Location")

    # -- mode selection ------------------------------------------------------

    def _area_or_admin(self, info: LocationInfo) -> str | None:
        if is_cjk(info):
            if self.mode == LocationMode.ADMIN:
                return self._cjk_name(info)
            return self._cjk_archive_name(info)
        if self.mode == LocationMode.ADMIN:
            return self._admin_non_cjk(info)
        return self._archive_non_cjk(info)

    def _cjk_archive_name(self, info: LocationInfo) -> str | None:
        """CJK archive naming — same as admin, except CN prefers the parent city."""
        country = self._cjk_country(info)
        if country == "HK":
            return "香港"
        if country == "MO":
            return "澳门"
        if country == "TW":
            return self._tw(info)
        if country == "JP":
            return self._jp(info)
        if country == "KR":
            return self._kr(info)
        return self._cn_archive(info)

    def _archive_non_cjk(self, info: LocationInfo) -> str | None:
        """Scenic area, then attraction/tourism, then city/town/locality, then admin.

        Overseas archives read most naturally as a scenic area or place,
        so administrative granularity only kicks in after the named places.
        Municipality names drop their legal type word (e.g. "Whistler
        Resort Municipality" -> "Whistler").
        """
        municipality = (
            _strip_municipality_suffix(info.municipality) if info.municipality else None
        )
        return _first(
            info.park_name,
            info.poi_name,
            info.city,
            info.town,
            info.locality,
            info.village,
            municipality,
            info.admin2,
            info.admin1,
            info.country,
        )

    def _admin_non_cjk(self, info: LocationInfo) -> str | None:
        return _first(
            info.city,
            info.town,
            info.village,
            info.municipality,
            info.admin2,
            info.admin1,
            info.country,
        )

    def _detail(self, info: LocationInfo) -> str | None:
        """Most specific place first: POI, park, neighborhood, then down."""
        return _first(
            info.poi_name,
            info.park_name,
            info.neighborhood,
            info.locality,
            info.city,
            info.town,
            info.village,
            info.municipality,
            info.admin2,
            info.admin1,
            info.country,
        )

    # -- CJK rules -----------------------------------------------------------

    def _cjk_country(self, info: LocationInfo) -> str:
        """Resolve the CJK country code, inferring from country name if needed."""
        code = (info.country_code or "").upper()
        if code in CJK_COUNTRIES:
            return code
        text = f"{info.country or ''} {info.display_name or ''}"
        if any(k in text for k in ("日本", "Japan")):
            return "JP"
        if any(k in text for k in ("Korea", "한국", "대한민국", "韩国")):
            return "KR"
        if any(k in text for k in ("台湾", "Taiwan")):
            return "TW"
        if any(k in text for k in ("Macau", "Macao", "澳门")):
            return "MO"
        if any(k in text for k in ("Hong Kong", "香港")):
            return "HK"
        return "CN"

    def _cjk_name(self, info: LocationInfo) -> str | None:
        country = self._cjk_country(info)
        if country == "HK":
            return "香港"
        if country == "MO":
            return "澳门"
        if country == "TW":
            return self._tw(info)
        if country == "JP":
            return self._jp(info)
        if country == "KR":
            return self._kr(info)
        return self._cn(info)

    def _cn(self, info: LocationInfo) -> str | None:
        """Mainland China: prefer the prefecture-level city (地级市)."""
        for field in (info.city, info.admin2, info.admin1, info.locality):
            if not field:
                continue
            stripped = _strip_suffixes(field, _CN_SUFFIXES)
            if stripped in _MUNICIPALITIES:
                return stripped
        for field in (info.city, info.admin2, info.locality, info.admin1):
            if not field:
                continue
            # Keep meaningful suffixes like 自治州 / 自治县.
            if "自治州" in field or "自治县" in field:
                return field
            return _strip_suffixes(field, _CN_SUFFIXES)
        return "中国"

    def _cn_archive(self, info: LocationInfo) -> str | None:
        """Mainland China *archive* name: parent city / municipality first.

        A district-level name (市辖区, e.g. 南山区) is never used directly
        as an archive folder — the prefecture-level city (深圳市) wins.
        """
        for field in (
            info.archive_city,
            info.municipality,
            info.admin2,
            info.city,
            info.locality,
            info.admin1,
        ):
            if not field:
                continue
            if "自治州" in field or "自治县" in field:
                return field
            if _is_cn_district(field):
                continue  # too granular for a folder name
            stripped = _strip_suffixes(field, _CN_SUFFIXES)
            if stripped in _MUNICIPALITIES:
                return stripped
            return stripped
        return "中国"

    def _tw(self, info: LocationInfo) -> str | None:
        """Taiwan: prefer the county/city level (县/市)."""
        for field in (info.city, info.admin2, info.locality, info.admin1):
            if field:
                return _strip_suffixes(field, _TW_SUFFIXES)
        return "台湾"

    def _jp(self, info: LocationInfo) -> str | None:
        """Japan: prefer the municipality (市町村), then prefecture; alias romanized names."""
        for field in (info.city, info.town, info.village, info.municipality, info.locality):
            if field:
                stripped = _strip_suffixes(field, _JP_SUFFIXES)
                return _JP_ALIASES.get(stripped, _JP_ALIASES.get(field, stripped))
        if info.admin1:
            stripped = _strip_suffixes(info.admin1, _JP_SUFFIXES)
            return _JP_ALIASES.get(stripped, stripped)
        return "日本"

    def _kr(self, info: LocationInfo) -> str | None:
        """Korea: prefer the city/county/district (市/郡/区); alias to Chinese."""
        fields = (
            info.city,
            info.town,
            info.village,
            info.municipality,
            info.admin2,
            info.admin1,
            info.locality,
        )
        for field in fields:
            if field:
                stripped = _strip_suffixes(field, _KR_SUFFIXES)
                return _KR_ALIASES.get(stripped, _KR_ALIASES.get(field, stripped))
        return "韩国"
