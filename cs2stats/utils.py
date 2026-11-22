"""Small shared helpers."""
from __future__ import annotations

import hashlib
from pathlib import Path


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    return numerator / denominator if denominator else default


def sha256_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def fmt_signed(value: float, decimals: int = 2, suffix: str = "") -> str:
    return f"{value:+.{decimals}f}{suffix}"


def fmt_plain(value: float, decimals: int = 2) -> str:
    return f"{value:.{decimals}f}"
