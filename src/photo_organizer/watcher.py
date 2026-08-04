"""Watch an inbox directory for newly added files.

The entry point for *automatic* import: when a new file appears, the
same pipeline used by the one-shot CLI command runs over it. Not wired
up in the MVP; this module defines the seam so a ``watch`` command can
be added later without reworking the pipeline.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path


class Watcher:
    """Monitors an inbox and calls *callback* with newly seen files."""

    def __init__(
        self,
        inbox: Path,
        callback: Callable[[list[Path]], None],
    ) -> None:
        """Prepare a watcher on *inbox* invoking *callback* on new files.

        Args:
            inbox: directory to monitor for new media files.
            callback: called with the batch of newly discovered paths.
        """
        self._inbox = inbox
        self._callback = callback

    def start(self) -> None:
        """Begin watching *inbox* (blocks the calling thread).

        MVP: interface skeleton. The real implementation will use a
        filesystem event watcher (e.g. ``watchdog``) and debounce bursts.

        Not implemented yet (MVP skeleton).
        """
        raise NotImplementedError("Watcher.start() is not implemented yet")

    def stop(self) -> None:
        """Stop watching; safe to call from another thread.

        Not implemented yet (MVP skeleton).
        """
        raise NotImplementedError("Watcher.stop() is not implemented yet")
