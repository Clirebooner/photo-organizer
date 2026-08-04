"""Unit tests for the location module — all geocoding is faked, no network."""

from datetime import date, datetime
from pathlib import Path

import pytest

from photo_organizer.domain.models import MediaKind, PhotoRecord
from photo_organizer.location.cache import GeocodingCache
from photo_organizer.location.geocoder import GeocodingError
from photo_organizer.location.models import LocationInfo, LocationMode
from photo_organizer.location.normalizer import LocationNameNormalizer, sanitize_filename
from photo_organizer.location.resolver import DailyLocationResolver


class FakeGeocoder:
    """Returns preset LocationInfo per cache key; never touches the network."""

    provider = "nominatim"
    language = "zh"

    def __init__(self, mapping: dict[str, LocationInfo | Exception]) -> None:
        self.mapping = mapping
        self.calls: list[tuple[float, float]] = []

    def reverse(self, latitude: float, longitude: float) -> LocationInfo:
        self.calls.append((latitude, longitude))
        value = self.mapping.get(_key(latitude, longitude))
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise GeocodingError(f"no result for ({latitude}, {longitude})")
        return value


def _key(latitude: float, longitude: float) -> str:
    """Cache key matching the FakeGeocoder's provider/language."""
    return GeocodingCache.cache_key(
        latitude, longitude, FakeGeocoder.provider, FakeGeocoder.language
    )


def _norm(info: LocationInfo, mode: LocationMode = LocationMode.ARCHIVE) -> str:
    return LocationNameNormalizer(mode).normalize(info)


def _cn(city: str) -> LocationInfo:
    """Shortcut for a mainland-China LocationInfo (used a lot in resolver tests)."""
    return LocationInfo(country_code="CN", country="中国", city=city)


def _photo(day: date, gps: tuple[float, float] | None = None, idx: int = 0) -> PhotoRecord:
    return PhotoRecord(
        source_path=Path(f"/tmp/shot_{day}_{idx}.NEF"),
        media_kind=MediaKind.RAW,
        captured_at=datetime(day.year, day.month, day.day, 12, 0, 0),
        gps=gps,
    )


# ---------------------------------------------------------------------------
# Filename sanitization
# ---------------------------------------------------------------------------


def test_sanitize_removes_windows_illegal_chars() -> None:
    name = 'A/B:C*D"E<F>G|H?I\\J'
    assert sanitize_filename(name) == "A_B_C_D_E_F_G_H_I_J"


def test_sanitize_spaces_and_collapse() -> None:
    assert sanitize_filename("  Banff  National Park  ") == "Banff_National_Park"
    assert sanitize_filename("Tokyo__Tower") == "Tokyo_Tower"


def test_sanitize_empty_falls_back() -> None:
    assert sanitize_filename("") == "Unknown_Location"
    assert sanitize_filename(None) == "Unknown_Location"
    assert sanitize_filename("   ") == "Unknown_Location"
    assert sanitize_filename("..._...") == "Unknown_Location"


# ---------------------------------------------------------------------------
# CJK naming rules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("city", "expected"),
    [
        ("深圳市", "深圳"),
        ("北京市", "北京"),
        ("上海市", "上海"),
        ("广州市", "广州"),
        ("重庆市", "重庆"),
    ],
)
def test_cn_city(city: str, expected: str) -> None:
    assert _norm(LocationInfo(country_code="CN", country="中国", city=city)) == expected


@pytest.mark.parametrize(
    ("cc", "country"),
    [("HK", "Hong Kong"), ("HK", "香港"), ("MO", "Macau"), ("MO", "澳门")],
)
def test_hk_mo(cc: str, country: str) -> None:
    expected = "香港" if cc == "HK" else "澳门"
    assert _norm(LocationInfo(country_code=cc, country=country)) == expected


@pytest.mark.parametrize(
    ("city", "expected"),
    [
        ("台北市", "台北"),
        ("新北市", "新北"),
        ("台中市", "台中"),
        ("高雄市", "高雄"),
        ("花莲县", "花莲"),
        ("台东县", "台东"),
    ],
)
def test_tw(city: str, expected: str) -> None:
    assert _norm(LocationInfo(country_code="TW", country="台湾", city=city)) == expected


def test_tw_country_only() -> None:
    assert _norm(LocationInfo(country_code="TW", country="Taiwan")) == "台湾"


@pytest.mark.parametrize(
    ("city", "expected"),
    [
        ("Tokyo", "東京"),
        ("Kyoto", "京都"),
        ("Osaka", "大阪"),
        ("Sapporo", "札幌"),
        ("Nara", "奈良"),
        ("Hakone", "箱根"),
        ("Fujikawaguchiko", "富士河口湖"),
        ("京都市", "京都"),
    ],
)
def test_jp(city: str, expected: str) -> None:
    assert _norm(LocationInfo(country_code="JP", country="日本", city=city)) == expected


