"""Read-only pipeline preview — run the full import pipeline and print a report.

Ties together the stages that are already implemented:
``discover -> metadata loader -> location resolver -> planner``. Nothing
here writes to the filesystem: no executor, no copy/move, no directory
creation. The planner is a pure function, so the destination tree is
never touched — this module only computes and formats the plan.

The three stages are injected through :class:`PipelineComponents`
(protocols) so tests can substitute fakes and so each stage stays
swappable (exiftool backend, a different geocoder, ...) without touching
the orchestration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from photo_organizer.discover.models import DiscoveredFile
from photo_organizer.discover.scanner import PhotoScanner
from photo_organizer.domain.models import PhotoRecord, PlannedAction
from photo_organizer.location.cache import GeocodingCache
from photo_organizer.location.geocoder import NominatimGeocoder
from photo_organizer.location.models import DailyLocationResult, LocationMode
from photo_organizer.location.resolver import DailyLocationResolver
from photo_organizer.pipeline.loader import PhotoLoader
from photo_organizer.planner import plan

PREVIEW_NOTICE = "This is preview only. No files were modified."


# ---------------------------------------------------------------------------
# Pluggable pipeline stages (protocols so tests can inject fakes)
# ---------------------------------------------------------------------------


class Scanner(Protocol):
    """Something that scans a directory for media files."""

    def scan(self, root: Path) -> list[DiscoveredFile]:
        """Return every supported media file under *root*, recursively."""
        ...


class Loader(Protocol):
    """Something that turns discovered files into PhotoRecords."""

    def load(self, files: list[DiscoveredFile]) -> list[PhotoRecord]:
        """Return the PhotoRecords successfully read from *files*."""
        ...


class Resolver(Protocol):
    """Something that resolves one dominant location name per date."""

    def resolve(
        self,
        records: list[PhotoRecord],
        date_overrides: dict[str, str] | None = None,
        cli_location_name: str | None = None,
        location_mode: LocationMode = LocationMode.ARCHIVE,
    ) -> dict[date, DailyLocationResult]:
        """Return the per-day dominant location results for *records*."""
        ...


@dataclass(frozen=True)
class PipelineComponents:
    """The three pluggable pipeline stages the preview command runs."""

    scanner: Scanner
    loader: Loader
    resolver: Resolver


def default_components() -> PipelineComponents:
    """Build the production components (real scanner / loader / geocoder)."""
    return PipelineComponents(
        scanner=PhotoScanner(),
        loader=PhotoLoader(),
        resolver=DailyLocationResolver(
            NominatimGeocoder(language="zh"),
            GeocodingCache(),
        ),
    )


# ---------------------------------------------------------------------------
# Orchestration + report
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PreviewReport:
    """Everything the report renderer needs, computed once per run."""

    source: Path
    dest_root: Path
    discovered_count: int
    metadata_ok: int
    skipped_count: int
    location_results: dict[date, DailyLocationResult]
    actions: list[PlannedAction]
    dry_run: bool


def run_preview(
    source: Path,
    dest_root: Path,
    components: PipelineComponents,
    location_mode: LocationMode = LocationMode.ARCHIVE,
    dry_run: bool = True,
) -> PreviewReport:
    """Run the read-only pipeline over *source* and summarize the plan.

    ``discover -> loader -> location resolver -> planner``. The executor
    is deliberately never invoked, so nothing here can copy, move, or
    create files. ``dry_run`` is recorded for the report; the command
    stays read-only either way.
    """
    files = components.scanner.scan(source)
    records = components.loader.load(files)
    location_results = components.resolver.resolve(records, location_mode=location_mode)
    actions = plan(records, dest_root, location_results)
    return PreviewReport(
        source=Path(source),
        dest_root=Path(dest_root),
        discovered_count=len(files),
        metadata_ok=len(records),
        skipped_count=len(files) - len(records),
        location_results=location_results,
        actions=actions,
        dry_run=dry_run,
    )


def render_report(report: PreviewReport, limit: int = 20) -> str:
    """Render a human-readable report, capping listed actions at *limit*."""
    lines: list[str] = [
        "Pipeline Preview",
        "================",
        f"Source: {report.source}",
        f"Destination root: {report.dest_root}",
        f"Dry-run: {report.dry_run}",
        "",
        "--- Scan ---",
        f"Discovered files: {report.discovered_count}",
        f"Metadata OK: {report.metadata_ok}",
        f"Skipped: {report.skipped_count}",
        "",
        "--- Dates & Locations ---",
    ]
    if report.location_results:
        for day in sorted(report.location_results):
            result = report.location_results[day]
            lines.append(f"{day} {result.location_name}")
            lines.append(f"{result.total_photos} photos")
            if result.location_name == "Unknown_Location":
                lines.append(f"reason: {result.reason}")
            lines.append("")
    else:
        lines.append("(no photos)")
        lines.append("")

    lines.append("--- Planned Actions ---")
    for action in report.actions[:limit]:
        lines.append(f"[{action.kind.value}]")
        lines.append(str(action.source))
        lines.append("↓")
        lines.append(str(action.dest))
        lines.append("")
    if len(report.actions) > limit:
        lines.append(f"... and {len(report.actions) - limit} more")
        lines.append("")

    lines.append(f"Total planned actions: {len(report.actions)}")
    lines.append("")
    lines.append(PREVIEW_NOTICE)
    return "\n".join(lines)
