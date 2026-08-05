"""Unit tests for the polling watcher — timing is driven by a fake clock.

Real files under ``tmp_path`` supply the ``(size, mtime)`` signals; the
injected ``now`` clock and a controllable scanner let each test advance
time deterministically without sleeping.
"""

import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from photo_organizer.discover.models import DiscoveredFile, MediaKind
from photo_organizer.watcher import Watcher


def _df(path: Path) -> DiscoveredFile:
    return DiscoveredFile(
        path=path,
        size=path.stat().st_size,
        suffix=path.suffix.lower(),
        media_kind=MediaKind.IMAGE,
    )


class FakeScanner:
    """Returns the paths the test currently wants visible."""

    def __init__(self, paths: list[Path] | None = None) -> None:
        self.paths = paths or []

    def scan(self, root: Path) -> list[DiscoveredFile]:
        return [_df(p) for p in self.paths]


def _make_watcher(
    inbox: Path,
    scanner: FakeScanner,
    calls: list[list[DiscoveredFile]],
    *,
    pending: Callable[[Path, int, float], bool] | None = None,
    interval: float = 1.0,
    settle: float = 2.0,
    quiet: float = 5.0,
) -> tuple[Watcher, list[float]]:
    clock = [0.0]

    def now() -> float:
        return clock[0]

    watcher = Watcher(
        inbox=inbox,
        callback=calls.append,
        interval=interval,
        settle=settle,
        quiet=quiet,
        scanner=scanner.scan,
        pending=pending,
        now=now,
    )
    return watcher, clock


def _poll(watcher: Watcher, clock: list[float], t: float) -> None:
    clock[0] = t
    watcher._poll()
    watcher._flush_if_quiet()


def test_validation_rejects_bad_timings(tmp_path: Path) -> None:
    inbox = tmp_path / "in"
    callback = lambda _batch: None  # noqa: E731
    with pytest.raises(ValueError):
        Watcher(inbox, callback, interval=2, settle=1, quiet=5)  # interval > settle
    with pytest.raises(ValueError):
        Watcher(inbox, callback, interval=2, settle=5, quiet=5)  # not settle < quiet
    with pytest.raises(ValueError):
        Watcher(inbox, callback, interval=0, settle=5, quiet=10)  # interval not > 0
    # Interval == settle is allowed; settle < quiet is enforced.
    Watcher(inbox, callback, interval=2, settle=2, quiet=5)


def test_new_file_emitted_only_after_settle_and_quiet(tmp_path: Path) -> None:
    inbox = tmp_path / "in"
    inbox.mkdir()
    photo = inbox / "a.NEF"
    photo.write_bytes(b"x" * 10)
    calls: list[list[DiscoveredFile]] = []
    scanner = FakeScanner([])
    watcher, clock = _make_watcher(inbox, scanner, calls)

    _poll(watcher, clock, 0.0)  # baseline (empty inbox)
    scanner.paths.append(photo)
    _poll(watcher, clock, 0.0)  # new file -> candidate
    assert calls == []

    _poll(watcher, clock, 2.0)  # stable but quiet not elapsed
    assert calls == []

    _poll(watcher, clock, 6.0)  # quiet elapsed -> one batch
    assert len(calls) == 1
    assert [str(f.path) for f in calls[0]] == [str(photo)]


def test_growing_file_not_emitted_until_stable(tmp_path: Path) -> None:
    inbox = tmp_path / "in"
    inbox.mkdir()
    photo = inbox / "big.NEF"
    photo.write_bytes(b"x" * 10)
    calls: list[list[DiscoveredFile]] = []
    scanner = FakeScanner([])
    watcher, clock = _make_watcher(inbox, scanner, calls)

    _poll(watcher, clock, 0.0)
    scanner.paths.append(photo)
    _poll(watcher, clock, 0.0)

    photo.write_bytes(b"x" * 20)
    _poll(watcher, clock, 1.0)
    assert calls == []

    photo.write_bytes(b"x" * 30)
    _poll(watcher, clock, 2.0)
    assert calls == []

    _poll(watcher, clock, 40.0)  # stable + quiet long ago
    assert len(calls) == 1
    assert calls[0][0].path == photo


