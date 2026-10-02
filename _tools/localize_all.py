#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
localize_all.py —— 对 site/ 下「由脚本生成」的 HTML 做统一的离线化重写。

复用 build_site.py 里的 map_url / rewrite_text，保证与四个主路由页一致的规则。
实际覆盖：site/index.html、site/{search,about,creating-a-doodle}/index.html、
以及 6375 个 site/doodle/<slug>/index.html。

⚠ 不处理的目录（见 SKIP_TOP）：
  site/{games,storybooks,img,fonts,vendor} 是「整目录硬链接」到工程根下的原始素材，
  内容本来就离线自洽。重写它们既产生噪音，又会因为共用 inode 而改坏根目录的原件 ——
  而那些原件没有第二份干净副本。
"""
import os
import sys
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "_tools"))

import build_site as B  # noqa: E402

# 顶层目录白名单（跳过）：整目录硬链接的原始素材 + 由专门脚本生成的目录
SKIP_TOP = {
    "vendor",       # 第三方桩件与 glue/charts，已是本地实现
    "games",        # 硬链接 -> 根目录 games/
    "storybooks",   # 硬链接 -> 根目录 storybooks/
    "img",          # 硬链接 -> 根目录涂鸦图片（二进制）
    "fonts",        # 硬链接 -> 根目录 fonts/（二进制）
    "assets",       # build_site 生成的占位/下载资源，内含 HTML 也只是占位
    "offline",      # offline_links.py 生成的提示页，其 ?to= 里含 URL 编码，不能再被改写
}


def main():
    # 复用构建阶段的 URL 映射表
    man_path = "_tools/build_manifest.json"
    if os.path.exists(man_path):
        man = json.load(open(man_path, encoding="utf-8"))
    else:
        man = B.manifest

    B.LOCAL_MAP = {}
    for url, lp in man.get("downloaded", {}).items():
        B.LOCAL_MAP[url.rstrip("/")] = lp
    for url, lp in man.get("placeholder", {}).items():
        B.LOCAL_MAP[url.rstrip("/")] = lp

    data = json.loads(open("doodles_data.js", encoding="utf-8")
                      .read().split("[", 1)[1].rsplit("]", 1)[0].join(["[", "]"]))
    B.SLUGS.update(r["slug"] for r in data)

    targets = []
    for dirpath, _dn, fns in os.walk("site"):
        # 这些目录是「整目录硬链接」到工程根下的原始素材（games/ storybooks/ img/ fonts/），
        # 里面的 URL 本来就是站内相对路径、已经离线自洽，不需要也不应该再重写：
        #   1) 重写会把相对路径展开成站内绝对路径，纯属噪音；
        #   2) 硬链接意味着写入会同时改掉根目录的原件，一旦规则有 bug 就连回滚源都没了。
        top = os.path.relpath(dirpath, "site").split(os.sep)[0]
        if top in SKIP_TOP:
            continue
        for fn in fns:
            if fn.lower().endswith((".html", ".htm")):
                targets.append(os.path.join(dirpath, fn))

    print(f"待处理 HTML: {len(targets)} 个")
    changed = 0
    stats_all = {}
    for i, p in enumerate(targets):
        src_dir = os.path.dirname(p)
        text = open(p, encoding="utf-8", errors="replace").read()
        new, stats = B.rewrite_text(text, src_dir)
        if new != text:
            open(p, "w", encoding="utf-8", newline="").write(new)
            changed += 1
            stats_all[p] = stats
        if (i + 1) % 1000 == 0:
            print(f"  {i+1}/{len(targets)}")

    print(f"已重写 {changed} 个文件")
    json.dump(stats_all, open("_tools/localize_stats.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
