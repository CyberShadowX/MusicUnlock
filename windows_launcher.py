"""Windowed Windows entry point; the existing UI lives inside a desktop window."""
from __future__ import annotations

import argparse
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import os
import sys
import threading
import uuid

from desktop_runtime import DesktopStartup, LocalService, own_child_processes
from desktop_saves import DesktopSaveBridge
from music_unlock.app_info import APP_NAME, load_build_info
from music_unlock.update_manager import UpdateManager
from settings_bridge import SettingsBridge
from update_bootstrap import atomic_json



def port_number(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("port must be an integer from 0 to 65535") from None
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be from 0 to 65535 (0 selects a free port)")
    return port


def run_desktop(data_dir: Path, port: int = 0, startup_report: Path | None = None) -> int:
    own_child_processes()
    import webview

    # Windowed builds have no stderr; route pywebview diagnostics to our file log.
    logging.getLogger("pywebview").handlers.clear()
    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
    profile = data_dir / f"webview-{uuid.uuid4().hex}"
    frozen = bool(getattr(sys, "frozen", False))
    info = load_build_info()
    manager = UpdateManager(None if frozen else Path(__file__).resolve().parent, data_dir, info)
    bridge = SettingsBridge(manager, data_dir, Path(sys.executable).resolve() if frozen else None)
    saves = DesktopSaveBridge()
    try:
        with LocalService(port) as service:
            from web.server import app
            app.config["DESKTOP_READY"] = False
            logging.info("Desktop local service: %s", service.url)
            window = webview.create_window(
                f"{APP_NAME} 音乐格式转换", service.url,
                width=1120, height=820, min_size=(880, 640),
                background_color="#090b11", text_select=True,
            )
            bridge.bind_window(window, service.url)
            saves.bind_window(window, service.url)
            window.expose(bridge.get_update_state, bridge.check_updates, bridge.start_update,
                          bridge.choose_source_workspace, bridge.restart_after_update,
                          saves.save_results, saves.get_save_state)
            startup = DesktopStartup(window, lambda: webview.renderer)

            def mark_ready(*_):
                if str(webview.renderer) == "edgechromium":
                    if startup_report is not None:
                        atomic_json(startup_report, {"status": "ready", "app_name": APP_NAME,
                                                   "source_revision": info["source_revision"]})
                    app.config["DESKTOP_READY"] = True

            window.events.loaded += mark_ready
            try:
                webview.start(func=startup.wait, gui="edgechromium", private_mode=True, storage_path=str(profile))
            finally:
                startup.cancel()
                app.config["DESKTOP_READY"] = False
            if startup.error is not None:
                raise startup.error
    finally:
        manager.close()
    logging.info("Desktop window closed; local service stopped")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MusicUnlock 桌面音乐格式转换工具")
    parser.add_argument("--port", type=port_number, default=0, help="端口，默认自动选择空闲端口")
    parser.add_argument("--no-browser", action="store_true", help="高级兼容模式：仅运行本机 API 服务")
    parser.add_argument("--startup-report", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    data_dir = root / "MusicUnlock-data"
    log_path = data_dir / f"MusicUnlock-{os.getpid()}.log"
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(log_path, maxBytes=1024 * 1024, backupCount=2, encoding="utf-8")
        logging.basicConfig(level=logging.INFO, handlers=[handler], force=True,
                            format="%(asctime)s %(levelname)s %(message)s")
        if not args.no_browser:
            return run_desktop(data_dir, args.port, startup_report=args.startup_report)
        with LocalService(args.port) as service:
            logging.info("Service compatibility mode: %s", service.url)
            if sys.stdout is not None:
                print(f"MusicUnlock URL: {service.url}", flush=True)
            threading.Event().wait()
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        logging.exception("MusicUnlock startup failed")
        if args.startup_report is not None:
            try:
                atomic_json(args.startup_report, {"status": "failed", "message": str(exc), "app_name": APP_NAME})
            except OSError:
                logging.exception("Could not write managed startup failure receipt")
        elif sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None, f"无法启动 MusicUnlock：{exc}\n\n日志位置：{log_path}",
                "MusicUnlock 启动失败", 0x10,
            )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
