"""Unit tests for the file discovery scanner."""

from pathlib import Path

from photo_organizer.discover.models import MediaKind
from photo_organizer.discover.scanner import PhotoScanner


def _write(root: Path, relative: str, data: bytes = b"x") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_recursive_discovery(tmp_path: Path) -> None:
    _write(tmp_path, "001/DSC001.NEF")
    _write(tmp_path, "002/DSC002.JPG")
    _write(tmp_path, "003/DSC003.MOV")
    found = PhotoScanner().scan(tmp_path)
    assert len(found) == 3


def test_identifies_nef(tmp_path: Path) -> None:
    _write(tmp_path, "001/DSC001.NEF")
    (file,) = PhotoScanner().scan(tmp_path)
    assert file.media_kind is MediaKind.IMAGE
    assert file.suffix == ".nef"
    assert file.size == 1


def test_identifies_jpg(tmp_path: Path) -> None:
    _write(tmp_path, "002/DSC002.JPG")
    (file,) = PhotoScanner().scan(tmp_path)
    assert file.media_kind is MediaKind.IMAGE
    assert file.suffix == ".jpg"


def test_identifies_video(tmp_path: Path) -> None:
    _write(tmp_path, "clip.MOV")
    _write(tmp_path, "clip2.mp4")
    found = PhotoScanner().scan(tmp_path)
    assert len(found) == 2
    assert all(file.media_kind is MediaKind.VIDEO for file in found)


def test_ignores_unknown_files(tmp_path: Path) -> None:
    _write(tmp_path, "notes.txt")
    _write(tmp_path, "DSC001.xmp")  # sidecar — not a supported type yet
    assert PhotoScanner().scan(tmp_path) == []


def test_ignores_temp_files(tmp_path: Path) -> None:
    _write(tmp_path, "DSC001.NEF.tmp")
    _write(tmp_path, "DSC002.partial")
    _write(tmp_path, "DSC003.cache")
    assert PhotoScanner().scan(tmp_path) == []


def test_ignores_hidden_files(tmp_path: Path) -> None:
    _write(tmp_path, ".hidden.NEF")
    _write(tmp_path, ".thumbnails/DSC001.NEF")
    assert PhotoScanner().scan(tmp_path) == []


def test_empty_directory_returns_empty(tmp_path: Path) -> None:
    assert PhotoScanner().scan(tmp_path) == []


def test_missing_root_returns_empty(tmp_path: Path) -> None:
    assert PhotoScanner().scan(tmp_path / "does-not-exist") == []
