"""Execution — apply planned actions to the filesystem.

The only module allowed to write to the destination tree. Honors
``Config.dry_run`` (default on):

- **Dry-run** logs the ``source -> destination`` lines for every action
  and touches nothing — no directories, no copies.
- **Copy** mode applies ``COPY`` actions by writing to a unique sibling
  ``.part-`` temp file, verifying its size against the source, and then
  atomically renaming it into place with :func:`os.replace` (metadata
  preserved via :func:`shutil.copy2`). It never overwrites an existing
  destination, and never lets one failed file stop the batch. Because the
  final file only ever appears via an atomic rename, a hard interruption
  leaves at most an ignorable temp file — never a half-written archive a
  later run would mistake for complete. An optional
  :class:`ProgressReporter` receives per-action events in copy mode only —
  a dry run never triggers a single event.

``MOVE``/``SYMLINK`` are not implemented in v1 and are counted as
skipped; a source file is never deleted.
"""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol
from uuid import uuid4

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
        mode creates the destination directory, copies each file to a
        sibling ``.part-`` temp file, validates the copied size, and
        atomically renames it into place. An existing destination is
        skipped (never overwritten), non-COPY kinds are skipped, and a
        failed action never stops the batch.

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
        """Copy one action's source to its destination, atomically.

        The bytes are written to a unique sibling ``.part-`` temp file,
        checked against the source size, then :func:`os.replace` moves the
        verified file into place in a single atomic step. A hard
        interruption mid-copy therefore leaves only an ignorable temp file
        — dot-prefixed, never mistaken for a finished archive — while the
        final destination never exists half-written, so a later run
        re-copies instead of wrongly skipping it.

        An existing destination is never overwritten: the batch-level
        ``execute`` skips it before this is reached, and the guard here
        turns a destination that appeared during the copy into an error.

        Raises:
            OSError: the copy failed, the copied size did not match the
                source, or the destination already exists.
        """
        if not action.source.is_file():
            raise FileNotFoundError(f"source does not exist: {action.source}")
        action.dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = action.dest.parent / _temp_name(action.dest)
        try:
            shutil.copy2(action.source, tmp)
            if not _same_size(action.source, tmp):
                raise OSError(
                    "size mismatch after copy: "
                    f"{action.source.stat().st_size} != {tmp.stat().st_size}"
                )
            if action.dest.exists():
                raise FileExistsError(f"destination already exists: {action.dest}")
            os.replace(tmp, action.dest)
        except Exception:
            _remove_quietly(tmp)
            raise


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


def _temp_name(dest: Path) -> str:
    """A unique sibling temp name for *dest*.

    The leading dot keeps it hidden from the scanner, and the ``.part-``
    token separates it from any real archive name; the pid plus a fresh
    uuid makes two copies for the same *dest* collision-proof.
    """
    return f".{dest.name}.part-{os.getpid()}-{uuid4().hex}"


def _remove_quietly(path: Path) -> None:
    """Best-effort removal of a temp file; never masks the original error."""
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("could not remove partial copy %s: %s", path, exc)


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
