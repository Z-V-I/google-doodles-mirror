#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_missing_svgs.py —— 生成本地缺失的 3 个 /static/ 矢量资源。

这三个文件原本由 doodles.google/static/ 提供，当前网络不可达，
因此用站内已有的真实几何数据 + 品牌配色重建：
  1. logo-doodle-color.svg  —— 复用 icons.svg 里真实的 doodle-logo 字形路径，填品牌色
  2. multicolor.svg         —— 检索页「multicolor」色环按钮底图
  3. not-found.svg          —— 空状态插画
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
STATIC = "site/static"

SPRITE = os.path.join(STATIC, "37ae077da8a25b6950c926233ed752617048c50792204bc7b9d6332bbb7a3a5f.icons.svg")

# Google 品牌色
BLUE, RED, YELLOW, GREEN = "#4285F4", "#EA4335", "#FBBC05", "#34A853"


def build_logo_doodle_color():
    """从图标精灵里取出真实的 doodle-logo 路径，做成独立彩色 SVG。

    原始字形是单条 path（96x21）。这里按 X 轴做四段裁切上色，
    复现 Doodle 标志「多彩字母」的观感，同时保留原始字形轮廓。
    """
    t = open(SPRITE, encoding="utf-8").read()
    m = re.search(r'<svg id="doodle-logo"[^>]*viewBox="([^"]+)"[^>]*>(.*?)</svg>', t, re.S)
    if not m:
        raise SystemExit("找不到 doodle-logo")
    viewbox, inner = m.group(1), m.group(2)
    paths = re.findall(r'<path[^>]*\bd="([^"]+)"', inner)
    if not paths:
        raise SystemExit("doodle-logo 内没有 path")
    d = paths[0]
    # 保留原有属性（fill-rule 等）
    extra = ""
    em = re.search(r'<path([^>]*)\bd="', inner)
    if em:
        for attr in ("fill-rule", "clip-rule"):
            am = re.search(attr + r'="([^"]+)"', em.group(1))
            if am:
                extra += f' {attr}="{am.group(1)}"'

    nx, ny, nw, nh = (viewbox.split() if viewbox else ["0", "0", "96", "21"])
    cols = [BLUE, RED, YELLOW, GREEN]
    q = float(nw) / 4.0
    defs, groups = [], []
    for i, c in enumerate(cols):
        cid = f"c{i}"
        defs.append(
            f'<clipPath id="{cid}"><rect x="{i*q:.2f}" y="0" '
            f'width="{q:.2f}" height="{nh}"/></clipPath>'
        )
        groups.append(
            f'<g clip-path="url(#{cid})"><path d="{d}" fill="{c}"{extra}/></g>'
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{viewbox}" '
        f'role="img" aria-label="Doodles">\n'
        f'  <defs>{"".join(defs)}</defs>\n  ' + "\n  ".join(groups) + "\n</svg>\n"
    )


def build_multicolor():
    """检索页 multicolor 色环：四色扇形组成的实心圆。"""
    r, cx, cy = 12, 12, 12
    parts = [(BLUE, 0), (RED, 90), (YELLOW, 180), (GREEN, 270)]
    segs = []
    for c, a in parts:
        segs.append(
            f'<path d="M{cx} {cy} L{cx} {cy-r} A{r} {r} 0 0 1 {cx+r} {cy} Z" '
            f'fill="{c}" transform="rotate({a} {cx} {cy})"/>'
        )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        'role="img" aria-label="Multicolor">\n  ' + "\n  ".join(segs) + "\n</svg>\n"
    )


def build_not_found():
    """空状态插画：放大镜 + 彩色笔触，风格贴近原站扁平插画。"""
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 180" role="img" aria-label="No results found.">
  <g fill="none" stroke-linecap="round">
    <circle cx="96" cy="78" r="46" stroke="{BLUE}" stroke-width="9"/>
    <path d="M130 112 L164 148" stroke="{BLUE}" stroke-width="13"/>
    <path d="M62 62 C74 46 96 42 112 52" stroke="{RED}" stroke-width="7"/>
    <path d="M66 96 C78 110 100 114 116 104" stroke="{YELLOW}" stroke-width="7"/>
    <path d="M84 44 C104 40 124 50 130 68" stroke="{GREEN}" stroke-width="6"/>
  </g>
  <g fill="{YELLOW}">
    <circle cx="196" cy="40" r="7"/><circle cx="214" cy="66" r="4"/>
  </g>
  <g fill="{RED}"><circle cx="36" cy="132" r="5"/></g>
  <g fill="{GREEN}"><circle cx="206" cy="124" r="6"/></g>
</svg>
'''


def main():
    targets = {
        "06f691de476eebf89687d94773da4f718358b15519235781275a4af9870ecc69.logo-doodle-color.svg":
            build_logo_doodle_color,
        "74a8cb769a8c3a0c2078f75877d0fd35afb2842f152f39c532344ac8870cbe27.multicolor.svg":
            build_multicolor,
        "a11622629b0cc8560074aa25353a5f6f276df6ad4245774dd9bd163d7ad42562.not-found.svg":
            build_not_found,
    }
    for name, fn in targets.items():
        p = os.path.join(STATIC, name)
        open(p, "w", encoding="utf-8").write(fn())
        print(f"  生成 {name[:16]}…  {os.path.getsize(p)}B")


if __name__ == "__main__":
    main()
