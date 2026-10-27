"""Compatibility entry point: verify the desktop window's normal close lifecycle."""
from __future__ import annotations

import argparse
from pathlib import Path

from check_desktop_exe import verify as verify_desktop


def verify(exe: Path) -> dict:
    return verify_desktop(exe, lifecycle_only=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    args = parser.parse_args()
    verify(args.executable.resolve(strict=True))
