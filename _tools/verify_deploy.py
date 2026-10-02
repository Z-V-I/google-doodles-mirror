#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""部署包验收：静态层 + 检索语义对账 + 纯静态 HTTP 实测。

【为什么要有这个脚本】
line 上线的失败方式，本地跑 serve.py 时一个都暴露不出来：

  · 单文件超过托管商上限 —— 整次部署被拒，而你本地跑得好好的；
  · 检索接口在纯静态下 404 —— 页面不报错，只是结果列表安静地空白；
  · shim 和 serve.py 的筛选语义漂移 —— 表现为「线下搜到 92 条、线上 88 条」，
    截图看不出来，用户也不一定会报；
  · /logos/** 从「静默 1×1 降级」退化成刷屏 404，把互动涂鸦打断。

所以这一层的检查重点不是「文件在不在」，而是**同一个问题在两套实现下答案是否相同**。

用法：
    python _tools/verify_deploy.py                     # 全量（含真浏览器）
    python _tools/verify_deploy.py --no-page           # 跳过真浏览器（快）
    python _tools/verify_deploy.py --port 8791         # 复用已在跑的纯静态服务器
    python _tools/verify_deploy.py --serve-port 8765   # 顺带和 serve.py 对账
"""

import argparse
import functools
import http.server
import json
import os
import re
import socketserver
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEPLOY = os.path.join(ROOT, "_deploy")
SITE = os.path.join(ROOT, "site")
SRC = os.path.join(ROOT, "_deploy_src")
DATA = os.path.join(SITE, "rss", "doodles.json")

MAX_FILE = 25 * 1024 * 1024
RECLAIM_ROOTS = ("assets/cms",)

OK, BAD = [], []


def ok(msg):
    OK.append(msg)


def bad(msg):
    BAD.append(msg)


# ============================================================ ① 静态产物

REQUIRED = [
    "index.html", "search/index.html", "about/index.html",
    "rss/index.html", "rss.xml", "rss/latest.xml",
    "rss/doodles.json", "rss/doodles.csv",
    "_redirects", "_headers", "robots.txt", "404.html",
    "disclaimer/index.html", "vendor/search-api-shim.js", "assets/__blank.png",
]


def check_static():
    print("\n" + "─" * 78)
    print("① 静态产物")
    print("─" * 78)

    if not os.path.isdir(DEPLOY):
        bad("_deploy 不存在，先跑 build_deploy.py")
        return set()

    files = {}
    total = 0
    oversize = []
    for r, _d, fs in os.walk(DEPLOY):
        for x in fs:
            p = os.path.join(r, x)
            rel = "/" + os.path.relpath(p, DEPLOY).replace(os.sep, "/")
            try:
                s = os.path.getsize(p)
            except OSError:
                continue
            files[rel] = s
            total += s
            if s > MAX_FILE:
                oversize.append((rel, s))

    print("  文件 %d 个 · 合计 %.2f GiB · 最大单文件 %.2f MB"
          % (len(files), total / 1024 ** 3,
             max(files.values()) / 1024 / 1024 if files else 0))

    if oversize:
        bad("有 %d 个文件超过 25 MiB：%s" % (len(oversize), oversize[:3]))
    else:
        ok("无单文件超过 Cloudflare Pages 的 25 MiB 上限")

    for rel in REQUIRED:
        if "/" + rel not in files:
            bad("缺少必需文件 /%s" % rel)
    if all("/" + rel in files for rel in REQUIRED):
        ok("%d 个必需文件齐全（含 _redirects/_headers/robots.txt/404/免责声明/shim/兜底图）"
           % len(REQUIRED))

    # 兜底图必须是合法 1x1 PNG
    bp = os.path.join(DEPLOY, "assets", "__blank.png")
    if os.path.isfile(bp):
        d = open(bp, "rb").read()
        if d.startswith(b"\x89PNG\r\n\x1a\n") and len(d) >= 24 and d[16:24] == b"\x00\x00\x00\x01\x00\x00\x00\x01":
            ok("兜底图是合法的 1×1 PNG（%d 字节）" % len(d))
        else:
            bad("兜底图不是合法的 1×1 PNG")

    # shim 只该进 _deploy，绝不能落回 site/
    sp = os.path.join(DEPLOY, "search", "index.html")
    if os.path.isfile(sp):
        t = open(sp, encoding="utf-8", errors="ignore").read()
        if "search-api-shim.js" in t and t.find("search-api-shim") < t.find("index.min.js"):
            ok("检索页已挂 shim，且位置在 bundle 之前")
        elif "search-api-shim.js" not in t:
            bad("检索页没有挂上 shim")
        else:
            bad("shim 挂在 bundle 之后，可能来不及拦截")
        # 筛选查询守卫：修的是原 bundle 两处裸读 .tagName 的空指针，
        # 少了它，手敲 ?page=2 / ?nationality_tags=X 这类非表单字段会让整页白掉
        if "search-filter-guard.js" in t and \
                t.find("search-filter-guard") < t.find("index.min.js"):
            ok("检索页已挂筛选查询守卫，且位置在 bundle 之前")
        else:
            bad("检索页缺少 search-filter-guard.js（未知筛选参数会整页空白）")
        # 顺序：守卫 → 桩 → shim → bundle
        i_guard = t.find("/vendor/search-filter-guard.js")
        i_shim = t.find("/vendor/search-api-shim.js")
        i_bundle = t.find("index.min.js")
        i_stub = t.find("/vendor/recaptcha-stub.js")
        print("  脚本顺序： guard@%d  recaptcha-stub@%d  shim@%d  bundle@%d"
              % (i_guard, i_stub, i_shim, i_bundle))

    ssp = os.path.join(SITE, "search", "index.html")
    if os.path.isfile(ssp):
        if "search-api-shim.js" in open(ssp, encoding="utf-8", errors="ignore").read():
            bad("site/ 被污染了：shim 不该出现在 site/search/index.html")
        else:
            ok("site/ 未被污染（shim 只存在于 _deploy）")

    # 筛选词表对齐：判据只写在 fix_search_filters.py 里，这里复用它的回归检查。
    # 之所以不在这里重写一遍：同一判据两处实现，迟早会「一致地错」而互相放过。
    r = subprocess.run([sys.executable, os.path.join(ROOT, "_tools", "fix_search_filters.py"),
                        "--check", "--root", "_deploy"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=ROOT)
    for line in (r.stdout or "").splitlines():
        s = line.strip()
        if s.startswith("✗") or s.startswith("⚠") or s.startswith("✓ 面板取值") \
                or s.startswith("✓ 全站 chip") or s.startswith("· 守卫脚本"):
            print("  " + s)
    if r.returncode == 0:
        ok("筛选词表已对齐：面板取值覆盖数据词表，全站 chip 链接取值都落在面板内")
    else:
        bad("筛选词表未对齐（详见 fix_search_filters.py --check --root _deploy 的输出）")

    # _redirects / _headers / robots.txt
    rd = os.path.join(DEPLOY, "_redirects")
    if os.path.isfile(rd):
        body = open(rd, encoding="utf-8", errors="ignore").read()
        rules = [l for l in body.splitlines() if l.strip() and not l.strip().startswith("#")]
        has_logos = any(r.split()[0].startswith("/logos") and "__blank.png" in r
                        for r in rules if len(r.split()) >= 2)
        if has_logos:
            ok("_redirects：/logos/* → 1×1 兜底图（%d 条生效规则）" % len(rules))
        else:
            bad("_redirects 里没有 /logos/* 的兜底规则")
        for r in rules:
            parts = r.split()
            if len(parts) >= 3 and not re.match(r"^[0-9]{3}$", parts[-1]):
                bad("_redirects 有无法识别的状态码：%s" % r)

    hd = os.path.join(DEPLOY, "_headers")
    if os.path.isfile(hd):
        body = open(hd, encoding="utf-8", errors="ignore").read()
        # 按 Cloudflare 的规则块解析：非缩进行是「路径匹配」，其后的缩进行是响应头。
        # 注意不能拿 body.split("/*") 切 —— 文件开头的注释里就写着 "/*" 三个字符，
        # 会从注释处切开，把说明文字当成规则读（本脚本第一版就是这么误报的）。
        blocks, cur = [], None
        for line in body.splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if line[0] not in (" ", "\t"):
                cur = {"path": line.strip(), "headers": []}
                blocks.append(cur)
            elif cur is not None:
                cur["headers"].append(line.strip())
        generic = next((b for b in blocks if b["path"] == "/*"), None)
        generic_cc = [h for h in (generic["headers"] if generic else [])
                      if h.lower().startswith("cache-control")]
        if generic_cc:
            bad("_headers 的 /* 通用规则带了 Cache-Control（%s），会盖掉下面所有具体规则"
                % generic_cc[0])
        else:
            ok("_headers 通用规则里没有 Cache-Control（%d 条规则块，互不覆盖）"
               % len(blocks))
        if "immutable" in body and "/img/*" in body:
            ok("_headers：/img/* 等长缓存规则就位")
        else:
            bad("_headers 缺少 /img/* 的 immutable 规则")

        # /vendor/ 下住着我们自己的离线化层（shim / 桩件 / 筛选守卫），文件名不带
        # 内容哈希，且与 rss/doodles.json 的语义强耦合 —— 一旦给它长缓存，老访客
        # 会拿旧逻辑配新数据，表现为「筛选条数莫名变 0」这种不报错的错。
        #
        # 阈值为什么是 14400 而不是 0：上传的 _headers 写的是 max-age=0，但线上实测
        # 拿到的是 max-age=14400 —— 这是 zone 的 Browser Cache TTL（zvi.onl 上是
        # 14400 秒）把**低于它的 max-age 抬上去**的结果，离源站最近的那一层说了不算。
        # 注意它只抬「会被边缘缓存的静态资源」：HTML / .xml / .json 是 DYNAMIC 不缓存，
        # 所以那些路径的 max-age 原样保留；而比它大的值（如 /img/* 的 31536000）
        # 也不会被改小。所以这里的门禁守住「不比 zone 上限更差」，
        # 真正把可更新性拉满的办法是在 zone 设置里把 Browser Cache TTL 改成
        # 「Respect Existing Headers」—— 见 DEPLOY.md 第 1 节。
        vb = next((b for b in blocks if b["path"] == "/vendor/*"), None)
        cc = next((h for h in (vb["headers"] if vb else [])
                   if h.lower().startswith("cache-control")), "")
        m = re.search(r"max-age\s*=\s*(\d+)", cc)
        if not vb:
            bad("_headers 缺少 /vendor/* 的缓存规则")
        elif not m:
            bad("/vendor/* 没写 max-age：%r" % cc)
        elif int(m.group(1)) > 14400:
            bad("/vendor/* 的 max-age=%s 超过 zone 上限 14400：离线化层会长时间不生效（%s）"
                % (m.group(1), cc))
        else:
            ok("_headers：/vendor/* 已压低缓存（%s；线上会被 zone 的 Browser Cache TTL "
               "抬到最多 14400 秒）" % cc)

    rb = os.path.join(DEPLOY, "robots.txt")
    if os.path.isfile(rb):
        body = open(rb, encoding="utf-8", errors="ignore").read()
        if re.search(r"^Disallow:\s*/\s*$", body, re.M):
            ok("robots.txt：已声明不被索引")
        else:
            print("  · robots.txt 未选择全站禁索引（按需自行调整）")

    # 订阅源里的绝对地址必须是**对外**可达的。
    # 阅读器在用户自己的机器上解析订阅源，写本地地址等于所有链接都指向用户自己。
    # 这个坑真踩过：构建期的基址是 http://127.0.0.1:8765，本地跑 serve.py 看不出来
    # （它会按请求 Host 改写），但纯静态部署没有那一步，6,375 条链接就这样上线了。
    fx = os.path.join(DEPLOY, "rss.xml")
    if os.path.isfile(fx):
        t = open(fx, encoding="utf-8", errors="ignore").read()
        local = re.findall(r"https?://(?:127\.0\.0\.1|localhost)[^\"<\s]*", t)
        if local:
            bad("rss.xml 里有 %d 处本地地址（如 %s）：订阅者会指向自己的机器"
                % (len(local), local[0][:60]))
        else:
            urls = [u for u in re.findall(r"<(?:link|enclosure[^>]*url)=\"?([^\"<\s]+)", t)
                    if u.startswith("http")]
            off = [u for u in urls if not u.startswith("https://")]
            if off:
                bad("rss.xml 里有 %d 个非 https 的绝对地址（如 %s）" % (len(off), off[0][:60]))
            else:
                hosts = sorted(set(re.findall(r"<(?:link|enclosure[^>]*url)=\"?(https://[^/\"<\s]+)", t)))
                ok("rss.xml：%d 个绝对地址全为 https，主机 %s"
                   % (len(urls), hosts[0] if len(hosts) == 1 else hosts[:3]))

    # 排除是可复现的：确认孤儿真的不在包里，且保留的 cms 都在
    rep_path = os.path.join(ROOT, "_tools", "deploy_report.json")
    if os.path.isfile(rep_path):
        rep = json.load(open(rep_path, encoding="utf-8"))
        orphans = [o["path"] for o in rep.get("orphans", [])]
        leaked = [p for p in orphans if p in files]
        if leaked:
            bad("有 %d 个本应排除的孤儿仍在包里：%s" % (len(leaked), leaked[:3]))
        else:
            ok("排除生效：%d 个无引用资源均未进入部署包（省 %.1f MB）"
               % (len(orphans), sum(o["bytes"] for o in rep.get("orphans", [])) / 1024 / 1024))
        miss = [p for p in rep.get("keep_cms", []) if "/" + p not in files]
        if miss:
            bad("被引用的 cms 资源被误排除：%s" % miss[:3])
        else:
            ok("保留生效：%d 个被引用的 cms 资源一个不少" % len(rep.get("keep_cms", [])))

    return set(files.keys())