def test_jp_country_only() -> None:
    assert _norm(LocationInfo(country_code="JP", country="Japan")) == "日本"


@pytest.mark.parametrize(
    ("city", "expected"),
    [
        ("Seoul", "首尔"),
        ("서울", "首尔"),
        ("Busan", "釜山"),
        ("부산", "釜山"),
        ("Jeju", "济州"),
        ("제주", "济州"),
        ("Seoul특별시", "首尔"),
    ],
)
def test_kr(city: str, expected: str) -> None:
    assert _norm(LocationInfo(country_code="KR", country="South Korea", city=city)) == expected


def test_kr_country_only() -> None:
    assert _norm(LocationInfo(country_code="KR", country="대한민국")) == "韩国"


# ---------------------------------------------------------------------------
# CN archive vs detail vs admin (parent city beats district)
# ---------------------------------------------------------------------------


def test_cn_archive_uses_parent_city_over_district() -> None:
    info = LocationInfo(
        country_code="CN",
        country="中国",
        city="南山区",
        admin2="深圳市",
        admin1="广东省",
    )
    assert _norm(info, LocationMode.ARCHIVE) == "深圳"
    assert _norm(info, LocationMode.DETAIL) == "南山区"
    assert _norm(info, LocationMode.ADMIN) == "南山区"


def test_cn_archive_prefers_archive_city() -> None:
    info = LocationInfo(country_code="CN", country="中国", city="南山区", archive_city="深圳市")
    assert _norm(info, LocationMode.ARCHIVE) == "深圳"


def test_cn_archive_skips_district_without_parent() -> None:
    info = LocationInfo(country_code="CN", country="中国", city="南山区")
    assert _norm(info, LocationMode.ARCHIVE) == "中国"


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------


def test_archive_prefers_scenic_area_over_poi() -> None:
    info = LocationInfo(
        country_code="CA",
        country="Canada",
        admin1="Alberta",
        town="Lake Louise",
        park_name="Banff National Park",
        poi_name="Moraine Lake",
    )
    assert _norm(info, LocationMode.ARCHIVE) == "Banff_National_Park"


def test_detail_prefers_poi() -> None:
    info = LocationInfo(
        country_code="JP",
        country="日本",
        city="Tokyo",
        park_name="Shiba Park",
        poi_name="Tokyo Tower",
    )
    assert _norm(info, LocationMode.DETAIL) == "Tokyo_Tower"


def test_admin_prefers_admin_division() -> None:
    info = LocationInfo(
        country_code="US",
        country="United States",
        admin1="California",
        city="San Francisco",
        park_name="Golden Gate Park",
        poi_name="Golden Gate Bridge",
    )
    assert _norm(info, LocationMode.ADMIN) == "San_Francisco"


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


def _resolver(geocoder: FakeGeocoder, tmp_path: Path) -> DailyLocationResolver:
    return DailyLocationResolver(geocoder, GeocodingCache(tmp_path / "g.json"))


def test_no_gps_unknown(tmp_path: Path) -> None:
    day = date(2026, 6, 5)
    result = _resolver(FakeGeocoder({}), tmp_path).resolve([_photo(day)])[day]
    assert result.location_name == "Unknown_Location"
    assert result.confidence == "none"
    assert result.photos_with_gps == 0


def test_high_confidence_dominant(tmp_path: Path) -> None:
    day = date(2026, 6, 5)
    geo = FakeGeocoder(
        {
            _key(22.54, 113.92): _cn("深圳市"),
            _key(23.12, 113.27): _cn("广州市"),
        }
    )
    records = [_photo(day, (22.54, 113.92), i) for i in range(3)] + [
        _photo(day, (23.12, 113.27), i) for i in range(2)
    ]
    result = _resolver(geo, tmp_path).resolve(records)[day]
    assert result.location_name == "深圳"
    assert result.confidence == "high"
    assert result.dominant_ratio == pytest.approx(0.6)


def test_medium_confidence_with_lead(tmp_path: Path) -> None:
    day = date(2026, 6, 5)
    geo = FakeGeocoder(
        {
            _key(22.54, 113.92): _cn("深圳市"),
            _key(23.12, 113.27): _cn("广州市"),
            _key(24.0, 114.0): _cn("东莞市"),
            _key(24.5, 114.5): _cn("惠州市"),
        }
    )
    records = [_photo(day, (22.54, 113.92), i) for i in range(2)] + [
        _photo(day, (23.12, 113.27), i) for i in range(1)
    ] + [_photo(day, (24.0, 114.0), i) for i in range(1)] + [
        _photo(day, (24.5, 114.5), i) for i in range(1)
    ]
    result = _resolver(geo, tmp_path).resolve(records)[day]
    # 2/5 = 0.4, lead over the next place (1) = 0.2 >= 0.15 -> medium
    assert result.confidence == "medium"
    assert result.location_name == "深圳"


