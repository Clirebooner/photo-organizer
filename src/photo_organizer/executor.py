"""Execution — apply planned actions to the filesystem.

The only module allowed to write to the destination tree. Honors
``Config.dry_run`` (default on):

- **Dry-run** logs the ``source -> destination`` lines for every action
  and touches nothing — no directories, no copies.
- **Copy** mode applies ``COPY`` actions with :func:`shutil.copy2` (file
  metadata preserved), never overwrites an existing destination, checks
  the copied size against the source, and never lets one failed file stop
  the batch. An optional :class:`ProgressReporter` receives per-action
  events in copy mode only — a dry run never triggers a single event.

``MOVE``/``SYMLINK`` are not implemented in v1 and are counted as
skipped; a source file is never deleted.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol

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


class ProgressOutcome(StrEnum):
    """The terminal result of one planned action, for progress reporting."""

    COPIED = "copied"
    SKIPPED = "skipped"
    FAILED = "failed"


class ProgressReporter(Protocol):
    """Receives execution progress events; implementations must not block.

    The executor only calls this protocol — it never imports Rich or any
    presentation library — so a reporter can be a terminal progress bar, a
    test spy, or anything else. It is invoked **only in copy mode**: a dry
    run never triggers a single event.
    """

    def begin(self, total: int) -> None:
        """Called once, before the first action, with the batch size."""
        ...

    def file_starting(self, filename: str) -> None:
        """Called immediately before an actual copy of *filename* starts."""
        ...

    def file_done(self, outcome: ProgressOutcome, filename: str, size: int) -> None:
        """Called exactly once per action, after it resolves.

        ``size`` is the number of bytes copied on ``COPIED``; it is 0 for
        ``SKIPPED`` and ``FAILED``.
        """
        ...

    def end(self, success: int, failed: int, skipped: int) -> None:
        """Called once, after the last action, with the final counts."""
        ...


class Executor:
    """Performs (or rehearses) a batch of planned actions."""

    def __init__(self, config: Config) -> None:
        """Create an executor bound to *config* (honors dry_run/mode)."""
        self._config = config

    def execute(
        self,
        actions: Sequence[PlannedAction],
        reporter: ProgressReporter | None = None,
    ) -> ExecutionReport:
        """Apply *actions* to the filesystem and return a report.

        Dry-run (the default) only logs ``source -> destination``; copy
        mode creates the destination directory, copies with
        :func:`shutil.copy2`, and validates the copied size. An existing
        destination is skipped (never overwritten), non-COPY kinds are
        skipped, and a failed action never stops the batch.

        *reporter* (optional) receives progress events in copy mode only;
        a dry run never invokes it. The returned report stays the source
        of truth for counts.
        """
        _ensure_log_file(self._config.log_path)

        total = len(actions)
        success = 0
        failed = 0
        skipped = 0
        errors: list[str] = []

        if reporter is not None and not self._config.dry_run:
            reporter.begin(total)

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
                _report_done(reporter, ProgressOutcome.SKIPPED, action.source.name, 0)
                continue
            if action.dest.exists():
                skipped += 1
                logger.info(
                    "skip %s -> %s (destination exists)", action.source, action.dest
                )
                _report_done(reporter, ProgressOutcome.SKIPPED, action.source.name, 0)
                continue
            if not action.source.is_file():
                failed += 1
                errors.append(
                    f"{action.source} -> {action.dest}: source does not exist: {action.source}"
                )
                logger.error(
                    "failed %s -> %s: source does not exist", action.source, action.dest
                )
                _report_done(reporter, ProgressOutcome.FAILED, action.source.name, 0)
                continue

            if reporter is not None:
                reporter.file_starting(action.source.name)
            try:
                self._copy(action)
            except Exception as exc:  # one bad file must not stop the batch
                failed += 1
                errors.append(f"{action.source} -> {action.dest}: {exc}")
                logger.error("failed %s -> %s: %s", action.source, action.dest, exc)
                _report_done(reporter, ProgressOutcome.FAILED, action.source.name, 0)
                continue

            success += 1
            logger.info("copied %s -> %s", action.source, action.dest)
            _report_done(
                reporter,
                ProgressOutcome.COPIED,
                action.source.name,
                action.source.stat().st_size,
            )

        if reporter is not None and not self._config.dry_run:
            reporter.end(success, failed, skipped)

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


def _report_done(
    reporter: ProgressReporter | None,
    outcome: ProgressOutcome,
    filename: str,
    size: int,
) -> None:
    """Forward one per-action outcome to *reporter*, if any."""
    if reporter is not None:
        reporter.file_done(outcome, filename, size)


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
