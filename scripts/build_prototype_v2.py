"""生成 UI 原型 v2：在 v1 基础上追加第二波生命特效。

直接产出 outputs/ui-prototype-v2.html（自包含单文件，头像内嵌）。
运行：python scripts/build_prototype_v2.py
"""
from __future__ import annotations

import base64
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "ui-prototype-v2.html"
AVATAR = Path(r"<local-avatar-path>")

AVATAR_URI = "data:image/png;base64," + base64.b64encode(AVATAR.read_bytes()).decode("ascii")

HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>苏晚晚 · UI 重设计原型 v2（特效增强版）</title>
<style>
:root {
  color-scheme: light;
  --ink: #3d332b;
  --ink-soft: #6b5d4f;
  --muted: #9a8b7a;
  --faint: #bfb2a3;
  --line: #ece2d6;
  --line-strong: #ddcfc0;
  --bg: #faf6f0;
  --panel: #fffdf9;
  --panel-soft: #fbf7f1;
  --hover: #f3ece2;
  --accent: #c98a6d;
  --accent-deep: #a96b4f;
  --accent-ink: #8a5138;
  --accent-soft: #faf0e8;
  --accent-border: #ecd5c4;
  --user-bubble: #e8ddd0;
  --user-ink: #4a3c2e;
  --danger: #c26b5e;
  --danger-soft: #fdefec;
  --night-ink: #2c3e5a;
  --mood-calm: #8ba888;   --mood-calm-soft: #eef4ec;
  --mood-happy: #dfae54;  --mood-happy-soft: #faf3e2;
  --mood-tired: #b0a3b8;  --mood-tired-soft: #f3eff5;
  --mood-sad: #8d9cb5;    --mood-sad-soft: #edf1f7;
  --mood-cold: #a5a5a0;   --mood-cold-soft: #f0f0ee;
  --mood-late: #4a5d82;   --mood-late-soft: #e8edf6;
  --shadow-sm: 0 1px 2px rgba(120, 90, 60, .06), 0 2px 8px rgba(120, 90, 60, .05);
  --shadow-md: 0 4px 14px rgba(120, 90, 60, .09), 0 8px 28px rgba(120, 90, 60, .06);
  --shadow-lg: 0 12px 36px rgba(120, 90, 60, .14), 0 24px 60px rgba(120, 90, 60, .09);
  --radius-bubble: 16px;
  --ease: cubic-bezier(.22, .61, .36, 1);
  --font-ui: "Segoe UI Variable Text", "Segoe UI", system-ui, -apple-system,
    "PingFang SC", "HarmonyOS Sans SC", "Microsoft YaHei UI", "Microsoft YaHei", sans-serif;
}
* { box-sizing: border-box; }
body { margin: 0; min-height: 100vh; background: var(--bg); color: var(--ink); overflow: hidden;
  font-family: var(--font-ui); font-size: 14.5px; line-height: 1.65;
  -webkit-font-smoothing: antialiased; }
button { cursor: pointer; border: 0; background: transparent; font: inherit; color: inherit; padding: 0; }
.hidden { display: none !important; }

.shell { height: 100vh; display: grid; grid-template-columns: 272px minmax(0, 1fr); }

/* ═══ 天气环境层（新增：光斑/雨/雪幕）═══ */
.weather-layer { position: fixed; inset: 0; pointer-events: none; z-index: 1; overflow: hidden; }
/* 晴天：右上角暖光斑，40s 缓慢漂移 */
.sunbeam { position: absolute; top: -12%; right: -6%; width: 46vw; height: 60vh;
  background: radial-gradient(ellipse at center, rgba(255, 214, 165, .16), transparent 65%);
  animation: beam-drift 40s ease-in-out infinite alternate; }
@keyframes beam-drift {
  0% { transform: translate(0, 0) rotate(0deg); opacity: .9; }
  50% { opacity: 1; }
  100% { transform: translate(-6vw, 4vh) rotate(6deg); opacity: .8; }
}
body[data-late="1"] .sunbeam { opacity: .25; }
/* 雨丝：细线缓落 */
.rain-drop { position: absolute; top: -8vh; width: 1px; height: 9vh;
  background: linear-gradient(transparent, rgba(141, 156, 181, .28));
  animation: rain-fall linear infinite; }
@keyframes rain-fall {
  0% { transform: translateY(-10vh); opacity: 0; }
  12% { opacity: 1; }
  100% { transform: translateY(112vh); opacity: .25; }
}
body[data-weather="sunny"] .rain-drop { display: none; }
body[data-weather="rain"] .sunbeam { opacity: .12; }
/* 天气状态角标 */
.weather-tag { position: absolute; top: 10px; right: 14px; font-size: 10.5px; color: var(--faint);
  background: color-mix(in srgb, var(--panel) 70%, transparent); padding: 3px 10px; border-radius: 999px;
  border: 1px solid var(--line); z-index: 6; }

/* ═══ 侧边栏 ═══ */
.sidebar { background: var(--panel-soft); border-right: 1px solid var(--line); display: flex; flex-direction: column; min-width: 0; z-index: 2; }
.side-head { display: flex; align-items: center; gap: 11px; padding: 18px 16px 12px; }
.brand-avatar { width: 40px; height: 40px; border-radius: 50%; overflow: hidden; flex: 0 0 40px;
  box-shadow: 0 2px 8px rgba(120, 90, 60, .18), 0 0 0 2px var(--panel); }
.brand-avatar img { width: 100%; height: 100%; object-fit: cover; display: block; }
.brand-name { font-weight: 600; letter-spacing: .03em; }
.brand-sub { font-size: 11px; color: var(--muted); }
.side-search { margin: 0 14px 10px; }
.side-search input { width: 100%; border: 1px solid var(--line); border-radius: 999px; background: var(--panel);
  padding: 8px 16px; font-size: 13px; color: var(--ink); outline: none; transition: border-color .15s, box-shadow .15s; }
