from __future__ import annotations

import argparse
import socket
import sys
import threading
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from app.config import load_config


def _serve(config, root: Path) -> None:
    """在当前线程跑 uvicorn（pywebview 模式由它自己的事件循环占主线程）。"""
    from app.main import create_app

    uvicorn.run(
        create_app(config, project_dir=root),
        host=config.server.host,
        port=config.server.port,
        reload=False,
        workers=1,
        log_level="info",
    )


def _run_window_mode(config, root: Path) -> int:
    """本地应用模式：独立窗口加载页面，关窗即退出服务。"""
    import webview

    base_url = f"http://{config.server.host}:{config.server.port}"
    server_thread = threading.Thread(target=_serve, args=(config, root), daemon=True)
    server_thread.start()

    # 等服务端口就绪再开窗口，避免窗口里先看到连接失败
    import time

    for _ in range(50):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.2)
                probe.connect((config.server.host, config.server.port))
            break
        except OSError:
            time.sleep(0.1)
    else:
        print(f"服务端口 {config.server.port} 未能就绪，退出。", file=sys.stderr)
        return 4

    print(f"苏晚晚已启动（本地应用模式）：{base_url}/")
    window = webview.create_window(
        "苏晚晚",
        base_url + "/",
        width=1180,
        height=800,
        min_size=(960, 640),
        background_color="#faf3ea",
    )
    # events.closed 在用户点掉窗口时触发；阻塞到窗口关闭，再让 daemon 服务线程随之结束
    window.events.closed.wait()
    return 0


def _run_browser_mode(config, root: Path) -> int:
    import webbrowser

    base_url = f"http://{config.server.host}:{config.server.port}"
    print(f"苏晚晚 Chatbot 已启动：{base_url}/")
    # 打开聊天首页（"/"）。历史上的 /settings 是一个无样式的静态说明页，
    # 新前端的设置中心已经收进界面内（齿轮按钮或 Ctrl+/），不再作为入口。
    threading.Timer(0.8, lambda: webbrowser.open(f"{base_url}/")).start()
    _serve(config, root)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="苏晚晚本地启动器")
    parser.add_argument(
        "--mode",
        choices=("window", "web"),
        default="window",
        help="window=独立应用窗口（默认）；web=老行为，自动打开系统浏览器",
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

    if args.mode == "window":
        return _run_window_mode(config, root)
    return _run_browser_mode(config, root)


if __name__ == "__main__":
    raise SystemExit(main())
