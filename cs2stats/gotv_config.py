"""GOTV helpers for the "watch the demo folder" workflow.

CS2 only starts GOTV when ``tv_enable 1`` is set **before** a map is loaded.
``tv_autorecord 1`` then makes the local server write a ``.dem`` automatically
when the match ends.  The commands live in ``stats.cfg`` (installed by
``first_run``); this module additionally offers the legacy
``cs2stats_gotv.cfg`` variant from the "高级 / GOTV 配置" dialog.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from .config import APP_NAME
from .gsi_config import find_cs2_cfg_dirs, steam_libraries

GOTV_CONFIG_NAME = "cs2stats_gotv.cfg"

GOTV_COMMANDS = (
    "tv_enable 1",
    "tv_autorecord 1",
)


def local_config_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or "."
    return Path(base) / "CS2StatsReport" / "cfg"


def find_cs2_game_dirs() -> list[Path]:
    """Return existing ``game/csgo`` directories, newest install first."""

    found: list[Path] = []
    for cfg in find_cs2_cfg_dirs():
        game = cfg.parent
        if game.is_dir() and game not in found:
            found.append(game)
    for library in steam_libraries():
        game = library / "steamapps" / "common" / "Counter-Strike Global Offensive" / "game" / "csgo"
        if game.is_dir() and game not in found:
            found.append(game)
    return found


def default_demo_dir() -> Path | None:
    """Best guess for the folder where GOTV writes ``.dem`` files.

    CS2 writes automatic GOTV demos into ``game/csgo/replays``.  Prefer a
    folder that already contains demos; otherwise return the expected path so
    the GUI can explain that the first map will create it.
    """

    games = find_cs2_game_dirs()
    if not games:
        return None
    for game in games:
        replays = game / "replays"
        try:
            if replays.is_dir() and any(replays.glob("*.dem")):
                return replays
        except OSError:
            continue
    for game in games:
        replays = game / "replays"
        if replays.is_dir():
            return replays
    return games[0] / "replays"


def build_config() -> str:
    lines = [
        f"// {APP_NAME} - GOTV demo recording",
        "// Execute this BEFORE loading a map:  exec cs2stats_gotv",
        "// tv_enable only takes effect for the next map load.",
        "",
        *GOTV_COMMANDS,
        "",
    ]
    return "\n".join(lines)


def write_config(directory: str | Path | None = None) -> Path:
    """Write the GOTV cfg to a directory, or to the per-user fallback."""

    target_dir = Path(directory) if directory else local_config_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / GOTV_CONFIG_NAME
    target.write_text(build_config(), encoding="utf-8", newline="\n")
    return target


def install_config(cfg_dir: str | Path | None = None) -> Path:
    """Write ``cs2stats_gotv.cfg`` into the detected CS2 ``cfg`` folder.

    A pre-existing file is backed up as ``.cfg.bak`` so a manual edit is not
    silently lost.
    """

    if cfg_dir:
        target_dir = Path(cfg_dir)
    else:
        dirs = find_cs2_cfg_dirs()
        if not dirs:
            raise FileNotFoundError(
                "没有找到 CS2 的 game\\csgo\\cfg 目录。请手动把 cs2stats_gotv.cfg "
                "放到 <CS2>/game/csgo/cfg/。"
            )
        target_dir = dirs[0]
    if not target_dir.is_dir():
        raise FileNotFoundError(f"CS2 cfg 目录不存在：{target_dir}")
    target = target_dir / GOTV_CONFIG_NAME
    if target.exists():
        try:
            shutil.copy2(target, target.with_suffix(".cfg.bak"))
        except OSError:
            pass
    target.write_text(build_config(), encoding="utf-8", newline="\n")
    return target
