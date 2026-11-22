"""First-run setup for the GOTV ``stats.cfg`` file.

The generated cfg deliberately contains only the two commands required for
demo recording.  It is written next to the executable and, when possible,
copied into the detected CS2 ``game/csgo/cfg`` folder.  A user's existing
``stats.cfg`` is never overwritten.

Older builds used ``stat.cfg`` (and ``stat_cs2stats.cfg`` when that name was
taken).  On install we remove those legacy copies, but only when their
content matches what this program generates — a foreign file is never
touched.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .gsi_config import find_cs2_cfg_dirs
from .paths import stat_cfg_path

STAT_CFG_NAME = "stats.cfg"
LEGACY_CFG_NAMES = ("stat_cs2stats.cfg", "stat.cfg")
STAT_CFG_CONTENT = "tv_enable 1\ntv_autorecord 1\n"


@dataclass
class StatSetupResult:
    local_path: Path
    target_path: Path | None
    command: str | None
    cfg_dir: Path | None
    message: str
    removed_legacy: list[Path] = field(default_factory=list)


def _is_ours(path: Path) -> bool:
    """True when *path* contains exactly the commands this program writes.

    Whitespace is normalised per line: an older build wrote ``stat.cfg`` with
    a blank line between the commands, which is still our file.
    """

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return " ".join(text.split()) == " ".join(STAT_CFG_CONTENT.split())


def _remove_legacy_cfgs(directory: Path) -> list[Path]:
    """Delete legacy cfg files we created earlier; never touch foreign files."""

    removed: list[Path] = []
    for name in LEGACY_CFG_NAMES:
        legacy = directory / name
        if legacy.is_file() and _is_ours(legacy):
            try:
                legacy.unlink()
                removed.append(legacy)
            except OSError:
                pass
    return removed


def ensure_local_stat_cfg() -> Path:
    """Create ``stats.cfg`` beside the executable if it is missing."""

    path = stat_cfg_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _remove_legacy_cfgs(path.parent)
    if not path.is_file():
        path.write_text(STAT_CFG_CONTENT, encoding="utf-8", newline="\n")
    return path


def detect_cfg_dir() -> Path | None:
    dirs = find_cs2_cfg_dirs()
    return dirs[0] if dirs else None


def stat_command(path: Path) -> str:
    return f"exec {path.stem}"


def install_stat_cfg(cfg_dir: str | Path) -> StatSetupResult:
    """Copy the local cfg into ``cfg_dir`` without replacing user files.

    Legacy ``stat.cfg`` / ``stat_cs2stats.cfg`` files that this program
    created are removed first.  If ``stats.cfg`` already exists with the same
    content it is reused; with different content ``FileExistsError`` is raised
    so the GUI can ask the user to resolve it.
    """

    local = ensure_local_stat_cfg()
    directory = Path(cfg_dir)
    if not directory.is_dir():
        raise FileNotFoundError(f"cfg 目录不存在：{directory}")

    removed = _remove_legacy_cfgs(directory)

    primary = directory / STAT_CFG_NAME
    if primary.exists():
        if _is_ours(primary):
            return StatSetupResult(
                local_path=local,
                target_path=primary,
                command=stat_command(primary),
                cfg_dir=directory,
                message=f"已存在相同的 {primary.name}，直接使用。",
                removed_legacy=removed,
            )
        raise FileExistsError(
            f"{primary} 已存在且内容不同；为避免覆盖你的文件，未写入。\n"
            "请手动把以下内容合并进你的 stats.cfg，或删除后重试：\n"
            "tv_enable 1\ntv_autorecord 1"
        )

    shutil.copy2(local, primary)
    note = f"已写入 {primary}。"
    if removed:
        names = "、".join(path.name for path in removed)
        note += f"（已清理旧文件：{names}）"
    return StatSetupResult(
        local_path=local,
        target_path=primary,
        command=stat_command(primary),
        cfg_dir=directory,
        message=note,
        removed_legacy=removed,
    )


def initialize_quietly() -> None:
    """Best-effort first-run setup for command-line starts.

    The GUI additionally offers a folder picker when no CS2 install is found.
    """

    from .paths import load_settings, save_settings

    settings = load_settings()
    ensure_local_stat_cfg()
    if settings.get("first_run_done"):
        cfg_dir = settings.get("cfg_dir") or ""
        if cfg_dir and Path(cfg_dir).is_dir():
            needs = settings.get("stat_cfg_command") != "exec stats" or any(
                (Path(cfg_dir) / name).is_file() for name in LEGACY_CFG_NAMES
            )
            if needs:
                try:
                    result = install_stat_cfg(cfg_dir)
                except Exception:
                    return
                settings.update(
                    {
                        "stat_cfg_target": str(result.target_path) if result.target_path else "",
                        "stat_cfg_command": result.command or "",
                    }
                )
                save_settings(settings)
        return
    cfg_dir = detect_cfg_dir()
    if cfg_dir is None:
        return
    try:
        result = install_stat_cfg(cfg_dir)
    except Exception:
        return
    settings.update(
        {
            "first_run_done": True,
            "stat_cfg_target": str(result.target_path) if result.target_path else "",
            "stat_cfg_command": result.command or "",
            "cfg_dir": str(result.cfg_dir) if result.cfg_dir else "",
        }
    )
    save_settings(settings)
