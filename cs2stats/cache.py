"""Parse-result cache.

Parsing a demo with awpy + demoparser2 takes several seconds.  The resulting
:class:`~cs2stats.demo_parser.ParsedDemo` is cached beside the app state,
keyed by the demo file's SHA-256 and guarded by a cache version plus the
parser library versions; any parser upgrade or code change silently falls
back to a fresh parse.  Set ``CS2STATS_NO_CACHE=1`` to disable.
"""

from __future__ import annotations

import logging
import os
import pickle
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import Any

from .paths import app_home
from .utils import sha256_file

LOGGER = logging.getLogger(__name__)

# Bump whenever ParsedDemo's shape or the parsing semantics change.
CACHE_VERSION = 5


def _disabled() -> bool:
    import os

    return os.environ.get("CS2STATS_NO_CACHE", "").strip() in {"1", "true", "yes"}


def _lib_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for package in ("awpy", "demoparser2"):
        try:
            versions[package] = pkg_version(package)
        except PackageNotFoundError:
            versions[package] = "?"
    return versions


def cache_dir() -> Path:
    return app_home() / "cache"


def _cache_file(sha256: str) -> Path:
    return cache_dir() / f"{sha256}.pickle"


def load_cached(demo_path: Path, sha256: str) -> Any | None:
    """Return the cached ParsedDemo for *sha256*, or None."""

    if _disabled():
        return None
    path = _cache_file(sha256)
    if not path.is_file():
        return None
    try:
        with path.open("rb") as handle:
            payload = pickle.load(handle)
    except Exception:
        LOGGER.warning("parse cache unreadable, reparsing: %s", path, exc_info=True)
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("cache_version") != CACHE_VERSION:
        return None
    if payload.get("lib_versions") != _lib_versions():
        return None
    parsed = payload.get("parsed")
    if parsed is None:
        return None
    try:
        parsed.path = Path(demo_path)
        parsed.from_cache = True
    except Exception:
        return None
    LOGGER.info("parse cache hit: %s", path.name)
    return parsed


def store(parsed: Any) -> None:
    """Cache *parsed* under its demo SHA-256.  Failures are logged only."""

    if _disabled():
        return
    sha256 = getattr(parsed, "sha256", "")
    if not sha256:
        return
    try:
        cache_dir().mkdir(parents=True, exist_ok=True)
        path = _cache_file(sha256)
        tmp = path.with_suffix(".tmp")
        with tmp.open("wb") as handle:
            pickle.dump(
                {
                    "cache_version": CACHE_VERSION,
                    "lib_versions": _lib_versions(),
                    "parsed": parsed,
                },
                handle,
                protocol=pickle.HIGHEST_PROTOCOL,
            )
        os.replace(tmp, path)
    except Exception:
        LOGGER.warning("could not write parse cache", exc_info=True)


def clear() -> int:
    """Delete all cached parse results; returns how many were removed."""

    removed = 0
    directory = cache_dir()
    if directory.is_dir():
        for entry in directory.glob("*.pickle"):
            try:
                entry.unlink()
                removed += 1
            except OSError:
                pass
    return removed
