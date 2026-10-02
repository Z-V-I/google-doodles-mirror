#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
shoot.py —— 用本机 Chrome 无头模式对离线镜像逐页截图，用于人工核对还原度。

用法：
    python _tools/shoot.py --port 8765 [--out _shots]
"""
import os
import sys
import time
import argparse
import subprocess
import urllib.request
from urllib.parse import urlencode

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

# 每页一个独立 profile 目录，避免多实例争抢同一 --user-data-dir
PROFILE_ROOT = os.path.join(ROOT, "_tools", ".chrome-profiles")


def _rmtree_quiet(path):
    """尽力清理 profile 目录。

    注意：本机沙箱会用「移到回收站」来接管 rmtree，而对这类 Chrome 目录常常
    trash 失败并**只删掉一部分**，留下一个残缺 profile（下一页启动即秒退）。
    因此失败就原样保留、复用，绝不让它删到一半。
    """
    import shutil
    if not os.path.isdir(path):
        return
    try:
        shutil.rmtree(path)
    except OSError:
        pass


CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

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
    """杀掉整棵进程树。只 kill 顶层进程时，Chrome 的 renderer 孙进程会存活并继续占用
    profile 锁，导致下一页启动时被「转交 URL 后立即退出」。"""
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                   capture_output=True)


def shoot(chrome, url, out, w, h, budget=9000, hard_timeout=60, settle=8.0,
          profile=None):
    """截一页。返回 (是否成功, 说明文本)。

    【为什么不用 subprocess.run】
    run() 配合 capture_output=True 时，超时后它仍要读完管道才返回；而 Chrome 会派生出
    多个 renderer 孙进程，这些孙进程继承了管道写端且不会随直接子进程一起被回收，
    于是读取线程永久阻塞 —— timeout 形同虚设，整条批量任务就此卡死（本机实测卡了 20 分钟）。
    改用 Popen + DEVNULL 从根上避开管道，超时后再自己清理进程树。

    【为什么每页要独立 profile】
    多页共用一个 --user-data-dir 时，若前一页的浏览器进程没有真正退干净，
    下一页会「把 URL 转交给已有实例后立刻退出」，既不截图也不报错（表现为 5s 内无产物）。
    每页一个独立 profile 目录可以彻底规避锁竞争。

    【为什么以「产物文件」而非「退出码」判成功】
    页面里有动画/定时器时，Chrome 常常「截图已落盘，进程却不退出」。
    所以判据是：产物出现且大小稳定。
    """
    args = [
        chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
        "--hide-scrollbars", "--force-device-scale-factor=1",
        "--disable-extensions", "--disable-background-networking",
        "--no-first-run", "--disable-sync", "--disable-component-update",
        "--user-data-dir=" + (profile or os.path.join(PROFILE_ROOT, "default")),
        f"--window-size={w},{h}",
        f"--virtual-time-budget={budget}",
        f"--screenshot={out}",
        url,
    ]
    if os.path.exists(out):
        os.remove(out)          # 保证「产物存在」等价于「本次确实截到了图」

    p = subprocess.Popen(args, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t0 = time.time()
    try:
        while time.time() - t0 < hard_timeout:
            if os.path.isfile(out) and os.path.getsize(out) > 0:
                break
            if p.poll() is not None:      # Chrome 自己退了但没出图 -> 失败
                break
            time.sleep(0.3)

        if os.path.isfile(out) and os.path.getsize(out) > 0:
            # 等文件大小稳定，避免截到半张图
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

    if os.path.isfile(out) and os.path.getsize(out) > 0:
        return True, ""
    return False, f"{time.time() - t0:.0f}s 内未产出截图"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--out", default="_shots")
    ap.add_argument("--only", default="", help="只截取名称包含该串的页面")
    ap.add_argument("--retry", type=int, default=1, help="失败页面的重试次数")
    args = ap.parse_args()

    chrome = find_chrome()
    outdir = os.path.join(ROOT, args.out)
    os.makedirs(outdir, exist_ok=True)

    if not wait_server(args.port):
        sys.exit(f"服务器未就绪：http://127.0.0.1:{args.port}/")
    print(f"服务器就绪，开始截图 -> {outdir}\n", flush=True)

    ok = 0
    for name, path, w, h in PAGES:
        if args.only and args.only not in name:
            continue
        url = f"http://127.0.0.1:{args.port}{path}"
        out = os.path.join(outdir, name + ".png")
        profile = os.path.join(PROFILE_ROOT, name)
        good, why = False, ""
        for attempt in range(args.retry + 1):
            try:
                good, why = shoot(chrome, url, out, w, h, profile=profile)
            except Exception as e:
                good, why = False, f"异常 {e}"
            if good:
                break
            if attempt < args.retry:
                print(f"  … {name:18s} 第 {attempt + 1} 次未成功，重试", flush=True)
                time.sleep(1.0)
        if good:
            kb = os.path.getsize(out) // 1024
            print(f"  ✓ {name:18s} {kb:5d} KB   {url}", flush=True)
            ok += 1
        else:
            print(f"  ✗ {name:18s} 失败：{why}", flush=True)

    print(f"\n完成 {ok} 张", flush=True)


if __name__ == "__main__":
    main()