def test_new_file_resets_quiet_so_batch_is_emitted_together(tmp_path: Path) -> None:
    inbox = tmp_path / "in"
    inbox.mkdir()
    a = inbox / "a.NEF"
    b = inbox / "b.NEF"
    a.write_bytes(b"a")
    b.write_bytes(b"b")
    calls: list[list[DiscoveredFile]] = []
    scanner = FakeScanner([])
    watcher, clock = _make_watcher(inbox, scanner, calls)

    _poll(watcher, clock, 0.0)
    scanner.paths.append(a)
    _poll(watcher, clock, 0.0)

    _poll(watcher, clock, 3.0)  # a stable, but quiet not elapsed
    assert calls == []

    scanner.paths.append(b)
    _poll(watcher, clock, 4.0)  # b resets the quiet timer
    assert calls == []

    _poll(watcher, clock, 10.0)  # 6s after b -> both in one batch
    assert len(calls) == 1
    assert sorted(str(f.path) for f in calls[0]) == sorted([str(a), str(b)])


def test_pre_existing_history_never_emitted(tmp_path: Path) -> None:
    inbox = tmp_path / "in"
    inbox.mkdir()
    old = inbox / "old.NEF"
    old.write_bytes(b"x")
    calls: list[list[DiscoveredFile]] = []
    scanner = FakeScanner([old])
    watcher, clock = _make_watcher(inbox, scanner, calls)

    _poll(watcher, clock, 0.0)  # baseline includes old
    _poll(watcher, clock, 50.0)
    assert calls == []


def test_pending_file_is_retried(tmp_path: Path) -> None:
    inbox = tmp_path / "in"
    inbox.mkdir()
    photo = inbox / "a.NEF"
    photo.write_bytes(b"a")
    calls: list[list[DiscoveredFile]] = []
    scanner = FakeScanner([photo])
    # Simulate a file the watcher touched but never confirmed.
    watcher, clock = _make_watcher(
        inbox, scanner, calls, pending=lambda _p, _s, _m: True
    )

    _poll(watcher, clock, 0.0)  # baseline
    _poll(watcher, clock, 2.0)  # pending -> candidate, not yet stable
    assert calls == []

    _poll(watcher, clock, 5.0)  # stable + quiet -> emitted
    assert len(calls) == 1


def test_done_file_is_not_retried(tmp_path: Path) -> None:
    inbox = tmp_path / "in"
    inbox.mkdir()
    photo = inbox / "a.NEF"
    photo.write_bytes(b"a")
    calls: list[list[DiscoveredFile]] = []
    scanner = FakeScanner([photo])
    # A done file is not "pending", so it stays history.
    watcher, clock = _make_watcher(
        inbox, scanner, calls, pending=lambda _p, _s, _m: False
    )

    _poll(watcher, clock, 0.0)
    _poll(watcher, clock, 40.0)
    assert calls == []


def test_zero_byte_file_not_emitted_until_it_has_data(tmp_path: Path) -> None:
    inbox = tmp_path / "in"
    inbox.mkdir()
    photo = inbox / "z.NEF"
    photo.write_bytes(b"")
    calls: list[list[DiscoveredFile]] = []
    scanner = FakeScanner([])
    watcher, clock = _make_watcher(inbox, scanner, calls)

    _poll(watcher, clock, 0.0)
    scanner.paths.append(photo)  # still 0 bytes
    _poll(watcher, clock, 0.0)
    _poll(watcher, clock, 50.0)
    assert calls == []  # size 0 never counts as stable

    photo.write_bytes(b"x" * 5)
    _poll(watcher, clock, 50.0)  # changed, last_change = 50
    _poll(watcher, clock, 55.0)  # stable (5s) + quiet (5s)
    assert len(calls) == 1


def test_stop_wakes_start(tmp_path: Path) -> None:
    inbox = tmp_path / "in"
    inbox.mkdir()
    scanner = FakeScanner([])
    watcher, _clock = _make_watcher(
        inbox, scanner, [], interval=0.2, settle=0.5, quiet=1.0
    )
    finished: list[str] = []

    def run() -> None:
        watcher.start()
        finished.append("exited")

    thread = threading.Thread(target=run)
    thread.start()
    time.sleep(0.1)
    watcher.stop()
    thread.join(timeout=2.0)

    assert finished == ["exited"]
    assert not thread.is_alive()
