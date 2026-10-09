"""生成苏晚情绪背景图（SVG）→ ui/assets/backgrounds/*.svg

意象全部来自《苏晚-对话角色包》《苏晚-完整人设》的真实素材：
晚霞下班路 / 深夜电台 / 猫爪太阳 / 周五工位 / 微雨窗。
色板沿用 ui/style.css 的「她的房间」暖调变量。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "ui" / "assets" / "backgrounds"


def _sky(size: tuple[int, int], stops: list[tuple[float, str]]) -> str:
    width, height = size
    stop_markup = "".join(
        f'<stop offset="{offset:.2f}" stop-color="{color}"/>' for offset, color in stops
    )
    return (
        f'<linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">{stop_markup}</linearGradient>'
        f'<rect width="{width}" height="{height}" fill="url(#sky)"/>'
    )


def sunset_road(size: tuple[int, int] = (1600, 900)) -> str:
    """随手拍美学 · calm：下班晚霞（「突然觉得今天还行」）。"""
    width, height = size
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" preserveAspectRatio="xMidYMid slice">
<defs>{_sky(size, [(0, "#f6dcc4"), (0.45, "#f0b98e"), (0.75, "#e39a76"), (1, "#c98a6d")])}
<radialGradient id="sun" cx="0.5" cy="0.5" r="0.5">
<stop offset="0" stop-color="#fff3e0" stop-opacity=".9"/><stop offset="1" stop-color="#fff3e0" stop-opacity="0"/>
</radialGradient></defs>
{""}
<circle cx="{width * 0.72:.0f}" cy="{height * 0.42:.0f}" r="{height * 0.34:.0f}" fill="url(#sun)"/>
<circle cx="{width * 0.72:.0f}" cy="{height * 0.42:.0f}" r="{height * 0.075:.0f}" fill="#fbe9d2" opacity=".85"/>
<ellipse cx="{width * 0.3:.0f}" cy="{height * 0.24:.0f}" rx="{width * 0.2:.0f}" ry="{height * 0.035:.0f}" fill="#f8d9bd" opacity=".55"/>
<ellipse cx="{width * 0.55:.0f}" cy="{height * 0.17:.0f}" rx="{width * 0.14:.0f}" ry="{height * 0.028:.0f}" fill="#f8d9bd" opacity=".4"/>
<rect x="0" y="{height * 0.78:.0f}" width="{width}" height="{height * 0.22:.0f}" fill="#8a5138" opacity=".16"/>
<g stroke="#8a5138" opacity=".2" stroke-width="3" stroke-linecap="round">
<path d="M {width * 0.12:.0f} {height * 0.8:.0f} V {height * 0.42:.0f}"/>
<path d="M {width * 0.12:.0f} {height * 0.42:.0f} h 26" />
<path d="M {width * 0.88:.0f} {height * 0.8:.0f} V {height * 0.5:.0f}"/>
<path d="M {width * 0.88:.0f} {height * 0.5:.0f} h -26"/>
</g>
<g fill="#8a5138" opacity=".12">
<circle cx="{width * 0.2:.0f}" cy="{height * 0.86:.0f}" r="5"/><circle cx="{width * 0.34:.0f}" cy="{height * 0.9:.0f}" r="4"/>
<circle cx="{width * 0.62:.0f}" cy="{height * 0.88:.0f}" r="5"/><circle cx="{width * 0.8:.0f}" cy="{height * 0.92:.0f}" r="4"/>
</g>
</svg>"""


def late_night_radio(size: tuple[int, int] = (1600, 900)) -> str:
    """情绪夜话 · tired/sad：深夜电台（「电量剩 3% 那种累」）。"""
    width, height = size
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" preserveAspectRatio="xMidYMid slice">
<defs>{_sky(size, [(0, "#2e3a54"), (0.6, "#41506e"), (1, "#4a5d82")])}
<radialGradient id="moon" cx="0.5" cy="0.5" r="0.5">
<stop offset="0" stop-color="#e8edf6" stop-opacity=".8"/><stop offset="1" stop-color="#e8edf6" stop-opacity="0"/>
</radialGradient>
<linearGradient id="glow" x1="0" y1="1" x2="0" y2="0">
<stop offset="0" stop-color="#e8c9a8" stop-opacity=".22"/><stop offset="1" stop-color="#e8c9a8" stop-opacity="0"/>
</linearGradient></defs>
<circle cx="{width * 0.78:.0f}" cy="{height * 0.24:.0f}" r="{height * 0.3:.0f}" fill="url(#moon)"/>
<circle cx="{width * 0.78:.0f}" cy="{height * 0.24:.0f}" r="{height * 0.07:.0f}" fill="#e8edf6" opacity=".75"/>
<g fill="#dfe6f2" opacity=".5">
<circle cx="{width * 0.18:.0f}" cy="{height * 0.18:.0f}" r="2.4"/><circle cx="{width * 0.32:.0f}" cy="{height * 0.1:.0f}" r="1.8"/>
<circle cx="{width * 0.5:.0f}" cy="{height * 0.22:.0f}" r="2"/><circle cx="{width * 0.62:.0f}" cy="{height * 0.08:.0f}" r="1.6"/>
<circle cx="{width * 0.4:.0f}" cy="{height * 0.34:.0f}" r="1.5"/>
</g>
<rect x="0" y="{height * 0.82:.0f}" width="{width}" height="{height * 0.18:.0f}" fill="url(#glow)"/>
<g stroke="#e8c9a8" opacity=".16" stroke-width="3" stroke-linecap="round">
<path d="M {width * 0.14:.0f} {height * 0.84:.0f} V {height * 0.55:.0f}"/>
<path d="M {width * 0.84:.0f} {height * 0.84:.0f} V {height * 0.6:.0f}"/>
</g>
</svg>"""


def cat_sun(size: tuple[int, int] = (1600, 900)) -> str:
    """猫与AI女孩 · happy：猫爪太阳（年糕晒太阳的下午）。"""
    width, height = size
    paw = (
        '<g transform="translate({x},{y}) rotate({rot}) scale({scale})" opacity="{opacity}">'
        '<ellipse cx="0" cy="10" rx="13" ry="10" fill="#c98a6d"/>'
        '<circle cx="-13" cy="-4" r="5" fill="#c98a6d"/>'
        '<circle cx="-4.5" cy="-9" r="5" fill="#c98a6d"/>'
        '<circle cx="4.5" cy="-9" r="5" fill="#c98a6d"/>'
        '<circle cx="13" cy="-4" r="5" fill="#c98a6d"/></g>'
    )
    paws = "".join(
        paw.format(x=x, y=y, rot=rot, scale=scale, opacity=opacity)
        for x, y, rot, scale, opacity in (
            (210, 180, -18, 1.0, 0.16), (520, 120, 22, 0.7, 0.12), (1180, 200, -30, 0.85, 0.13),
            (1420, 560, 12, 0.65, 0.11), (320, 660, 30, 0.9, 0.14), (900, 740, -14, 0.7, 0.1),
            (700, 320, 40, 0.5, 0.09),
        )
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" preserveAspectRatio="xMidYMid slice">
<defs>{_sky(size, [(0, "#faf3e2"), (0.55, "#f8e6c8"), (1, "#f3d9ae")])}
<radialGradient id="warm" cx="0.5" cy="0.5" r="0.5">
<stop offset="0" stop-color="#ffdf9e" stop-opacity=".85"/><stop offset="1" stop-color="#ffdf9e" stop-opacity="0"/>
</radialGradient></defs>
<circle cx="{width * 0.74:.0f}" cy="{height * 0.36:.0f}" r="{height * 0.42:.0f}" fill="url(#warm)"/>
<circle cx="{width * 0.74:.0f}" cy="{height * 0.36:.0f}" r="{height * 0.1:.0f}" fill="#ffd98e" opacity=".8"/>
{paws}
</svg>"""


