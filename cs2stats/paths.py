"""Paths that are stable for both source runs and the packaged exe.

The program keeps its mutable state next to the executable, not in a user
profile.  For the onedir build the layout is::

    CS2Stats-1.2.0-onedir/
        CS2Stats/CS2Stats.exe      <- sys.executable
        README.txt
        output/                    <- reports
        stats.cfg                  <- first-run GOTV commands
        settings.json              <- first-run marker + manual paths

For a onefile build the executable sits directly in the package root.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_home() -> Path:
    """Return the folder that owns output/, stats.cfg and settings.json."""

    override = os.environ.get("CS2STATS_APP_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if is_frozen():
        exe_dir = Path(sys.executable).resolve().parent
        # PyInstaller onedir puts the exe in <package>/CS2Stats/.
        if exe_dir.name.lower() == "cs2stats" and (exe_dir.parent / "README.txt").is_file():
            return exe_dir.parent
        return exe_dir
    return Path(__file__).resolve().parent.parent


def output_dir() -> Path:
    return app_home() / "output"


def settings_path() -> Path:
    return app_home() / "settings.json"


def stat_cfg_path() -> Path:
    return app_home() / "stats.cfg"


def load_settings() -> dict[str, Any]:
    path = settings_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def save_settings(settings: dict[str, Any]) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def update_settings(**values: Any) -> dict[str, Any]:
    settings = load_settings()
    settings.update(values)
    save_settings(settings)
    return settings
