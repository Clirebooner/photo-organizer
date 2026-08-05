"""Watch an inbox for newly arrived media files.

Polling implementation of the automatic-import seam: every ``interval``
seconds it scans the inbox, tracks each file's ``(size, mtime)``, and only
considers a file "stable" once its ``(size, mtime)`` has been unchanged
for at least ``settle`` seconds with ``size > 0``. After the inbox has
been quiet for ``quiet`` seconds (no new or changed file), all stable
files are handed to *callback* as one batch (batch quiescence).

The first scan is a baseline: files already present are treated as
history and never emitted as a fresh batch — unless *pending* says the
watcher previously touched them (a failed or interrupted run). No
external dependencies: plain polling, no watchdog.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from photo_organizer.discover.models import DiscoveredFile
from photo_organizer.discover.scanner import PhotoScanner

DEFAULT_INTERVAL = 2.0  # poll period, seconds
DEFAULT_SETTLE = 5.0  # a file's (size, mtime) must be stable this long
DEFAULT_QUIET = 30.0  # inbox must be quiet this long before a batch fires


@dataclass
class _Candidate:
    """One file being tracked for stability."""

    file: DiscoveredFile
    size: int
    mtime: float
    last_change: float  # monotonic time its (size, mtime) last changed


class Watcher:
    """Polls *inbox* and calls *callback* with batches of stable files."""

    def __init__(
        self,
        inbox: Path,
        callback: Callable[[list[DiscoveredFile]], None],
        interval: float = DEFAULT_INTERVAL,
        settle: float = DEFAULT_SETTLE,
        quiet: float = DEFAULT_QUIET,
        scanner: Callable[[Path], list[DiscoveredFile]] | None = None,
        pending: Callable[[Path, int, float], bool] | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        """Prepare a watcher on *inbox*.

        Args:
            inbox: directory to monitor recursively for new media files.
            callback: called with each quiescent batch of stable files.
            interval: poll period in seconds.
            settle: seconds a file's ``(size, mtime)`` must be stable.
            quiet: seconds of inbox quiet before a batch is emitted.
            scanner: how to list media files under *inbox* (default:
                :class:`PhotoScanner`).
            pending: ``pending(path, size, mtime)`` -> True when a file the
                watcher previously touched should be retried (failed /
                interrupted / new version). Default: never.
            now: monotonic clock (default: ``time.monotonic``).
        """
        if not (0 < interval <= settle < quiet):
            raise ValueError(
                f"expected 0 < interval <= settle < quiet, "
                f"got interval={interval}, settle={settle}, quiet={quiet}"
            )
        self._inbox = Path(inbox)
        self._callback = callback
        self._interval = interval
        self._settle = settle
        self._quiet = quiet
        self._scanner = scanner or PhotoScanner().scan
        self._pending = pending or (lambda _path, _size, _mtime: False)
        self._now = now or time.monotonic

        self._known: dict[str, tuple[int, float]] = {}
        self._candidates: dict[str, _Candidate] = {}
        self._last_activity = 0.0
        self._seeded = False
        self._stop_event: threading.Event | None = None

    def start(self) -> None:
        """Begin watching (blocks the calling thread) until :meth:`stop`."""
        self._stop_event = threading.Event()
        self._poll()  # first scan seeds the baseline (history)
        while not self._stop_event.wait(self._interval):
            self._poll()
            self._flush_if_quiet()

    def stop(self) -> None:
        """Ask a running :meth:`start` to exit; safe from another thread."""
        if self._stop_event is not None:
            self._stop_event.set()

    # -- internals ----------------------------------------------------------

    def _poll(self) -> None:
        """One scan: update the baseline and candidate set from the inbox."""
        now = self._now()
        files = self._scanner(self._inbox)
        current: dict[str, DiscoveredFile] = {}
        for file in files:
            current[str(file.path)] = file

        # Files that vanished are no longer tracked.
        for key in list(self._known):
            if key not in current:
                del self._known[key]
        for key in list(self._candidates):
            if key not in current:
                del self._candidates[key]

        if not self._seeded:
            # First scan is the baseline: remember everything, emit nothing.
            for key, file in current.items():
                try:
                    st = file.path.stat()
                except OSError:
                    continue
                self._known[key] = (st.st_size, st.st_mtime)
            self._seeded = True
            return

        for key, file in current.items():
            try:
                st = file.path.stat()
            except OSError:
                continue
            state = (st.st_size, st.st_mtime)
            known = self._known.get(key)
            if known is None:
                # Brand-new file -> candidate; resets the quiet timer.
                self._known[key] = state
                self._candidates[key] = _Candidate(file, st.st_size, st.st_mtime, now)
                self._last_activity = now
            elif known != state:
                # File changed (still being written / a new version).
                self._known[key] = state
                candidate = self._candidates.get(key)
                if candidate is None:
                    self._candidates[key] = _Candidate(file, st.st_size, st.st_mtime, now)
                else:
                    candidate.file = file
                    candidate.size = st.st_size
                    candidate.mtime = st.st_mtime
                    candidate.last_change = now
                self._last_activity = now
            else:
                candidate = self._candidates.get(key)
                if candidate is None and self._pending(file.path, st.st_size, st.st_mtime):
                    # Previously touched by the watcher but unconfirmed -> retry.
                    self._candidates[key] = _Candidate(file, st.st_size, st.st_mtime, now)

    def _flush_if_quiet(self) -> None:
        """Emit one batch once the inbox is quiet and stable files exist."""
        now = self._now()
        if now - self._last_activity < self._quiet:
            return
        stable: dict[str, DiscoveredFile] = {}
        for key, candidate in self._candidates.items():
            if candidate.size > 0 and now - candidate.last_change >= self._settle:
                stable[key] = candidate.file
        if not stable:
            return
        batch = sorted(stable.values(), key=lambda file: str(file.path))
        for key in stable:
            del self._candidates[key]
        self._last_activity = now  # space retries; avoid a tight re-flush
        self._callback(batch)
