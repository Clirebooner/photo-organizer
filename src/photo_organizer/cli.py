"""Command-line entry point.

- no arguments: prints a bare banner
- ``inspect``: print metadata for a single photo
- ``location-preview``: per-day dominant locations (no files are moved)
- ``plan``: preview the full pipeline without touching files
- ``import``: run the full pipeline and (optionally) apply it via the
  executor — dry-run by default, ``--execute`` to copy
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    ProgressColumn,
    Task,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
)
from rich.text import Text

from photo_organizer.config import Config, load_config
from photo_organizer.domain.models import PhotoRecord
from photo_organizer.executor import ExecutionReport, Executor, ProgressOutcome
from photo_organizer.location.cache import GeocodingCache
from photo_organizer.location.geocoder import NominatimGeocoder
from photo_organizer.location.models import LocationMode
from photo_organizer.location.resolver import DailyLocationResolver
from photo_organizer.metadata.reader import ExifReader, MetadataError
from photo_organizer.pipeline.preview import (
    PreviewReport,
    default_components,
    render_report,
    run_preview,
)

app = typer.Typer(add_completion=False, no_args_is_help=False)

_SUPPORTED_PHOTO_SUFFIXES = {".nef", ".dng", ".jpg", ".jpeg"}


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Photo Organizer — organize Nikon camera photos."""
    if ctx.invoked_subcommand is None:
        typer.echo("Photo Organizer")


@app.command()
def inspect(
    path: Annotated[Path, typer.Argument(help="Path to a photo (NEF/JPG/JPEG).")],
) -> None:
    """Print metadata for a single photo (debug helper)."""
    try:
        record = ExifReader().read(path)
    except MetadataError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from None

    typer.echo(f"File: {record.source_path}")
    typer.echo(f"Capture Time: {record.captured_at or 'N/A'}")
    typer.echo(f"Camera: {record.camera_model or 'N/A'}")
    typer.echo(f"Lens: {record.lens or 'N/A'}")
    if record.gps is not None:
        typer.echo(f"GPS: {record.gps[0]:.6f}, {record.gps[1]:.6f}")
    else:
        typer.echo("GPS: N/A")


@app.command()
def location_preview(
    path: Annotated[Path, typer.Argument(help="Photo file or folder to preview.")],
    location_name: Annotated[
        str | None, typer.Option(help="Override the location name for all dates.")
    ] = None,
    mode: Annotated[
        LocationMode, typer.Option(help="Naming mode: archive | detail | admin.")
    ] = LocationMode.ARCHIVE,
) -> None:
    """Preview per-day dominant locations (read-only; moves/copies nothing)."""
    photos = _collect_photos(path)
    if not photos:
        typer.echo("No supported photos found.", err=True)
        raise typer.Exit(code=1)

    reader = ExifReader()
    records: list[PhotoRecord] = []
    for photo in photos:
        try:
            records.append(reader.read(photo))
        except MetadataError:
            continue

    resolver = DailyLocationResolver(
        NominatimGeocoder(language="zh"),
        GeocodingCache(),
    )
    results = resolver.resolve(
        records,
        cli_location_name=location_name,
        location_mode=mode,
    )

    for day in sorted(results):
        result = results[day]
        typer.echo(f"Date: {day}")
        typer.echo(f"Total photos: {result.total_photos}")
        typer.echo(f"Photos with GPS: {result.photos_with_gps}")
        typer.echo(f"Location: {result.location_name}")
        typer.echo(f"Dominant ratio: {result.dominant_ratio:.2f}")
        typer.echo(f"Confidence: {result.confidence}")
        typer.echo(f"Reason: {result.reason}")
        typer.echo(f"Detailed places: {', '.join(result.detailed_places)}")
        typer.echo("")


@app.command()
def plan(
    source: Annotated[Path, typer.Argument(help="Source directory to scan for photos.")],
    dest_root: Annotated[Path, typer.Argument(help="Destination root for the organized tree.")],
    limit: Annotated[
        int, typer.Option(help="Maximum planned actions to show (default 20).")
    ] = 20,
    location_mode: Annotated[
        LocationMode, typer.Option(help="Naming mode: archive | detail | admin.")
    ] = LocationMode.ARCHIVE,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run/--no-dry-run",
            help="Preview only; no files are touched (default on).",
        ),
    ] = True,
) -> None:
    """Preview the full pipeline for a photo directory (read-only).

    Runs discover -> metadata -> location resolver -> planner and prints
    the plan without copying, moving, or creating anything. The executor
    is never invoked.
    """
    if not source.is_dir():
        typer.echo(f"Error: source is not a directory: {source}", err=True)
        raise typer.Exit(code=1)

    report = run_preview(
        source,
        dest_root,
        default_components(),
        location_mode=location_mode,
        dry_run=dry_run,
    )
    typer.echo(render_report(report, limit))


@app.command("import")
def import_photos(
    source: Annotated[Path, typer.Argument(help="Source directory to scan for photos.")],
    destination: Annotated[
        Path, typer.Argument(help="Destination root for the organized tree.")
    ],
    execute: Annotated[
        bool,
        typer.Option(help="Apply the plan (copy files); default is a dry-run preview."),
    ] = False,
    no_progress: Annotated[
        bool,
        typer.Option(help="Disable the progress bar even when copying to a terminal."),
    ] = False,
) -> None:
    """Import photos from SOURCE into DESTINATION (dry-run unless --execute).

    Runs discover -> metadata -> location resolver -> planner -> executor.
    Without ``--execute`` the executor rehearses only: nothing is created,
    copied, or deleted. With ``--execute`` a Rich progress bar is shown on
    stderr while copying (unless ``--no-progress`` or not a terminal).
    """
    if not source.is_dir():
        typer.echo(f"Error: source is not a directory: {source}", err=True)
        raise typer.Exit(code=1)

    components = default_components()
    preview = run_preview(
        source, destination, components, location_mode=LocationMode.ARCHIVE
    )
    config = _import_config(source, destination, execute)

    if _use_progress(execute, no_progress, sys.stderr.isatty()):
        progress = RichProgressBar()
        progress.start()
        try:
            report = Executor(config).execute(preview.actions, reporter=progress)
        finally:
            progress.stop()
    else:
        report = Executor(config).execute(preview.actions)

    typer.echo(render_import_report(preview, report))


