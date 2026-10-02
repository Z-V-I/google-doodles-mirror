#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
audit_embedded_urls.py —— 审计「页面真正引用的本地 JS」里内嵌的外链加载调用。

【为什么必须单独做这一步】
  verify_offline.py 扫的是 HTML 属性（<script src> / <link href> / <img src> / CSS url()）。
  它看不见**代码里拼出来的**请求：
      fetch("https://www.gstatic.com" + d + c)
      xhr.open("GET", "https://apis.google.com/js/client.js")
      s.src = "https://.../gtm.js"
  而这类恰恰会真的发出去（本项目的 cookie 通知条就栽在这里）。

方法：
  1) 从所有 HTML 里收集被引用的本地 .js（<script src>）。
  2) 只审计**被引用到的**脚本（没被引用的死文件不算风险，单独列出即可）。
  3) 在脚本里搜索可执行位置上的绝对 http(s) URL：
       fetch( / XMLHttpRequest / .open( / .src= / .href= / importScripts( / new Image()
  4) 输出每个命中的文件、URL、上下文，便于判断是否真会触发。

用法：
    python _tools/audit_embedded_urls.py
"""
import os
import re
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

DOC = "site"

# 页面里引用本地脚本 / 样式
REF = re.compile(
    r'(?<![-\w])(?:src|href)\s*=\s*["\']([^"\']+\.(?:js|mjs))["\']', re.I)

# 可执行位置上的绝对 URL —— 分两类，只有第一类才会在**页面加载期**发出去
# ① 加载期：脚本一跑就可能触发（这才是必须切断的）
LOAD_CALL = re.compile(
    r'(?:fetch\s*\(|XMLHttpRequest|importScripts\s*\(|'
    r'\.src\s*=|\.load\s*\(|new\s+Image\s*\(|'
    r'\.createElement\s*\(\s*["\']script["\']\s*\))'
    r'[^;{}]{0,160}?(https?://[^\s"\'`)\\]+)', re.I)

# ② 点击期：只有用户点分享/外链按钮才会打开新窗口，不影响离线渲染
CLICK_CALL = re.compile(
    r'(?:window\.open\s*\(|location\.(?:href|assign|replace)\s*=)'
    r'[^;{}]{0,160}?(https?://[^\s"\'`)\\]+)', re.I)

# 直白的字符串常量（可能只是配置表，未必会请求）
LITERAL = re.compile(r'["\'](https?://[^\s"\'`]{6,})["\']')

LOCAL_HOSTS = ("127.0.0.1", "localhost")


def _classify(match_iter, text):
    out = []
    for m in match_iter:
        u = m.group(1)
        host = re.match(r'https?://([^/:]+)', u)
        host = host.group(1).lower() if host else "?"
        if host.endswith(LOCAL_HOSTS):
            continue
        out.append((host, u, text[max(0, m.start() - 70):m.end() + 40]))
    return out



def normalize(ref, page_dir):
    """把页面里的脚本引用换算成 site/ 下的本地路径。"""
    ref = ref.split("?")[0].split("#")[0]
    if ref.startswith("//") or re.match(r'^[a-z]+:', ref, re.I):
        return None                      # 外链，交给 verify_offline 管
    if ref.startswith("/"):
        rel = ref.lstrip("/")
    else:
        rel = os.path.normpath(os.path.join(page_dir, ref)).replace(os.sep, "/")
        if rel.startswith(".."):
            return None
    return os.path.join(DOC, *rel.split("/"))


def main():
    refs = defaultdict(set)              # 本地 js 路径 -> 引用它的页面集合
    n = 0
    for dp, _dn, fns in os.walk(DOC):
        for fn in fns:
            if not fn.lower().endswith((".html", ".htm")):
                continue
            p = os.path.join(dp, fn)
            n += 1
            try:
                t = open(p, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            page_dir = os.path.relpath(dp, DOC).replace(os.sep, "/")
            if page_dir == ".":
                page_dir = ""
            for m in REF.finditer(t):
                loc = normalize(m.group(1), page_dir)
                if loc:
                    refs[loc].add(p)

    print("=" * 78)
    print(f"扫描 HTML            : {n:,}")
    print(f"被引用的本地 JS 文件  : {len(refs)}")
    print("=" * 78)

    risky = []
    clicky = []
    only_literal = []
    for loc in sorted(refs):
        if not os.path.isfile(loc):
            print(f"[缺失] {loc}")
            continue
        try:
            t = open(loc, encoding="utf-8", errors="replace").read()
        except OSError:
            continue

        hits = _classify(LOAD_CALL.finditer(t), t)
        chits = _classify(CLICK_CALL.finditer(t), t)

        if hits:
            risky.append((loc, len(refs[loc]), hits))
        elif chits:
            clicky.append((loc, len(refs[loc]), chits))
        else:
            lits = {m.group(1) for m in LITERAL.finditer(t)
                    if not re.match(r'https?://(?:127\.0\.0\.1|localhost)', m.group(1))}
            if lits:
                only_literal.append((loc, len(refs[loc]), sorted(lits)[:6]))

    if risky:
        print(f"\n!! 加载期就会发外链的脚本（{len(risky)} 个）—— 必须切断：")
        for loc, cnt, hits in risky:
            print(f"\n  {loc}")
            print(f"    被 {cnt} 个页面引用")
            seen = set()
            for host, u, ctx in hits:
                if (host, u) in seen:
                    continue
                seen.add((host, u))
                print(f"      · {u[:110]}")
                print(f"        …{ctx.replace(chr(10), ' ')[:150]}…")
    else:
        print("\n✅ 没有任何被引用的脚本在加载期发起外链请求。")

    if clicky:
        from collections import Counter
        hosts = Counter(h for _l, _c, hs in clicky for h, _u, _x in hs)
        total = sum(len(hs) for _l, _c, hs in clicky)
        print(f"\n· 仅「用户点击时」才会打开外链的脚本（{len(clicky)} 个 / {total} 处）"
              f" —— 不影响离线渲染：")
        for h, c in hosts.most_common():
            print(f"      {h:32s} {c:4d} 处")
        print("    （这类是分享按钮等，等同外链 <a>；离线时点它本来也打不开）")

    if only_literal:
        print(f"\n· 仅含外链字符串常量（未出现在可执行位置）的脚本"
              f"（{len(only_literal)} 个）：")
        for loc, cnt, lits in only_literal:
            print(f"    {loc}   被 {cnt} 个页面引用")
            for u in lits:
                print(f"        {u[:104]}")

    return 1 if risky else 0


if __name__ == "__main__":
    raise SystemExit(main())
