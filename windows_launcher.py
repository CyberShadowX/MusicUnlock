"""Windows portable entry point for the existing local browser interface."""
from __future__ import annotations

import argparse
import threading
import webbrowser

from werkzeug.serving import make_server

from web.server import app


def port_number(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("port must be an integer from 0 to 65535") from None
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be from 0 to 65535 (0 selects a free port)")
    return port


def open_browser(url: str) -> None:
    try:
        if webbrowser.open(url):
            return
    except Exception:
        pass
    print(f"无法自动打开浏览器，请手动打开：{url}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MusicUnlock 本机音乐格式转换工具")
    parser.add_argument("--port", type=port_number, default=0, help="端口，默认自动选择空闲端口")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args(argv)

    # Bind before opening the browser, so its first request can reach the service.
    server = make_server("127.0.0.1", args.port, app, threaded=True)
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"MusicUnlock URL: {url}", flush=True)
    print("文件在本机处理。使用期间请保留此窗口；关闭窗口或按 Ctrl+C 退出。", flush=True)
    browser_timer = None
    if not args.no_browser:
        browser_timer = threading.Timer(0.5, open_browser, args=(url,))
        browser_timer.daemon = True
        browser_timer.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nMusicUnlock 已退出。", flush=True)
    finally:
        if browser_timer is not None:
            browser_timer.cancel()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
