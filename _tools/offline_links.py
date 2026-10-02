#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
offline_links.py —— 收尾一步：把「指向谷歌系站点的普通链接」改为站内提示页。

此前校验已确认「阻断型」谷歌依赖（script/link/img/iframe/字体）为 0，
页面已经完全能离线渲染。但页脚里的 Privacy / Terms / Help / Google 等
<a> 链接仍指向真实谷歌站点，演示时一点就跳出镜像。

本步只为这些链接生成一个站内落地页 /offline/，保持链接文字与外观完全不变，
仅把目标换成提示页，让整个镜像在点击层面也自洽。
"""
import os
import re
import sys
import html
import urllib.parse
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
SITE = "site"

# 需要接管链接的谷歌系主机（精确或后缀匹配）
GOOGLE_HOSTS = (
    "google.com", "about.google", "youtube.com", "youtu.be",
    "google.dev", "goo.gl", "withgoogle.com", "googleblog.com",
)

ANCHOR_RE = re.compile(r'(<a\b[^>]*?\bhref=")(https?://([^"/]+)(?:/[^"]*)?)(")', re.I)


def is_google_host(host):
    host = (host or "").lower().split(":")[0]
    return any(host == h or host.endswith("." + h) for h in GOOGLE_HOSTS)


NOTICE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>外部链接 - Google Doodles 离线镜像</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
    background: #f8f9fa; padding: 24px;
    font-family: "Google Sans", "Product Sans", Roboto, "Segoe UI", "PingFang SC",
                 "Microsoft YaHei", Arial, sans-serif;
    color: #202124;
  }}
  .card {{
    max-width: 620px; width: 100%; background: #fff; border: 1px solid #dadce0;
    border-radius: 16px; padding: 40px 36px; box-shadow: 0 1px 3px rgba(60,64,67,.12);
  }}
  .logo {{ font-size: 26px; font-weight: 500; letter-spacing: -.5px; margin-bottom: 26px; }}
  .logo span:nth-child(1) {{ color: #4285f4; }}
  .logo span:nth-child(2) {{ color: #ea4335; }}
  .logo span:nth-child(3) {{ color: #fbbc05; }}
  .logo span:nth-child(4) {{ color: #4285f4; }}
  .logo span:nth-child(5) {{ color: #34a853; }}
  .logo span:nth-child(6) {{ color: #ea4335; }}
  .logo em {{ font-style: normal; color: #5f6368; font-weight: 400; margin-left: 10px; font-size: 17px; }}
  h1 {{ font-size: 24px; font-weight: 500; margin: 0 0 14px; }}
  p {{ font-size: 15px; line-height: 1.7; color: #5f6368; margin: 0 0 14px; }}
  .target {{
    margin: 22px 0 26px; padding: 14px 16px; border-radius: 10px;
    background: #f1f3f4; border: 1px solid #e0e0e0;
    font-family: ui-monospace, Consolas, "Courier New", monospace;
    font-size: 12.5px; color: #3c4043; word-break: break-all; line-height: 1.6;
  }}
  .actions {{ display: flex; gap: 12px; flex-wrap: wrap; }}
  a.btn {{
    display: inline-block; padding: 11px 22px; border-radius: 100px;
    text-decoration: none; font-size: 14.5px; font-weight: 500;
  }}
  a.primary {{ background: #1a73e8; color: #fff; }}
  a.primary:hover {{ background: #1765cc; }}
  a.ghost {{ color: #1a73e8; border: 1px solid #dadce0; }}
  a.ghost:hover {{ background: #f8f9fa; }}
  .foot {{ margin-top: 26px; padding-top: 18px; border-top: 1px solid #e8eaed;
           font-size: 12.5px; color: #80868b; }}
</style>
</head>
<body>
  <div class="card">
    <div class="logo">
      <span>G</span><span>o</span><span>o</span><span>g</span><span>l</span><span>e</span>
      <em>Doodles 离线镜像</em>
    </div>
    <h1>这个链接指向谷歌的在线服务</h1>
    <p>当前页面运行在完全离线的本地镜像中，不会访问任何谷歌服务器，
       因此这个链接无法继续跳转。</p>
    <div class="target" id="target"></div>
    <div class="actions">
      <a class="primary" href="javascript:history.length>1?history.back():location.replace('/')">返回上一页</a>
      <a class="ghost" href="/">回到 Doodles 首页</a>
      <a class="ghost" href="/search/">浏览全部 Doodles</a>
    </div>
    <div class="foot">
      离线镜像 · 仅供学习与研究 · 链接文字与版式均保持与原站一致，仅跳转目标改为本页。
    </div>
  </div>
<script>
  (function () {{
    var q = new URLSearchParams(location.search).get('to') || '';
    var el = document.getElementById('target');
    el.textContent = q ? decodeURIComponent(q) : '（未附带目标地址）';
  }})();
</script>
</body>
</html>
"""


def write_notice():
    os.makedirs(os.path.join(SITE, "offline"), exist_ok=True)
    p = os.path.join(SITE, "offline", "index.html")
    open(p, "w", encoding="utf-8", newline="").write(NOTICE)
    print(f"已生成提示页 {p}")


def rewrite_file(path):
    t = open(path, encoding="utf-8", errors="replace").read()
    if "/offline/?to=" in t:
        return 0
    n = 0

    def repl(m):
        nonlocal n
        pre, url, host, post = m.group(1), m.group(2), m.group(3), m.group(4)
        if not is_google_host(host):
            return m.group(0)
        n += 1
        return pre + "/offline/?to=" + urllib.parse.quote(url, safe="") + post

    new = ANCHOR_RE.sub(repl, t)
    if n:
        # Windows 上刚生成的文件偶尔被杀软/索引占用，写入会瞬时失败，这里重试
        import time
        for attempt in range(6):
            try:
                with open(path, "w", encoding="utf-8", newline="") as f:
                    f.write(new)
                return n
            except PermissionError:
                time.sleep(0.4 * (attempt + 1))
        raise
    return n


def main():
    write_notice()
    targets = []
    for dirpath, _dn, fns in os.walk(SITE):
        if os.sep + "offline" in dirpath:
            continue
        for fn in fns:
            if fn.lower().endswith((".html", ".htm")):
                targets.append(os.path.join(dirpath, fn))

    print(f"待扫描 HTML: {len(targets)}")
    total, changed, failed = 0, 0, []
    for i, p in enumerate(targets):
        try:
            c = rewrite_file(p)
        except Exception as e:
            failed.append((p, str(e)[:80]))
            continue
        if c:
            total += c
            changed += 1
        if (i + 1) % 1500 == 0:
            print(f"  {i+1}/{len(targets)}")
    print(f"已改写 {changed} 个文件，共接管 {total} 条谷歌系链接")
    if failed:
        print(f"失败 {len(failed)} 个：")
        for p, e in failed[:5]:
            print("   ", p, e)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
