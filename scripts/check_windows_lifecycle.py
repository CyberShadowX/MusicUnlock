"""Exercise Ctrl+C and console-close against dedicated, hidden EXE consoles."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CONTROL_HELPER = r"""
import ctypes, sys, time
kernel = ctypes.WinDLL('kernel32', use_last_error=True)
kernel.FreeConsole()
if not kernel.AttachConsole(int(sys.argv[1])):
    raise ctypes.WinError(ctypes.get_last_error())
if not kernel.SetConsoleCtrlHandler(None, True):
    raise ctypes.WinError(ctypes.get_last_error())
if sys.argv[2] == 'ctrl_c':
    if not kernel.GenerateConsoleCtrlEvent(0, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    time.sleep(0.2)
    kernel.FreeConsole()
else:
    kernel.GetConsoleWindow.restype = ctypes.c_void_p
    window = kernel.GetConsoleWindow()
    kernel.FreeConsole()
    if not window:
        raise RuntimeError('Test process has no dedicated console window')
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.PostMessageW.argtypes = (ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t)
    if not user.PostMessageW(window, 0x0010, 0, 0):
        raise ctypes.WinError(ctypes.get_last_error())
"""


def verify(exe: Path) -> dict:
    evidence = ROOT / "build" / "lifecycle"
    evidence.mkdir(parents=True, exist_ok=True)
    checks = []
    for action in ("ctrl_c", "console_close"):
        log = evidence / f"{action}.log"
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0  # Hidden, dedicated console; never signal the user's console.
        with log.open("wb") as output:
            process = subprocess.Popen(
                [str(exe), "--no-browser"], cwd=evidence,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                stdout=output, stderr=subprocess.STDOUT, startupinfo=startup,
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
            try:
                deadline = time.monotonic() + 30
                url = None
                while time.monotonic() < deadline:
                    text = log.read_text(encoding="utf-8", errors="replace")
                    assert process.poll() is None, text
                    match = re.search(r"MusicUnlock URL: (http://127\.0\.0\.1:\d+)", text)
                    if match:
                        url = match.group(1)
                        with urllib.request.urlopen(f"{url}/api/health", timeout=5) as response:
                            assert json.load(response)["ok"] is True
                        break
                    time.sleep(0.1)
                assert url, "EXE did not start"
                subprocess.run(
                    [sys.executable, "-c", CONTROL_HELPER, str(process.pid), action],
                    check=True, capture_output=True, text=True, timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                code = process.wait(timeout=15)
                if action == "ctrl_c":
                    assert code == 0, (code, log.read_text(encoding="utf-8", errors="replace"))
                try:
                    urllib.request.urlopen(f"{url}/api/health", timeout=1)
                except OSError:
                    pass
                else:
                    raise AssertionError("HTTP server remained running after console exit")
                checks.append({"action": action, "exit_code": code, "listener_stopped": True})
                print(f"PASS {action}: owned EXE tree exited; listener stopped; code={code}", flush=True)
            finally:
                if process.poll() is None:
                    taskkill = Path(os.environ["SystemRoot"]) / "System32" / "taskkill.exe"
                    subprocess.run(
                        [str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15,
                        creationflags=subprocess.CREATE_NO_WINDOW, check=True,
                    )
                    process.wait(timeout=15)
    import hashlib
    report = {"executable": str(exe), "sha256": hashlib.sha256(exe.read_bytes()).hexdigest(), "checks": checks}
    (evidence / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    args = parser.parse_args()
    verify(args.executable.resolve(strict=True))