.side-search input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(201, 138, 109, .14); }
.session-list { flex: 1; overflow-y: auto; padding: 4px 8px 12px; }
.session-item { display: flex; gap: 10px; align-items: center; padding: 10px 10px; border-radius: 12px;
  cursor: pointer; position: relative; transition: background .15s; }
.session-item:hover { background: var(--hover); }
.session-item:hover .session-avatar { transform: scale(1.06); }
.session-avatar { transition: transform .18s var(--ease); }
.session-item.active { background: var(--accent-soft); }
.session-item.active::before { content: ""; position: absolute; left: 0; top: 14px; bottom: 14px; width: 3px;
  border-radius: 3px; background: var(--accent); }
.session-avatar { width: 34px; height: 34px; border-radius: 50%; background: var(--hover); flex: 0 0 34px;
  display: grid; place-items: center; font-size: 13px; color: var(--ink-soft); border: 1px solid var(--line); }
.session-main { flex: 1; min-width: 0; }
.session-title-row { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }
.session-title { font-size: 13.5px; font-weight: 600; }
.session-time { font-size: 11px; color: var(--faint); white-space: nowrap; }
.session-preview { font-size: 12px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 1px; }
@keyframes swing { 0%,100% { transform: rotate(0); } 25% { transform: rotate(2.4deg); } 60% { transform: rotate(-1.8deg); } }
.session-item.swing { animation: swing .38s var(--ease); }
.side-foot { padding: 10px 14px 14px; border-top: 1px solid var(--line); display: flex; align-items: center; gap: 6px; }
.foot-entry { display: flex; align-items: center; gap: 7px; font-size: 12px; color: var(--muted);
  padding: 6px 9px; border-radius: 9px; transition: background .15s, color .15s; }
.foot-entry:hover { background: var(--hover); color: var(--ink); }
.foot-entry svg { width: 15px; height: 15px; }
.side-version { margin-left: auto; font-size: 10px; color: var(--faint); }

/* ═══ 主区 ═══ */
.main { display: grid; grid-template-rows: auto minmax(0,1fr) auto; min-width: 0; z-index: 2; }

.topbar { display: flex; align-items: center; gap: 12px; padding: 12px 20px; border-bottom: 1px solid var(--line);
  background: color-mix(in srgb, var(--panel) 72%, transparent); }
.top-avatar-wrap { position: relative; width: 44px; height: 44px; flex: 0 0 44px; }
.top-avatar { width: 44px; height: 44px; border-radius: 50%; overflow: hidden; box-shadow: var(--shadow-sm);
  border: 2px solid var(--panel); }
.top-avatar img { width: 100%; height: 100%; object-fit: cover; display: block; }
@keyframes breathe { 0%,100% { transform: scale(1); } 50% { transform: scale(1.022); } }
.life { animation: breathe 4.2s var(--ease) infinite; transform-origin: center bottom; }
body[data-mood="tired"] .life { animation-duration: 6.5s; }
body[data-mood="sad"] .life { animation-duration: 5.6s; }
@keyframes blink { 0%,100% { transform: scaleY(1); } 45% { transform: scaleY(.93); } 60% { transform: scaleY(1); } }
.life.blinking { animation: blink .16s ease-in-out; }
body[data-mood="happy"] .life { rotate: .8deg; }
body[data-mood="cold"] .life { animation: none; rotate: 0deg; }
body[data-late="1"] .top-avatar img { filter: sepia(.12) brightness(.95); }
.status-dot { position: absolute; right: -1px; bottom: -1px; width: 13px; height: 13px; border-radius: 50%;
  border: 2.5px solid var(--panel); background: var(--mood-calm); transition: background .25s; cursor: default; }
body[data-mood="happy"] .status-dot { background: var(--mood-happy); }
body[data-mood="tired"] .status-dot { background: var(--mood-tired); }
body[data-mood="sad"] .status-dot { background: var(--mood-sad); }
body[data-mood="cold"] .status-dot { background: var(--mood-cold); }
.top-title { min-width: 0; }
.top-title h1 { margin: 0; font-size: 15.5px; font-weight: 600; letter-spacing: .02em; }
.top-title p { margin: 0; font-size: 11.5px; color: var(--muted); }
.top-badges { margin-left: auto; display: flex; align-items: center; gap: 6px; }
.mood-badge { display: inline-flex; align-items: center; gap: 5px; font-size: 11.5px; padding: 4px 11px;
  border-radius: 999px; background: var(--hover); color: var(--ink-soft); border: 1px solid var(--line); }
.mood-badge .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--mood-calm); }
body[data-mood="happy"] .mood-badge .dot { background: var(--mood-happy); }
body[data-mood="tired"] .mood-badge .dot { background: var(--mood-tired); }
body[data-mood="sad"] .mood-badge .dot { background: var(--mood-sad); }
body[data-mood="cold"] .mood-badge .dot { background: var(--mood-cold); }
.mood-badge.late { background: var(--mood-late-soft); color: var(--mood-late); border-color: transparent; }
.mood-badge.guard { background: var(--danger-soft); color: var(--danger); border-color: transparent; font-weight: 600; }
@keyframes guard-nudge { 0%,100% { transform: translateX(0); } 30% { transform: translateX(-2px); } 60% { transform: translateX(2px); } }
.mood-badge.guard.nudge { animation: guard-nudge .3s ease 2; }
.ghost-btn { font-size: 12.5px; color: var(--muted); padding: 6px 12px; border-radius: 999px; transition: background .15s, color .15s; }
.ghost-btn:hover { background: var(--hover); color: var(--ink); }

