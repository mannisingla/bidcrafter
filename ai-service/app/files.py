"""Resolve stored relative paths safely inside FILES_ROOT."""
from __future__ import annotations

from pathlib import Path

from .config import get_settings


def resolve(relative_path: str | Path) -> Path:
    root = get_settings().files_root.resolve()
    path = (root / relative_path).resolve()
    if root not in path.parents:
        raise ValueError(f"Path escapes the file store: {relative_path}")
    return path


def relative(path: Path) -> str:
    return str(path.resolve().relative_to(get_settings().files_root.resolve()))
