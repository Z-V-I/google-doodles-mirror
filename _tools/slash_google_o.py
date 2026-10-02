#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
slash_google_o.py —— 把页眉 Google 彩色字标里的两个 o 改成 Ø（显示为 Gøøgle）。

【为什么要动精灵图，而不是动 HTML】
页眉的字标是「精灵图 + <use>」结构：

    <use href="/static/<hash>.glue-icons.svg#google-color-logo"></use>

也就是说 6378 个页面共用同一份 SVG 定义，字标本身并不写在 HTML 里。
**只改精灵图一处，全站页眉（桌面条 + 移动条）同时生效**，
不需要碰 6500 多个 HTML —— 这也是它比逐页替换更不容易出错的原因。

【Ø 是怎么画出来的】
字标里两个 o 各自是一条独立的 <path>，是「环」而非「带描边的圆」，
所以在环上叠一块同色斜条，就是标准 Ø：斜条穿过字怀，两端探出环外。
方向必须是「左下 → 右上」（即 "/"），这是 Ø 的字形规范，画反就成了别的东西。

斜条用**平行四边形**而不是 <line>：
    <line> 的端头只能垂直于笔画方向（butt cap），放大看是斜切口；
    而 Ø 的字形规范是**竖直切边**（对照 Arial 的 Ø 可验证）。
    所以这里直接给四个顶点，两条短边保持竖直。

几何（按精灵图的 74×24 坐标系）：
    o#1  圆心 (25, 12)   颜色 #EA4335   外径 5.83
    o#2  圆心 (38, 12)   颜色 #FBBC05   外径 5.83
    斜条中心线：过圆心、斜率 -1
    水平半长 L = 4.35，竖直半边长 1.30
        → 竖直边长 2.60，正交厚度 ≈ 1.84（环自身厚 2.55，斜杠略细）
        → 外侧角点距圆心 7.13，比环外径 5.83 多出 1.30，与系统字体的探出量一致

  左端 x = cx - 4.35，竖直边从 y = cy + 3.05 到 cy + 5.65
  右端 x = cx + 4.35，竖直边从 y = cy - 5.65 到 cy - 3.05

【这两个数是怎么定的】
不是拍脑袋：把几种画法（平行四边形三档 + <line> 两档）并排渲染成
真实页眉尺寸（130px）到 520px 的对比图，肉眼比对系统字体的 Ø 之后取的中档。
探出太多会显得像「禁止符」，太少又不像 Ø。

用法：
    python _tools/slash_google_o.py              # 打补丁（幂等，可重复跑）
    python _tools/slash_google_o.py --revert     # 还原成原始 Google 字标
    python _tools/slash_google_o.py --check      # 只报告状态，不写文件