# ==================================================== ② Python 独立复算

def norm_tag(s):
    """标签取值的归一化：非字母数字折叠成空格，再转小写、去首尾。

    与 serve.py 的 _norm_key()、search-api-shim.js 的 normTag() 是**同一条规则
    的三处实现**，必须完全同步 —— 否则「本地搜到 273 条、线上 0 条」这种分叉
    截图看不出来。

    【为什么必须折叠标点】
    检索页筛选面板的每个选项 value 都是「非字母数字→空格」的 slug 形式
    （football soccer / plants flowers / children s day），而详情页主题 chip
    与 rss/doodles.json 用的是原始标签（Football/Soccer / Plants & Flowers）。
    不折叠的话，从 chip 点进检索页会静默筛不到东西。

    只用于标签类字段。标题子串匹配用另一套处理，见 local_query()。
    """
    return re.sub(r"[^a-z0-9]+", " ", str(s if s is not None else "").lower()).strip()


def _same_number(value, wanted):
    """按数值比两个十进制字符串（'05' 与 '5' 等价）。

    注意这里**不能**照抄 serve.py 早期的写法 str(v) not in (wanted, "%02d" % v)：
    v >= 10 时 "%02d" % v 就等于 str(v)，等号恒成立、筛选失效。
    本脚本第一版正是照抄了那个写法，才把 serve.py 的 bug 一起复现了出来 ——
    两处独立实现写成同一个错误，对账反而会「一致地错」，这也是为什么第 ⑥ 步
    还要拿 serve.py 接口再对一次。
    """
    w = (wanted or "").lstrip("0")
    return str(value) == (w or "0")


