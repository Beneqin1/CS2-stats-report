"""Throwaway verification for Bug 1 (GOTV dialog) and Bug 2 (stats.cfg rename)."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cs2stats.first_run import (
    STAT_CFG_CONTENT,
    ensure_local_stat_cfg,
    install_stat_cfg,
    stat_command,
)

failures = []


def check(label, cond, extra=""):
    print(("PASS" if cond else "FAIL"), label, extra)
    if not cond:
        failures.append(label)


# --- Bug 2: rename + legacy cleanup -------------------------------------
with tempfile.TemporaryDirectory() as tmp:
    cfg = Path(tmp)
    # Legacy files created by older builds (identical content).
    (cfg / "stat.cfg").write_text(STAT_CFG_CONTENT, encoding="utf-8")
    (cfg / "stat_cs2stats.cfg").write_text(STAT_CFG_CONTENT, encoding="utf-8")
    # A foreign file must survive.
    (cfg / "autoexec.cfg").write_text("echo hi\n", encoding="utf-8")

    result = install_stat_cfg(cfg)
    check("stats.cfg written", (cfg / "stats.cfg").is_file())
    check("command is exec stats", result.command == "exec stats", result.command)
    check("legacy stat.cfg removed", not (cfg / "stat.cfg").exists())
    check("legacy stat_cs2stats.cfg removed", not (cfg / "stat_cs2stats.cfg").exists())
    check("foreign autoexec.cfg kept", (cfg / "autoexec.cfg").is_file())
    check("content correct", (cfg / "stats.cfg").read_text() == STAT_CFG_CONTENT)

    # Reinstall on top of identical stats.cfg -> reuse, no error.
    again = install_stat_cfg(cfg)
    check("reinstall reuses stats.cfg", again.target_path == cfg / "stats.cfg")

with tempfile.TemporaryDirectory() as tmp:
    cfg = Path(tmp)
    (cfg / "stats.cfg").write_text("echo mine\n", encoding="utf-8")
    try:
        install_stat_cfg(cfg)
        check("foreign stats.cfg raises", False)
    except FileExistsError:
        check("foreign stats.cfg raises", True)
    check("foreign stats.cfg untouched",
          (cfg / "stats.cfg").read_text() == "echo mine\n")
    # A foreign stat.cfg (different content) must not be deleted.
    (cfg / "stat.cfg").write_text("echo theirs\n", encoding="utf-8")
    try:
        install_stat_cfg(cfg)
    except FileExistsError:
        pass
    check("foreign stat.cfg kept", (cfg / "stat.cfg").is_file())

# Local file next to app home uses the new name.
local = ensure_local_stat_cfg()
check("local cfg is stats.cfg", local.name == "stats.cfg", str(local))
check("stat_command", stat_command(Path("x/stats.cfg")) == "exec stats")

# --- Bug 1: Toplevel import + dialog opens --------------------------------
import tkinter as tk

root = tk.Tk()
root.withdraw()
from cs2stats.gui import DemoWatchPanel  # noqa: E402

panel = DemoWatchPanel(root)
state = {"opened": False}


def probe():
    try:
        panel._open_gotv_dialog()
        state["opened"] = True
    except Exception as exc:  # noqa: BLE001
        state["error"] = exc


# Close the dialog shortly after it opens (wait_window blocks).
def closer():
    for win in root.winfo_children():
        if isinstance(win, tk.Toplevel):
            for child in win.winfo_children():
                pass
            win.destroy()
    root.after(50, closer)


root.after(100, probe)
root.after(400, closer)
root.after(3000, root.destroy)
root.mainloop()
check("GOTV dialog opens without exception", state.get("opened", False),
      repr(state.get("error", "")))

print()
if failures:
    print("FAILURES:", failures)
    sys.exit(1)
print("ALL CHECKS PASSED")
