"""Unit tests for the executor — real file copies, confined to tmp_path.

No real photo files are involved; every source/destination lives under
pytest's ``tmp_path``. These tests pin the safety contract: dry-run
touches nothing, an existing destination is never overwritten, MOVE is
not implemented, and a single failure never stops the batch.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from photo_organizer.config import Config
from photo_organizer.domain.models import ActionKind, PlannedAction
from photo_organizer.executor import Executor


def _config(tmp_path: Path, *, dry_run: bool = True) -> Config:
    return Config(
        inbox=str(tmp_path / "inbox"),
        dest_root=str(tmp_path / "out"),
        mode="copy",
        dry_run=dry_run,
        log_path=str(tmp_path / "logs" / "executor.log"),
    )


def _action(
    source: Path, dest: Path, kind: ActionKind = ActionKind.COPY
) -> PlannedAction:
    return PlannedAction(kind=kind, source=source, dest=dest, reason="test")


def _write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------


def test_dry_run_creates_nothing(tmp_path: Path) -> None:
    source = _write(tmp_path / "inbox" / "DSC001.NEF", b"raw bytes")
    dest = tmp_path / "out" / "2026" / "2026-06-05_Whistler" / "RAW" / "DSC001.NEF"

    report = Executor(_config(tmp_path, dry_run=True)).execute([_action(source, dest)])

    assert report.dry_run is True
    assert report.total == 1
    assert report.success == 0
    assert not dest.exists()
    assert not (tmp_path / "out").exists()

    # The rehearsal is logged via Config.log_path.
    log = (tmp_path / "logs" / "executor.log").read_text()
    assert f"{source} -> {dest}" in log


# ---------------------------------------------------------------------------
# Copy mode
# ---------------------------------------------------------------------------


def test_copy_success(tmp_path: Path) -> None:
    source = _write(tmp_path / "inbox" / "DSC001.NEF", b"x" * 1234)
    dest = tmp_path / "out" / "RAW" / "DSC001.NEF"

    report = Executor(_config(tmp_path, dry_run=False)).execute([_action(source, dest)])

    assert report.success == 1
    assert report.failed == 0
    assert report.skipped == 0
    assert report.errors == []
    assert dest.is_file()
    assert dest.read_bytes() == b"x" * 1234


def test_copy_preserves_file_size(tmp_path: Path) -> None:
    source = _write(tmp_path / "src.NEF", b"abc" * 100)
    dest = tmp_path / "out" / "dst.NEF"

    Executor(_config(tmp_path, dry_run=False)).execute([_action(source, dest)])

    assert source.stat().st_size == dest.stat().st_size == 300


def test_copy_preserves_metadata(tmp_path: Path) -> None:
    source = _write(tmp_path / "src.NEF", b"data")
    os.utime(source, (1_700_000_000, 1_700_000_000))
    dest = tmp_path / "out" / "dst.NEF"

    Executor(_config(tmp_path, dry_run=False)).execute([_action(source, dest)])

    assert dest.stat().st_mtime == 1_700_000_000


def test_existing_destination_skipped_not_overwritten(tmp_path: Path) -> None:
    source = _write(tmp_path / "src.NEF", b"new content")
    dest = _write(tmp_path / "out" / "dst.NEF", b"old content")

    report = Executor(_config(tmp_path, dry_run=False)).execute([_action(source, dest)])

    assert report.skipped == 1
    assert report.success == 0
    assert report.failed == 0
    assert dest.read_bytes() == b"old content"  # never overwritten


def test_missing_source_recorded_as_failure(tmp_path: Path) -> None:
    dest = tmp_path / "out" / "dst.NEF"

    report = Executor(_config(tmp_path, dry_run=False)).execute(
        [_action(tmp_path / "missing.NEF", dest)]
    )

    assert report.failed == 1
    assert report.success == 0
    assert len(report.errors) == 1
    assert "missing.NEF" in report.errors[0]
    assert not dest.exists()


def test_move_not_implemented_skipped(tmp_path: Path) -> None:
    source = _write(tmp_path / "src.NEF", b"data")
    dest = tmp_path / "out" / "dst.NEF"

    report = Executor(_config(tmp_path, dry_run=False)).execute(
        [_action(source, dest, ActionKind.MOVE)]
    )

    assert report.skipped == 1
    assert report.success == 0
    assert source.exists()  # the source file is never touched
    assert not dest.exists()


def test_single_failure_does_not_stop_batch(tmp_path: Path) -> None:
    missing = tmp_path / "missing.NEF"
    good = _write(tmp_path / "good.NEF", b"ok")

    actions = [
        _action(missing, tmp_path / "out" / "missing.NEF"),
        _action(good, tmp_path / "out" / "good.NEF"),
    ]

    report = Executor(_config(tmp_path, dry_run=False)).execute(actions)

    assert report.failed == 1
    assert report.success == 1
    assert report.total == 2
    assert len(report.errors) == 1
    assert (tmp_path / "out" / "good.NEF").is_file()


def test_size_mismatch_recorded_as_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _write(tmp_path / "src.NEF", b"x" * 100)
    dest = tmp_path / "out" / "dst.NEF"

    def bad_copy2(_source: Path, _dest: Path) -> None:
        Path(_dest).write_bytes(b"y" * 10)  # wrong size

    monkeypatch.setattr(shutil, "copy2", bad_copy2)

    report = Executor(_config(tmp_path, dry_run=False)).execute([_action(source, dest)])

    assert report.failed == 1
    assert report.success == 0
    assert len(report.errors) == 1
    assert "size mismatch" in report.errors[0]


def test_report_counts(tmp_path: Path) -> None:
    a = _write(tmp_path / "a.NEF", b"a")
    b = _write(tmp_path / "b.NEF", b"b")
    c = _write(tmp_path / "c.NEF", b"c")
    existing = _write(tmp_path / "out" / "b.NEF", b"old")

    actions = [
        _action(a, tmp_path / "out" / "a.NEF"),
        _action(b, existing),  # skipped: destination exists
        _action(c, tmp_path / "out" / "c.NEF"),
    ]

    report = Executor(_config(tmp_path, dry_run=False)).execute(actions)

    assert report.total == 3
    assert report.success == 2
    assert report.skipped == 1
    assert report.failed == 0
    assert report.errors == []
    assert report.total == report.success + report.failed + report.skipped