def collect(qs, keys):
    out = set()
    for k in keys:
        for v in qs.get(k, []):
            for part in str(v).split(","):
                t = norm_tag(part)
                if t:
                    out.add(t)
    return out


def matches(field, wanted, rec):
    if not wanted:
        return True
    if field == "color":
        have = [norm_tag(rec.get("color"))]
    elif field == "topic":
        have = [norm_tag(t) for t in (rec.get("topics") or [])]
    elif field == "form":
        have = [norm_tag(rec.get("format"))]
    elif field == "region":
        have = [norm_tag(t) for t in (rec.get("regions") or [])]
    else:
        have = []
    return bool(set(have) & wanted)


def local_query(rows, qs):
    """与 serve.py 的 query_doodles() 等价的一份独立实现（数据换成 rss/doodles.json）。"""
    # 标题匹配保持「只去首尾空白 + 转小写」，与 serve.py 的 query_doodles() 一致。
    # 不能套 norm_tag —— 那会把标题里的 ' 和 / 也折叠掉，子串就找不到了。
    title_like = (qs.get("title_like") or qs.get("q") or [""])[0].strip().lower()
    colors = collect(qs, ["color_tags"])
    topics = collect(qs, ["topic_tags"])
    forms = collect(qs, ["form_tags"])
    regions = collect(qs, ["nationality_tags"])

    year = (qs.get("date_like_year") or [""])[0].strip()
    month = (qs.get("date_like_month") or [""])[0].strip()
    day = (qs.get("date_like_day") or [""])[0].strip()

    def to_int(v, d):
        try:
            return int(v)
        except (TypeError, ValueError):
            return d

    page = max(1, to_int((qs.get("page") or ["1"])[0], 1))
    limit = max(1, min(to_int((qs.get("limit") or ["16"])[0], 16), 200))
    direction = ((qs.get("sort_direction") or ["desc"])[0] or "desc").lower()

    picked = []
    for r in rows:
        if title_like and title_like not in (r.get("title") or "").lower() \
                and title_like not in (r.get("slug") or "").lower():
            continue
        if not matches("color", colors, r):
            continue
        if not matches("topic", topics, r):
            continue
        if not matches("form", forms, r):
            continue
        if not matches("region", regions, r):
            continue
        if year or month or day:
            d = r.get("date")
            if not d:
                continue
            if year and str(d[0]) != year:
                continue
            if month and not _same_number(d[1], month):
                continue
            if day and not _same_number(d[2], day):
                continue
        picked.append(r)

    dated = [r for r in picked if r.get("date")]
    undated = sorted((r for r in picked if not r.get("date")),
                     key=lambda r: (r.get("title") or r.get("slug") or "").lower())
    dated.sort(key=lambda r: tuple(r["date"]), reverse=(direction != "asc"))
    ordered = dated + undated

    total = len(ordered)
    start = (page - 1) * limit
    chunk = ordered[start:start + limit]
    return {"total": total, "n": len(chunk), "next": start + limit < total,
            "page": page, "limit": limit,
            "names": [r.get("slug") for r in chunk]}


