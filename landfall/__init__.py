"""Landfall: a draft FEMA damage assessment from aerial photos, building by building."""

import os
from pathlib import Path


def _load_env(path: Path = Path(".env")) -> None:
    """Read KEY=value lines from ./.env (see .env.example) into the environment.

    Variables already set win, and nothing is printed, so keys stay out of logs.
    A missing file is fine: with no keys at all, Landfall runs in mock mode."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip("'\"")
        if key and value and key not in os.environ:
            os.environ[key] = value


_load_env()
