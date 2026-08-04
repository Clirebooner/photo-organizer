"""Execution — apply planned actions to the filesystem.

The only module allowed to write to the destination tree. Honors
``dry_run``: in dry-run mode it reports what *would* happen without
touching a single file.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from photo_organizer.config import Config
from photo_organizer.domain.models import PlannedAction


@dataclass(frozen=True)
class ExecutionReport:
    """Summary of an execution run."""

    applied: int = 0
    skipped: int = 0
    failed: int = 0
    dry_run: bool = True
    details: list[str] = field(default_factory=list)


class Executor:
    """Performs (or rehearses) a batch of planned actions."""

    def __init__(self, config: Config) -> None:
        """Create an executor bound to *config* (honors dry_run/mode)."""
        self._config = config

    def execute(self, actions: Sequence[PlannedAction]) -> ExecutionReport:
        """Apply *actions* to the filesystem and return a report.

        MVP: interface skeleton. The real implementation will:
          1. Skip actions whose source no longer exists.
          2. In dry-run mode, only log what would happen.
          3. Otherwise perform COPY/MOVE/SYMLINK, tracking per-file
             success/failure into the report.

        Not implemented yet (MVP skeleton).
        """
        raise NotImplementedError("Executor.execute() is not implemented yet")
