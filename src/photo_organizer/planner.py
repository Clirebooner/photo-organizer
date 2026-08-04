"""Planning — decide the destination for each photo.

Pure decision logic: takes PhotoRecords and location results, returns a
list of PlannedActions. No filesystem reads or writes happen here, which
keeps this module fully unit-testable.

Target tree (v1)::

    destination_root/
      YYYY/
        YYYY-MM-DD_LocationName/
          RAW/
            <original filename>

RAW / JPEG / VIDEO all land in ``RAW`` for now; ``Selects`` and
``Exports`` are conceptual only. Original filenames are kept — no
renaming, no extension changes.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from pathlib import Path

from photo_organizer.domain.models import ActionKind, MediaKind, PhotoRecord, PlannedAction
from photo_organizer.location.models import DailyLocationResult

# Media kinds archived into the RAW folder in v1.
_ARCHIVED_KINDS = frozenset({MediaKind.RAW, MediaKind.IMAGE, MediaKind.VIDEO})

_RAW_FOLDER = "RAW"
_UNKNOWN_LOCATION = "Unknown_Location"


def plan(
    records: Iterable[PhotoRecord],
    destination_root: Path | str,
    location_results: Mapping[date, DailyLocationResult] | None = None,
) -> list[PlannedAction]:
    """Map every *record* to a destination under *destination_root*.

    Each archived photo lands in
    ``<root>/<year>/<YYYY-MM-DD_Location>/RAW/<original name>``. The year
    and date come from the capture time; the location name comes from
    ``location_results[date].location_name``, falling back to
    ``Unknown_Location``. Original filenames are preserved.

    Collisions on the final path are resolved by appending ``_1``,
    ``_2``, ... before the extension — nothing is ever overwritten.
    Records without a capture time, and sidecars, are not planned.

    Returns one :class:`PlannedAction` (``kind=COPY``) per archived
    record; the executor will honor the configured transfer mode.
    """
    location_results = location_results or {}
    root = Path(destination_root)

    planned: list[PlannedAction] = []
    used_paths: set[str] = set()
    for record in records:
        captured_at = record.captured_at
        if captured_at is None or record.media_kind not in _ARCHIVED_KINDS:
            continue  # no timestamp -> no date folder; sidecars not archived yet

        capture_date = captured_at.date()
        location = _location_name(location_results, capture_date)
        target_dir = (
            root
            / str(capture_date.year)
            / f"{capture_date.isoformat()}_{location}"
            / _RAW_FOLDER
        )
        dest = _unique_path(target_dir / record.source_path.name, used_paths)
        planned.append(
            PlannedAction(
                kind=ActionKind.COPY,
                source=record.source_path,
                dest=dest,
                reason=f"{capture_date.isoformat()} {location} -> RAW",
            )
        )
    return planned


def _location_name(
    location_results: Mapping[date, DailyLocationResult],
    capture_date: date,
) -> str:
    """The archive location name for *capture_date*, or ``Unknown_Location``."""
    result = location_results.get(capture_date)
    if result is None or not result.location_name:
        return _UNKNOWN_LOCATION
    return result.location_name


def _unique_path(candidate: Path, used: set[str]) -> Path:
    """Return *candidate*, suffixed with ``_1``, ``_2``, ... if already taken.

    Comparison is case-insensitive so the plan stays safe on Windows and
    WSL mounts regardless of the destination filesystem.
    """
    key = str(candidate).lower()
    if key not in used:
        used.add(key)
        return candidate

    index = 1
    while True:
        alt = candidate.with_name(f"{candidate.stem}_{index}{candidate.suffix}")
        alt_key = str(alt).lower()
        if alt_key not in used:
            used.add(alt_key)
            return alt
        index += 1
