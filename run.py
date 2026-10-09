from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

from app.config import load_config


def _port_open(host: str, port: int, timeout: float = 0.25) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(timeout)
            probe.connect((host, port))
        return True
    except OSError:
        return False


def _child_main(config, root: Path) -> int:
    """后台线程跑 uvicorn，webview.start() 占主线程开窗口；关窗即整体退出。"""
    import uvicorn
    import webview

    from app.main import create_app

    base_url = f"http://{config.server.host}:{config.server.port}"

    def run_server() -> None:
        uvicorn.run(
            create_app(config, project_dir=root),
            host=config.server.host,
            port=config.server.port,
            reload=False,
            workers=1,
            log_level="warning",
        )

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    for _ in range(80):
        if _port_open(config.server.host, config.server.port):
            break
        time.sleep(0.1)
    else:
        print(f"服务端口 {config.server.port} 未能就绪，退出。", file=sys.stderr)
        return 4

    print(f"苏晚晚已启动：{base_url}/")

    window = webview.create_window(
        "苏晚晚",
        base_url + "/",
        width=1180,
        height=800,
        min_size=(960, 640),
        background_color="#faf3ea",
    )

    def on_closed() -> None:
        # 关窗即退出：daemon 服务线程随进程一起消亡，端口立即释放。
        os._exit(0)

    window.events.closed += on_closed
    webview.start()
    # start() 返回也意味着窗口已关闭
    on_closed()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="苏晚晚本地启动器")
    parser.add_argument(
        "--mode",
        choices=("window", "web"),
        default="window",
        help="window=独立应用窗口（默认）；web=自动打开系统浏览器",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    load_dotenv(root / ".env", override=False)
    config_path = root / "config.yaml"
    if not config_path.is_file():
        config_path = root / "config.example.yaml"

    try:
        config = load_config(config_path)
    except Exception as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((config.server.host, config.server.port))
    except OSError:
        print(
            f"本机端口 {config.server.port} 已被占用。若苏晚晚已在运行，请使用已有窗口；否则关闭占用端口的程序后重试。",
            file=sys.stderr,
        )
        return 3

    if args.mode == "web":
        import webbrowser

        import uvicorn

        from app.main import create_app

        base_url = f"http://{config.server.host}:{config.server.port}"
        print(f"苏晚晚 Chatbot 已启动：{base_url}/")
        threading.Timer(0.8, lambda: webbrowser.open(f"{base_url}/")).start()
        uvicorn.run(
            create_app(config, project_dir=root),
            host=config.server.host,
            port=config.server.port,
            reload=False,
            workers=1,
            log_level="info",
        )
        return 0

    return _child_main(config, root)


if __name__ == "__main__":
    raise SystemExit(main())
