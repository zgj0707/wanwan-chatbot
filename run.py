from __future__ import annotations

import socket
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from app.config import load_config


def main() -> int:
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

    from app.main import create_app

    # 打开聊天首页（"/"）。历史上的 /settings 是一个无样式的静态说明页，
    # 新前端的设置中心已经收进界面内（齿轮按钮或 Ctrl+/），不再作为入口。
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


if __name__ == "__main__":
    raise SystemExit(main())
