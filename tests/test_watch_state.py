"""Unit tests for the watcher's durable JSON state store."""

from pathlib import Path

from photo_organizer.watch_state import WatchState, WatchStatus


def _path(name: str = "a.NEF") -> Path:
    return Path("/abs") / name


def test_is_done_matches_size_and_mtime(tmp_path: Path) -> None:
    state = WatchState(tmp_path / "state.json")
    state.mark_done(_path(), 10, 1.0)

    assert state.is_done(_path(), 10, 1.0)
    assert not state.is_done(_path(), 11, 1.0)  # size differs
    assert not state.is_done(_path(), 10, 2.0)  # mtime differs


def test_changed_file_is_a_new_version_and_retryable(tmp_path: Path) -> None:
    state = WatchState(tmp_path / "state.json")
    state.mark_done(_path(), 10, 1.0)

    assert not state.needs_retry(_path(), 10, 1.0)  # done at this version
    assert state.needs_retry(_path(), 11, 1.0)  # new version -> retryable


def test_failed_and_in_progress_stay_retryable(tmp_path: Path) -> None:
    state = WatchState(tmp_path / "state.json")

    state.mark_failed(_path(), 10, 1.0)
    assert state.needs_retry(_path(), 10, 1.0)

    state.mark_in_progress(_path(), 10, 1.0)
    assert state.needs_retry(_path(), 10, 1.0)

    state.mark_done(_path(), 10, 1.0)
    assert not state.needs_retry(_path(), 10, 1.0)


def test_never_seen_path_is_not_retryable(tmp_path: Path) -> None:
    state = WatchState(tmp_path / "state.json")
    # Plain pre-existing inbox history was never touched by the watcher.
    assert not state.needs_retry(_path(), 5, 1.0)


def test_failures_counter_accumulates(tmp_path: Path) -> None:
    state = WatchState(tmp_path / "state.json")
    state.mark_failed(_path(), 10, 1.0)
    state.mark_failed(_path(), 10, 1.0)
    entry = state._entries[str(_path())]
    assert entry.failures == 2
    assert entry.status is WatchStatus.FAILED


def test_no_flush_means_no_file(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = WatchState(path)
    state.mark_done(_path(), 1, 1.0)

    assert not path.exists()  # memory-only until flush
    state.flush()
    assert path.exists()


def test_flush_reload_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = WatchState(path)
    state.mark_done(_path("a.NEF"), 10, 1.5)
    state.mark_failed(_path("b.NEF"), 20, 2.5)
    state.flush()

    reloaded = WatchState(path)
    assert reloaded.is_done(_path("a.NEF"), 10, 1.5)
    assert reloaded.needs_retry(_path("b.NEF"), 20, 2.5)
    assert not path.with_suffix(path.suffix + ".tmp").exists()  # tmp cleaned up


def test_corrupt_state_falls_back_empty(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{ not json")

    state = WatchState(path)
    assert not state.is_done(_path(), 1, 1.0)
    assert not state.needs_retry(_path(), 1, 1.0)