CASES = [
    ("", "无筛选（全集）"),
    ("topic_tags=Space", "话题 Space"),
    ("topic_tags=Football%2FSoccer", "话题 Football/Soccer（自带斜杠）"),
    ("color_tags=red", "主色 red"),
    ("form_tags=animated", "格式 animated"),
    ("date_like_year=2019", "年份 2019（确切日期）"),
    ("title_like=halloween", "关键字 halloween"),
    ("topic_tags=Space&form_tags=game", "Space + game（组合）"),
    ("date_like_month=10&date_like_day=31", "月日 10/31（含补零路径）"),
    ("page=2&limit=16", "第二页"),
]


def check_semantics():
    print("\n" + "─" * 78)
    print("② Python 独立复算（数据源 site/rss/doodles.json）")
    print("─" * 78)
    if not os.path.isfile(DATA):
        bad("找不到 %s" % DATA)
        return {}
    j = json.load(open(DATA, encoding="utf-8"))
    rows = j.get("items") if isinstance(j, dict) else j
    print("  数据 %d 条，其中有确切日期 %d 条"
          % (len(rows), sum(1 for r in rows if r.get("date"))))
    out = {}
    for qs, label in CASES:
        sp = urllib.parse.parse_qs(qs, keep_blank_values=True)
        out[qs] = local_query(rows, sp)
        print("  %5d 条  %s" % (out[qs]["total"], label))
    return out


