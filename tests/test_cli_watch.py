"""Tests for the ``watch`` CLI command — automatic import via polling.

The watcher's real timing loop is exercised in ``test_watcher.py``; here
the ``watch`` command is wired to a fake Watcher that feeds one batch
through the real ``_process_batch`` path, so dry-run/execute semantics,
state persistence and interruption safety are tested end to end.
"""

import shutil
from datetime import date, datetime
from pathlib import Path

from typer.testing import CliRunner

from photo_organizer.cli import app
from photo_organizer.config import Config
from photo_organizer.discover.models import DiscoveredFile
from photo_organizer.discover.models import MediaKind as DiscoverMediaKind
from photo_organizer.domain.models import MediaKind, PhotoRecord
from photo_organizer.location.models import DailyLocationResult, LocationMode
from photo_organizer.pipeline.preview import PipelineComponents
from photo_organizer.watch_state import WatchState

_DAY = date(2026, 6, 5)
_LOCATION = "深圳"

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeScanner:
    def __init__(self, files: list[DiscoveredFile], steps: list[str] | None = None) -> None:
        self._files = files
        self._steps = steps

    def scan(self, root: Path) -> list[DiscoveredFile]:
        if self._steps is not None:
            self._steps.append("scan")
        return self._files


class FakeLoader:
    def __init__(self, records: list[PhotoRecord], steps: list[str] | None = None) -> None:
        self._records = records
        self._steps = steps

    def load(self, files: list[DiscoveredFile]) -> list[PhotoRecord]:
        if self._steps is not None:
            self._steps.append("load")
        return self._records


class FakeResolver:
    def __init__(
        self, results: dict[date, DailyLocationResult], steps: list[str] | None = None
    ) -> None:
        self._results = results
        self._steps = steps

    def resolve(
        self,
        records: list[PhotoRecord],
        date_overrides: dict[str, str] | None = None,
        cli_location_name: str | None = None,
        location_mode: LocationMode = LocationMode.ARCHIVE,
    ) -> dict[date, DailyLocationResult]:
        if self._steps is not None:
            self._steps.append("resolve")
        return self._results


def _discovered(path: Path) -> DiscoveredFile:
    return DiscoveredFile(
        path=path,
        size=path.stat().st_size,
        suffix=path.suffix.lower(),
        media_kind=DiscoverMediaKind.IMAGE,
    )


def _record(path: Path) -> PhotoRecord:
    return PhotoRecord(
        source_path=path,
        media_kind=MediaKind.RAW,
        captured_at=datetime(2026, 6, 5, 10, 30, 0),
    )


def _location_result() -> DailyLocationResult:
    return DailyLocationResult(
        date=_DAY,
        location_name=_LOCATION,
        total_photos=1,
        photos_with_gps=1,
        dominant_count=1,
        dominant_ratio=1.0,
        confidence="high",
        reason="fake",
        detailed_places=[],
    )


def _patch_watch(
    monkeypatch,
    tmp_path: Path,
    paths: list[Path],
    *,
    interrupt: bool = False,
    steps: list[str] | None = None,
) -> list[DiscoveredFile]:
    """Fake components/config and a Watcher that feeds one batch then exits."""
    files = [_discovered(p) for p in paths]
    records = [_record(p) for p in paths]
    results = {_DAY: _location_result()}

    def fake_components() -> PipelineComponents:
        return PipelineComponents(
            scanner=FakeScanner(files, steps),
            loader=FakeLoader(records, steps),
            resolver=FakeResolver(results, steps),
        )

    monkeypatch.setattr("photo_organizer.cli.default_components", fake_components)

    def fake_load_config() -> Config:
        return Config(
            inbox="",
            dest_root="",
            mode="copy",
            dry_run=True,
            log_path=str(tmp_path / "logs" / "watch.log"),
        )

    monkeypatch.setattr("photo_organizer.cli.load_config", fake_load_config)

    class FakeWatcher:
        def __init__(self, inbox: Path, callback, **kwargs) -> None:
            self.inbox = inbox
            self.callback = callback

        def start(self) -> None:
            self.callback(files)
            if interrupt:
                raise KeyboardInterrupt()

        def stop(self) -> None:
            pass

    monkeypatch.setattr("photo_organizer.cli.Watcher", FakeWatcher)
    return files


def _make_batch(tmp_path: Path, names: list[str]) -> tuple[Path, list[Path]]:
    """Create an inbox with *names* and return (inbox, paths)."""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    paths = []
    for name in names:
        path = inbox / name
        path.write_bytes(b"x" * 16)
        paths.append(path)
    return inbox, paths


