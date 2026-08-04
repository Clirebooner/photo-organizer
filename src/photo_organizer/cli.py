"""Command-line entry point.

Commands are read-only preview/debug helpers for now:
- no arguments: prints a bare banner
- ``inspect``: print metadata for a single photo
- ``location-preview``: per-day dominant locations (no files are moved)
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from photo_organizer.domain.models import PhotoRecord
from photo_organizer.location.cache import GeocodingCache
from photo_organizer.location.geocoder import NominatimGeocoder
from photo_organizer.location.models import LocationMode
from photo_organizer.location.resolver import DailyLocationResolver
from photo_organizer.metadata.reader import ExifReader, MetadataError

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


def _collect_photos(path: Path) -> list[Path]:
    """Return supported photo files for *path* (a file or a folder)."""
    if path.is_file():
        if path.suffix.lower() in _SUPPORTED_PHOTO_SUFFIXES:
            return [path]
        raise typer.BadParameter(f"unsupported file type: {path.name}")
    return sorted(
        p for p in path.rglob("*") if p.suffix.lower() in _SUPPORTED_PHOTO_SUFFIXES
    )


if __name__ == "__main__":
    app()
