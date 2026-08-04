"""Tests for the read-only ``plan`` CLI command (full pipeline preview).

The pipeline stages are injected as fakes via a monkeypatched
``default_components``, so no real photos, network geocoding, or
filesystem writes are involved. These tests pin the contract that the
preview command never invokes the executor and never modifies files.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from typer.testing import CliRunner

from photo_organizer.cli import app
from photo_organizer.discover.models import DiscoveredFile
from photo_organizer.discover.models import MediaKind as DiscoverMediaKind
from photo_organizer.domain.models import ActionKind, MediaKind, PhotoRecord
from photo_organizer.location.models import DailyLocationResult, LocationMode
from photo_organizer.pipeline.preview import PipelineComponents

_DAY = date(2026, 6, 5)
_LOCATION = "深圳"

runner = CliRunner()


class FakeScanner:
    """Fake scanner returning a fixed list of discovered files."""

    def __init__(self, files: list[DiscoveredFile]) -> None:
        self._files = files

    def scan(self, root: Path) -> list[DiscoveredFile]:
        return self._files


class FakeLoader:
    """Fake loader returning a fixed list of PhotoRecords."""

    def __init__(self, records: list[PhotoRecord]) -> None:
        self._records = records

    def load(self, files: list[DiscoveredFile]) -> list[PhotoRecord]:
        return self._records


class FakeResolver:
    """Fake resolver returning a fixed per-day location map."""

    def __init__(self, results: dict[date, DailyLocationResult]) -> None:
        self._results = results

    def resolve(
        self,
        records: list[PhotoRecord],
        date_overrides: dict[str, str] | None = None,
        cli_location_name: str | None = None,
        location_mode: LocationMode = LocationMode.ARCHIVE,
    ) -> dict[date, DailyLocationResult]:
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
        camera_model="NIKON Z 30",
    )


def _location_result(total: int = 2) -> DailyLocationResult:
    return DailyLocationResult(
        date=_DAY,
        location_name=_LOCATION,
        total_photos=total,
        photos_with_gps=total,
        dominant_count=total,
        dominant_ratio=1.0,
        confidence="high",
        reason=f"{_LOCATION} covers 100% of the GPS photos",
        detailed_places=["南山区"],
    )


def _patch_components(
    monkeypatch,
    files: list[DiscoveredFile],
    records: list[PhotoRecord],
    results: dict[date, DailyLocationResult],
) -> None:
    """Monkeypatch ``default_components`` so the CLI runs on fakes."""

    def fake_components() -> PipelineComponents:
        return PipelineComponents(
            scanner=FakeScanner(files),
            loader=FakeLoader(records),
            resolver=FakeResolver(results),
        )

    monkeypatch.setattr("photo_organizer.cli.default_components", fake_components)


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


def test_plan_runs_full_pipeline_preview(tmp_path, monkeypatch) -> None:
    source, paths = _make_source(tmp_path, ["DSC_0001.NEF"])
    dest_root = tmp_path / "out"

    files = [_discovered(path) for path in paths]
    records = [_record(path) for path in paths]
    _patch_components(monkeypatch, files, records, {_DAY: _location_result(2)})

    result = runner.invoke(app, ["plan", str(source), str(dest_root)])

    assert result.exit_code == 0, result.output
    out = result.stdout
    assert "Discovered files: 1" in out
    assert "Metadata OK: 1" in out
    assert "Skipped: 0" in out
    assert f"{_DAY} {_LOCATION}" in out
    assert "2 photos" in out
    assert f"[{ActionKind.COPY.value}]" in out
    assert str(paths[0]) in out
    assert (
        str(dest_root / "2026" / "2026-06-05_深圳" / "RAW" / "DSC_0001.NEF") in out
    )
    assert "Total planned actions: 1" in out
    assert "This is preview only. No files were modified." in out
    assert not dest_root.exists()  # target tree is never created


def test_plan_does_not_call_executor(tmp_path, monkeypatch) -> None:
    source, paths = _make_source(tmp_path, ["DSC_0001.NEF"])
    dest_root = tmp_path / "out"

    _patch_components(
        monkeypatch,
        [_discovered(paths[0])],
        [_record(paths[0])],
        {_DAY: _location_result(1)},
    )

    def boom(*args, **kwargs) -> None:
        raise AssertionError("Executor.execute must not be called by plan")

    monkeypatch.setattr("photo_organizer.executor.Executor.execute", boom)

    result = runner.invoke(app, ["plan", str(source), str(dest_root)])

    assert result.exit_code == 0, result.output
    assert "Total planned actions: 1" in result.stdout


def test_plan_does_not_modify_files(tmp_path, monkeypatch) -> None:
    source, paths = _make_source(tmp_path, ["DSC_0001.NEF", "DSC_0002.NEF"])
    dest_root = tmp_path / "out"

    before = {path: path.read_bytes() for path in paths}
    _patch_components(
        monkeypatch,
        [_discovered(path) for path in paths],
        [_record(path) for path in paths],
        {_DAY: _location_result(2)},
    )

    runner.invoke(app, ["plan", str(source), str(dest_root)])

    assert {path: path.read_bytes() for path in paths} == before
    assert not dest_root.exists()
    assert not (source / "2026").exists()


def test_plan_limit_caps_shown_actions(tmp_path, monkeypatch) -> None:
    source, paths = _make_source(
        tmp_path, ["DSC_0001.NEF", "DSC_0002.NEF", "DSC_0003.NEF"]
    )
    dest_root = tmp_path / "out"

    _patch_components(
        monkeypatch,
        [_discovered(path) for path in paths],
        [_record(path) for path in paths],
        {_DAY: _location_result(3)},
    )

    result = runner.invoke(
        app, ["plan", str(source), str(dest_root), "--limit", "2"]
    )

    assert result.exit_code == 0, result.output
    out = result.stdout
    assert out.count("[copy]") == 2
    assert "... and 1 more" in out
    assert "Total planned actions: 3" in out


def test_plan_accepts_mode_and_no_dry_run(tmp_path, monkeypatch) -> None:
    source, paths = _make_source(tmp_path, ["DSC_0001.NEF"])
    dest_root = tmp_path / "out"

    _patch_components(
        monkeypatch,
        [_discovered(paths[0])],
        [_record(paths[0])],
        {_DAY: _location_result(1)},
    )

    result = runner.invoke(
        app,
        [
            "plan",
            str(source),
            str(dest_root),
            "--location-mode",
            "detail",
            "--no-dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Dry-run: False" in result.stdout
    assert "This is preview only. No files were modified." in result.stdout


def test_plan_rejects_non_directory_source(tmp_path) -> None:
    missing = tmp_path / "nope"
    result = runner.invoke(app, ["plan", str(missing), str(tmp_path / "out")])

    assert result.exit_code == 1
    assert "not a directory" in result.stderr
