"""Tests for the ``import`` CLI command — full pipeline wired to the executor.

The pipeline stages are injected as fakes (via a monkeypatched
``default_components``); the executor runs against real files under
``tmp_path`` so the dry-run vs. copy contract is exercised without any
real photos. The executor's log path is redirected into ``tmp_path``.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from photo_organizer.cli import _use_progress, app
from photo_organizer.config import Config
from photo_organizer.discover.models import DiscoveredFile
from photo_organizer.discover.models import MediaKind as DiscoverMediaKind
from photo_organizer.domain.models import MediaKind, PhotoRecord
from photo_organizer.executor import ExecutionReport
from photo_organizer.location.models import DailyLocationResult, LocationMode
from photo_organizer.pipeline.preview import PipelineComponents

_DAY = date(2026, 6, 5)
_LOCATION = "深圳"

runner = CliRunner()


class FakeScanner:
    """Fake scanner returning a fixed list of discovered files."""

    def __init__(
        self, files: list[DiscoveredFile], steps: list[str] | None = None
    ) -> None:
        self._files = files
        self._steps = steps

    def scan(self, root: Path) -> list[DiscoveredFile]:
        if self._steps is not None:
            self._steps.append("scan")
        return self._files


class FakeLoader:
    """Fake loader returning a fixed list of PhotoRecords."""

    def __init__(self, records: list[PhotoRecord], steps: list[str] | None = None) -> None:
        self._records = records
        self._steps = steps

    def load(self, files: list[DiscoveredFile]) -> list[PhotoRecord]:
        if self._steps is not None:
            self._steps.append("load")
        return self._records


class FakeResolver:
    """Fake resolver returning a fixed per-day location map."""

    def __init__(
        self,
        results: dict[date, DailyLocationResult],
        steps: list[str] | None = None,
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
        size=123,
        suffix=".nef",
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


def _make_source(tmp_path: Path, names: list[str]) -> tuple[Path, list[Path]]:
    """Create *names* under *tmp_path*'s inbox and return (inbox, paths)."""
    source = tmp_path / "inbox"
    source.mkdir()
    paths = []
    for name in names:
        path = source / name
        path.write_bytes(b"fake bytes")
        paths.append(path)
    return source, paths


def _patch_components(
    monkeypatch,
    files: list[DiscoveredFile],
    records: list[PhotoRecord],
    results: dict[date, DailyLocationResult],
    steps: list[str] | None = None,
) -> None:
    """Monkeypatch ``default_components`` so the CLI runs on fakes."""

    def fake_components() -> PipelineComponents:
        return PipelineComponents(
            scanner=FakeScanner(files, steps),
            loader=FakeLoader(records, steps),
            resolver=FakeResolver(results, steps),
        )

    monkeypatch.setattr("photo_organizer.cli.default_components", fake_components)


def _patch_config(monkeypatch, tmp_path: Path) -> None:
    """Point the executor's log file into tmp_path (never the repo)."""

    def fake_load_config() -> Config:
        return Config(
            inbox="",
            dest_root="",
            mode="copy",
            dry_run=True,
            log_path=str(tmp_path / "logs" / "import.log"),
        )

    monkeypatch.setattr("photo_organizer.cli.load_config", fake_load_config)