# ---------------------------------------------------------------------------
# Dry-run vs execute
# ---------------------------------------------------------------------------


def test_watch_dry_run_copies_nothing_and_writes_no_state(tmp_path, monkeypatch) -> None:
    inbox, paths = _make_batch(tmp_path, ["a.NEF", "b.JPG"])
    dest = tmp_path / "out"
    state_file = tmp_path / "state.json"
    _patch_watch(monkeypatch, tmp_path, paths)

    result = runner.invoke(
        app, ["watch", str(inbox), str(dest), "--state", str(state_file)]
    )

    assert result.exit_code == 0, result.output
    assert "dry-run" in result.stdout.lower()
    assert "[batch]" in result.stdout
    assert not dest.exists()
    assert not state_file.exists()  # dry-run never creates/writes state


def test_watch_execute_copies_and_writes_state(tmp_path, monkeypatch) -> None:
    inbox, paths = _make_batch(tmp_path, ["a.NEF", "b.JPG"])
    dest = tmp_path / "out"
    state_file = tmp_path / "state.json"
    _patch_watch(monkeypatch, tmp_path, paths)

    result = runner.invoke(
        app,
        ["watch", str(inbox), str(dest), "--execute", "--state", str(state_file)],
    )

    assert result.exit_code == 0, result.output
    assert "success:2" in result.stdout
    copied = [p for p in dest.rglob("*") if p.is_file()]
    assert len(copied) == 2

    state = WatchState(state_file)
    for path in paths:
        st = path.stat()
        assert state.is_done(path, st.st_size, st.st_mtime)
        assert not state.needs_retry(path, st.st_size, st.st_mtime)


def test_watch_batch_summary_before_copy(tmp_path, monkeypatch) -> None:
    inbox, paths = _make_batch(tmp_path, ["a.NEF"])
    dest = tmp_path / "out"
    state_file = tmp_path / "state.json"
    steps: list[str] = []
    _patch_watch(monkeypatch, tmp_path, paths, steps=steps)

    result = runner.invoke(
        app,
        ["watch", str(inbox), str(dest), "--execute", "--state", str(state_file)],
    )

    assert result.exit_code == 0, result.output
    # The watcher scans; _process_batch runs load -> resolve in order.
    assert steps == ["load", "resolve"]
    assert "planned destinations:" in result.stdout
    assert "success:1" in result.stdout


# ---------------------------------------------------------------------------
# Interruption safety
# ---------------------------------------------------------------------------


def test_watch_interrupt_leaves_unconfirmed_files_retryable(tmp_path, monkeypatch) -> None:
    inbox, paths = _make_batch(tmp_path, ["a.NEF", "b.NEF"])
    dest = tmp_path / "out"
    state_file = tmp_path / "state.json"
    _patch_watch(monkeypatch, tmp_path, paths, interrupt=True)

    def interrupted_execute(self, actions, reporter=None):
        # Simulate a crash mid-batch: only the first action lands on disk.
        for action in actions[:1]:
            action.dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(action.source, action.dest)
        raise KeyboardInterrupt()

    monkeypatch.setattr("photo_organizer.cli.Executor.execute", interrupted_execute)

    result = runner.invoke(
        app,
        ["watch", str(inbox), str(dest), "--execute", "--state", str(state_file)],
    )

    assert result.exit_code == 0, result.output
    assert len([p for p in dest.rglob("*") if p.is_file()]) == 1

    state = WatchState(state_file)
    for path in paths:  # nothing confirmed as done; both stay retryable
        st = path.stat()
        assert not state.is_done(path, st.st_size, st.st_mtime)
        assert state.needs_retry(path, st.st_size, st.st_mtime)


def test_watch_interrupt_after_success_keeps_done_entries(tmp_path, monkeypatch) -> None:
    """A clean copy followed by Ctrl-C must persist the done marks."""
    inbox, paths = _make_batch(tmp_path, ["a.NEF"])
    dest = tmp_path / "out"
    state_file = tmp_path / "state.json"
    _patch_watch(monkeypatch, tmp_path, paths, interrupt=True)

    result = runner.invoke(
        app,
        ["watch", str(inbox), str(dest), "--execute", "--state", str(state_file)],
    )

    assert result.exit_code == 0, result.output
    state = WatchState(state_file)
    st = paths[0].stat()
    assert state.is_done(paths[0], st.st_size, st.st_mtime)
