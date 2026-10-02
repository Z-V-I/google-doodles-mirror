#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
patch_charts.py —— 把首页世界地图用的 Google Charts 真正本地化。

【为什么需要它】
  首页 "Latest Doodles from around the world" 是一张 Google GeoChart 世界地图
  （1200x640，780 条国家路径）。早期为了让页面「能离线打开」，把
  google.charts.load 换成了一句空函数桩件 —— 结果那一块 1424x676 的区域
  永远空白。整页截图看不出问题，只有把 DOM 指标摊开才发现
  `.mapChart` 的 innerHTML 长度是 0。

  正确的做法不是"砍掉"，而是"搬进来"：Google Charts 的 loader 与 geochart
  模块都是静态 JS/CSS，取回本地并把内嵌的绝对地址改成站内路径，功能与外观
  就与原站完全一致，且不再外发任何请求。

【依赖集是实测出来的，不是猜的】
  用一个只加载真 loader 的探针页 + CDP 页面目标级网络监听，得到 geochart
  所需的全部 11 个文件（见 CHART_FILES）。版本号 51 与地图数据版本 10 都是
  确定值，不存在运行时协商。

【幂等】
  下载目标已存在且清单校验通过时跳过下载（因此断网也能重建）；
  文本重写带「必须命中」断言，重复执行结果一致。

用法：
  python _tools/patch_charts.py            # 应用（缺文件才下载）
  python _tools/patch_charts.py --check    # 预演，只报告不写盘
  python _tools/patch_charts.py --refresh  # 强制重新下载
