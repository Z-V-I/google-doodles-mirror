#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
scan_deadlinks.py —— 全站内部死链扫描（离线，不发 HTTP 请求）。

思路：把 HTML 里以 / 开头的站内 URL 按本地服务器的解析规则换算成文件路径，
判断是否存在。比逐个发请求快几个数量级，又不依赖服务器在跑。

判定规则与 serve.py 的 translate_path 保持一致：
    /a/b/     -> site/a/b/index.html
    /a/b      -> site/a/b 或 site/a/b.html
    /a/b.png  -> site/a/b.png

只统计 src / href / data-src / poster 这四类**会被浏览器真正加载或跳转**的属性。
data-g-* 之类埋点属性不算 —— 它们不参与跳转。

结果分三档，只有第三档才算失败（退出码 1）：
  ① 可达        —— 文件在磁盘上真实存在
  ② 良性降级    —— 磁盘没有，但 serve.py 有意兜底（详见 BENIGN_RULES），属于
                   "已知且已接受"的取舍，不影响页面外观
  ③ 脚本上下文  —— 出现在 <script> 里的字符串/拼接片段（如 data-base-href="/doodle/"
                   模板、SWF 内嵌 JS 的路径前缀），由 JS 在运行时拼装，不是静态加载
  ④ 真实死链    —— HTML 正文里的静态引用且无兜底 → 这才是需要修的

历史坑（务必保留）：
  * ATTR 里的 \b 会匹配 data-base-href / data-g-cta_url 的尾巴，必须用 (?<![-\w])。
    早期版本因此把 href="/doodle/" 这种 JS 模板当成死链误报。
  * | head -N 会通过 SIGPIPE 静默掐断本脚本，核对时改用重定向到日志。
"""
import os
import re
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

DOC = "site"

# 保留原样即可、无需本地存在的 URL
IGNORE_PREFIX = (
    "/offline/?to=",   # 外链提示页
    "//",              # 协议相对，属外链
)

# 有意降级：磁盘上没有、但 serve.py 会兜底的资源，属"已接受的取舍"而非 bug。
BENIGN_RULES = (
    (re.compile(r"^/doodle/$"),
     "搜索结果卡片的空模板骨架（JS 克隆后填入真实 slug）"),
    (re.compile(r"^/doodle/[^/]+/$"),
     "首页快照比数据集新，serve.py 返回样式化提示页"),
    (re.compile(r"^/logos/"),
     "抓取期不可达的精灵图，serve.py 返回 1x1 透明 PNG"),
    (re.compile(r"^/rss/images\.zip"),
     "服务端实时流式打包，磁盘上本就不存在（全量 1.7 GB 不可能预先落盘）"),
)

# 真正会被浏览器加载/跳转的属性。注意前置否定环视，避免命中 data-base-href 之类。
ATTR = re.compile(
    r'(?<![-\w])(?:src|href|data-src|poster)\s*=\s*["\'](/[^"\'#?]*)["\']', re.I)

SCRIPT_OPEN = re.compile(r"<script\b", re.I)
SCRIPT_CLOSE = re.compile(r"</script\s*>", re.I)


def local_of(url):
    parts = [p for p in url.split("/") if p and p not in (".", "..")]
    return os.path.join(DOC, *parts) if parts else DOC


def exists(url):
    if not url.startswith("/") or url.startswith(IGNORE_PREFIX):
        return True
    local = local_of(url)
    if os.path.isfile(local):
        return True
    if os.path.isdir(local) and os.path.isfile(os.path.join(local, "index.html")):
        return True
    if not os.path.splitext(local)[1] and os.path.isfile(local + ".html"):
        return True
    return False


def in_script(text, pos):
    """判断 pos 处是否落在 <script>…</script> 区间内。"""
    pre = text[:pos]
    return len(SCRIPT_OPEN.findall(pre)) > len(SCRIPT_CLOSE.findall(pre))


def benign_reason(url):
    for rx, why in BENIGN_RULES:
        if rx.match(url):
            return why
    return None


def main():
    reachable = 0
    benign = Counter()      # url -> 次数
    benign_why = {}         # url -> 原因
    scripted = Counter()    # url -> 次数
    fatal = Counter()       # url -> 次数
    fatal_files = Counter()
    n = 0

    for dp, _dn, fns in os.walk(DOC):
        for fn in fns:
            if not fn.lower().endswith((".html", ".htm")):
                continue
            p = os.path.join(dp, fn)
            n += 1
            text = open(p, encoding="utf-8", errors="replace").read()
            for m in ATTR.finditer(text):
                u = m.group(1)
                if not u:
                    continue
                if exists(u):
                    reachable += 1
                    continue
                why = benign_reason(u)
                if why:
                    benign[u] += 1
                    benign_why[u] = why
                elif in_script(text, m.start()):
                    scripted[u] += 1
                else:
                    fatal[u] += 1
                    fatal_files[p] += 1

    print("=" * 72)
    print(f"扫描 HTML        : {n}")
    print(f"站内可达引用      : {reachable}")
    print(f"良性降级（有兜底）: {sum(benign.values())} 处 / {len(benign)} 个目标")
    print(f"脚本上下文（非静态）: {sum(scripted.values())} 处 / {len(scripted)} 个目标")
    print(f"真实死链          : {sum(fatal.values())} 处 / {len(fatal)} 个目标 "
          f"/ {len(fatal_files)} 个文件")
    print("=" * 72)

    if benign:
        print("\n① 良性降级（serve.py 已兜底，不影响外观完整性）")
        for u, c in benign.most_common(20):
            print(f"   {c:5d}  {u}")
            print(f"          └─ {benign_why[u]}")

    if scripted:
        print("\n② 脚本上下文（JS 运行时拼装，非静态加载）")
        for u, c in scripted.most_common(20):
            print(f"   {c:5d}  {u}")

    if fatal:
        print("\n③ 真实死链 —— 需要修复")
        for u, c in fatal.most_common(40):
            print(f"   {c:5d}  {u}")
        print()
        return 1

    print("\n✅ 站内静态引用全部可达（无真实死链）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
