"""把 ui/ 三件套 + 预览假后端打包成一个自包含 HTML。

产物：outputs/suwan-preview.html —— 双击即可打开，不需要后端、不需要构建工具。
线上服务仍使用 ui/ 下的三件套，本脚本不改动它们。

用法：
    python scripts/build_preview_html.py
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
UI_DIR = ROOT / "ui"
OUTPUT = ROOT / "outputs" / "suwan-preview.html"

BOOTSTRAP_ANCHOR = "<!-- SUWAN_BOOTSTRAP -->"
STYLESHEET_TAG = '<link rel="stylesheet" href="/ui/style.css">'
SCRIPT_TAG = '<script src="/ui/app.js" defer></script>'

BOOTSTRAP_JSON = '{"token":"preview-only","mode":"fake"}'
BANNER_HTML = (
    '<div class="preview-banner"><span class="pv-full">单文件预览 · 内置假数据，不连接后端、不写入任何文件 · '
    '真实运行请打开 <b>http://127.0.0.1:8000/</b></span>'
    '<span class="pv-mini">单文件预览 · 假数据</span></div>'
)
BANNER_CSS = """<style>
/* 仅单文件预览包含：顶部提示条。
   正常服务页面没有这一条，故这些高度修正不会影响 ui/ 的真实布局。 */
.preview-banner{position:relative;z-index:95;display:flex;gap:8px;align-items:center;justify-content:center;
box-sizing:border-box;height:32px;padding:0 14px;background:#fff4d6;border-bottom:1px solid #f0dca8;
color:#6b5320;font:500 12.5px/1 system-ui,sans-serif;letter-spacing:.01em;white-space:nowrap;overflow:hidden}
.preview-banner b{font-weight:700}
.pv-mini{display:none}
/* 提示条占了 32px，全屏层与固定元素整体下移，避免遮挡 */
.app-shell,.chat-panel{height:calc(100vh - 32px)}
.settings-overlay,.shortcut-sheet{top:32px}
.toast-layer{top:48px}
@media (max-width:900px){
  .sidebar{top:32px}
  .pv-full{display:none}
  .pv-mini{display:inline}
}
</style>"""


def read(name: str) -> str:
    path = UI_DIR / name
    if not path.is_file():
        raise SystemExit(f"缺少源文件：{path}")
    return path.read_text(encoding="utf-8")


def main() -> int:
    html = read("index.html")
    css = read("style.css")
    app = read("app.js")
    mock = (ROOT / "scripts" / "preview_mock.js").read_text(encoding="utf-8")

    # 内联脚本里出现结束标签会提前闭合 <script>，先确认源码干净
    for name, content in (("app.js", app), ("preview_mock.js", mock)):
        if "</script" in content:
            raise SystemExit(f"{name} 中出现了 </script，内联打包会被浏览器截断。")

    for tag in (BOOTSTRAP_ANCHOR, STYLESHEET_TAG, SCRIPT_TAG):
        if tag not in html:
            raise SystemExit(f"ui/index.html 结构已变化，找不到 {tag}；需要同步本脚本。")

    # 1. 样式内联
    html = html.replace(STYLESHEET_TAG, f"<style>\n{css}\n</style>")
    # 2. 提示条样式插到 </head> 前
    html = html.replace("</head>", f"{BANNER_CSS}\n</head>")
    # 3. 锚点换成 bootstrap JSON + 提示条
    html = html.replace(
        BOOTSTRAP_ANCHOR,
        f'<script id="suwan-bootstrap" type="application/json">{BOOTSTRAP_JSON}</script>\n  {BANNER_HTML}',
    )
    # 4. 脚本内联：假后端在前，业务代码在后（业务代码在 DOM 解析完成后立即执行）
    html = html.replace(SCRIPT_TAG, f"<script>\n{mock}\n{app}\n</script>")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(html, encoding="utf-8")
    print(f"已生成 {OUTPUT.relative_to(ROOT)}（{OUTPUT.stat().st_size / 1024:.0f} KB），双击即可打开。")
    return 0


if __name__ == "__main__":
    sys.exit(main())