def desk_morning(size: tuple[int, int] = (1600, 900)) -> str:
    """打工人纪实 · calm：周五工位（晨光百叶窗影）。"""
    width, height = size
    slats = "".join(
        f'<rect x="0" y="{y:.0f}" width="{width}" height="26" fill="#c9b39a" opacity=".14"/>'
        for y in range(-40, int(height * 0.7), 88)
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" preserveAspectRatio="xMidYMid slice">
<defs>{_sky(size, [(0, "#f9f1e4"), (0.6, "#f3e4cd"), (1, "#ecd7ba")])}
<linearGradient id="light" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="#fff6e3" stop-opacity=".8"/><stop offset="1" stop-color="#fff6e3" stop-opacity="0"/>
</linearGradient></defs>
{slats}
<rect x="{width * 0.55:.0f}" y="0" width="{width * 0.45:.0f}" height="{height}" fill="url(#light)"/>
<g fill="#8a5138" opacity=".1">
<rect x="{width * 0.08:.0f}" y="{height * 0.72:.0f}" width="{width * 0.2:.0f}" height="10" rx="5"/>
<rect x="{width * 0.12:.0f}" y="{height * 0.78:.0f}" width="{width * 0.14:.0f}" height="10" rx="5"/>
<rect x="{width * 0.68:.0f}" y="{height * 0.74:.0f}" width="{width * 0.16:.0f}" height="10" rx="5"/>
</g>
</svg>"""


def rainy_window(size: tuple[int, int] = (1600, 900)) -> str:
    """情绪夜话 · sad：微雨窗（雨丝贴玻璃）。"""
    width, height = size
    import random
    rng = random.Random(20261009)
    drops = "".join(
        f'<line x1="{rng.uniform(0, width):.0f}" y1="{rng.uniform(-60, height):.0f}" '
        f'x2="{rng.uniform(0, width):.0f}" y2="{rng.uniform(0, height + 60):.0f}" '
        f'stroke="#aebdd4" stroke-width="{rng.uniform(1, 2.2):.1f}" opacity="{rng.uniform(0.18, 0.4):.2f}" '
        f'stroke-linecap="round"/>'
        for _ in range(46)
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" preserveAspectRatio="xMidYMid slice">
<defs>{_sky(size, [(0, "#c7d0de"), (0.55, "#aab8cc"), (1, "#8d9cb5")])}
<radialGradient id="pane" cx="0.5" cy="0.42" r="0.62">
<stop offset="0" stop-color="#e8edf6" stop-opacity=".22"/><stop offset="1" stop-color="#e8edf6" stop-opacity="0"/>
</radialGradient></defs>
<rect width="{width}" height="{height}" fill="url(#pane)"/>
{drops}
<g stroke="#5f6f8c" opacity=".25" stroke-width="4" fill="none">
<rect x="{width * 0.07:.0f}" y="{height * 0.08:.0f}" width="{width * 0.86:.0f}" height="{height * 0.84:.0f}" rx="22"/>
<path d="M {width * 0.5:.0f} {height * 0.08:.0f} V {height * 0.92:.0f}"/>
</g>
</svg>"""


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    backgrounds = {
        "sunset-road": sunset_road(),
        "late-night-radio": late_night_radio(),
        "cat-sun": cat_sun(),
        "desk-morning": desk_morning(),
        "rainy-window": rainy_window(),
    }
    for name, markup in backgrounds.items():
        path = OUT / f"{name}.svg"
        path.write_text(markup, encoding="utf-8")
        print(f"{path.relative_to(ROOT)}  ({len(markup.encode('utf-8')) // 1024} KB)")


if __name__ == "__main__":
    main()