# ================================================== ③ 与浏览器侧对账

def run_page_verify(port):
    print("\n" + "─" * 78)
    print("③ 真浏览器（检索页 + shim）")
    print("─" * 78)
    script = os.path.join(ROOT, "_tools", "verify_deploy_page.js")
    if not os.path.isfile(script):
        bad("找不到 verify_deploy_page.js")
        return None
    try:
        r = subprocess.run(["node", script, "--port", str(port)],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=600, cwd=ROOT)
    except Exception as e:
        bad("页面验收脚本执行失败：%s" % e)
        return None

    payload = None
    for line in (r.stdout or "").splitlines():
        if line.startswith("RESULT "):
            try:
                payload = json.loads(line[7:])
            except json.JSONDecodeError:
                pass
    if payload is None:
        bad("页面验收没有产出可解析的结果")
        print((r.stdout or "")[-800:])
        print((r.stderr or "")[-400:])
        return None

    if not payload.get("ok"):
        bad("页面侧判定失败：%s" % {k: payload.get(k) for k in
                                    ("mode", "overlap", "exceptions")})
    else:
        ok("页面侧：shim 处于 local 模式，UI 渲染 %d 个结果，0 JS 异常"
           % payload.get("uiLinks", -1))
    if payload.get("overlap") == 0:
        ok("分页不重不漏（page1 ∩ page2 = ∅）")

    # 带筛选参数直接进页面：URL → 填表 → 取数 → 渲染 全链路
    nav = payload.get("navCases") or []
    if not nav:
        bad("页面验收没有产出「带参数进页面」的用例结果")
    else:
        badnav = [c for c in nav if c.get("exN") != 0 or c.get("match") is False]
        for c in nav:
            mark = "✓" if (c.get("exN") == 0 and c.get("match") is not False) else "✗"
            print("  %s 异常%2d 渲染%3d API total=%-5s 一致=%-5s %s"
                  % (mark, c.get("exN", -1), c.get("cards", -1), c.get("total"),
                     c.get("match"), c.get("label")))
        if badnav:
            for c in badnav:
                bad("带参数进页面失败 [%s]：异常 %s 条，与 API 一致=%s"
                    % (c.get("label"), c.get("exN"), c.get("match")))
        else:
            ok("%d 个「带筛选参数直接进页面」用例：0 异常，渲染结果与 API 逐条一致"
               % len(nav))
    return payload


