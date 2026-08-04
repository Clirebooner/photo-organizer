"""Configuration loading.

MVP keeps this deliberately simple: a single TOML file is read with the
stdlib ``tomllib`` and exposed on a small :class:`Config` dataclass.
There is no layered merging or validation framework yet — that can be
added later (e.g. pydantic-settings) without changing callers.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("config/default.toml")


@dataclass(frozen=True)
class Config:
    """Runtime settings read from a TOML file.

    Attributes (a deliberate subset of the full design):
        inbox:     directory to scan for new photos (camera import point)
        dest_root: root of the organized output tree
        mode:      how files are applied: "copy" | "move" | "symlink"
        dry_run:   when True, plan but do not touch the filesystem
        log_path:  where the executor writes its log (file log)
    """

    inbox: str = "~/Pictures/Inbox"
    dest_root: str = "~/Pictures/Organized"
    mode: str = "copy"
    dry_run: bool = True
    log_path: str = "logs/photo_organizer.log"


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> Config:
    """Load configuration from *path* (TOML), falling back to defaults.

    Missing file -> defaults; missing keys -> defaults; unknown keys are
    ignored for now. Validation is deferred to a later iteration.
    """
    data: dict[str, object] = {}
    if Path(path).is_file():
        with Path(path).open("rb") as fh:
            data = tomllib.load(fh)

    return Config(
        inbox=str(data.get("inbox", Config.inbox)),
        dest_root=str(data.get("dest_root", Config.dest_root)),
        mode=str(data.get("mode", Config.mode)),
        dry_run=bool(data.get("dry_run", Config.dry_run)),
        log_path=str(data.get("log_path", Config.log_path)),
    )
