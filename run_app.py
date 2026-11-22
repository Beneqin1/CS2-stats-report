"""Double-click / command-line entry point for the packaged application."""

from __future__ import annotations

import multiprocessing
import sys

from cs2stats.cli import main

if __name__ == "__main__":
    # PyInstaller-built windowed apps should freeze support before any heavy
    # import that might spawn workers.  awpy/polars do not need it today, but
    # this keeps the entry point safe if that changes.
    multiprocessing.freeze_support()
    raise SystemExit(main())