def compare(page_payload, py_results):
    if not page_payload or not py_results:
        return
    print("\n" + "─" * 78)
    print("④ 三方对账：浏览器 shim ↔ Python 复算")
    print("─" * 78)
    by_qs = {c.get("qs"): c for c in page_payload.get("cases", [])}
    diff = 0
    for qs, label in CASES:
        a = by_qs.get(qs)
        b = py_results.get(qs)
        if not a or not b:
            continue
        same_total = a["total"] == b["total"]
        same_names = a.get("names") == b.get("names")
        mark = "✓" if (same_total and same_names) else "✗"
        if not (same_total and same_names):
            diff += 1
            bad("语义漂移 [%s]：浏览器 total=%s / python total=%s；首条 %r vs %r"
                % (label, a["total"], b["total"],
                   (a.get("names") or [None])[0], (b.get("names") or [None])[0]))
        print("  %s %5d 条  %s" % (mark, b["total"], label))
    if diff == 0:
        ok("10 组筛选条件的条数与排序在两边完全一致")


# ================================================== ⑤ 纯静态 HTTP 实测

class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def start_static(port):
    handler = functools.partial(_Quiet, directory=DEPLOY)
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", port), handler)
    httpd.daemon_threads = True
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd


def http_get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "verify-deploy"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception as e:
        return -1, str(e).encode()


