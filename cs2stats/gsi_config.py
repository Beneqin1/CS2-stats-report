"""Generate and install the CS2 Game State Integration configuration.

CS2 reads ``gamestate_integration_*.cfg`` files from
``.../Counter-Strike Global Offensive/game/csgo/cfg`` at startup.  This module
locates the installed game, writes a config for the local listener, and keeps a
copy under ``%LOCALAPPDATA%\\CS2StatsReport\\gsi`` for reference.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from .config import APP_NAME

GSI_CONFIG_NAME = "gamestate_integration_cs2stats.cfg"
DEFAULT_GSI_PORT = 32123
DEFAULT_TOKEN = "cs2stats-local"
GSI_DATA_BLOCKS = (
    "provider",
    "map",
    "round",
    "player_id",
    "player_state",
    "player_weapons",
    "player_match_stats",
    "allplayers_id",
    "allplayers_state",
    "allplayers_match_stats",
    "allplayers_weapons",
    "allplayers_position",
    "phase_countdowns",
    "allgrenades",
    "bomb",
    "player_position",
)


def local_gsi_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or "."
    return Path(base) / "CS2StatsReport" / "gsi"


def _steam_root() -> Path | None:
    for env_name in ("STEAM_PATH", "STEAM_ROOT"):
        value = os.environ.get(env_name)
        if value and (Path(value) / "steamapps").is_dir():
            return Path(value)
    program_files = [Path("C:/Program Files (x86)/Steam"), Path("D:/Program Files/Steam"), Path("D:/program files/steam")]
    try:
        import winreg  # type: ignore

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            value, _ = winreg.QueryValueEx(key, "SteamPath")
            if value:
                program_files.insert(0, Path(value.replace("/", "\\")))
    except Exception:
        pass
    for candidate in program_files:
        if (candidate / "steamapps").is_dir():
            return candidate
    return None


def steam_libraries() -> list[Path]:
    """Return Steam library roots that contain a CS2 installation."""

    result: list[Path] = []
    root = _steam_root()
    roots: list[Path] = [root] if root else []
    if root:
        vdf = root / "steamapps" / "libraryfolders.vdf"
        try:
            text = vdf.read_text(encoding="utf-8", errors="replace")
            for match in re.finditer(r'"path"\s+"([^"]+)"', text):
                candidate = Path(match.group(1).replace("\\\\", "\\"))
                if candidate not in roots:
                    roots.append(candidate)
        except OSError:
            pass
    for candidate in roots:
        if candidate and candidate not in result:
            result.append(candidate)
    return result


def find_cs2_cfg_dirs() -> list[Path]:
    """Find CS2 ``game/csgo/cfg`` directories, newest install first."""

    found: list[Path] = []
    env = os.environ.get("CS2_CFG_DIR")
    if env:
        path = Path(env)
        if path.is_dir():
            found.append(path)
    for library in steam_libraries():
        path = library / "steamapps" / "common" / "Counter-Strike Global Offensive" / "game" / "csgo" / "cfg"
        if path.is_dir() and path not in found:
            found.append(path)
    return found


def build_config(port: int = DEFAULT_GSI_PORT, token: str = DEFAULT_TOKEN) -> str:
    lines = [
        f'"{APP_NAME} GSI v1"',
        "{",
        f'  "uri" "http://127.0.0.1:{int(port)}"',
        '  "timeout" "5.0"',
        '  "buffer"  "0.1"',
        '  "throttle" "0.2"',
        '  "heartbeat" "10.0"',
        '  "auth"',
        "  {",
        f'    "token" "{token}"',
        "  }",
        '  "output"',
        "  {",
        '    "precision_time" "3"',
        '    "precision_position" "1"',
        '    "precision_vector" "3"',
        "  }",
        '  "data"',
        "  {",
    ]
    for block in GSI_DATA_BLOCKS:
        lines.append(f'    "{block}" "1"')
    lines.extend(["  }", "}", ""])
    return "\n".join(lines)


def write_local_config(port: int = DEFAULT_GSI_PORT, token: str = DEFAULT_TOKEN) -> Path:
    target = local_gsi_dir() / GSI_CONFIG_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_config(port, token), encoding="utf-8", newline="\n")
    return target


def install_config(
    cfg_dir: str | Path | None = None,
    port: int = DEFAULT_GSI_PORT,
    token: str = DEFAULT_TOKEN,
) -> Path:
    """Write the config into a CS2 cfg directory (or the first detected one)."""

    target_dir = Path(cfg_dir) if cfg_dir else (find_cs2_cfg_dirs()[0] if find_cs2_cfg_dirs() else None)
    if target_dir is None:
        raise FileNotFoundError(
            "没有找到 CS2 安装目录。请把下面的配置文件放到 "
            "<CS2>/game/csgo/cfg/ 下，或设置环境变量 CS2_CFG_DIR。"
        )
    if not target_dir.is_dir():
        raise FileNotFoundError(f"CS2 cfg 目录不存在：{target_dir}")
    target = target_dir / GSI_CONFIG_NAME
    if target.exists():
        try:
            shutil.copy2(target, target.with_suffix(".cfg.bak"))
        except OSError:
            pass
    target.write_text(build_config(port, token), encoding="utf-8", newline="\n")
    return target
