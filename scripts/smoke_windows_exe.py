"""Verify the real portable EXE using only generated, non-user music fixtures."""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from builders import (  # noqa: E402
    CROSS_PLAIN, build_kgm, build_kwm, build_ncm, build_qmc_v1, build_qmc_v2,
    build_stag, load_kgm_pub_key, make_ekey_v1, make_ekey_v2,
)

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@contextmanager
def temporary_workspace(root: Path):
    directory = Path(tempfile.mkdtemp(prefix="EXE 中文 空格 ", dir=root)).resolve()
    try:
        yield directory
    finally:
        if not directory.is_relative_to(root.resolve()):
            raise RuntimeError(f"Refusing cleanup outside smoke directory: {directory}")
        # Windows can retain a mapped EXE briefly after taskkill and parent wait.
        # Retry only access-denied errors, and fail if the lock persists.
        deadline = time.monotonic() + 10
        while True:
            try:
                shutil.rmtree(directory)
                break
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)


def get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def upload(base: str, name: str, data: bytes, target: str = "", embed: str = "0", ekey: str = ""):
    boundary = uuid.uuid4().hex
    parts = [
        (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
         'Content-Type: application/octet-stream\r\n\r\n').encode(),
        data, b"\r\n",
    ]
    for field, value in (("format", target), ("embed_cover", embed), ("ekey", ekey)):
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"\r\n\r\n{value}\r\n'.encode()
        )
    parts.append(f"--{boundary}--\r\n".encode())
    request = urllib.request.Request(
        f"{base}/api/decode", data=b"".join(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


@contextmanager
def launched(exe: Path, directory: Path, label: str, env: dict):
    log = directory / f"{label}.log"
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    with log.open("wb") as output:
        process = subprocess.Popen(
            [str(exe), "--no-browser", "--port", str(port)], cwd=directory, env=env,
            stdout=output, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                content = log.read_text(encoding="utf-8", errors="replace")
                if process.poll() is not None:
                    raise RuntimeError(f"EXE exited ({process.returncode}): {content}")
                try:
                    with urllib.request.urlopen(f"{base}/api/health", timeout=1) as response:
                        health = json.load(response)
                except (OSError, ValueError):
                    pass
                else:
                    assert health["ok"] is True, health
                    yield base
                    return
                time.sleep(0.2)
            raise RuntimeError(f"Timed out starting EXE: {content}")
        finally:
            if process.poll() is None:
                # A one-file PyInstaller process has a bootloader parent and worker.
                # Terminate the whole owned tree so no localhost server is orphaned.
                taskkill = Path(os.environ["SystemRoot"]) / "System32" / "taskkill.exe"
                subprocess.run(
                    [str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    timeout=15, check=True,
                )
            process.wait(timeout=15)


def verify(exe: Path) -> dict:
    checks = []

    def passed(label: str):
        checks.append(label)
        print(f"PASS {label}", flush=True)

    smoke_root = ROOT / "build" / "smoke"
    smoke_root.mkdir(parents=True, exist_ok=True)
    restricted_path = str(Path(os.environ["SystemRoot"]) / "System32")
    assert shutil.which("python", path=restricted_path) is None
    assert shutil.which("ffmpeg", path=restricted_path) is None
    with temporary_workspace(smoke_root) as directory:
        application = directory / "application"
        application.mkdir()
        portable_exe = application / "MusicUnlock.exe"
        shutil.copy2(exe, portable_exe)
        working = directory / "foreign working directory 中文"
        working.mkdir()
        runtime = directory / "runtime temp"
        runtime.mkdir()
        env = dict(os.environ)
        env.update(PATH=restricted_path, TEMP=str(runtime), TMP=str(runtime), PYTHONIOENCODING="utf-8")
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        with launched(portable_exe, working, "primary", env) as base:
            passed("standalone EXE: no Python/FFmpeg in PATH; Unicode path; foreign cwd; health")
            assert b"MusicUnlock" in get(base)
            passed("bundled index and static UI")
            with launched(portable_exe, working, "concurrent", env) as second:
                assert second != base
            passed("concurrent instances use separate available loopback ports")

            master = bytes(range(24))
            rc4_master = bytes((i * 7 + 3) & 0xFF for i in range(512))
            cases = [
                ("网易云 合成.ncm", build_ncm(CROSS_PLAIN, b"0123456789abcdef", {"format": "mp3"}), CROSS_PLAIN, ""),
                ("qmc-v1.tkm", build_qmc_v1(CROSS_PLAIN), CROSS_PLAIN, ""),
                ("qmc-map.mflac", build_qmc_v2(CROSS_PLAIN, master, make_ekey_v1(master)), CROSS_PLAIN, ""),
                ("qmc-rc4.mgg", build_qmc_v2(CROSS_PLAIN, rc4_master, make_ekey_v2(rc4_master)), CROSS_PLAIN, ""),
                ("qmc-stag.mflac", build_stag(CROSS_PLAIN, master), CROSS_PLAIN, make_ekey_v1(master)),
                ("kugou.kgm", build_kgm(CROSS_PLAIN, load_kgm_pub_key()), CROSS_PLAIN, ""),
                ("kuwo.kwm", build_kwm(b"\x00" * 64 + CROSS_PLAIN), b"\x00" * 64 + CROSS_PLAIN, ""),
            ]
            results = {}
            for name, blob, expected, ekey in cases:
                response = upload(base, name, blob, ekey=ekey)
                assert response["ok"], (name, response)
                result = response["result"]
                assert get(f"{base}/api/download/{result['id']}") == expected, name
                results[result["id"]] = (result["output_name"], expected)
                passed(f"exact decode/download: {name}")

            archive = zipfile.ZipFile(io.BytesIO(get(f"{base}/api/zip?ids={','.join(results)}")))
            assert sorted(archive.namelist()) == sorted(name for name, _ in results.values())
            for name, expected in results.values():
                assert archive.read(name) == expected
            passed("ZIP contains every decoded file with exact bytes and Unicode names")

            metadata = {"format": "mp3", "musicName": "本机 EXE 验证", "artist": [["合成歌手", 1]], "album": "测试专辑"}
            response = upload(base, "cover.ncm", build_ncm(CROSS_PLAIN, b"0123456789abcdef", metadata, cover=PNG), embed="1")
            assert response["ok"], response
            result = response["result"]
            assert result["tags"]["title"] == "本机 EXE 验证"
            assert result["tags"]["artist"] == "合成歌手"
            assert result["has_cover"] is True
            assert get(f"{base}/api/cover/{result['id']}") == PNG
            passed("NCM metadata and embedded cover")

            flac = None
            for target in ("flac", "mp3", "m4a", "wav", "ogg"):
                plain = flac if target == "mp3" else CROSS_PLAIN
                blob = build_ncm(plain, b"0123456789abcdef", {"format": "flac" if target == "mp3" else "mp3"})
                response = upload(base, "transcode.ncm", blob, target=target)
                assert response["ok"], (target, response)
                result = response["result"]
                assert result["output_name"].endswith(f".{target}")
                data = get(f"{base}/api/download/{result['id']}")
                if target == "flac":
                    assert data.startswith(b"fLaC")
                    flac = data
                elif target == "mp3":
                    assert data.startswith(b"ID3") or (data[0] == 0xFF and data[1] & 0xE0 == 0xE0)
                elif target == "m4a":
                    assert data[4:8] == b"ftyp"
                elif target == "wav":
                    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE"
                else:
                    assert data.startswith(b"OggS")
                passed(f"bundled FFmpeg transcode: {target}")

            response = upload(base, "invalid.ncm", b"NOT A MUSIC FILE" * 10)
            assert response["ok"] is False and response.get("error")
            passed("invalid input returns a readable failure")
            clear_request = urllib.request.Request(f"{base}/api/clear", data=b"", method="POST")
            with urllib.request.urlopen(clear_request, timeout=10) as response:
                assert json.load(response)["ok"] is True
            passed("clear results")

    return {
        "executable": str(exe),
        "sha256": hashlib.sha256(exe.read_bytes()).hexdigest(),
        "restricted_path": restricted_path,
        "checks": checks,
        "passed": len(checks),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    args = parser.parse_args()
    report = verify(args.executable.resolve(strict=True))
    destination = ROOT / "build" / "windows-exe-smoke.json"
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"PASS {report['passed']} packaged checks; report: {destination}")
