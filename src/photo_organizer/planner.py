"""Planning — decide the destination for each photo.

Pure decision logic: takes PhotoRecords (and config), returns a list of
PlannedActions. No filesystem reads or writes happen here, which keeps
this module fully unit-testable.
"""

from __future__ import annotations

from collections.abc import Iterable

from photo_organizer.config import Config
from photo_organizer.domain.models import PhotoRecord, PlannedAction


def plan(records: Iterable[PhotoRecord], config: Config) -> list[PlannedAction]:
    """Map every *record* to a destination under ``config.dest_root``.

    MVP: interface skeleton. The real implementation will:
      1. Derive a target folder from the capture date (later: place).
      2. Build a filename from a naming template, resolving collisions.
      3. Return one :class:`PlannedAction` per record.

    Not implemented yet (MVP skeleton).
    """
    raise NotImplementedError("plan() is not implemented yet")
