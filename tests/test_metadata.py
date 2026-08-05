"""Unit tests for the metadata reader's pure helpers."""

import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import exifread

from photo_organizer.domain.models import MediaKind
from photo_organizer.metadata.reader import (
    ExifReader,
    _dms_to_decimal,
    _suffix_kind,
)


def test_suffix_kind_maps_common_types() -> None:
    assert _suffix_kind(Path("DSC_0001.NEF")) is MediaKind.RAW
    assert _suffix_kind(Path("DSC_0001.dng")) is MediaKind.RAW
    assert _suffix_kind(Path("DSC_0001.JPG")) is MediaKind.IMAGE
    assert _suffix_kind(Path("DSC_0001.jpeg")) is MediaKind.IMAGE
    assert _suffix_kind(Path("DSC_0001.MOV")) is MediaKind.VIDEO
    assert _suffix_kind(Path("DSC_0001.xmp")) is MediaKind.SIDECAR


def test_dms_to_decimal_with_ref() -> None:
    tag = SimpleNamespace(values=[22, 6549 / 200, 0])
    assert _dms_to_decimal(tag, "N") == 22 + (6549 / 200) / 60
    assert _dms_to_decimal(tag, "S") == -(22 + (6549 / 200) / 60)
    assert _dms_to_decimal(tag, "W") == -(22 + (6549 / 200) / 60)
    assert _dms_to_decimal(tag, None) == 22 + (6549 / 200) / 60


def test_dms_to_decimal_missing_values() -> None:
    assert _dms_to_decimal(None, "N") is None
    assert _dms_to_decimal(SimpleNamespace(values=[]), "N") is None


def test_mtime_fallback_when_no_exif_date(monkeypatch, tmp_path: Path) -> None:
    """A file without EXIF dates falls back to its modification time."""
    path = tmp_path / "plain.jpg"
    path.write_bytes(b"not a real jpeg")
    monkeypatch.setattr(exifread, "process_file", lambda stream: {})

    record = ExifReader().read(path)

    assert record.captured_at is not None
    assert isinstance(record.captured_at, datetime)
    assert record.camera_model is None
    assert record.gps is None


def test_video_skips_exifread_and_uses_mtime(monkeypatch, tmp_path: Path) -> None:
    """Videos bypass EXIF parsing and use the file mtime as capture time."""
    path = tmp_path / "clip.MOV"
    path.write_bytes(b"video bytes")
    os.utime(path, (1_700_000_000, 1_700_000_000))

    calls: list[object] = []

    def fake_process(stream: object) -> dict[str, object]:
        calls.append(stream)
        return {"EXIF DateTimeOriginal": "2020:01:01 00:00:00"}  # never used

    monkeypatch.setattr(exifread, "process_file", fake_process)

    record = ExifReader().read(path)

    assert calls == []  # exifread is never invoked for a video
    assert record.media_kind is MediaKind.VIDEO
    assert record.captured_at == datetime.fromtimestamp(1_700_000_000)
    assert record.camera_model is None
    assert record.gps is None


def test_image_still_goes_through_exifread(monkeypatch, tmp_path: Path) -> None:
    """Non-video files keep the existing EXIF parsing path."""
    path = tmp_path / "shot.jpg"
    path.write_bytes(b"jpeg bytes")
    calls: list[object] = []

    def fake_process(stream: object) -> dict[str, object]:
        calls.append(stream)
        return {"EXIF DateTimeOriginal": "2020:06:05 10:30:00"}

    monkeypatch.setattr(exifread, "process_file", fake_process)

    record = ExifReader().read(path)

    assert len(calls) == 1
    assert record.media_kind is MediaKind.IMAGE
    assert record.captured_at == datetime(2020, 6, 5, 10, 30, 0)
