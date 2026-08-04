"""Unit tests for the discover->metadata loader (PhotoLoader).

A fake MetadataReader isolates the loader; no real photos are touched.
"""

import logging
from pathlib import Path

import pytest

from photo_organizer.discover.models import DiscoveredFile
from photo_organizer.discover.models import MediaKind as DiscoveredMediaKind
from photo_organizer.domain.models import MediaKind, PhotoRecord
from photo_organizer.metadata.reader import MetadataError
from photo_organizer.pipeline.loader import PhotoLoader


class FakeMetadataReader:
    """Returns a stub PhotoRecord per path, or fails for marked paths."""

    def __init__(self) -> None:
        self.failures: set[Path] = set()

    def read(self, path: Path) -> PhotoRecord:
        if path in self.failures:
            raise MetadataError(f"cannot read {path}")
        return PhotoRecord(source_path=path, media_kind=MediaKind.IMAGE)


def _discovered(tmp_path: Path, relative: str) -> DiscoveredFile:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return DiscoveredFile(
        path=path,
        size=1,
        suffix=path.suffix.lower(),
        media_kind=DiscoveredMediaKind.IMAGE,
    )


def test_single_file_produces_record(tmp_path: Path) -> None:
    file = _discovered(tmp_path, "001/DSC001.NEF")
    records = PhotoLoader(FakeMetadataReader()).load([file])
    assert len(records) == 1
    assert records[0].source_path == file.path
    assert records[0].media_kind is MediaKind.IMAGE


def test_multiple_files_all_converted(tmp_path: Path) -> None:
    files = [
        _discovered(tmp_path, "001/DSC001.NEF"),
        _discovered(tmp_path, "002/DSC002.JPG"),
        _discovered(tmp_path, "003/DSC003.MOV"),
    ]
    records = PhotoLoader(FakeMetadataReader()).load(files)
    assert [record.source_path for record in records] == [f.path for f in files]


def test_single_failure_does_not_abort_batch(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    good1 = _discovered(tmp_path, "001/DSC001.NEF")
    bad = _discovered(tmp_path, "002/DSC002.JPG")
    good2 = _discovered(tmp_path, "003/DSC003.MOV")
    reader = FakeMetadataReader()
    reader.failures.add(bad.path)

    with caplog.at_level(logging.WARNING, logger="photo_organizer.pipeline.loader"):
        records = PhotoLoader(reader).load([good1, bad, good2])

    assert [record.source_path for record in records] == [good1.path, good2.path]
    assert "skipping" in caplog.text


def test_return_order_matches_input(tmp_path: Path) -> None:
    c = _discovered(tmp_path, "c/DSC001.NEF")
    a = _discovered(tmp_path, "a/DSC002.JPG")
    b = _discovered(tmp_path, "b/DSC003.MOV")
    records = PhotoLoader(FakeMetadataReader()).load([c, a, b])
    assert [record.source_path for record in records] == [c.path, a.path, b.path]
