#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
repair_prefix.py —— 消除早期 localize_all 在 games/ 与 storybooks/ 留下的路径前缀污染。

【成因】
build_site._norm_rel 早期版本缺少 `site/` 分支，相对路径直接落到兜底分支
`out = "/" + joined`，于是对 games/<slug>/ 下的 HTML 做重写时：

    原始值 "assets/pacman10-hp.2.png"
    -> 拼上文件所在目录 site/games/<slug>
    -> 兜底分支输出 "/site/games/<slug>/assets/pacman10-hp.2.png"

该字符串替换对「属性里的相对 URL」和「<meta content> 里的散文」一视同仁，
所以 <meta content="IE=Edge"> 也变成了 <meta content="/site/games/<slug>/IE=Edge">。

【为何可精确还原】
注入的前缀只是「文件所在目录的站内路径」的纯字面量拼接，没有任何歧义，
因此把 /site/<文件所在目录>/ 这一个字面前缀删掉，就逐字节还原成原始值：

    /site/games/<slug>/assets/x.png  ->  assets/x.png      （相对路径，语义不变）
    /site/games/<slug>/IE=Edge       ->  IE=Edge           （散文，语义不变）

【为什么不能靠重新生成】
games/ 与 storybooks/ 没有第二份干净副本（site/ 下同名文件是硬链接，共用 inode），
所以只能在现有文件上做这个可逆的字面量回滚。

用法：
    python _tools/repair_prefix.py            # 正式执行
    python _tools/repair_prefix.py --dry-run  # 只统计不写入
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

# 两份都要扫：根目录是原件，site/ 下是硬链接（同一 inode，扫到第二遍时已是 0 命中）
TARGET_DIRS = ["games", "storybooks", "site/games", "site/storybooks"]

TEXT_EXT = (".html", ".htm", ".js", ".css", ".json", ".txt", ".xml", ".svg")

DRY = "--dry-run" in sys.argv


def repair_file(path):
    """返回 (替换次数, 前缀)。前缀为空表示该目录下无需处理。

    两类注入前缀：
      a) /site/<文件所在目录>/<原值>  —— 绝大多数（相对 URL 与 meta 散文）
      b) /site/<站内根路径>           —— 形如 ../../index.html 这种跨目录相对链接，
                                        归一化后落在 site/ 根，前缀恰好是 /site/ 本身
    """
    rel_dir = os.path.dirname(path).replace("\\", "/")
    if not rel_dir:
        return 0, ""
    prefix = "/site/" + rel_dir + "/"

    with open(path, "r", encoding="utf-8", errors="surrogateescape", newline="") as f:
        text = f.read()

    n = text.count(prefix)
    rest = text.count("/site/") - n          # (b) 类：剩下的 /site/ 都是站点根引用

    if not n and not rest:
        return 0, prefix

    if not DRY:
        if n:
            text = text.replace(prefix, "")
        if rest:
            text = text.replace("/site/", "/")
        with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
            f.write(text)
    return n + rest, prefix


def main():
    files = 0
    hits = 0
    by_dir = {}
    for top in TARGET_DIRS:
        if not os.path.isdir(top):
            continue
        for dirpath, _dn, fns in os.walk(top):
            for fn in fns:
                if not fn.lower().endswith(TEXT_EXT):
                    continue
                p = os.path.join(dirpath, fn)
                files += 1
                try:
                    n, prefix = repair_file(p)
                except Exception as e:
                    print(f"  ✗ {p}: {str(e)[:90]}")
                    continue
                if n:
                    hits += n
                    key = prefix
                    by_dir[key] = by_dir.get(key, 0) + n

    mode = "（dry-run，未写入）" if DRY else ""
    print(f"[扫描] {files} 个文本文件，命中前缀 {hits} 处 {mode}")
    for k in sorted(by_dir, key=lambda x: -by_dir[x])[:10]:
        print(f"   {by_dir[k]:6d}  {k}")
    if len(by_dir) > 10:
        print(f"   ... 另有 {len(by_dir) - 10} 个目录前缀")


if __name__ == "__main__":
    main()
