"""Execution — apply planned actions to the filesystem.

The only module allowed to write to the destination tree. Honors
``Config.dry_run`` (default on):

- **Dry-run** logs the ``source -> destination`` lines for every action
  and touches nothing — no directories, no copies.
- **Copy** mode applies ``COPY`` actions with :func:`shutil.copy2` (file
  metadata preserved), never overwrites an existing destination, checks
  the copied size against the source, and never lets one failed file stop
  the batch.

``MOVE``/``SYMLINK`` are not implemented in v1 and are counted as
skipped; a source file is never deleted.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from photo_organizer.config import Config
from photo_organizer.domain.models import ActionKind, PlannedAction

logger = logging.getLogger("photo_organizer.executor")

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


@dataclass(frozen=True)
class ExecutionReport:
    """Summary of an execution run.

    ``total`` equals ``success + failed + skipped`` in copy mode; in
    dry-run mode nothing is applied, so the counts stay zero and only the
    ``source -> destination`` lines are logged. ``errors`` holds one
    message per failed action.
    """

    total: int = 0
    success: int = 0
    failed: int = 0
    skipped: int = 0
    dry_run: bool = True
    errors: list[str] = field(default_factory=list)


class Executor:
    """Performs (or rehearses) a batch of planned actions."""

    def __init__(self, config: Config) -> None:
        """Create an executor bound to *config* (honors dry_run/mode)."""
        self._config = config

    def execute(self, actions: Sequence[PlannedAction]) -> ExecutionReport:
        """Apply *actions* to the filesystem and return a report.

        Dry-run (the default) only logs ``source -> destination``; copy
        mode creates the destination directory, copies with
        :func:`shutil.copy2`, and validates the copied size. An existing
        destination is skipped (never overwritten), non-COPY kinds are
        skipped, and a failed action never stops the batch.
        """
        _ensure_log_file(self._config.log_path)

        total = len(actions)
        success = 0
        failed = 0
        skipped = 0
        errors: list[str] = []

        for action in actions:
            if self._config.dry_run:
                logger.info("%s -> %s", action.source, action.dest)
                continue

            if action.kind is not ActionKind.COPY:
                skipped += 1
                logger.info(
                    "skip %s -> %s (kind=%s not implemented in v1)",
                    action.source,
                    action.dest,
                    action.kind.value,
                )
                continue
            if action.dest.exists():
                skipped += 1
                logger.info(
                    "skip %s -> %s (destination exists)", action.source, action.dest
                )
                continue

            try:
                self._copy(action)
            except Exception as exc:  # one bad file must not stop the batch
                failed += 1
                errors.append(f"{action.source} -> {action.dest}: {exc}")
                logger.error("failed %s -> %s: %s", action.source, action.dest, exc)
                continue

            success += 1
            logger.info("copied %s -> %s", action.source, action.dest)

        return ExecutionReport(
            total=total,
            success=success,
            failed=failed,
            skipped=skipped,
            dry_run=self._config.dry_run,
            errors=errors,
        )

    def _copy(self, action: PlannedAction) -> None:
        """Copy one action's source to its destination with metadata.

        Raises:
            OSError: source missing, the copy failed, or the copied size
                does not match the source.
        """
        if not action.source.is_file():
            raise FileNotFoundError(f"source does not exist: {action.source}")
        action.dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(action.source, action.dest)
        if not _same_size(action.source, action.dest):
            raise OSError(
                "size mismatch after copy: "
                f"{action.source.stat().st_size} != {action.dest.stat().st_size}"
            )


def _same_size(source: Path, dest: Path) -> bool:
    return source.stat().st_size == dest.stat().st_size


def _ensure_log_file(log_path: str | Path) -> None:
    """Point the executor logger at *log_path*, replacing stale handlers.

    Only the log file (from :class:`Config`) is created here; the
    destination tree is never touched. Re-pointing the logger on each
    call keeps a changed ``log_path`` effective and keeps tests writing
    to their own ``tmp_path``.
    """
    path = Path(log_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