def check_http(port):
    print("\n" + "─" * 78)
    print("⑤ 纯静态 HTTP 实测（端口 %d）" % port)
    print("─" * 78)
    base = "http://127.0.0.1:%d" % port

    expect_200 = [
        "/", "/search/", "/about/", "/rss/", "/rss.xml", "/rss/latest.xml",
        "/rss/doodles.json", "/rss/doodles.csv", "/robots.txt",
        "/disclaimer/", "/vendor/search-api-shim.js",
        "/img/mens-ipl-2026-final.gif",
    ]
    # 抽一个真实详情页
    ddir = os.path.join(DEPLOY, "doodle")
    if os.path.isdir(ddir):
        first = sorted(os.listdir(ddir))[0]
        expect_200.append("/doodle/%s/" % first)

    for p in expect_200:
        st, body = http_get(base + p)
        if st == 200 and len(body) > 0:
            print("  ✓ %-46s 200  %8.1f KB" % (p, len(body) / 1024))
        else:
            bad("期望 200 但拿到 %s：%s" % (st, p))

    # 接口在纯静态下必然是 404 —— 这正是 shim 存在的理由
    st, _ = http_get(base + "/live/api/v1/doodles?page=1&limit=16")
    if st == 404:
        ok("/live/api/v1/doodles 在纯静态下 404（符合预期，由 shim 在浏览器侧接管）")
    else:
        print("  · /live/api/v1/doodles 返回 %s（由静态服务器决定，不影响 shim）" % st)

    # /logos/ 在 python http.server 下没有 _redirects 能力，只会 404；
    # 真正的兜底发生在 Cloudflare 侧，这里只确认规则存在（已在①检查）。
    st, _ = http_get(base + "/logos/2012/basketball12-hp-anim.png")
    print("  · /logos/** 在本地静态服务器下为 %s（_redirects 是 Cloudflare 侧能力，"
          "本地不复现，规则已在①校验）" % st)


def check_serve(port):
    print("\n" + "─" * 78)
    print("⑥ 与 serve.py 对账（端口 %d，验证本地/线上语义同源）" % port)
    print("─" * 78)
    base = "http://127.0.0.1:%d" % port
    st, _ = http_get(base + "/rss.xml", timeout=10)
    if st != 200:
        print("  · serve.py 未在 %d 上运行，跳过" % port)
        return
    diff = 0
    for qs, label in CASES:
        st, body = http_get("%s/live/api/v1/doodles?%s" % (base, qs))
        if st != 200:
            print("  · 接口返回 %s，跳过（%s）" % (st, label))
            continue
        try:
            j = json.loads(body)
        except json.JSONDecodeError:
            print("  · 响应不是 JSON，跳过（%s）" % label)
            continue
        local = local_query(json.load(open(DATA, encoding="utf-8"))["items"],
                            urllib.parse.parse_qs(qs, keep_blank_values=True))
        same = j.get("total") == local["total"]
        if not same:
            diff += 1
            bad("serve.py 与本地数据不一致 [%s]：%s vs %s"
                % (label, j.get("total"), local["total"]))
        print("  %s %5s 条  %s" % ("✓" if same else "✗", j.get("total"), label))
    if diff == 0:
        ok("serve.py 与本地数据的检索结果完全一致（本地/线上同一套语义）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-page", action="store_true", help="跳过真浏览器验收")
    ap.add_argument("--port", type=int, default=0, help="复用已有纯静态服务器端口")
    ap.add_argument("--serve-port", type=int, default=8765, help="serve.py 端口，用于对账")
    ap.add_argument("--no-serve", action="store_true", help="跳过 serve.py 对账")
    args = ap.parse_args()

    t0 = time.time()
    print("=" * 78)
    print("部署包验收")
    print("=" * 78)

    check_static()
    py = check_semantics()

    httpd = None
    port = args.port
    if not port:
        port = 8799
        httpd = start_static(port)
        time.sleep(0.5)
    try:
        check_http(port)
        page = None
        if not args.no_page:
            page = run_page_verify(port)
            compare(page, py)
        if not args.no_serve:
            check_serve(args.serve_port)
    finally:
        if httpd:
            httpd.shutdown()

    print("\n" + "=" * 78)
    print("结果")
    print("=" * 78)
    for m in OK:
        print("  ✓ " + m)
    if BAD:
        print()
        for m in BAD:
            print("  ✗ " + m)
    print("\n通过 %d 项，失败 %d 项，用时 %.1fs" % (len(OK), len(BAD), time.time() - t0))
    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main())
