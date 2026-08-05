"""Watch state — durable record of files the watcher has ingested.

Only ``--execute`` runs ever write here; a dry run loads the file (if
present) but never creates or flushes it. Entries are keyed by absolute
path and carry the file's size/mtime, so a re-imported (changed) file is
a new version that may be processed again. Writes go through a temp file
+ ``os.replace`` so a crash never leaves a half-written state.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

DEFAULT_STATE_PATH = Path.home() / ".cache" / "photo-organizer" / "watch_state.json"


class WatchStatus(StrEnum):
    """Lifecycle of one file in the watcher's durable state."""

    DONE = "done"
    IN_PROGRESS = "in_progress"  # handed to a batch; not yet confirmed on disk
    FAILED = "failed"  # a copy failed; stays retryable


@dataclass
class WatchEntry:
    """One file's watch state."""

    size: int
    mtime: float
    status: WatchStatus = WatchStatus.DONE
    failures: int = 0
    last_attempt: float | None = None
    processed_at: float | None = None


class WatchState:
    """JSON-backed store of ingested files (atomic writes)."""

    def __init__(self, path: Path | str | None = None) -> None:
        """Use *path* (default: ``~/.cache/photo-organizer/watch_state.json``).

        Loading never creates the file; only :meth:`flush` writes it.
        """
        self._path = Path(path) if path is not None else DEFAULT_STATE_PATH
        self._entries: dict[str, WatchEntry] = {}
        self._dirty = False
        self._load()

    @property
    def path(self) -> Path:
        """The state file path (useful for CLI banners)."""
        return self._path

    # -- queries ------------------------------------------------------------

    def is_done(self, path: Path, size: int, mtime: float) -> bool:
        """True when *path* at exactly this size/mtime was already ingested."""
        entry = self._entries.get(str(path))
        return (
            entry is not None
            and entry.status is WatchStatus.DONE
            and entry.size == size
            and entry.mtime == mtime
        )

    def needs_retry(self, path: Path, size: int, mtime: float) -> bool:
        """True when *path* was touched by the watcher but is not done here.

        Covers ``failed`` and ``in_progress`` entries (retryable) and done
        entries whose size/mtime changed (a new version). Files never
        recorded before — plain pre-existing inbox history — return False,
        so the watcher never re-queues history it never saw.
        """
        if str(path) not in self._entries:
            return False
        return not self.is_done(path, size, mtime)

    # -- mutations ----------------------------------------------------------

    def mark_in_progress(self, path: Path, size: int, mtime: float) -> None:
        """Record that *path* was handed to a batch; not yet confirmed."""
        self._entries[str(path)] = WatchEntry(
            size=size, mtime=mtime, status=WatchStatus.IN_PROGRESS
        )
        self._dirty = True

    def mark_done(self, path: Path, size: int, mtime: float) -> None:
        """Record *path* as confirmed in the library at this version."""
        self._entries[str(path)] = WatchEntry(
            size=size,
            mtime=mtime,
            status=WatchStatus.DONE,
            processed_at=time.time(),
        )
        self._dirty = True

    def mark_failed(self, path: Path, size: int, mtime: float) -> None:
        """Record a failed attempt; the file stays eligible for retry."""
        key = str(path)
        previous = self._entries.get(key)
        self._entries[key] = WatchEntry(
            size=size,
            mtime=mtime,
            status=WatchStatus.FAILED,
            failures=(previous.failures + 1) if previous else 1,
            last_attempt=time.time(),
        )
        self._dirty = True

    # -- persistence --------------------------------------------------------

    def flush(self) -> None:
        """Atomically persist (tmp + ``os.replace``); a no-op when clean."""
        if not self._dirty:
            return
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload: dict[str, Any] = {
                "version": 1,
                "files": {key: asdict(entry) for key, entry in self._entries.items()},
            }
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(tmp, self._path)
            self._dirty = False
        except OSError:
            pass  # a failed state write must not crash the watcher

    def _load(self) -> None:
        try:
            if self._path.is_file():
                data = json.loads(self._path.read_text(encoding="utf-8"))
                files = data.get("files") or {}
                self._entries = {
                    key: WatchEntry(
                        size=int(entry["size"]),
                        mtime=float(entry["mtime"]),
                        status=WatchStatus(entry.get("status", "done")),
                        failures=int(entry.get("failures", 0)),
                        last_attempt=entry.get("last_attempt"),
                        processed_at=entry.get("processed_at"),
                    )
                    for key, entry in files.items()
                }
        except (OSError, ValueError, KeyError, TypeError):
            self._entries = {}
