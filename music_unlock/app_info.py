"""Stable product identity and build provenance used by desktop updates."""
from __future__ import annotations

import json
from pathlib import Path
import sys

APP_NAME = "MusicUnlock"
UPSTREAM_REPOSITORY = "HRuiCcc/music-geshizhuanhuan"
UPSTREAM_BRANCH = "main"
UPSTREAM_URL = f"https://github.com/{UPSTREAM_REPOSITORY}.git"


def load_build_info(path: Path | None = None) -> dict:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    location = path or (root / "build-info.json" if getattr(sys, "frozen", False)
                        else root / "build" / "build-info.json")
    result = {"app_name": APP_NAME, "source_revision": "", "upstream_revision": "",
              "upstream_repository": UPSTREAM_REPOSITORY, "upstream_branch": UPSTREAM_BRANCH,
              "source_workspace": "", "built_at": ""}
    try:
        data = json.loads(location.read_text(encoding="utf-8-sig"))
        if isinstance(data, dict):
            result.update(data)
    except (OSError, ValueError):
        pass
    result["app_name"] = APP_NAME
    return result
