"""Unit tests for the planner — pure decision logic, no filesystem access."""

from datetime import date, datetime
from pathlib import Path

from photo_organizer.domain.models import ActionKind, MediaKind, PhotoRecord
from photo_organizer.location.models import DailyLocationResult
from photo_organizer.planner import plan


def _record(name: str, day: date, kind: MediaKind = MediaKind.RAW) -> PhotoRecord:
    return PhotoRecord(
        source_path=Path(f"/inbox/{name}"),
        media_kind=kind,
        captured_at=datetime(day.year, day.month, day.day, 12, 0, 0),
    )


def _location_results(day: date, name: str) -> dict[date, DailyLocationResult]:
    return {
        day: DailyLocationResult(
            date=day,
            location_name=name,
            total_photos=1,
            photos_with_gps=1,
            dominant_count=1,
            dominant_ratio=1.0,
            confidence="manual",
            reason="test",
        )
    }


def _expected(root: str, day: date, location: str, filename: str) -> Path:
    return Path(f"{root}/{day.year}/{day.isoformat()}_{location}/RAW/{filename}")


def test_raw_goes_to_raw() -> None:
    day = date(2026, 6, 5)
    actions = plan([_record("DSC001.NEF", day)], "/dest", _location_results(day, "深圳"))
    assert actions[0].dest == _expected("/dest", day, "深圳", "DSC001.NEF")


def test_jpg_goes_to_raw() -> None:
    day = date(2026, 6, 5)
    actions = plan(
        [_record("DSC002.JPG", day, MediaKind.IMAGE)], "/dest", _location_results(day, "深圳")
    )
    assert actions[0].dest == _expected("/dest", day, "深圳", "DSC002.JPG")


def test_video_goes_to_raw() -> None:
    day = date(2026, 6, 5)
    actions = plan(
        [_record("DSC003.MOV", day, MediaKind.VIDEO)], "/dest", _location_results(day, "深圳")
    )
    assert actions[0].dest == _expected("/dest", day, "深圳", "DSC003.MOV")


def test_year_is_correct() -> None:
    actions = plan([_record("DSC001.NEF", date(2024, 3, 9))], "/dest", {})
    assert "2024" in actions[0].dest.parts


def test_date_is_correct() -> None:
    actions = plan([_record("DSC001.NEF", date(2026, 6, 5))], "/dest", {})
    assert any("2026-06-05" in part for part in actions[0].dest.parts)


def test_location_is_correct() -> None:
    day = date(2026, 6, 5)
    actions = plan([_record("DSC001.NEF", day)], "/dest", _location_results(day, "深圳"))
    assert "2026-06-05_深圳" in actions[0].dest.parts


def test_no_location_uses_unknown() -> None:
    actions = plan([_record("DSC001.NEF", date(2026, 6, 5))], "/dest", {})
    assert actions[0].dest == _expected("/dest", date(2026, 6, 5), "Unknown_Location", "DSC001.NEF")


def test_filename_collision_appends_suffix() -> None:
    day = date(2026, 6, 5)
    records = [_record("DSC001.NEF", day), _record("DSC001.NEF", day)]
    actions = plan(records, "/dest", _location_results(day, "深圳"))
    assert len(actions) == 2
    assert actions[0].dest == _expected("/dest", day, "深圳", "DSC001.NEF")
    assert actions[1].dest == _expected("/dest", day, "深圳", "DSC001_1.NEF")


def test_action_is_copy_and_source_kept() -> None:
    day = date(2026, 6, 5)
    record = _record("DSC001.NEF", day)
    actions = plan([record], "/dest", _location_results(day, "深圳"))
    assert actions[0].kind is ActionKind.COPY
    assert actions[0].source == record.source_path


def test_does_not_create_files(tmp_path: Path) -> None:
    day = date(2026, 6, 5)
    dest = tmp_path / "dest"
    actions = plan([_record("DSC001.NEF", day)], dest, _location_results(day, "深圳"))
    assert len(actions) == 1
    assert not actions[0].dest.exists()
    assert not dest.exists()


def test_sidecar_not_planned() -> None:
    actions = plan([_record("DSC001.xmp", date(2026, 6, 5), MediaKind.SIDECAR)], "/dest", {})
    assert actions == []


def test_missing_capture_time_not_planned() -> None:
    record = PhotoRecord(source_path=Path("/inbox/DSC001.NEF"), media_kind=MediaKind.RAW)
    assert plan([record], "/dest", {}) == []
