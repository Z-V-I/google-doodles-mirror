#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
offline_probe.py —— 用「拦截代理」证明页面渲染期零外网请求。

原理：
  给 Chrome 设 --proxy-server=127.0.0.1:<port>，指向本脚本内的一个「只记录、只返 502」
  的代理。Chrome 默认对 localhost / 127.0.0.1 绕过代理，所以：
      · 页面与它自己的本地资源  -> 直连本地服务器，正常加载
      · 任何指向外网的请求      -> 一定落到代理上，被记录并失败
  于是「代理日志为空」就等价于「这次页面加载没有碰过外网」。

  再叠加 --host-resolver-rules="MAP * ~NOTFOUND, EXCLUDE 127.0.0.1"（DNS 层兜底），
  两者同时拦截，避免漏网。

严谨性处理：
  Chrome 自身会有极少量后台请求（组件的、variations 的）。先跑一次 about:blank
  取「底噪」，再跑各路由并减除，避免把浏览器噪声算成页面依赖。

用法：
    python _tools/offline_probe.py                # 默认 http://127.0.0.1:8765
    python _tools/offline_probe.py --port 8765 --shots _shots_offline
"""
import os
import re
import sys
import json
import time
import shutil
import argparse
import threading
import subprocess
import http.server
import socketserver
import urllib.request
from collections import Counter
from urllib.parse import urlencode

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

PROXY_PORT = 18912
PROFILE_ROOT = os.path.join(ROOT, "_tools", ".chrome-profiles-offline")

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

# 与 shoot.py 同一批页面，便于逐张比对
PAGES = [
    ("01-home",          "/",                                  1440, 2400),
    ("02-search",        "/search/",                           1440, 2200),
    ("03-search-pac",    "/search/?" + urlencode({"title_like": "pac"}), 1440, 2000),
    ("04-search-topic",  "/search/?" + urlencode({"topic_tags": "Birthday"}), 1440, 2000),
    ("05-about",         "/about/",                            1440, 2600),
    ("07-doodle-still",  "/doodle/celebrating-the-staurikosaurus/", 1440, 1900),
    ("08-doodle-game",   "/doodle/30th-anniversary-of-pac-man/",    1440, 1700),
    ("09-doodle-mobile", "/doodle/fourth-of-july-2022/",       420,  1500),
    ("10-game-raw",      "/games/30th-anniversary-of-pac-man/pacman10-hp.html", 1000, 900),
]

# 体积过小基本等于白屏（1x1 或纯色）
MIN_BYTES = 9_000


class LoggingProxy(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.hits = []          # [(method, target)]
        self.lock = threading.Lock()
        self.enabled = True     # 采集窗口开关

    def log_hit(self, method, target):
        if not self.enabled:
            return
        with self.lock:
            self.hits.append((method, target))

    def reset(self):
        with self.lock:
            self.hits.clear()


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _record(self):
        # CONNECT 的目标形如 host:443；普通请求是完整 URL
        target = self.path
        self.server.log_hit(self.command, target)

    def _refuse(self):
        body = b"blocked by offline probe"
        self.send_response(502)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def do_CONNECT(self):
        self._record()
        self._refuse()

    def do_GET(self):
        self._record()
        self._refuse()

    def do_POST(self):
        self._record()
        self._refuse()

    def do_HEAD(self):
        self._record()
        self._refuse()

    def log_message(self, *a):
        pass


def find_chrome():
    for p in CHROME_CANDIDATES:
        if os.path.isfile(p):
            return p
    sys.exit("找不到 Chrome / Edge")


def wait_server(port, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2).read(64)
            return True
        except Exception:
            time.sleep(0.4)
    return False


def _kill_tree(pid):
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)


def open_page(chrome, url, out, w, h, budget=9000, hard_timeout=60, settle=8.0,
              profile=None):
    """在「外网全断」的环境下打开一个页面，产出截图。判成功依据是产物文件。"""
    args = [
        chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
        "--hide-scrollbars", "--force-device-scale-factor=1",
        "--disable-extensions", "--disable-background-networking",
        "--no-first-run", "--disable-sync", "--disable-component-update",
        # ↓ 双保险：所有出口走拦截代理，且 DNS 层直接判不存在
        f"--proxy-server=127.0.0.1:{PROXY_PORT}",
        '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1',
        "--user-data-dir=" + profile,
        f"--window-size={w},{h}",
        f"--virtual-time-budget={budget}",
        f"--screenshot={out}",
        url,
    ]
    if os.path.exists(out):
        os.remove(out)

    p = subprocess.Popen(args, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t0 = time.time()
    try:
        while time.time() - t0 < hard_timeout:
            if os.path.isfile(out) and os.path.getsize(out) > 0:
                break
            if p.poll() is not None:
                break
            time.sleep(0.3)
        if os.path.isfile(out) and os.path.getsize(out) > 0:
            t1 = time.time()
            prev = -1
            while time.time() - t1 < settle:
                cur = os.path.getsize(out)
                if cur == prev and cur > 0:
                    break
                prev = cur
                time.sleep(0.3)
    finally:
        if p.poll() is None:
            _kill_tree(p.pid)
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

    ok = os.path.isfile(out) and os.path.getsize(out) > 0
    return ok, (os.path.getsize(out) if ok else 0)



def host_of(t):
    m = re.match(r"^([\w.\-]+):\d+$", t)
    if m:
        return m.group(1).lower()
    m = re.match(r"^https?://([^/:]+)", t, re.I)
    return m.group(1).lower() if m else "(unknown)"


# 浏览器自身的后台请求（与页面内容无关）。注意：只用来**解释**增量，
# 不用来屏蔽增量 —— 任何超出基线的量都必须显式列出来由。
BROWSER_HOSTS = ("clients2.google.com", "update.googleapis.com",
                 "accounts.google.com", "www.google.com", "www.gstatic.com")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--shots", default="_shots_offline")
    args = ap.parse_args()

    chrome = find_chrome()
    outdir = os.path.join(ROOT, args.shots)
    os.makedirs(outdir, exist_ok=True)
    if not wait_server(args.port):
        sys.exit(f"本地服务器未就绪：http://127.0.0.1:{args.port}/")

    # 注意：这里**绝不能**按进程名批量杀 chrome.exe —— 用户自己的浏览器可能正在跑。
    # 每页使用独立 profile 目录，天然不存在锁竞争，无需任何预先清理。

    proxy = LoggingProxy(("127.0.0.1", PROXY_PORT), ProxyHandler)
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    print(f"拦截代理已启动 127.0.0.1:{PROXY_PORT}（只记录 + 返 502）\n", flush=True)

    base = f"http://127.0.0.1:{args.port}"

    # ---------- 基线：about:blank（取浏览器自身底噪，按主机计数） ----------
    proxy.reset()
    ok, _ = open_page(chrome, "about:blank",
                      os.path.join(outdir, "_blank.png"), 400, 300,
                      profile=os.path.join(PROFILE_ROOT, "_blank"))
    noise_raw = list(proxy.hits)
    baseline = Counter(host_of(t) for _m, t in noise_raw)
    print("─" * 78)
    print(f"基线（about:blank）代理命中 {len(noise_raw)} 次，按主机：{dict(baseline)}")
    print("  说明：这些是浏览器自身的后台请求，与页面内容无关。")
    print("─" * 78)

    results = []
    total_page_external = 0
    for name, path, w, h in PAGES:
        url = base + path
        out = os.path.join(outdir, name + ".png")
        proxy.reset()
        ok, size = open_page(chrome, url, out, w, h,
                             profile=os.path.join(PROFILE_ROOT, name))
        hits = list(proxy.hits)
        counts = Counter(host_of(t) for _m, t in hits)
        # 关键：逐主机做「本页 - 基线」，任何正增量都要解释
        delta = {h: counts[h] - baseline.get(h, 0)
                 for h in counts if counts[h] - baseline.get(h, 0) > 0}
        page_external = {h: v for h, v in delta.items()}
        total_page_external += sum(page_external.values())
        results.append({"name": name, "url": url, "ok": ok, "bytes": size,
                        "counts": dict(counts), "delta_over_baseline": delta})
        flag = "OK " if (ok and size >= MIN_BYTES and not page_external) else "!! "
        print(f"{flag}{name:18s} {size/1024:8.0f} KB  超出基线的请求 {sum(page_external.values()):3d}"
              f"  {page_external if page_external else ''}", flush=True)

    # ---------- 汇总 ----------
    print("\n" + "=" * 78)
    ok_pages = [r for r in results if r["ok"] and r["bytes"] >= MIN_BYTES]
    blank = [r for r in results if not r["ok"] or r["bytes"] < MIN_BYTES]
    print(f"页面渲染成功          : {len(ok_pages)}/{len(results)}"
          f"（阈值 {MIN_BYTES/1024:.0f} KB，低于此视为白屏）")
    print(f"渲染期超出基线的请求数: {total_page_external}")
    print("=" * 78)

    if blank:
        print("\n以下页面未正常渲染：")
        for r in blank:
            print(f"  · {r['name']}  {r['bytes']} B  {r['url']}")

    if total_page_external:
        print("\n以下请求超出了浏览器自身底噪，说明是页面发出的（需要改造）：")
        for r in results:
            for h, v in r["delta_over_baseline"].items():
                print(f"  · [{r['name']}] {h}  ×{v}")
    else:
        print("\n✅ 全部页面在「外网全断」的条件下正常渲染，")
        print("   且没有任何超出浏览器自身底噪的请求 —— 页面渲染期零外链。")

    with open(os.path.join(ROOT, "_tools", "offline_probe_result.json"),
              "w", encoding="utf-8") as f:
        json.dump({"baseline": dict(baseline), "baseline_raw": noise_raw,
                   "pages": results}, f, ensure_ascii=False, indent=2)

    proxy.shutdown()
    return 0 if (not blank and total_page_external == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