"""
import os
import re
import sys
import glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

# 三个副本都要改：site/static 是实际被浏览器加载的那份，
# _clone_search / _google_clone 是重建时的源料，漏改会在下次重建被动回归。
SPRITE_GLOBS = [
    "site/static/0f99e028*.glue-icons.svg",
    "_clone_search/0f99e028*.glue-icons.svg",
    "_google_clone/0f99e028*.glue-icons.svg",
]

# 抓取期原样落盘的副本是 gzip 压缩的（文件头 1f 8b），
# 不是文本 SVG —— 读之前要先解压，写完再压回去，保持文件形态不变。
GZIP_MAGIC = b"\x1f\x8b"

SYMBOL_ID = "google-color-logo"
MARK_ATTR = "data-zvi-slash"

# (标记, o 的 d 属性开头, 圆心, 颜色)
O_TARGETS = [
    ("o1", "M25 6.19", 25.0, "#EA4335"),
    ("o2", "M38 6.19", 38.0, "#FBBC05"),
]

HALF_LEN = 4.35     # 斜条水平半长（决定两端探出环外多少）
HALF_H = 1.30       # 竖直半边长（竖直边长 2.60 → 正交厚度约 1.84）


def build_bar(tag, cx, cy, color):
    """生成一块「左下 → 右上」的同色斜条，叠在 o 上就是 Ø。

    四个顶点按 左上 → 右上 → 右下 → 左下 走一圈；
    两条短边保持竖直，正是 Ø 的字形规范。
    """
    x0, x1 = cx - HALF_LEN, cx + HALF_LEN
    d = 2 * HALF_LEN                 # 斜条的水平跨度
    h = 2 * HALF_H                   # 竖直边长
    y_left_top = cy + HALF_LEN - HALF_H
    pts = [
        (x0, y_left_top),            # 左上
        (x1, y_left_top - d),        # 右上
        (x1, y_left_top - d + h),    # 右下
        (x0, y_left_top + h),        # 左下
    ]
    d_attr = "M" + " L".join("%.2f %.2f" % p for p in pts) + " Z"
    return '<path fill="%s" d="%s" %s="%s"></path>' % (color, d_attr, MARK_ATTR, tag)


def path_re(d_prefix):
    """匹配 d 以指定片段开头的那条 <path>（自闭合或成对标签都能吃到）。"""
    return re.compile(
        r'<path\b[^>]*?\bd="' + re.escape(d_prefix) + r'[^"]*"[^>]*?(?:/>|></path>)'
    )


def patch_text(text):
    """在 google-color-logo 内部的两个 o 后面各插一根斜线，返回 (新文本, 插入数)。"""
    i = text.find('id="%s"' % SYMBOL_ID)
    if i < 0:
        return text, 0
    start = text.rfind("<svg", 0, i)
    end = text.find("</svg>", i)
    if start < 0 or end < 0:
        return text, 0
    end += len("</svg>")

    block = text[start:end]
    inserted = 0
    for tag, d_prefix, cx, color in O_TARGETS:
        if '%s="%s"' % (MARK_ATTR, tag) in block:
            continue
        m = path_re(d_prefix).search(block)
        if not m:
            print("    ! 找不到 %s 对应的 o（%s）" % (tag, d_prefix))
            continue
        bar = build_bar(tag, cx, 12.0, color)
        block = block[:m.end()] + bar + block[m.end():]
        inserted += 1

    return text[:start] + block + text[end:], inserted


# 还原时要把两种历史形态都吃掉：早期版本用的是 <line>，现版本是 <path>，
# 否则从旧版本升上来的文件会残留一根 <line>，跟新的斜条叠成双线。
REVERT_RE = re.compile(
    r'<(?:line|path)\b[^>]*\b%s="(?:o1|o2)"[^>]*?(?:/>|></(?:line|path)>)' % MARK_ATTR
)


def revert_text(text):
    new, n = REVERT_RE.subn("", text)
    return new, n


def read_sprite(p):
    """读取精灵图。返回 (文本, 是否 gzip) —— 抓取期落盘的那份是 gzip。"""
    raw = open(p, "rb").read()
    if raw[:2] == GZIP_MAGIC:
        import gzip
        return gzip.decompress(raw).decode("utf-8", "replace"), True
    return raw.decode("utf-8", "replace"), False


def write_sprite(p, text, is_gzip):
    if is_gzip:
        import gzip
        # mtime=0：让输出可复现，不把「什么时候打的补丁」写进文件头
        with gzip.GzipFile(p, "wb", mtime=0) as f:
            f.write(text.encode("utf-8"))
    else:
        # newline="" —— 原样写回，不碰各文件自身的换行风格
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(text)


def main():
    args = set(sys.argv[1:])
    do_revert = "--revert" in args
    check_only = "--check" in args

    files = []
    for pat in SPRITE_GLOBS:
        files.extend(sorted(glob.glob(pat)))
    files = sorted(set(files))

    if not files:
        sys.exit("没有找到 glue-icons 精灵图，检查路径。")

    total = 0
    for p in files:
        text, is_gzip = read_sprite(p)
        tag = "（gzip 压缩包）" if is_gzip else ""

        if do_revert:
            new, n = revert_text(text)
            act = "还原"
        else:
            new, n = patch_text(text)
            act = "打补丁"

        if check_only:
            has = '%s="o1"' % MARK_ATTR in text
            print("  [%s] %s%s" % ("已改" if has else "原样", p, tag))
            total += 1 if has else 0
            continue

        if n == 0 or new == text:
            print("  [跳过] %s（%s无需改动）%s" % (p, act, tag))
            continue

        write_sprite(p, new, is_gzip)
        print("  [%s] %s  改动 %d 处%s" % (act, p, n, tag))
        total += n

    if check_only:
        print("\n共 %d/%d 份精灵图已是 Gøøgle" % (total, len(files)))
    else:
        print("\n处理完成，共 %d 处改动，涉及 %d 份精灵图" % (total, len(files)))


if __name__ == "__main__":
    main()
