#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
repair_logos.py —— 把互动涂鸦里残留的 /logos/... 绝对路径改回本地 assets/ 相对路径。

【背景】
games/ 与 storybooks/ 里的互动涂鸦是从谷歌 /logos/ 目录整站抓下来的。抓取工具
把「HTML 里的相对引用」一并落盘成 assets/，但 JS 内部用绝对路径写死的那部分
（例如 a.button.src="/logos/2011/henson11-hp-bg.jpg"）原样保留了。

本地服务器以 site/ 为站点根，/logos/... 必然 404，于是这些互动涂鸦会出现
背景、精灵图、音效、延迟加载脚本缺失。这类引用共约 1 万处，是互动模块
还原度的主要缺口。

【如何对齐】
谷歌的 /logos/ 路径层级很深（/logos/2020/mothersday20/r8/paper.png），而本地
assets/ 是扁平的（assets/r8/paper.png 或 assets/paper.png）。因此按「后缀匹配」
解析：从 /logos/ 之后的最长后缀开始逐级缩短，命中本地 assets/ 里的实际文件即采用。

【安全性】
* 只改 games/<slug>/ 与 storybooks/<slug>/ 中「确实能在同级 assets/ 找到同名文件」
  的引用；找不到的保持原样，绝不臆造路径。
* 默认 dry-run，必须显式加 --apply 才写盘。

用法：
    python _tools/repair_logos.py            # 预演，只统计
    python _tools/repair_logos.py --apply    # 实际写入
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

ROOTS = ["games", "storybooks"]
TEXT_EXT = (".html", ".htm", ".js", ".css", ".json", ".txt", ".xml", ".svg")

# /logos/ 之后的路径允许的字符集（不含引号、括号、空白，避免吃到 JS 语法）
LOGOS_RE = re.compile(r'/logos/[A-Za-z0-9_@./%+=~$-]+')

APPLY = "--apply" in sys.argv


def asset_index(unit_dir):
    """收集 unit 的 assets/ 下所有文件的相对路径。"""
    assets = os.path.join(unit_dir, "assets")
    idx = set()
    if not os.path.isdir(assets):
        return idx
    for dp, _dn, fns in os.walk(assets):
        for fn in fns:
            rel = os.path.relpath(os.path.join(dp, fn), assets).replace("\\", "/")
            idx.add(rel)
    return idx


def resolve(logos_path, idx):
    """把 /logos/a/b/c.png 解析成 idx 里的某项（最长后缀优先）。"""
    tail = logos_path[len("/logos/"):].strip("/")
    if not tail:
        return None
    segs = tail.split("/")
    for k in range(1, len(segs) + 1):
        cand = "/".join(segs[-k:])
        if cand in idx:
            return cand
    return None


def process_unit(unit_dir):
    idx = asset_index(unit_dir)
    if not idx:
        return 0, 0, {}

    units_dir = os.path.dirname(unit_dir)
    resolved = 0
    unresolved = 0
    miss = {}

    for dp, _dn, fns in os.walk(unit_dir):
        for fn in fns:
            if not fn.lower().endswith(TEXT_EXT):
                continue
            p = os.path.join(dp, fn)
            try:
                with open(p, "r", encoding="utf-8", errors="surrogateescape", newline="") as f:
                    text = f.read()
            except Exception:
                continue
            if "/logos/" not in text:
                continue

            hits = [0, 0]

            def repl(m):
                lp = m.group(0)
                rel = resolve(lp, idx)
                if rel is None:
                    hits[1] = hits[1] + 1
                    miss[os.path.basename(lp)] = miss.get(os.path.basename(lp), 0) + 1
                    return lp
                hits[0] += 1
                target = os.path.relpath(os.path.join(unit_dir, "assets", rel), dp)
                return target.replace("\\", "/")

            new = LOGOS_RE.sub(repl, text)
            resolved += hits[0]
            unresolved += hits[1]

            if APPLY and new != text:
                with open(p, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
                    f.write(new)

    _ = units_dir
    return resolved, unresolved, miss


def main():
    total_ok = total_miss = units = 0
    all_miss = {}
    for root in ROOTS:
        if not os.path.isdir(root):
            continue
        for slug in sorted(os.listdir(root)):
            unit_dir = os.path.join(root, slug)
            if not os.path.isdir(unit_dir):
                continue
            ok, bad, miss = process_unit(unit_dir)
            if ok or bad:
                units += 1
                total_ok += ok
                total_miss += bad
                for k, v in miss.items():
                    all_miss[k] = all_miss.get(k, 0) + v

    mode = "已写入" if APPLY else "预演（未写入，加 --apply 生效）"
    print("=" * 68)
    print(f"{mode}")
    print(f"涉及目录      : {units}")
    print(f"可本地对齐    : {total_ok}   <-- 改回 assets/ 相对路径")
    print(f"本地缺失      : {total_miss}   <-- 保持原样，不臆造")
    print("=" * 68)
    if all_miss:
        print("\n本地确实没有的资源（按文件名去重，前 25 个）：")
        for k in sorted(all_miss, key=lambda x: -all_miss[x])[:25]:
            print(f"   {all_miss[k]:5d}  {k}")


if __name__ == "__main__":
    main()
