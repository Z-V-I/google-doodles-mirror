#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""审计 doodles.google 克隆工程：找出所有未本地化的外部依赖与绝对路由。"""
import os
import re
import sys
import json
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCAN_DIRS = ["_google_clone", "_clone_search"]
EXTS = (".html", ".htm", ".css", ".js")

URL_RE = re.compile(r"""(?:https?:)?//[^\s"'<>()\\]+""")
ATTR_RE = re.compile(r"""(?:src|href|srcset|content|data-src|poster)\s*=\s*["']([^"']+)["']""", re.I)
ABS_PATH_RE = re.compile(r"""(?:src|href)\s*=\s*["'](/[^"']*)["']""", re.I)

GOOGLE_HOSTS = re.compile(
    r"(google|gstatic|googleapis|youtube|ytimg|ggpht|googleusercontent|googleplex|googlevideo|withgoogle)\.", re.I
)

def iter_files():
    for d in SCAN_DIRS:
        base = os.path.join(ROOT, d)
        if not os.path.isdir(base):
            continue
        for dirpath, _dirnames, filenames in os.walk(base):
            for fn in filenames:
                if fn.lower().endswith(EXTS):
                    yield os.path.join(dirpath, fn)

def main():
    host_counter = Counter()
    google_urls = Counter()
    abs_paths = Counter()
    per_file = defaultdict(list)

    for path in iter_files():
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except Exception as e:
            print("READ FAIL", path, e)
            continue
        rel = os.path.relpath(path, ROOT)

        for m in URL_RE.finditer(text):
            u = m.group(0).rstrip(").,;'\"")
            host = re.sub(r"^(https?:)?//", "", u).split("/")[0].lower()
            if not host:
                continue
            host_counter[host] += 1
            if GOOGLE_HOSTS.search(host + "."):
                google_urls[u] += 1
                per_file[rel].append(u)

        for m in ABS_PATH_RE.finditer(text):
            p = m.group(1)
            if p.startswith("//"):
                continue
            abs_paths[p] += 1

    print("=" * 70)
    print("【1】外部 host 出现次数 TOP")
    print("=" * 70)
    for h, c in host_counter.most_common(40):
        flag = "  <<< GOOGLE依赖" if GOOGLE_HOSTS.search(h + ".") else ""
        print(f"{c:6d}  {h}{flag}")

    print()
    print("=" * 70)
    print(f"【2】谷歌域名下唯一 URL 数: {len(google_urls)}")
    print("=" * 70)
    by_host = defaultdict(list)
    for u in google_urls:
        host = re.sub(r"^(https?:)?//", "", u).split("/")[0].lower()
        by_host[host].append(u)
    for host, urls in sorted(by_host.items(), key=lambda kv: -len(kv[1])):
        print(f"\n--- {host}  ({len(urls)} 个) ---")
        for u in sorted(urls)[:25]:
            print("   ", u[:150])
        if len(urls) > 25:
            print(f"    ... 另外 {len(urls)-25} 个")

    print()
    print("=" * 70)
    print(f"【3】站内绝对路径引用: {len(abs_paths)} 种")
    print("=" * 70)
    for p, c in abs_paths.most_common(60):
        print(f"{c:6d}  {p}")

    with open(os.path.join(ROOT, "_tools", "audit_result.json"), "w", encoding="utf-8") as f:
        json.dump({
            "hosts": host_counter.most_common(),
            "google_urls": sorted(google_urls),
            "abs_paths": abs_paths.most_common(),
            "per_file": {k: sorted(set(v)) for k, v in per_file.items()},
        }, f, ensure_ascii=False, indent=2)
    print("\n已写出 _tools/audit_result.json")

if __name__ == "__main__":
    main()
