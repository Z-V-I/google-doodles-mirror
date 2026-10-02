#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
attribute_gstatic.py —— 归因实验：页面渲染时那次 www.gstatic.com 连接，是谁发的？

背景：
  拦截代理探测发现，10 个页面里有 8 个比 about:blank 基线**多出一次**
  CONNECT www.gstatic.com:443。可能是：
    (a) 浏览器自身的后台请求（组件/variations 更新）
    (b) 页面里 glue 的 cookie 通知条脚本里那句
        fetch("https://www.gstatic.com" + d + c)
  仅看主机名无法区分，必须做对照实验。

方法：
  起一个独立的小静态服务器（端口 8766），只提供两个极简页面：
    bare.html      —— 纯文字，不含任何脚本
    cookiebar.html —— 只加载 cookie 通知条的 CSS + JS（从真实镜像服务器取）
  各自重复 N 次，统计代理上 www.gstatic.com 的命中次数。
  若 cookiebar 组显著高于 bare 组 -> 是页面发的；两组相当 -> 是浏览器底噪。

用法：
    python _tools/attribute_gstatic.py --port 8765 --repeat 3
"""
import os
import re
import sys
import time
import argparse
import threading
import subprocess
import http.server
import socketserver
import urllib.request
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "_tools"))

import offline_probe as op          # 复用拦截代理与页面打开逻辑

PROBE_PORT = 8766
PAGE_DIR = os.path.join(ROOT, "_tools", "probe_pages")

BARE = """<!doctype html><html><head><meta charset="utf-8">
<title>bare</title></head><body><h1>bare page</h1>
<p>no scripts, no styles, nothing external</p></body></html>
"""

COOKIEBAR = """<!doctype html><html><head><meta charset="utf-8">
<title>cookiebar</title>
<link href="{base}/vendor/glue/cookienotificationbar/cookienotificationbar.min.css" rel="stylesheet">
</head><body><h1>cookiebar page</h1>
<script src="{base}/vendor/glue/cookienotificationbar/cookienotificationbar.min.js"></script>
</body></html>
"""


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=PAGE_DIR, **kw)

    def log_message(self, *a):
        pass


def start_page_server():
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", PROBE_PORT), QuietHandler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def host_of(t):
    m = re.match(r"^([\w.\-]+):\d+$", t)
    if m:
        return m.group(1).lower()
    m = re.match(r"^https?://([^/:]+)", t, re.I)
    return m.group(1).lower() if m else "(unknown)"


def run_group(proxy, chrome, url, name, repeat, outdir):
    hosts = Counter()
    sizes = []
    for i in range(repeat):
        proxy.reset()
        out = os.path.join(outdir, f"attr_{name}_{i}.png")
        ok, size = op.open_page(chrome, url, out, 500, 400,
                                profile=os.path.join(op.PROFILE_ROOT, f"attr-{name}"))
        sizes.append(size)
        for meth, target in proxy.hits:
            hosts[host_of(target)] += 1
    return hosts, sizes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--repeat", type=int, default=3)
    args = ap.parse_args()

    os.makedirs(PAGE_DIR, exist_ok=True)
    os.makedirs(os.path.join(ROOT, "_shots_offline"), exist_ok=True)
    base = f"http://127.0.0.1:{args.port}"

    with open(os.path.join(PAGE_DIR, "bare.html"), "w", encoding="utf-8") as f:
        f.write(BARE)
    with open(os.path.join(PAGE_DIR, "cookiebar.html"), "w", encoding="utf-8") as f:
        f.write(COOKIEBAR.format(base=base))

    start_page_server()
    proxy = op.LoggingProxy(("127.0.0.1", op.PROXY_PORT), op.ProxyHandler)
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    chrome = op.find_chrome()

    if not op.wait_server(args.port):
        sys.exit(f"镜像服务器未就绪：{base}/")

    outdir = os.path.join(ROOT, "_shots_offline")
    print(f"归因实验：每组各 {args.repeat} 次\n", flush=True)

    groups = [
        ("bare",      f"http://127.0.0.1:{PROBE_PORT}/bare.html"),
        ("cookiebar", f"http://127.0.0.1:{PROBE_PORT}/cookiebar.html"),
        ("real-home", base + "/"),
    ]
    result = {}
    for name, url in groups:
        hosts, sizes = run_group(proxy, chrome, url, name, args.repeat, outdir)
        result[name] = hosts
        print(f"{name:11s} 渲染 {sum(1 for s in sizes if s > 0)}/{args.repeat} 次成功"
              f"  主机命中: {dict(hosts)}", flush=True)

    print("\n" + "=" * 74)
    b = result["bare"]
    for name, _url in groups[1:]:
        h = result[name]
        g_delta = h.get("www.gstatic.com", 0) - b.get("www.gstatic.com", 0)
        print(f"{name:11s} 相对 bare：www.gstatic.com "
              f"{h.get('www.gstatic.com',0)} vs {b.get('www.gstatic.com',0)}"
              f"  → 差值 {g_delta:+d}（每组 {args.repeat} 次）")
    print("=" * 74)
    print("\n判读：cookiebar 组若明显高于 bare 组，说明是页面脚本发的；")
    print("      两者相当则说明是浏览器自身底噪。")
    proxy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
