#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_offline.py —— 校验 site/ 是否已彻底脱离谷歌生态。

判定口径：
  * 阻断型依赖（会导致渲染失败或发起外部请求）：<script src>、<link href>、
    <img src>、<iframe src>、CSS 里的 url()  —— 出现谷歌域名即「不合格」
  * 普通外链（<a href>）：不影响本地渲染，仅作提示
"""
import os
import re
import json
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

GOOGLE_HOST = re.compile(
    r"(?:^|\.)(google|gstatic|googleapis|youtube|ytimg|ggpht|googleusercontent"
    r"|googleplex|withgoogle|appspot|googlevideo|gvt1|doubleclick)\.", re.I)

BLOCKING_ATTR = re.compile(
    r'<(script|link|img|iframe|source|video|audio|embed|object)\b[^>]*?'
    r'\b(src|href|data-src|poster)\s*=\s*["\']([^"\']+)["\']', re.I | re.S)

ANY_ATTR = re.compile(r'\b(?:src|href|data-src|poster|content)\s*=\s*["\']([^"\']+)["\']', re.I)
CSS_URL = re.compile(r'url\(\s*["\']?([^)"\']+)["\']?\s*\)', re.I)
BARE = re.compile(r'https?://[^\s"\'<>\\)]+', re.I)

# offline_links.py 会把谷歌系 <a href> 换成 /offline/?to=<百分号编码的原地址>。
# 那个「原地址」是被刻意保留下来的（提示页要显示给用户看），不是活跃依赖，
# 因此扫描前先把它抹平，否则校验会把已接管的链接重复计成「外链」。
OFFLINE_TAKEOVER = re.compile(r'/offline/\?to=[^\s"\'&<>]*', re.I)

# 原站给每个可点元素挂了 data-g-cta_url 之类的埋点属性，值同样是谷歌站点地址。
# 它不是 href，浏览器不会据此跳转；本地 GTM 又是空实现，因此不构成任何依赖。
# 扫描前先摘掉，避免把「埋点痕迹」误报成「仍指向谷歌的普通链接」。
ANALYTICS_ATTR = re.compile(r'\bdata-g-[\w-]*\b\s*=\s*["\'][^"\']*["\']', re.I)


def host_of(u):
    u = re.sub(r"^(https?:)?//", "", u.strip())
    return u.split("/")[0].split("?")[0].lower()


def is_google(u):
    h = host_of(u)
    return bool(GOOGLE_HOST.search("." + h)) if h else False


def main():
    targets = []
    for dirpath, _dn, fns in os.walk("site"):
        if os.sep + "vendor" in dirpath or os.sep + "assets" in dirpath:
            continue
        for fn in fns:
            if fn.lower().endswith((".html", ".htm", ".css", ".js")):
                targets.append(os.path.join(dirpath, fn))

    blocking = defaultdict(set)
    anchors = defaultdict(set)
    total_deps = 0

    for p in targets:
        text = open(p, encoding="utf-8", errors="replace").read()
        rel = os.path.relpath(p, "site")
        text = OFFLINE_TAKEOVER.sub("/offline/?to=…", text)
        text = ANALYTICS_ATTR.sub("", text)

        for m in BLOCKING_ATTR.finditer(text):
            u = m.group(3)
            if is_google(u):
                blocking[rel].add(u)
        for m in CSS_URL.finditer(text):
            u = m.group(1)
            if is_google(u):
                blocking[rel].add(u)
        for m in BARE.finditer(text):
            u = m.group(0)
            if not is_google(u):
                continue
            total_deps += 1
            # 已在阻断集里的跳过
            if any(u in s for s in blocking.get(rel, ())):
                continue
            anchors[rel].add(u)

    n_block = sum(len(v) for v in blocking.values())
    print("=" * 72)
    print(f"扫描文件        : {len(targets)}")
    print(f"阻断型谷歌依赖  : {n_block}  <-- 必须为 0")
    print(f"非阻断外链      : {sum(len(v) for v in anchors.values())}")
    print("=" * 72)

    if blocking:
        print("\n【不合格】仍存在阻断型谷歌依赖：")
        c = Counter()
        for f, urls in blocking.items():
            for u in urls:
                c[host_of(u)] += 1
            print(f"\n  {f}")
            for u in sorted(urls)[:5]:
                print(f"     {u[:130]}")
            if len(urls) > 5:
                print(f"     ... 另外 {len(urls)-5} 条")
        print("\n  按主机统计:", dict(c))
    else:
        print("\n✅ 阻断型谷歌依赖为 0 —— 镜像可完全离线渲染。")

    if anchors:
        print("\n【提示】仍指向谷歌站点的普通链接（不影响离线渲染，仅点击会跳外网）：")
        c = Counter()
        for f, urls in anchors.items():
            for u in urls:
                c[host_of(u)] += 1
        for h, n in c.most_common(20):
            print(f"   {n:5d}  {h}")

    json.dump({"blocking": {k: sorted(v) for k, v in blocking.items()},
               "anchors": {k: sorted(v) for k, v in anchors.items()}},
              open("_tools/verify_result.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print("\n明细 -> _tools/verify_result.json")
    return 1 if blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())
