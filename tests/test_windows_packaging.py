"""Portable launcher behavior and bundled FFmpeg discovery."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request

import pytest

from music_unlock.transcode import resolve_ffmpeg

ROOT = Path(__file__).resolve().parents[1]


def test_frozen_ffmpeg_works_without_path(tmp_path, monkeypatch):
    binary = tmp_path / "bin" / "ffmpeg.exe"
    binary.parent.mkdir()
    binary.touch()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setenv("PATH", "")
    assert resolve_ffmpeg() == str(binary)


def test_explicit_ffmpeg_takes_precedence(tmp_path, monkeypatch):
    binary = tmp_path / "custom ffmpeg.exe"
    binary.touch()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setenv("PATH", "")
    assert resolve_ffmpeg(str(binary)) == str(binary)
    assert resolve_ffmpeg(str(tmp_path / "missing.exe")) is None


@contextmanager
def running_launcher(directory: Path, label: str):
    directory.mkdir(parents=True, exist_ok=True)
    log = directory / f"{label}.log"
    with log.open("w", encoding="utf-8") as output:
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / "windows_launcher.py"), "--no-browser"],
            cwd=directory,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            stdout=output, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                content = log.read_text(encoding="utf-8")
                assert proc.poll() is None, content
                match = re.search(r"MusicUnlock URL: (http://127\.0\.0\.1:\d+)", content)
                if match:
                    url = match.group(1)
                    try:
                        with urllib.request.urlopen(f"{url}/api/health", timeout=1) as response:
                            assert json.load(response)["ok"] is True
                    except OSError:
                        pass
                    else:
                        yield url
                        return
                time.sleep(0.1)
            pytest.fail(f"Launcher did not become ready: {log.read_text(encoding='utf-8')}")
        finally:
            proc.terminate()
            proc.wait(timeout=10)


def test_launcher_serves_ui_from_foreign_unicode_directory(tmp_path):
    with running_launcher(tmp_path / "中文 空格 working directory", "launcher") as url:
        with urllib.request.urlopen(url, timeout=5) as response:
            assert b"MusicUnlock" in response.read()


def test_concurrent_launchers_use_available_ports(tmp_path):
    with running_launcher(tmp_path, "first") as first:
        with running_launcher(tmp_path, "second") as second:
            assert first != second


@pytest.mark.parametrize("port", ["-1", "65536"])
def test_launcher_rejects_invalid_ports(port):
    result = subprocess.run(
        [sys.executable, str(ROOT / "windows_launcher.py"), "--port", port, "--no-browser"],
        capture_output=True, text=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 2
    assert "argument --port" in result.stderr