.chat-wrap { position: relative; overflow-y: auto; }
.chat { max-width: 760px; margin: 0 auto; padding: 26px 24px 30px; position: relative; }
body[data-late="1"] .chat::after { content: ""; position: fixed; inset: 0; pointer-events: none;
  background: var(--night-ink); opacity: .05; z-index: 5; }
.day-divider { display: flex; align-items: center; gap: 14px; margin: 6px 0 20px; color: var(--faint); font-size: 11.5px; }
.day-divider::before, .day-divider::after { content: ""; flex: 1; height: 1px; background: var(--line); }
.first-chat { text-align: center; color: var(--muted); font-size: 12px; margin: 0 0 22px; }

/* 里程碑横幅（closeness 升档 / 百日记）*/
@keyframes banner-in { 0% { opacity: 0; transform: translateY(10px) scale(.96); } 100% { opacity: 1; transform: none; } }
@keyframes glow-pulse { 0% { box-shadow: 0 0 0 0 rgba(201, 138, 109, 0); } 45% { box-shadow: 0 0 0 9px rgba(201, 138, 109, .12); } 100% { box-shadow: 0 0 0 0 rgba(201, 138, 109, 0); } }
.milestone { text-align: center; margin: 4px 0 24px; }
.milestone .banner { display: inline-block; background: var(--panel); border: 1px solid var(--accent-border);
  border-radius: 999px; padding: 8px 22px; font-size: 12.5px; color: var(--accent-ink);
  box-shadow: var(--shadow-sm); animation: banner-in .5s var(--ease), glow-pulse 1.6s ease .4s 2; }

.msg-row { display: flex; gap: 10px; margin-bottom: 18px; align-items: flex-start; }
.msg-row.user { flex-direction: row-reverse; }
.msg-avatar { width: 38px; height: 38px; border-radius: 50%; overflow: hidden; flex: 0 0 38px; margin-top: 2px;
  box-shadow: var(--shadow-sm); }