def _one_file(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    """Standard setup: one real source file + fake pipeline, real executor."""
    source, paths = _make_source(tmp_path, ["DSC_0001.NEF"])
    dest = tmp_path / "out"
    _patch_components(
        monkeypatch,
        [_discovered(p) for p in paths],
        [_record(p) for p in paths],
        {_DAY: _location_result()},
    )
    _patch_config(monkeypatch, tmp_path)
    return source, dest


# ---------------------------------------------------------------------------
# Dry-run vs execute
# ---------------------------------------------------------------------------


def test_import_default_is_dry_run(tmp_path, monkeypatch) -> None:
    source, dest = _one_file(tmp_path, monkeypatch)

    result = runner.invoke(app, ["import", str(source), str(dest)])

    assert result.exit_code == 0, result.output
    out = result.stdout
    assert "Import Preview" in out
    assert "dry_run:" in out
    assert "success:0" in out
    assert "failed:0" in out
    assert "skipped:0" in out
    assert "Dry-run mode. No files were modified." in out
    assert not dest.exists()  # the executor copied nothing


def test_import_execute_copies(tmp_path, monkeypatch) -> None:
    source, dest = _one_file(tmp_path, monkeypatch)

    result = runner.invoke(app, ["import", str(source), str(dest), "--execute"])

    assert result.exit_code == 0, result.output
    out = result.stdout
    assert "execute:" in out
    assert "success:1" in out
    assert "failed:0" in out
    assert "skipped:0" in out
    assert "Files copied successfully." in out
    copied = dest / "2026" / "2026-06-05_深圳" / "RAW" / "DSC_0001.NEF"
    assert copied.is_file()
    assert copied.read_bytes() == b"fake bytes"


# ---------------------------------------------------------------------------
# Pipeline order
# ---------------------------------------------------------------------------


def test_import_runs_pipeline_in_order(tmp_path, monkeypatch) -> None:
    source, paths = _make_source(tmp_path, ["DSC_0001.NEF"])
    dest = tmp_path / "out"
    steps: list[str] = []
    _patch_components(
        monkeypatch,
        [_discovered(p) for p in paths],
        [_record(p) for p in paths],
        {_DAY: _location_result()},
        steps,
    )
    _patch_config(monkeypatch, tmp_path)

    calls: list[str] = []

    class SpyExecutor:
        def __init__(self, config: Config) -> None:
            self.config = config

        def execute(self, actions: list[object]) -> ExecutionReport:
            calls.append("execute")
            return ExecutionReport(total=len(actions), dry_run=self.config.dry_run)

    monkeypatch.setattr("photo_organizer.cli.Executor", SpyExecutor)

    result = runner.invoke(app, ["import", str(source), str(dest)])

    assert result.exit_code == 0, result.output
    assert steps == ["scan", "load", "resolve"]
    assert calls == ["execute"]


# ---------------------------------------------------------------------------
# Report output
# ---------------------------------------------------------------------------


def test_import_output_contains_counts_and_report(tmp_path, monkeypatch) -> None:
    source, dest = _one_file(tmp_path, monkeypatch)

    result = runner.invoke(app, ["import", str(source), str(dest)])

    assert result.exit_code == 0, result.output
    out = result.stdout
    assert f"Source: {source}" in out
    assert f"Destination: {dest}" in out
    assert "Files discovered: 1" in out
    assert "Metadata: 1" in out
    assert "Planned: 1" in out
    assert "Execution:" in out
    assert "success:0" in out
    assert "failed:0" in out
    assert "skipped:0" in out


# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------


def test_import_rejects_non_directory_source(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["import", str(tmp_path / "nope"), str(tmp_path / "out")]
    )

    assert result.exit_code == 1
    assert "not a directory" in result.stderr


def test_import_parses_source_and_destination(tmp_path, monkeypatch) -> None:
    source, dest = _one_file(tmp_path, monkeypatch)

    result = runner.invoke(app, ["import", str(source), str(dest)])

    assert result.exit_code == 0, result.output
    assert f"Source: {source}" in result.stdout
    assert f"Destination: {dest}" in result.stdout


# ---------------------------------------------------------------------------
# Progress bar gating
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("execute", "no_progress", "isatty", "expected"),
    [
        (True, False, True, True),  # copy to a terminal -> show
        (True, False, False, False),  # copy but piped -> hide
        (True, True, True, False),  # opted out -> hide
        (True, True, False, False),
        (False, False, True, False),  # dry-run -> never show
        (False, True, True, False),
    ],
)
def test_use_progress_gate(
    execute: bool, no_progress: bool, isatty: bool, expected: bool
) -> None:
    assert _use_progress(execute, no_progress, isatty) is expected


def test_import_execute_no_progress_when_not_tty(tmp_path, monkeypatch) -> None:
    source, dest = _one_file(tmp_path, monkeypatch)

    result = runner.invoke(app, ["import", str(source), str(dest), "--execute"])

    assert result.exit_code == 0, result.output
    assert result.stderr == ""  # no TTY under CliRunner -> no Rich progress on stderr
    assert "Files copied successfully." in result.stdout


def test_import_execute_accepts_no_progress_flag(tmp_path, monkeypatch) -> None:
    source, dest = _one_file(tmp_path, monkeypatch)

    result = runner.invoke(
        app, ["import", str(source), str(dest), "--execute", "--no-progress"]
    )

    assert result.exit_code == 0, result.output
    assert "Files copied successfully." in result.stdout