def test_spread_multi_location(tmp_path: Path) -> None:
    day = date(2026, 6, 5)
    geo = FakeGeocoder(
        {
            _key(22.54, 113.92): _cn("深圳市"),
            _key(23.12, 113.27): _cn("广州市"),
        }
    )
    records = [_photo(day, (22.54, 113.92), i) for i in range(2)] + [
        _photo(day, (23.12, 113.27), i) for i in range(2)
    ]
    result = _resolver(geo, tmp_path).resolve(records)[day]
    assert result.location_name == "Multi_Location"
    assert result.confidence == "low"


def test_date_override_wins(tmp_path: Path) -> None:
    day = date(2026, 6, 5)
    geo = FakeGeocoder(
        {
            _key(22.54, 113.92): _cn("深圳市"),
        }
    )
    records = [_photo(day, (22.54, 113.92), i) for i in range(3)]
    result = _resolver(geo, tmp_path).resolve(records, date_overrides={"2026-06-05": "黄山"})[day]
    assert result.location_name == "黄山"
    assert geo.calls == []  # manual override never geocodes


def test_cli_location_name_wins_over_gps_but_not_override(tmp_path: Path) -> None:
    day = date(2026, 6, 5)
    geo = FakeGeocoder(
        {
            _key(22.54, 113.92): _cn("深圳市"),
        }
    )
    records = [_photo(day, (22.54, 113.92), i) for i in range(3)]
    resolver = _resolver(geo, tmp_path)

    result = resolver.resolve(records, cli_location_name="Banff National Park")[day]
    assert result.location_name == "Banff_National_Park"
    assert result.confidence == "manual"

    result2 = resolver.resolve(
        records, cli_location_name="Banff National Park", date_overrides={"2026-06-05": "深圳"}
    )[day]
    assert result2.location_name == "深圳"  # date override beats CLI name


def test_detailed_places_dedup_and_cap(tmp_path: Path) -> None:
    day = date(2026, 6, 5)
    infos = {
        _key(30.0 + i * 0.01, 120.0): LocationInfo(
            country_code="CN", country="中国", city=f"Place{i}"
        )
        for i in range(15)
    }
    geo = FakeGeocoder(infos)
    records = [_photo(day, (30.0 + i * 0.01, 120.0), i) for i in range(15)]
    result = _resolver(geo, tmp_path).resolve(records)[day]
    assert len(result.detailed_places) <= 10
    assert len(set(result.detailed_places)) == len(result.detailed_places)


def test_cache_hit_does_not_call_geocoder(tmp_path: Path) -> None:
    day = date(2026, 6, 5)
    info = _cn("深圳市")
    cache = GeocodingCache(tmp_path / "g.json")
    cache.put(22.54575, 113.92350, FakeGeocoder.provider, FakeGeocoder.language, info)

    geo = FakeGeocoder({})
    record = _photo(day, (22.54575, 113.92350))
    result = DailyLocationResolver(geo, cache).resolve([record])[day]

    assert result.location_name == "深圳"
    assert geo.calls == []


def test_geocoder_failure_becomes_unknown(tmp_path: Path) -> None:
    day = date(2026, 6, 5)
    geo = FakeGeocoder({_key(22.54, 113.92): GeocodingError("network down")})
    record = _photo(day, (22.54, 113.92))
    result = _resolver(geo, tmp_path).resolve([record])[day]
    assert result.location_name == "Unknown_Location"
    assert result.confidence == "none"


# ---------------------------------------------------------------------------
# Cache: key isolation and deferred persistence
# ---------------------------------------------------------------------------


def test_cache_isolated_by_language(tmp_path: Path) -> None:
    cache = GeocodingCache(tmp_path / "g.json")
    cache.put(22.54, 113.92, "nominatim", "zh", _cn("深圳市"))
    assert cache.get(22.54, 113.92, "nominatim", "en") is None
    assert cache.get(22.54, 113.92, "nominatim", "zh") is not None


def test_cache_isolated_by_provider(tmp_path: Path) -> None:
    cache = GeocodingCache(tmp_path / "g.json")
    cache.put(22.54, 113.92, "nominatim", "zh", _cn("深圳市"))
    assert cache.get(22.54, 113.92, "google", "zh") is None


def test_cache_put_defers_write_until_flush(tmp_path: Path) -> None:
    path = tmp_path / "g.json"
    cache = GeocodingCache(path)
    cache.put(22.54, 113.92, "nominatim", "zh", _cn("深圳市"))
    assert not path.exists()  # memory-only until flush
    cache.flush()
    assert path.exists()


def test_cache_flush_persists_reload(tmp_path: Path) -> None:
    path = tmp_path / "g.json"
    cache = GeocodingCache(path)
    cache.put(22.54, 113.92, "nominatim", "zh", _cn("深圳市"))
    cache.flush()

    reloaded = GeocodingCache(path)
    info = reloaded.get(22.54, 113.92, "nominatim", "zh")
    assert info is not None
    assert info.city == "深圳市"