.msg-avatar img { width: 100%; height: 100%; object-fit: cover; display: block; }
.msg-avatar.me { background: linear-gradient(150deg, #d9cbb8, #c4b29a); display: grid; place-items: center;
  color: #fff; font-size: 13px; }
.msg-main { max-width: 78%; min-width: 0; }
.author-row { display: flex; gap: 8px; align-items: baseline; margin-bottom: 3px; }
.msg-row.user .author-row { justify-content: flex-end; }
.author { font-size: 11.5px; color: var(--muted); }
.bubble { position: relative; border-radius: var(--radius-bubble); padding: 10px 15px; width: fit-content;
  box-shadow: var(--shadow-sm); }
.bubble.her { background: var(--panel); border: 1px solid var(--line); border-bottom-color: var(--line-strong); }
.bubble.her::before { content: ""; position: absolute; left: -5.5px; top: 13px; width: 11px; height: 11px;
  background: var(--panel); border-left: 1px solid var(--line); border-bottom: 1px solid var(--line);
  transform: rotate(45deg); border-bottom-left-radius: 3px; }
.bubble.mine { background: var(--user-bubble); color: var(--user-ink); }
.bubble.mine::before { content: ""; position: absolute; right: -5.5px; top: 13px; width: 11px; height: 11px;
  background: var(--user-bubble); border-right: 1px solid var(--line-strong); border-top: 1px solid var(--line-strong);
  transform: rotate(45deg); border-top-right-radius: 3px; }
@keyframes paper-in { from { opacity: 0; transform: translateY(9px) scale(.985); } to { opacity: 1; transform: none; } }
@keyframes quick-in { from { opacity: 0; transform: translateY(5px); } to { opacity: 1; transform: none; } }
.msg-row.her-msg .bubble { animation: paper-in .34s var(--ease); }
.msg-row.user .bubble { animation: quick-in .18s var(--ease); }
.msg-actions { display: flex; gap: 4px; margin-top: 5px; opacity: 0; transition: opacity .16s; height: 26px; }
.msg-row:hover .msg-actions, .msg-actions:focus-within { opacity: 1; }
.msg-row.user .msg-actions { justify-content: flex-end; }
.act { font-size: 11.5px; color: var(--muted); padding: 3px 9px; border-radius: 999px; transition: background .14s, color .14s; }
.act:hover { background: var(--hover); color: var(--ink); }
/* 复制时气泡轻跳（被拿起来看）*/
@keyframes pick-up { 0% { transform: translateY(0); } 40% { transform: translateY(-3px); } 100% { transform: translateY(0); } }
.bubble.picked { animation: pick-up .3s var(--ease); }
.paw-stamp { position: absolute; right: -8px; bottom: -10px; width: 26px; height: 26px; color: var(--accent);
  opacity: 0; pointer-events: none; }
@keyframes stamp { 0% { opacity: 0; transform: scale(1.7) rotate(-14deg); } 55% { opacity: .95; transform: scale(.94) rotate(-8deg); } 100% { opacity: .8; transform: scale(1) rotate(-8deg); } }
.paw-stamp.stamped { animation: stamp .34s var(--ease) forwards; }
/* 她提到年糕时：气泡角落浮出小猫 */
.cat-peek { position: absolute; left: -14px; bottom: -12px; width: 30px; height: 30px; opacity: 0;
  animation: cat-pop .6s var(--ease) .5s forwards; pointer-events: none; }
@keyframes cat-pop { from { opacity: 0; transform: translateY(6px); } to { opacity: .92; transform: none; } }

.typing-row { display: flex; gap: 10px; margin-bottom: 18px; align-items: flex-start; }
.typing-bubble { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius-bubble);
  border-bottom-left-radius: 6px; padding: 12px 16px; display: flex; align-items: center; gap: 7px;
  box-shadow: var(--shadow-sm); color: var(--muted); font-size: 12px; min-height: 44px; }
/* 打字速度随 energy：calm/happy 快，tired/sad 慢 */
@keyframes paw-bounce { 0%,60%,100% { transform: translateY(0); opacity: .4; } 30% { transform: translateY(-4px); opacity: 1; } }
.typing-bubble .paw { width: 14px; height: 14px; color: var(--accent); animation: paw-bounce 1.1s ease infinite; }
.typing-bubble .paw:nth-child(2) { animation-delay: .13s; }
.typing-bubble .paw:nth-child(3) { animation-delay: .26s; }
body[data-mood="tired"] .typing-bubble .paw,
body[data-mood="sad"] .typing-bubble .paw { animation-duration: 2.1s; }
body[data-mood="tired"] .typing-bubble .paw:nth-child(2),
body[data-mood="sad"] .typing-bubble .paw:nth-child(2) { animation-delay: .25s; }
body[data-mood="tired"] .typing-bubble .paw:nth-child(3),
body[data-mood="sad"] .typing-bubble .paw:nth-child(3) { animation-delay: .5s; }
body[data-mood="cold"] .typing-bubble .paw { animation: none; opacity: .3; }

/* 打错字再删： tired 模式的输入气泡文字 */
.type-slip { display: inline-block; }
.type-slip .slip-char { text-decoration: line-through; opacity: .55; animation: slip-out .5s ease 1.1s forwards; }
@keyframes slip-out { to { opacity: 0; width: 0; display: inline-block; } }

/* 年糕巡逻彩蛋 */
.cat-walk { position: fixed; bottom: 0; left: -80px; z-index: 40; pointer-events: none;
  animation: walk-across 13s linear forwards; }
@keyframes walk-across {
  0% { transform: translateX(0) scaleX(1); }
  88% { transform: translateX(calc(100vw + 160px)) scaleX(1); }
  89% { transform: translateX(calc(100vw + 160px)) scaleX(1); }
  100% { transform: translateX(calc(100vw + 160px)) scaleX(1); }
}
.cat-walk svg { width: 64px; height: 40px; opacity: .5; display: block;
  animation: tail-sway 1.6s ease-in-out infinite; }
@keyframes tail-sway { 0%,100% { transform: rotate(0); } 50% { transform: rotate(1.4deg) translateY(-1px); } }
.cat-walk .walk-note { text-align: center; font-size: 10px; color: var(--muted); opacity: .8; margin-top: 2px; }

.composer-wrap { padding: 10px 24px 20px; }
.composer { max-width: 760px; margin: 0 auto; background: var(--panel); border: 1px solid var(--line);
  border-radius: 18px; box-shadow: var(--shadow-sm); padding: 12px 14px 10px; transition: border-color .15s, box-shadow .15s; }
.composer:focus-within { border-color: var(--accent-border); box-shadow: 0 0 0 4px rgba(201, 138, 109, .1), var(--shadow-sm); }
.composer textarea { width: 100%; border: 0; outline: none; resize: none; font: inherit; color: var(--ink);
  background: transparent; min-height: 24px; max-height: 120px; }
.composer textarea::placeholder { transition: opacity .2s; }
.composer-foot { display: flex; align-items: center; justify-content: space-between; margin-top: 6px; }
.hint { font-size: 11px; color: var(--faint); }
.send-btn { display: inline-flex; align-items: center; gap: 7px; background: linear-gradient(180deg, #d29a7c, var(--accent));
  color: #fff; font-size: 13px; font-weight: 600; border-radius: 999px; padding: 8px 20px;
  box-shadow: 0 3px 10px rgba(201, 138, 109, .35); transition: filter .15s, transform .15s; }
.send-btn svg { width: 14px; height: 14px; transition: transform .2s var(--ease); }
.send-btn:hover svg { transform: rotate(-8deg) translateY(-1px); }
.send-btn:active { transform: translateY(1px); }

/* 声音开关示意（Web Audio 哒声） */
.sound-chip { font-size: 11.5px; color: var(--muted); display: inline-flex; align-items: center; gap: 5px;
  padding: 4px 10px; border-radius: 999px; border: 1px solid var(--line); transition: all .15s; }
.sound-chip.on { color: var(--accent-ink); border-color: var(--accent-border); background: var(--accent-soft); }

/* ═══ 演示控制台 ═══ */
.demo-panel { position: fixed; right: 18px; bottom: 18px; z-index: 50; background: #2f2a24; color: #e8ddd0;
  border-radius: 14px; padding: 12px 14px; box-shadow: var(--shadow-lg); font-size: 12px; width: 224px;
  max-height: 78vh; overflow-y: auto; }
.demo-panel h3 { margin: 0 0 8px; font-size: 11.5px; letter-spacing: .1em; color: #bfb2a3; font-weight: 600; }
.demo-grid { display: flex; flex-wrap: wrap; gap: 5px; }
.demo-btn { font-size: 11.5px; padding: 4px 10px; border-radius: 999px; background: rgba(255,255,255,.08);
  color: #e8ddd0; transition: background .14s; }
.demo-btn:hover { background: rgba(255,255,255,.18); }
.demo-btn.on { background: var(--accent); color: #fff; }
.demo-note { margin: 8px 0 0; font-size: 10.5px; color: #9a8b7a; line-height: 1.5; }

/* 设置中心一瞥 */
.settings-overlay { position: fixed; inset: 0; z-index: 60; background: rgba(61, 51, 43, .32);
  display: grid; place-items: center; }
.settings-card { width: min(640px, calc(100vw - 48px)); max-height: 82vh; overflow-y: auto; background: var(--panel);
  border-radius: 20px; box-shadow: var(--shadow-lg); padding: 22px 24px; }
.settings-head { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 4px; }
.settings-head h2 { margin: 0; font-size: 16px; }
.settings-close { color: var(--muted); font-size: 13px; }
.her-tone { color: var(--muted); font-size: 12.5px; margin: 0 0 16px; }
.settings-card .her-tone::before { content: "晚晚："; color: var(--accent-ink); font-weight: 600; }
.settings-section { border: 1px solid var(--line); border-radius: 16px; padding: 14px 16px; margin-bottom: 12px; background: var(--panel-soft); }
.settings-section h4 { margin: 0 0 8px; font-size: 13px; display: flex; align-items: center; gap: 7px; }
.kv-row { display: flex; justify-content: space-between; font-size: 12.5px; padding: 5px 0; color: var(--ink-soft); align-items: center; }
.kv-row .k { color: var(--muted); }
.cat-status { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--ink-soft); }
.cat-status svg { width: 16px; height: 16px; color: var(--accent-deep); }

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition: none !important; }
}
@media (max-width: 720px) {
  .shell { grid-template-columns: 1fr; }
  .sidebar { display: none; }
  .msg-main { max-width: 86%; }
}
</style>
</head>
<body data-mood="calm" data-weather="sunny">

<!-- 天气环境层 -->
<div class="weather-layer" id="weatherLayer">
  <div class="sunbeam"></div>
  <span class="weather-tag" id="weatherTag">☀ 广州 · 晴</span>
</div>

<div class="shell">
  <aside class="sidebar">
    <div class="side-head">
      <div class="brand-avatar life"><img src="__AVATAR_URI__" alt="苏晚晚"></div>
      <div><div class="brand-name">苏晚晚</div><div class="brand-sub">广州 · 产品经理</div></div>
    </div>
    <div class="side-search"><input type="search" placeholder="搜索对话"></div>
    <div class="session-list">
      <div class="session-item active">
        <div class="session-avatar">我</div>
        <div class="session-main">
          <div class="session-title-row"><span class="session-title">周五晚的碎碎念</span><span class="session-time">刚刚</span></div>
          <div class="session-preview">需求改了第八版。年糕说它也不懂。</div>
        </div>
      </div>
      <div class="session-item" id="swingTarget">
        <div class="session-avatar">我</div>
        <div class="session-main">
          <div class="session-title-row"><span class="session-title">晚霞照片那晚</span><span class="session-time">昨天</span></div>
          <div class="session-preview">下班看到这片晚霞，突然觉得今天还行。</div>
        </div>
      </div>
      <div class="session-item">
        <div class="session-avatar">我</div>
        <div class="session-main">
          <div class="session-title-row"><span class="session-title">第一次聊天</span><span class="session-time">周三</span></div>
          <div class="session-preview">嗯……我在听。那确实挺难受的。</div>
        </div>
      </div>
    </div>
    <div class="side-foot">
      <button class="foot-entry"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="15.5" rx="4.6" ry="3.6"/><circle cx="7.2" cy="10.4" r="1.7"/><circle cx="10.6" cy="7.4" r="1.7"/><circle cx="14.6" cy="7.6" r="1.7"/><circle cx="17.4" cy="11" r="1.7"/></svg>晚晚档案</button>
      <button class="foot-entry"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3.5h9.5L19 7v13.5H6z"/><path d="M15 3.5V7h4"/></svg>素材库</button>
      <span class="side-version">p3 预览 v2</span>
    </div>
  </aside>

  <main class="main">
    <header class="topbar">
      <div class="top-avatar-wrap">
        <div class="top-avatar"><img class="life" id="lifeAvatar" src="__AVATAR_URI__" alt="苏晚晚"></div>
        <span class="status-dot" id="statusDot"></span>
      </div>
      <div class="top-title"><h1>周五晚的碎碎念</h1><p id="sceneLine">广州 · 周五 20:47 · 下班路上</p></div>
      <div class="top-badges">
        <span class="mood-badge" id="moodBadge"><span class="dot"></span><span id="moodText">平静</span></span>
        <span class="mood-badge late hidden" id="lateBadge">🌙 深夜</span>
        <span class="mood-badge guard hidden" id="guardBadge">需要一点空间</span>
        <button class="ghost-btn" id="openSettings">设置中心</button>
      </div>
    </header>

    <div class="chat-wrap" id="chatWrap">
      <div class="chat" id="chat">
        <div class="milestone" id="milestoneBanner">
          <span class="banner">🎂 这是你们认识的第一百天</span>
        </div>
        <div class="day-divider">今天</div>

        <div class="msg-row her-msg">
          <div class="msg-avatar"><img src="__AVATAR_URI__" alt=""></div>
          <div class="msg-main">
            <div class="author-row"><span class="author">苏晚晚</span><span class="author">20:31</span></div>
            <div class="bubble her">刚出地铁。今天需求改了第八版，改到我怀疑人生……你下班了吗？</div>
            <div class="msg-actions"><button class="act">复制</button><button class="act collect-btn">收藏素材</button></div>
          </div>
        </div>

        <div class="msg-row user">
          <div class="msg-avatar me">我</div>
          <div class="msg-main">
            <div class="author-row"><span class="author">20:33</span></div>
            <div class="bubble mine">也刚到家。第八版？需求又变了？</div>
            <div class="msg-actions"><button class="act">复制</button><button class="act collect-btn">收藏素材</button></div>
          </div>
        </div>

        <div class="msg-row her-msg">
          <div class="msg-avatar"><img src="__AVATAR_URI__" alt=""></div>
          <div class="msg-main">
            <div class="author-row"><span class="author">苏晚晚</span><span class="author">20:34</span></div>
            <div class="bubble her" id="catBubble">嗯，评审会上又推翻了。算了……下班路上看到一片晚霞，突然觉得今天还行。给你看看，路灯下面还蹲了只猫，好高冷。</div>
            <div class="msg-actions"><button class="act">复制</button><button class="act collect-btn">收藏素材</button></div>
          </div>
        </div>

        <div class="typing-row hidden" id="typingRow">
          <div class="msg-avatar"><img src="__AVATAR_URI__" alt=""></div>
          <div class="typing-bubble">
            <svg class="paw" viewBox="0 0 24 24" fill="currentColor"><ellipse cx="12" cy="15.8" rx="4.8" ry="3.8"/><circle cx="6.8" cy="10.6" r="1.9"/><circle cx="10.4" cy="7.2" r="1.9"/><circle cx="14.6" cy="7.4" r="1.9"/><circle cx="17.6" cy="11.2" r="1.9"/></svg>
            <svg class="paw" viewBox="0 0 24 24" fill="currentColor"><ellipse cx="12" cy="15.8" rx="4.8" ry="3.8"/><circle cx="6.8" cy="10.6" r="1.9"/><circle cx="10.4" cy="7.2" r="1.9"/><circle cx="14.6" cy="7.4" r="1.9"/><circle cx="17.6" cy="11.2" r="1.9"/></svg>
            <svg class="paw" viewBox="0 0 24 24" fill="currentColor"><ellipse cx="12" cy="15.8" rx="4.8" ry="3.8"/><circle cx="6.8" cy="10.6" r="1.9"/><circle cx="10.4" cy="7.2" r="1.9"/><circle cx="14.6" cy="7.4" r="1.9"/><circle cx="17.6" cy="11.2" r="1.9"/></svg>
            <span id="typingText">正在想怎么回你</span>
          </div>
        </div>
      </div>
    </div>

    <div class="composer-wrap">
      <form class="composer" id="composer">
        <textarea id="input" rows="1" placeholder="说点什么吧…（Enter 发送，Shift+Enter 换行）"></textarea>
        <div class="composer-foot">
          <div style="display:flex;align-items:center;gap:8px">
            <button type="button" class="sound-chip" id="soundChip">🔇 到达声（关）</button>
            <span class="hint">Enter 发送 · 原型回复为演示数据</span>
          </div>
          <button class="send-btn" type="submit">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 3 10.5 13.5"/><path d="M21 3 14 21l-3.5-7.5L3 10z"/></svg>
            发送
          </button>
        </div>
      </form>
    </div>
  </main>
</div>

<div class="settings-overlay hidden" id="settingsOverlay">
  <div class="settings-card">
    <div class="settings-head"><h2>设置中心</h2><button class="settings-close" id="closeSettings">返回对话 ✕</button></div>
    <p class="her-tone">这里放的是我的固定资料，改不动的，但喜好可以商量。</p>
    <div class="settings-section">
      <h4>模型状态</h4>
      <div class="kv-row"><span class="k">模式</span><span>离线模拟（fake）</span></div>
      <div class="kv-row"><span class="k">聊天模型</span><span>qwen-flash-character</span></div>
    </div>
    <div class="settings-section">
      <h4>晚晚档案</h4>
      <div class="kv-row"><span class="k">亲密度</span><span id="closenessLine">熟悉（42/100）</span></div>
      <div class="kv-row"><span class="k">年糕</span>
        <span class="cat-status"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4.5 9.5 6 4.5l3.2 2.6a6.6 6.6 0 0 1 5.6 0L18 4.5l1.5 5"/><ellipse cx="12" cy="13.5" rx="6.5" ry="5.5"/><path d="M10.5 21v-2.2M13.5 21v-2.2"/></svg><span id="catStatusLine">拆家中</span></span>
      </div>
    </div>
    <div class="settings-section">
      <h4>数据与备份</h4>
      <div class="kv-row"><span class="k">导出数据库备份</span><button class="ghost-btn" style="color:var(--accent-ink)">导出 ZIP</button></div>
    </div>
  </div>
</div>

<div class="demo-panel">
  <h3>原型演示控制 v2</h3>
  <div class="demo-grid">
    <button class="demo-btn" data-mood="calm">平静</button>
    <button class="demo-btn" data-mood="happy">开心</button>
    <button class="demo-btn" data-mood="tired">有点累</button>
    <button class="demo-btn" data-mood="sad">低落</button>
    <button class="demo-btn" data-mood="cold">距离感</button>
    <button class="demo-btn" id="toggleLate">深夜</button>
    <button class="demo-btn" id="toggleGuard">警戒</button>
    <button class="demo-btn" id="toggleTyping">正在输入</button>
    <button class="demo-btn" id="swingBtn">轻晃</button>
    <button class="demo-btn" id="toggleRain">🌧 下雨</button>
    <button class="demo-btn" id="catWalkBtn">🐈 年糕巡逻</button>
    <button class="demo-btn" id="closenessBtn">✨ 亲密度升档</button>
    <button class="demo-btn" id="slipBtn">⌫ 打错字</button>
  </div>
  <p class="demo-note">新增 v2 特效：天气光斑/雨丝（右上角角标可切）、年糕巡逻、打字速度随心情（切"有点累"再看爪印变慢）、打错字重打、亲密度升档脉冲、百日记横幅、复制轻跳、声音开关。年糕巡逻点一次走一只。</p>
</div>

<script>
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);

  /* 眨眼 */
  const avatar = $("lifeAvatar");
  const blink = () => {
    avatar.classList.add("blinking");
    setTimeout(() => avatar.classList.remove("blinking"), 170);
    setTimeout(blink, 6000 + Math.random() * 6000);
  };
  setTimeout(blink, 3200);

  /* mood */
  const MOOD_TEXT = { calm: "平静", happy: "开心", tired: "有点累", sad: "低落", cold: "有点距离感" };
  const SCENE = {
    calm: "广州 · 周五 20:47 · 下班路上",
    happy: "广州 · 周六 15:20 · 东山口探店",
    tired: "广州 · 周三 22:10 · 加班夜归",
    sad: "广州 · 周日 23:41 · 深夜",
    cold: "广州 · 周四 19:02 · 刚下评审会",
  };
  const TYPING_FAST = ["正在想怎么回你", "嗯…让我想想", "年糕踩过键盘了稍等"];
  const TYPING_TIRED = ["让我缓缓再回你……", "在想，有点慢", "今天好累，容我想想"];
  let typingTired = false;
  document.querySelectorAll(".demo-btn[data-mood]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const mood = btn.dataset.mood;
      document.body.dataset.mood = mood;
      $("moodText").textContent = MOOD_TEXT[mood];
      $("sceneLine").textContent = SCENE[mood];
      typingTired = mood === "tired" || mood === "sad";
      $("typingText").textContent = (typingTired ? TYPING_TIRED : TYPING_FAST)[0];
      document.querySelectorAll(".demo-btn[data-mood]").forEach((b) => b.classList.toggle("on", b === btn));
    });
  });
  document.querySelector('.demo-btn[data-mood="calm"]').classList.add("on");

  /* 深夜 */
  $("toggleLate").addEventListener("click", () => {
    const on = document.body.dataset.late === "1";
    document.body.dataset.late = on ? "0" : "1";
    $("lateBadge").classList.toggle("hidden", on);
    $("toggleLate").classList.toggle("on", !on);
  });
  $("toggleGuard").addEventListener("click", () => {
    const badge = $("guardBadge");
    const on = !badge.classList.contains("hidden");
    badge.classList.toggle("hidden", on);
    if (!on) { badge.classList.add("nudge"); setTimeout(() => badge.classList.remove("nudge"), 700); }
    $("toggleGuard").classList.toggle("on", !on);
  });
  $("toggleTyping").addEventListener("click", () => {
    const row = $("typingRow");
    row.classList.toggle("hidden");
    const pool = typingTired ? TYPING_TIRED : TYPING_FAST;
    $("typingText").textContent = pool[Math.floor(Math.random() * pool.length)];
    $("toggleTyping").classList.toggle("on", !row.classList.contains("hidden"));
  });
  $("swingBtn").addEventListener("click", () => {
    const item = $("swingTarget");
    item.classList.remove("swing");
    void item.offsetWidth;
    item.classList.add("swing");
  });

  /* ── v2 新增 ── */
  /* 天气：雨丝生成 */
  const weatherLayer = $("weatherLayer");
  function makeRain(count) {
    weatherLayer.querySelectorAll(".rain-drop").forEach((d) => d.remove());
    if (!count) return;
    for (let i = 0; i < count; i++) {
      const drop = document.createElement("div");
      drop.className = "rain-drop";
      drop.style.left = Math.random() * 100 + "vw";
      drop.style.animationDuration = 0.9 + Math.random() * 1.1 + "s";
      drop.style.animationDelay = -Math.random() * 2 + "s";
      drop.style.height = 7 + Math.random() * 6 + "vh";
      drop.style.opacity = 0.5 + Math.random() * 0.5;
      weatherLayer.appendChild(drop);
    }
  }
  $("toggleRain").addEventListener("click", () => {
    const raining = document.body.dataset.weather === "rain";
    document.body.dataset.weather = raining ? "sunny" : "rain";
    makeRain(raining ? 0 : 26);
    $("weatherTag").textContent = raining ? "☀ 广州 · 晴" : "🌧 广州 · 中雨";
    $("toggleRain").classList.toggle("on", !raining);
  });

  /* 年糕巡逻：猫剪影走过屏幕底部 */
  const CAT_SVG = '<svg viewBox="0 0 64 40" fill="#3d332b"><path d="M8 34c-2-6 0-13 5-16l-2-7 6 4a14 14 0 0 1 10 0l6-4-2 7c4 3 6 9 5 14l6 2c1 .4 1 2-.5 2H38c-1 2-3 3-5 3H12c-2 0-3.4-2-4-5z"/></svg>';
  $("catWalkBtn").addEventListener("click", () => {
    const cat = document.createElement("div");
    cat.className = "cat-walk";
    cat.innerHTML = CAT_SVG + '<div class="walk-note">年糕巡逻中…</div>';
    document.body.appendChild(cat);
    cat.addEventListener("animationend", () => cat.remove());
  });

  /* 亲密度升档：暖光脉冲横幅 + 徽章变化 */
  let closenessStage = 1;
  const STAGES = ["初识", "熟悉", "亲近"];
  $("closenessBtn").addEventListener("click", () => {
    if (closenessStage >= 2) { closenessStage = 0; } else { closenessStage += 1; }
    $("closenessLine").textContent = STAGES[closenessStage] + "（" + [18, 42, 76][closenessStage] + "/100）";
    const chat = $("chat");
    const div = document.createElement("div");
    div.className = "milestone";
    div.innerHTML = '<span class="banner">✨ 你们的关系更近了一步 · 现在是「' + STAGES[closenessStage] + '」</span>';
    chat.insertBefore(div, chat.firstChild);
    setTimeout(() => div.remove(), 6000);
  });

  /* 打错字再删（tired 模式的输入气泡演示）*/
  $("slipBtn").addEventListener("click", () => {
    const row = $("typingRow");
    const label = $("typingText");
    row.classList.remove("hidden");
    label.innerHTML = '在想，<span class="type-slip"><span class="slip-char">今 dne</span></span>今天好累';
    setTimeout(() => { label.textContent = "今天好累，容我想想"; row.classList.add("hidden"); }, 2600);
  });

  /* 声音开关 + Web Audio 哒声 */
  let audioCtx = null;
  let soundOn = false;
  function ding() {
    if (!soundOn) return;
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.frequency.value = 880;
    osc.type = "sine";
    gain.gain.setValueAtTime(0.0001, audioCtx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.06, audioCtx.currentTime + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + 0.18);
    osc.connect(gain).connect(audioCtx.destination);
    osc.start();
    osc.stop(audioCtx.currentTime + 0.2);
  }
  $("soundChip").addEventListener("click", () => {
    soundOn = !soundOn;
    $("soundChip").textContent = soundOn ? "🔔 到达声（开）" : "🔇 到达声（关）";
    $("soundChip").classList.toggle("on", soundOn);
    if (soundOn) ding();
  });

  /* 年糕 peek：她提到"猫/年糕"的气泡角落浮出小猫头 */
  const CAT_PEEK = '<svg class="cat-peek" viewBox="0 0 24 24" fill="none" stroke="#8a5138" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4.5 9.5 6 4.5l3.2 2.6a6.6 6.6 0 0 1 5.6 0L18 4.5l1.5 5"/><ellipse cx="12" cy="13.5" rx="6.5" ry="5.5"/><circle cx="9.6" cy="13" r=".4" fill="#8a5138"/><circle cx="14.4" cy="13" r=".4" fill="#8a5138"/></svg>';
  document.querySelectorAll(".bubble.her").forEach((bubble) => {
    if (/年糕|猫/.test(bubble.textContent)) bubble.insertAdjacentHTML("beforeend", CAT_PEEK);
  });

  /* 复制轻跳 */
  function bindCardActions(root) {
    root.querySelectorAll(".act").forEach((btn) => {
      if (btn.dataset.bound) return;
      btn.dataset.bound = "1";
      if (btn.textContent === "复制") {
        btn.addEventListener("click", () => {
          const bubble = btn.closest(".msg-main").querySelector(".bubble");
          bubble.classList.remove("picked");
          void bubble.offsetWidth;
          bubble.classList.add("picked");
        });
      } else if (btn.classList.contains("collect-btn")) {
        btn.addEventListener("click", () => {
          const bubble = btn.closest(".msg-main").querySelector(".bubble");
          if (bubble.querySelector(".paw-stamp")) return;
          bubble.insertAdjacentHTML("beforeend", PAW);
          requestAnimationFrame(() => bubble.querySelector(".paw-stamp").classList.add("stamped"));
          btn.textContent = "已收藏";
          btn.disabled = true;
          ding();
        });
      }
    });
  }
  const PAW = '<svg class="paw-stamp" viewBox="0 0 24 24" fill="currentColor"><ellipse cx="12" cy="15.8" rx="4.8" ry="3.8"/><circle cx="6.8" cy="10.6" r="1.9"/><circle cx="10.4" cy="7.2" r="1.9"/><circle cx="14.6" cy="7.4" r="1.9"/><circle cx="17.6" cy="11.2" r="1.9"/></svg>';
  bindCardActions(document);

  /* 发送演示（她慢你快 + 打字气泡 + 到达声）*/
  const REPLIES = [
    "嗯……我在听。那确实挺难受的。",
    "需求改了第八版。年糕说它也不懂。",
    "下班看到这片晚霞，突然觉得今天还行。",
  ];
  let replyIdx = 0;
  $("composer").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = $("input");
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    const chat = $("chat");
    chat.insertAdjacentHTML("beforeend",
      '<div class="msg-row user"><div class="msg-avatar me">我</div><div class="msg-main">' +
      '<div class="author-row"><span class="author">刚刚</span></div>' +
      '<div class="bubble mine"></div><div class="msg-actions"><button class="act">复制</button>' +
      '<button class="act collect-btn">收藏素材</button></div></div></div>');
    chat.lastElementChild.querySelector(".bubble").textContent = text;
    bindCardActions(chat.lastElementChild);
    $("chatWrap").scrollTop = $("chatWrap").scrollHeight;
    setTimeout(() => {
      $("typingRow").classList.remove("hidden");
      const pool = typingTired ? TYPING_TIRED : TYPING_FAST;
      $("typingText").textContent = pool[Math.floor(Math.random() * pool.length)];
      $("chatWrap").scrollTop = $("chatWrap").scrollHeight;
      setTimeout(() => {
        $("typingRow").classList.add("hidden");
        chat.insertAdjacentHTML("beforeend",
          '<div class="msg-row her-msg"><div class="msg-avatar"><img src="' + avatar.src + '" alt=""></div>' +
          '<div class="msg-main"><div class="author-row"><span class="author">苏晚晚</span><span class="author">刚刚</span></div>' +
          '<div class="bubble her"></div><div class="msg-actions"><button class="act">复制</button>' +
          '<button class="act collect-btn">收藏素材</button></div></div></div>');
        const bubble = chat.lastElementChild.querySelector(".bubble");
        bubble.textContent = REPLIES[replyIdx++ % REPLIES.length];
        if (/年糕|猫/.test(bubble.textContent)) bubble.insertAdjacentHTML("beforeend", CAT_PEEK);
        bindCardActions(chat.lastElementChild);
        ding();
        $("chatWrap").scrollTop = $("chatWrap").scrollHeight;
      }, 1400);
    }, 500);
  });

  $("openSettings").addEventListener("click", () => $("settingsOverlay").classList.remove("hidden"));
  $("closeSettings").addEventListener("click", () => $("settingsOverlay").classList.add("hidden"));
  $("settingsOverlay").addEventListener("click", (e) => {
    if (e.target === $("settingsOverlay")) $("settingsOverlay").classList.add("hidden");
  });
})();
</script>
</body>
</html>
"""

def main() -> None:
    html = HTML.replace("__AVATAR_URI__", AVATAR_URI)
    OUT.write_text(html, encoding="utf-8")
    size_mb = OUT.stat().st_size / 1024 / 1024
    print(f"written: {OUT} ({size_mb:.1f}MB)")


if __name__ == "__main__":
    main()