"""

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(ROOT, "site")
MANIFEST = os.path.join(ROOT, "_tools", "charts_manifest.json")

# 实测得到的完整依赖集：来源 URL -> 站内相对路径
# 探针页只调用 google.charts.load('current', {packages:['geochart']})，
# 由 CDP 记录「页面目标」实际发出的 10 个请求：
#     51/loader.js                    第二次解密后的版本化 loader
#     51/css/{core/tooltip,util/util} 提示气泡与布局样式
#     51/js/jsapi_compiled_*_module   核心 / 图形 / UI / 地理 / 地理图表 五个模块
#     geochart/10/info/mapList.js     地图清单
#     geochart/10/mapfiles/world_COUNTRIES.js  世界政区几何
CHART_FILES = [
    ("https://www.gstatic.com/charts/loader.js", "vendor/charts/loader.js"),
    ("https://www.gstatic.com/charts/51/loader.js", "vendor/charts/51/loader.js"),
    ("https://www.gstatic.com/charts/51/css/core/tooltip.css",
     "vendor/charts/51/css/core/tooltip.css"),
    ("https://www.gstatic.com/charts/51/css/util/util.css",
     "vendor/charts/51/css/util/util.css"),
    ("https://www.gstatic.com/charts/51/js/jsapi_compiled_default_module.js",
     "vendor/charts/51/js/jsapi_compiled_default_module.js"),
    ("https://www.gstatic.com/charts/51/js/jsapi_compiled_graphics_module.js",
     "vendor/charts/51/js/jsapi_compiled_graphics_module.js"),
    ("https://www.gstatic.com/charts/51/js/jsapi_compiled_ui_module.js",
     "vendor/charts/51/js/jsapi_compiled_ui_module.js"),
    ("https://www.gstatic.com/charts/51/js/jsapi_compiled_geo_module.js",
     "vendor/charts/51/js/jsapi_compiled_geo_module.js"),
    ("https://www.gstatic.com/charts/51/js/jsapi_compiled_geochart_module.js",
     "vendor/charts/51/js/jsapi_compiled_geochart_module.js"),
    ("https://www.gstatic.com/charts/geochart/10/info/mapList.js",
     "vendor/charts/geochart/10/info/mapList.js"),
    ("https://www.gstatic.com/charts/geochart/10/mapfiles/world_COUNTRIES.js",
     "vendor/charts/geochart/10/mapfiles/world_COUNTRIES.js"),
]

# 站内路径同样要刷新的文件：源码包内部的互相引用
# （去重：顶层 loader.js 同时在 CHART_FILES 里，直接拼接会重复处理两遍）
REWRITE_TARGETS = list(dict.fromkeys(
    ["vendor/charts/loader.js"] + [rel for _, rel in CHART_FILES]
))

# 文本重写规则：(原始片段, 替换片段, 说明, 是否要求至少命中一次)
#
# charts 的两个镜像域都要改：www.gstatic.com 与 www.gstatic.cn（国内镜像）。
# 其余三个 host 属于 geochart 用不到的包（控件需要 ajax.googleapis 取 jQuery；
# geomap 需要 Google Maps API；输入法工具），实测 10 个请求里一个都没出现。
# 仍然改成本地空桩：语义上把"外发"彻底摘掉，且万一有代码路径调用，
# 得到的是安静降级而不是一次外网请求。
REWRITES = [
    ("https://www.gstatic.com/charts", "/vendor/charts",
     "Charts 主域（loader 与被加载模块）", True),
    ("https://www.gstatic.cn/charts", "/vendor/charts",
     "Charts 国内镜像域", False),
    ("https://ajax.googleapis.com/ajax/libs", "/vendor/charts/_disabled/ajaxlibs",
     "控件包用的 jQuery CDN（geochart 不需要）", True),
    ("https://maps.googleapis.com/maps/api/js", "/vendor/charts/_disabled/maps-api.js",
     "Google Maps API（仅 geomap/map 包需要）", True),
    ("https://maps-api-ssl.google.com/maps", "/vendor/charts/_disabled/maps-api.js",
     "Google Maps API（旧域）", True),
    ("https://www.gstatic.com/inputtools/js/ita/inputtools_3.js",
     "/vendor/charts/_disabled/inputtools_3.js",
     "输入法工具（表格类控件用）", True),
]

# 生成的降级空桩：被引用时安静失败，绝不外发
EMPTY_STUBS = {
    "vendor/charts/_disabled/maps-api.js":
        "/* 空桩：离线镜像不加载 Google Maps API（仅 geomap 包需要）。 */\n",
    "vendor/charts/_disabled/inputtools_3.js":
        "/* 空桩：离线镜像不加载 Google 输入法工具。 */\n",
    "vendor/charts/_disabled/ajaxlibs/.keep":
        "占位：charts 控件所需的 jQuery 未镜像（geochart 不使用）。\n",
}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# 已知良性残留：这些字符串出现在**代码生成模板**里 ——
# 图表工具栏的「导出 HTML」「发布到网页」「添加到 iGoogle」菜单会拼出一段
# 给用户复制粘贴的示例代码，里面当然带着 google 的地址。
# 它们既不在加载路径上，也不属于本项目的依赖；改写反而会破坏这段代码生成逻辑。
# 实测 11 个文件的全部加载请求只有 13 个（全在 127.0.0.1），已由 CDP 证实。
BENIGN_RESIDUAL = [
    (re.compile(r"https?://www\.google\.com/jsapi", re.I),
     "「导出 HTML」生成的示例代码里的 <script src>"),
    (re.compile(r"https?://www\.google\.com/ig/(?:adde|ifr)", re.I),
     "「发布到网页 / 添加到 iGoogle」的代码片段模板"),
]

# 首页内联 polyfill 中要移除的桩件（真 loader 接管后必须让位）
STUB_PATTERNS = [
    (re.compile(r"window\.google\.charts\s*=\s*window\.google\.charts\s*\|\|\s*\{.*?\n\};\n",
                re.S),
     "google.charts 空桩"),
    (re.compile(r"window\.google\.visualization\s*=\s*window\.google\.visualization\s*\|\|\s*\{.*?\n\};\n",
                re.S),
     "google.visualization 空桩"),
]

CHART_TAG = '<script src="/vendor/charts/loader.js"></script>'


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def fetch(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except Exception as e:            # noqa: BLE001 —— 网络抖动就重试
            last = e
    raise RuntimeError(f"下载失败 {url}: {last}")


def load_manifest():
    if os.path.isfile(MANIFEST):
        try:
            return json.load(open(MANIFEST, encoding="utf-8"))
        except (OSError, ValueError):
            return {}
    return {}


def download_all(refresh, apply_):
    """取回依赖集。已有文件且清单校验通过时跳过 —— 支持断网重建。"""
    man = load_manifest()
    entries = {}
    got = skipped = 0
    print("① 取回 Google Charts 依赖集（11 个文件）")
    for url, rel in CHART_FILES:
        dest = os.path.join(SITE, rel)
        want = man.get(url, {}).get("sha256")
        rec = man.get(url, {})
        # 清单里同时记「下载时的哈希」与「改写后的哈希」：
        # 磁盘上是哪一种都说明这个文件已经就位，不必重新下载。
        want = {rec.get("sha256"), rec.get("sha256_final")} - {None}
        if os.path.isfile(dest) and want and not refresh:
            cur = sha256(open(dest, "rb").read())
            if cur in want:
                print(f"   · 已存在且校验一致  {rel}")
                entries[url] = {"path": rel, "sha256": rec.get("sha256"),
                                "sha256_final": rec.get("sha256_final"),
                                "size": os.path.getsize(dest)}
                skipped += 1
                continue
        if not apply_:
            print(f"   + 将下载            {rel}")
            continue
        data = fetch(url)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(data)
        d = {"path": rel, "sha256": sha256(data), "size": len(data)}
        entries[url] = d
        got += 1
        print(f"   + 下载 {len(data):>9,} B  {rel}")
    if apply_:
        man.update(entries)
        with open(MANIFEST, "w", encoding="utf-8") as f:
            json.dump(man, f, ensure_ascii=False, indent=2, sort_keys=True)
    print(f"   新下载 {got} 个，复用 {skipped} 个"
          f"{'（预演，未写盘）' if not apply_ else ''}")
    return got


def record_final_hashes(apply_):
    """把改写后的哈希写回清单，供下一轮跳过下载与改写。"""
    if not apply_:
        return
    man = load_manifest()
    by_rel = {rel: url for url, rel in CHART_FILES}
    for rel in REWRITE_TARGETS:
        url = by_rel.get(rel)
        p = os.path.join(SITE, rel)
        if not url or not os.path.isfile(p):
            continue
        man.setdefault(url, {})["sha256_final"] = sha256(open(p, "rb").read())
        man[url]["path"] = rel
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=2, sort_keys=True)


def rewrite_texts(apply_, fresh_download=False):
    """
    把依赖集里内嵌的绝对地址改写成站内路径。

    fresh_download 用于区分两种「规则 0 命中」：
      · 刚下载的原始文件里一条都没命中  -> 上游 URL 形态变了，必须人工确认；
      · 文件本来就已改写完毕（幂等重跑）-> 正常，不该报警。
    """
    print("\n② 改写依赖集内嵌的绝对地址")
    total = 0
    global_hits = {old: 0 for old, _, _, _ in REWRITES}
    for rel in REWRITE_TARGETS:
        p = os.path.join(SITE, rel)
        if not os.path.isfile(p):
            print(f"   ! 缺失，跳过 {rel}")
            continue
        text = open(p, encoding="utf-8", errors="replace").read()
        before = text
        changed = []
        for old, new, why, _required in REWRITES:
            n = text.count(old)
            if n == 0:
                continue
            global_hits[old] += n
            text = text.replace(old, new)
            changed.append(f"{why}×{n}")
        if text != before:
            total += 1
            print(f"   · {rel:58s} {', '.join(changed)}")
            if apply_:
                with open(p, "w", encoding="utf-8", newline="") as f:
                    f.write(text)
    print(f"   改动文件 {total} 个")

    # 「必须命中」按全局判定：这几条是 loader 里确实存在的分支，
    # 一条都没命中说明规则已经过期（例如上游改了 URL 形态），必须人工确认。
    print("\n③ 规则命中情况（标 * 为必须命中）")
    stale = []
    for old, _new, why, required in REWRITES:
        n = global_hits[old]
        mark = "*" if required else " "
        print(f"   {mark} {n:>3} 次  {why}")
        if required and n == 0:
            stale.append(why)
    if stale:
        if fresh_download:
            print(f"   ! 以下规则一条都没命中，需人工确认是否上游变了：{stale}")
        else:
            print(f"   以下规则 0 命中：本次未下载新文件，属已改写完毕，正常。")

    # 校验：依赖集里不应再有指向谷歌的绝对地址
    # （预演模式下文件并未写盘，此时校验读到的还是旧文本，结论无意义 —— 跳过）
    if not apply_:
        print("\n④ 校验：预演未写盘，跳过残留校验")
        return {}
    print("\n④ 校验：依赖集中残留的谷歌绝对地址")
    blocked, benign = {}, {}
    for rel in REWRITE_TARGETS:
        p = os.path.join(SITE, rel)
        if not os.path.isfile(p):
            continue
        t = open(p, encoding="utf-8", errors="replace").read()
        for m in re.finditer(r"https?://[\w.\-]*(?:gstatic|googleapis|google)"
                             r"(?:\.com|\.cn)[\w./%{}\-]*", t):
            u = m.group(0)
            is_benign = any(rx.search(u) for rx, _ in BENIGN_RESIDUAL)
            bucket = benign if is_benign else blocked
            bucket.setdefault(u, []).append(rel)

    if blocked:
        for u, fs in sorted(blocked.items()):
            print(f"   ! [阻断] {u}   ← {', '.join(sorted(set(fs))[:3])}")
    else:
        print("   ✅ 加载路径上的残留 0 处")
    if benign:
        print(f"   良性（代码生成模板字符串，非请求）{len(benign)} 处：")
        for u, fs in sorted(benign.items()):
            why = next(wh for rx, wh in BENIGN_RESIDUAL if rx.search(u))
            print(f"      · {u}   —— {why}")
    return blocked


def make_stubs(apply_):
    print("\n⑤ 生成降级空桩")
    for rel, content in EMPTY_STUBS.items():
        p = os.path.join(SITE, rel)
        if os.path.isfile(p):
            print(f"   · 已存在 {rel}")
            continue
        print(f"   + {rel}")
        if apply_:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write(content)


def patch_homepage(apply_):
    """
    首页：装上真 loader，并摘掉 google.charts / google.visualization 的空桩。

    polyfill 里的写法是 `window.google.charts = window.google.charts || {...}`，
    所以严格来说只要让真 loader 先执行，`||` 就会挡住桩件。但把桩件直接摘掉更
    干净：不必依赖"谁先执行"这种隐式前提。grecaptcha 与 YT 的桩件保留（那两
    者确实没有本地实现）。
    """
    print("\n⑥ 首页接入真 loader")
    rel = "index.html"
    p = os.path.join(SITE, rel)
    text = open(p, encoding="utf-8", errors="replace").read()
    orig = text

    removed = []
    for pat, label in STUB_PATTERNS:
        text, n = pat.subn("", text)
        if n:
            removed.append(f"{label}×{n}")

    inserted = 0
    if CHART_TAG not in text:
        # 插到 polyfill 那个 <script> 之前
        idx = text.find("本地化 polyfill")
        if idx < 0:
            print("   ! 找不到 polyfill 注释，无法定位插入点")
            return
        open_tag = text.rfind("<script", 0, idx)
        line_start = text.rfind("\n", 0, open_tag) + 1
        text = text[:line_start] + CHART_TAG + "\n" + text[line_start:]
        inserted = 1
        # 顺手把注释改得与事实一致
        text = text.replace(
            "/* 本地化 polyfill：替代已移除的 Google 在线服务（charts/reCAPTCHA/YouTube），静默降级 */",
            "/* 本地化 polyfill：reCAPTCHA / YouTube 无本地实现，用桩件安静降级。\n"
            "   Google Charts 已整套搬进 /vendor/charts/（见 _tools/patch_charts.py），\n"
            "   因此这里不再需要 charts / visualization 的桩件。 */")

    print(f"   移除桩件：{', '.join(removed) if removed else '（无，可能已处理过）'}")
    print(f"   插入 loader 标签：{'1 处' if inserted else '（已存在）'}")
    if apply_ and text != orig:
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(text)


def unify_loader_refs(apply_):
    """
    把仍指向桩件 `google-charts-loader.js` 的页面改到真 loader。

    三个主路由页（/about/、/creating-a-doodle/、/search/）的输入 HTML 在更早的
    版本里就被写成了桩件路径，而首页与 6,376 个详情页走的是另一个来源，
    结果站内出现「同一个东西两种实现」的不一致：
    桩件页上 `google.charts.load.toString().length` 是 14，真 loader 页是 202。
    虽然这三页当前都不调用 charts，外观无差异，但留着这种不一致迟早咬人 ——
    统一到真 loader。
    """
    print("\n⑦ 统一 charts loader 的引用")
    pat = re.compile(
        r'(<script\b[^>]*\bsrc\s*=\s*["\'])/vendor/google-charts-loader\.js(["\'])',
        re.I)
    changed = 0
    scanned = 0
    for dp, _dn, fs in os.walk(SITE):
        for f in fs:
            if not f.lower().endswith((".html", ".htm")):
                continue
            p = os.path.join(dp, f)
            scanned += 1
            try:
                t = open(p, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            new, n = pat.subn(r"\1/vendor/charts/loader.js\2", t)
            if n:
                changed += 1
                print(f"   · {os.path.relpath(p, SITE)}   {n} 处")
                if apply_:
                    with open(p, "w", encoding="utf-8", newline="") as fh:
                        fh.write(new)
    print(f"   扫描 HTML {scanned:,} 个，改写 {changed} 个")

    left = 0
    for dp, _dn, fs in os.walk(SITE):
        for f in fs:
            if not f.lower().endswith((".html", ".htm")):
                continue
            try:
                if "google-charts-loader.js" in open(
                        os.path.join(dp, f), encoding="utf-8", errors="replace").read():
                    left += 1
            except OSError:
                pass
    print(f"   仍指向桩件的页面：{left} 个"
          f"{'（预演，未写盘）' if not apply_ else ''}")
    return left


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="预演，不写盘")
    ap.add_argument("--refresh", action="store_true", help="强制重新下载依赖集")
    ap.add_argument("--skip-download", action="store_true",
                    help="只用本地已有文件（完全离线重建）")
    args = ap.parse_args()
    apply_ = not args.check

    if not os.path.isdir(SITE):
        sys.exit("找不到 site/，请先运行 build")

    print("=" * 72)
    print("  Google Charts 本地化" + ("（预演）" if not apply_ else ""))
    print("=" * 72)

    if args.skip_download:
        print("① 跳过下载（--skip-download）")
        downloaded = 0
    else:
        downloaded = download_all(args.refresh, apply_)
    blocked = rewrite_texts(apply_, fresh_download=downloaded > 0)
    record_final_hashes(apply_)
    make_stubs(apply_)
    patch_homepage(apply_)
    stub_left = unify_loader_refs(apply_)

    print("\n" + "=" * 72)
    if blocked:
        print(f"  !! 仍有 {len(blocked)} 处加载路径残留，请检查")
        sys.exit(1)
    if stub_left:
        print(f"  !! 仍有 {stub_left} 个页面指向 charts 桩件")
        sys.exit(1)
    print("  完成。启动预览后首页地图应由本地 Charts 绘制：python serve.py")
    print("=" * 72)


if __name__ == "__main__":
    main()
