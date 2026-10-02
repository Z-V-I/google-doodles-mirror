#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
patch_vendor.py —— 把 vendor 里**已下载的真实第三方脚本**内置的外链请求本地化。

【为什么需要它】
  build_site.py 的 URL 重写规则里本来有「把 cookienotificationbar.min.js 换成
  本地桩件」的意图，但规则 1（LOCAL_MAP 命中）排在前面：`www.gstatic.com/glue/...`
  已经被成功下载到 `/vendor/glue/...`，于是命中规则 1 直接返回真实文件，
  那条桩件规则成了死代码 —— 页面加载的是**真实**的 Google cookie 通知条脚本。
  而它内部有一句（构造函数里无条件执行）：
      fetch("https://www.gstatic.com" + d + c)
  其中 d 是 /glue/cookienotificationbar/config/{2a,2b}.json。这属于
  「代码里拼出来的请求」，verify_offline.py 扫 HTML 属性时看不见。

  按「保留原物，只切断必须切断的地方」的原则，正确做法不是换成桩件（会丢掉
  原组件行为），而是保留真实脚本、只把它的配置源指向本地。

【做法】
  1) 下载真实的 2a.json / 2b.json 到 site/vendor/glue/cookienotificationbar/config/
     （实测两份内容都是 {"required": false}，即真站在此配置下也不展示通知条，
       所以本地化后视觉结果与原站一致）。
  2) 把脚本里的 fetch("https://www.gstatic.com"+d+c) 改成 fetch("/vendor"+d+c)。
     d 本身以 /glue/... 开头，拼出来就是 /vendor/glue/cookienotificationbar/config/…
  3) 顺带审计 vendor/static 下**所有** JS 里「可执行位置上的外链调用」，
     确保没有第二处漏网。

【幂等】替换串不含被替换的原串，且带出现次数断言；重复执行结果完全一致。

用法：
    python _tools/patch_vendor.py --check     # 只审计，不写
    python _tools/patch_vendor.py             # 应用
"""
import os
import re
import sys
import json
import argparse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

DOC = "site"

COOKIEBAR = os.path.join(DOC, "vendor", "glue", "cookienotificationbar",
                         "cookienotificationbar.min.js")
CONFIG_DIR = os.path.join(DOC, "vendor", "glue", "cookienotificationbar", "config")

CONFIG_URLS = {
    "2a.json": "https://www.gstatic.com/glue/cookienotificationbar/config/2a.json",
    "2b.json": "https://www.gstatic.com/glue/cookienotificationbar/config/2b.json",
}

OLD = 'fetch("https://www.gstatic.com"+d+c)'
NEW = 'fetch("/vendor"+d+c)'

# 兜底内容：实测真站返回的就是这一份。联网时仍优先下载真实文件。
CONFIG_FALLBACK = '{ "required": false }\n'

# 「可执行位置上的外链调用」——静态扫描 HTML 属性时抓不到的那一类
EXEC_CALL = re.compile(
    r'(?:fetch\s*\(|XMLHttpRequest|\.open\s*\(|importScripts\s*\(|'
    r'\.src\s*=|\.href\s*=|new\s+Image\s*\(|'
    r'\.createElement\s*\(\s*["\']script["\']\s*\))'
    r'[^;{}]{0,160}?(https?://[^\s"\'`)\\]+)', re.I)

SKIP_HOSTS = ("127.0.0.1", "localhost")


def log(msg):
    print(msg, flush=True)


def fetch(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def ensure_configs(check_only):
    """把 cookie 条的配置 JSON 落到本地。已存在则保留（避免联网）。"""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    for name, url in CONFIG_URLS.items():
        dest = os.path.join(CONFIG_DIR, name)
        if os.path.isfile(dest) and os.path.getsize(dest) > 0:
            log(f"  [配置] 已存在，保留  {dest}")
            continue
        if check_only:
            log(f"  [配置] 缺失（--check 不写）  {dest}")
            continue
        body = None
        try:
            body = fetch(url)
            log(f"  [配置] 已下载 {url}  ({len(body)} 字节)")
        except Exception as e:
            log(f"  [配置] 下载失败（{type(e).__name__}），改用已验证内容")
        if body is None:
            body = CONFIG_FALLBACK.encode("utf-8")
        with open(dest, "wb") as f:
            f.write(body)
        log(f"  [配置] 已写入 {dest}")


def patch_cookiebar(check_only):
    if not os.path.isfile(COOKIEBAR):
        log(f"  [脚本] 未找到 {COOKIEBAR}（可能尚未构建）")
        return False
    t = open(COOKIEBAR, encoding="utf-8", errors="replace").read()
    n_old, n_new = t.count(OLD), t.count(NEW)

    if n_old == 0 and n_new == 1:
        log(f"  [脚本] 已是本地化状态，无需改动")
        return True
    if n_old != 1 or n_new != 0:
        log(f"!! [脚本] 状态异常：原串出现 {n_old} 次、新串出现 {n_new} 次，"
            f"预期 (1,0) 或 (0,1)。已中止，请人工检查。")
        return False
    if check_only:
        log(f"  [脚本] 待本地化：{OLD}  ->  {NEW}")
        return False

    t2 = t.replace(OLD, NEW)
    assert t2.count(NEW) == 1 and OLD not in t2
    with open(COOKIEBAR, "w", encoding="utf-8", newline="") as f:
        f.write(t2)
    log(f"  [脚本] 已本地化  fetch 目标：/vendor/glue/cookienotificationbar/config/…")
    return True


def audit_exec_calls():
    """扫描 site 下会被加载的 JS，找「可执行位置上的外链调用」。"""
    scope = [os.path.join(DOC, "vendor"), os.path.join(DOC, "static"),
             os.path.join(DOC, "assets"), os.path.join(DOC, "games")]
    found = []
    n = 0
    for base in scope:
        for dp, _dn, fns in os.walk(base):
            for fn in fns:
                if not fn.lower().endswith((".js", ".mjs")):
                    continue
                p = os.path.join(dp, fn)
                n += 1
                try:
                    t = open(p, encoding="utf-8", errors="replace").read()
                except OSError:
                    continue
                for m in EXEC_CALL.finditer(t):
                    u = m.group(1)
                    host = re.match(r'https?://([^/:]+)', u)
                    host = host.group(1).lower() if host else "?"
                    if host.endswith(SKIP_HOSTS):
                        continue
                    found.append((p, u, host))
    log(f"\n  [审计] 扫描 JS {n:,} 个，命中可执行位置外链 {len(found)} 处")
    if not found:
        log("  [审计] ✅ 无残留")
        return 0
    # 按主机归类，便于判断性质
    from collections import Counter
    by_host = Counter(h for _p, _u, h in found)
    for host, c in by_host.most_common():
        log(f"      {host:34s} {c:6d} 处")
    return len(found)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只审计不写入")
    args = ap.parse_args()

    log("=" * 74)
    log(f"vendor 内嵌外链本地化（{'仅审计' if args.check else '应用'}模式）")
    log("=" * 74)

    log("\n① 安装 cookie 通知条配置")
    ensure_configs(args.check)

    log("\n② 本地化 cookie 通知条的 fetch 目标")
    ok = patch_cookiebar(args.check)

    log("\n③ 审计其余脚本")
    n = audit_exec_calls()

    if args.check:
        log("\n（--check 模式，未写入任何文件）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