def _import_config(source: Path, destination: Path, execute: bool) -> Config:
    """Build the executor config for an import from the user's Config."""
    base = load_config()
    return Config(
        inbox=str(source),
        dest_root=str(destination),
        mode=base.mode,
        dry_run=not execute,
        log_path=base.log_path,
    )


def render_import_report(preview: PreviewReport, report: ExecutionReport) -> str:
    """Render the import summary: pipeline counts plus the executor report."""
    header = "Import Preview" if report.dry_run else "Import"
    mode = "dry_run" if report.dry_run else "execute"
    final = (
        "Dry-run mode. No files were modified."
        if report.dry_run
        else "Files copied successfully."
    )
    return "\n".join(
        [
            header,
            "=" * len(header),
            "",
            f"Source: {preview.source}",
            f"Destination: {preview.dest_root}",
            "",
            f"Files discovered: {preview.discovered_count}",
            f"Metadata: {preview.metadata_ok}",
            f"Planned: {len(preview.actions)}",
            "",
            "Execution:",
            "",
            f"{mode}:",
            f"success:{report.success}",
            f"failed:{report.failed}",
            f"skipped:{report.skipped}",
            "",
            final,
        ]
    )


def _collect_photos(path: Path) -> list[Path]:
    """Return supported photo files for *path* (a file or a folder)."""
    if path.is_file():
        if path.suffix.lower() in _SUPPORTED_PHOTO_SUFFIXES:
            return [path]
        raise typer.BadParameter(f"unsupported file type: {path.name}")
    return sorted(
        p for p in path.rglob("*") if p.suffix.lower() in _SUPPORTED_PHOTO_SUFFIXES
    )


def _use_progress(execute: bool, no_progress: bool, isatty: bool) -> bool:
    """Whether to show the Rich progress bar on stderr.

    A progress bar is meaningful only while files are actually copied
    (``--execute``), the user did not opt out, and stderr is a terminal —
    piped or captured output must stay free of ANSI / ``\\r`` sequences.
    """
    return execute and not no_progress and isatty


class _TransferBytesColumn(ProgressColumn):
    """Live byte-transfer speed (MB/s) for the copy batch.

    The built-in ``TransferSpeedColumn`` keys off ``task.completed``, but
    here ``completed`` counts *files*, not bytes — so MB/s is derived from
    the task's ``bytes_copied`` field and wall time.
    """

    def render(self, task: Task) -> Text:
        elapsed = task.elapsed
        if not elapsed or elapsed <= 0:
            return Text("? MB/s", style="progress.data.speed")
        bytes_per_sec = task.fields.get("bytes_copied", 0) / elapsed
        return Text(f"{bytes_per_sec / (1024 * 1024):.1f} MB/s", style="progress.data.speed")


class RichProgressBar:
    """Rich progress bar for one copy batch, written to stderr.

    Output goes to stderr so the stdout report stays parseable. The bar's
    ``completed/total`` counts files; MB/s and ETA are byte/time based.
    Only constructed for ``--execute`` runs on a terminal (see
    :func:`_use_progress`) — never for dry runs, where the executor does
    not invoke the reporter anyway.
    """

    def __init__(self) -> None:
        self._progress = Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),  # "3/5"
            TaskProgressColumn(),  # "60%"
            _TransferBytesColumn(),
            TimeRemainingColumn(),
            TextColumn("{task.fields[counts]}"),
            console=Console(stderr=True),
        )
        self._task: TaskID | None = None
        self._files_done = 0
        self._bytes_copied = 0
        self._success = 0
        self._failed = 0
        self._skipped = 0

    def start(self) -> None:
        """Start the live display (before executing the batch)."""
        self._progress.start()

    def stop(self) -> None:
        """Stop the live display (after the batch)."""
        self._progress.stop()

    # -- ProgressReporter ---------------------------------------------------

    def begin(self, total: int) -> None:
        self._task = self._progress.add_task(
            "copying…",
            total=total,
            counts="✓0 ✗0 −0",
            bytes_copied=0,
        )

    def file_starting(self, filename: str) -> None:
        if self._task is not None:
            self._progress.update(self._task, description=f"copying {filename}")

    def file_done(self, outcome: ProgressOutcome, filename: str, size: int) -> None:
        if self._task is None:
            return
        if outcome == ProgressOutcome.COPIED:
            self._success += 1
            self._bytes_copied += size
        elif outcome == ProgressOutcome.SKIPPED:
            self._skipped += 1
        else:
            self._failed += 1
        self._files_done += 1
        self._progress.update(
            self._task,
            advance=1,
            description=f"done {filename}",
            counts=f"✓{self._success} ✗{self._failed} −{self._skipped}",
            bytes_copied=self._bytes_copied,
        )

    def end(self, success: int, failed: int, skipped: int) -> None:
        if self._task is not None:
            self._progress.update(
                self._task,
                description="done",
                counts=f"✓{self._success} ✗{self._failed} −{self._skipped}",
            )


if __name__ == "__main__":
    app()
