#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
remove_creating_doodle.py —— 从离线镜像中彻底摘除 /creating-a-doodle/ 路由。

【背景】
导航栏与页脚各有一个指向 /creating-a-doodle/ 的入口，它们是**原始抓取页面
自带的**（不是构建时注入的模板），因此 6378 个 HTML 全部命中，每页 4 处：
    header 的 href + data-g-cta_url
    footer 的 href + data-g-cta_url
只把目录删掉是不够的 —— 6 千多个页面会立刻多出一条站内死链。

【为什么必须同时改源头】
gen_doodle_pages.py 每次都从 _clone_search/doodle-slideshow.html 重新抽取
header / footer 骨架来生成 6375 个详情页。只改 site/ 下的产物，下一次
rebuild 就会把入口整批复活。所以这里同时改两处：
    ① site/**/*.html   —— 已生成的产物，改动立刻可见
    ② 源头模板          —— 保证重新构建后依然干净

【换行符】
site/ 下是 LF，_clone_search/ 下是 CRLF。全程用 newline="" 收发，
不做 universal-newlines 转换，避免「改一个链接顺手重写整个文件行尾」。

【幂等】
重复运行不会出错也不会重复改写：已摘除的文件匹配不到块，直接跳过。

用法：
    python _tools/remove_creating_doodle.py --check   # 预演，只报告不落盘
    python _tools/remove_creating_doodle.py           # 执行
"""
import os
import re
import sys
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

ROUTE = "/creating-a-doodle/"
PAGE_DIR = os.path.join("site", "creating-a-doodle")
PAGE_FILE = os.path.join(PAGE_DIR, "index.html")

# 承载单个导航/页脚入口的 <li> 块。
# 两个 class 分别对应 header 的链接栏与 footer 的站点链接列，块内不含嵌套 <li>，
# 所以非贪婪匹配到第一个 </li> 即为完整块。
BLOCK_RE = re.compile(
    r"[ \t\r\n]*<li class=\"(?:glue-header__item glue-header__item|"
    r"glue-footer__site-links-list-item)\">.*?</li>",
    re.DOTALL,
)

# 原站埋点里的「第 N 项 / 共 M 项」。
# 只在**确实摘掉过块的那个列表内部**重编号，绝不能全局替换：
#   · 首页的 hero-tag-carousel 用 1/3、2/3、3/3
#   · 详情页模板的 glue-jumplinks（页内跳转）也用 1/3、2/3、3/3
# 它们与导航毫无关系，全局替换会把这两组一起改坏。
POS_ANY = re.compile(r'data-g-template_position="\d+/\d+"')

# 源头模板：不一起改的话，下次构建会把入口重新写回 6375 个页面
SOURCE_FILES = [
    "_clone_search/doodle-slideshow.html",   # gen_doodle_pages.py 的骨架模板（最关键）
    "_clone_search/about-page.html",         # /about/
    "_clone_search/page.html",               # /search/
    "_clone_search/header.html",
    "_clone_search/footer.html",
    "_clone_search/doodle-pacman-game.html",
    "_google_clone/index_local.html",        # /
    "_google_clone/index.html",
]

# 该页面自身的抓取源料 _clone_search/creating-page.html **不在这里删除**。
# 它是这份页面的唯一副本（镜像已离线，原站无法重新抓取），且 build_site.py 的
# PAGES 映射一旦摘除它就不再参与任何构建。站点层面已经彻底移除，留一份惰性源料
# 零影响；需要清理时手动删掉即可。


def read_text(path):
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        return f.read()


def write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def strip_entries(text):
    """摘掉含 ROUTE 的导航/页脚 <li> 块，并给同列表内幸存的兄弟项重新编号。

    返回 (新文本, 摘掉的块数, 重编号的处数)。

    块必须**按列表分组**处理：同一个 <ul> 里的兄弟项之间只隔空白字符，据此把
    匹配到的块聚类，只有「组内确实摘掉过块」的组才重编号。这样 hero-tag-carousel
    与 glue-jumplinks 那类格式相同但语义无关的列表就完全不会被碰到。
    """
    matches = list(BLOCK_RE.finditer(text))
    if not matches:
        return text, 0, 0

    groups = [[matches[0]]]
    for m in matches[1:]:
        if text[groups[-1][-1].end():m.start()].strip() == "":
            groups[-1].append(m)              # 与上一块同属一个列表
        else:
            groups.append([m])

    edits = []
    removed = 0
    renum = 0

    for g in groups:
        doomed = [m for m in g if ROUTE in m.group(0)]
        if not doomed:
            continue                          # 该列表与导航无关，整组放过
        keep = [m for m in g if ROUTE not in m.group(0)]
        removed += len(doomed)

        for m in doomed:
            edits.append((m.start(), m.end(), ""))

        for i, m in enumerate(keep, start=1):
            body = m.group(0)
            if not POS_ANY.search(body):
                continue
            new_body = POS_ANY.sub(
                'data-g-template_position="%d/%d"' % (i, len(keep)), body, count=1)
            edits.append((m.start(), m.end(), new_body))
            renum += 1

    edits.sort(key=lambda e: e[0], reverse=True)
    out = text
    for s, e, rep in edits:
        out = out[:s] + rep + out[e:]

    return out, removed, renum


def collect_targets():
    targets = []
    for base, _dirs, names in os.walk("site"):
        for n in names:
            if not n.endswith(".html"):
                continue
            p = os.path.join(base, n)
            if os.path.normpath(p) == os.path.normpath(PAGE_FILE):
                continue            # 待删页面本身不处理，直接删目录
            targets.append(p)
    targets += [p for p in SOURCE_FILES if os.path.exists(p)]
    return sorted(targets)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="预演，只报告不落盘")
    args = ap.parse_args()

    targets = collect_targets()
    print(f"扫描 {len(targets)} 个文件（site/ 产物 + {len(SOURCE_FILES)} 个源头模板）\n")

    touched = 0
    blocks = 0
    renumbered = 0
    leftover = []

    for p in targets:
        text = read_text(p)
        if "creating-a-doodle" not in text:
            continue                      # 未命中，不读不写，省 IO
        new, b, r = strip_entries(text)
        if b == 0:
            leftover.append(p)            # 命中但没摘到块：需要人工看
            continue
        touched += 1
        blocks += b
        renumbered += r
        if not args.check:
            write_text(p, new)
        if ROUTE in new:
            leftover.append(p)

    print(f"  改动文件      : {touched}")
    print(f"  摘除入口块    : {blocks}")
    print(f"  收敛序号      : {renumbered}")

    if leftover:
        print(f"\n  仍需人工确认（{len(leftover)} 个）:")
        for p in leftover[:20]:
            print(f"    {p}")
        if len(leftover) > 20:
            print(f"    ...另 {len(leftover) - 20} 个")

    if args.check:
        print("\n[预演] 未写入任何文件。去掉 --check 即执行。")
        return

    removed_files = 0
    if os.path.isdir(PAGE_DIR):
        for base, _dirs, names in os.walk(PAGE_DIR):
            for n in names:
                os.remove(os.path.join(base, n))
                removed_files += 1
        for base, dirs, _names in os.walk(PAGE_DIR, topdown=False):
            for d in dirs:
                os.rmdir(os.path.join(base, d))
        os.rmdir(PAGE_DIR)
        print(f"\n  已删除路由目录 : {PAGE_DIR}（{removed_files} 个文件）")

    print("\n完成。启动预览：python serve.py")


if __name__ == "__main__":
    main()
