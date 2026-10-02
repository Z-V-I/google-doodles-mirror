#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""线上验收：对已经部署好的域名跑一遍，而不是对本地目录跑。

【为什么不复用 verify_deploy.py】
那个脚本查的是「磁盘上的部署包」和「本机起的静态服务器」——
它验不了三件只有上线后才会暴露的事：

  · 自定义域有没有真的生效（CNAME 建了、证书签了、Pages 认了这个域）；
  · `_headers` / `_redirects` 在**边缘**的实际效果（本地 http.server 不认这两个文件）；
  · 边缘缓存与源站压缩有没有把内容弄坏（大文件被截断、Content-Type 被改）。

用法：
    python _tools/verify_online.py
    python _tools/verify_online.py --base https://google.zvi.onl
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

OK, BAD = [], []


def ok(m):
    OK.append(m)


def bad(m):
    BAD.append(m)


def get(url, timeout=90):
    """返回 (状态码, 响应头 dict, 字节)。任何异常都折成状态码 -1。"""
    req = urllib.request.Request(url, headers={"User-Agent": "verify-online/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, e.read()
    except Exception as e:
        return -1, {}, ("%s: %s" % (type(e).__name__, e)).encode()


def main():
    ap = argparse.ArgumentParser(description="线上（HTTPS）验收")
    ap.add_argument("--base", default="https://google.zvi.onl")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    print("=" * 78)
    print("线上验收  %s" % base)
    print("=" * 78)

    # ---- ① 主要路由 ---------------------------------------------------------
    print("\n① 主要路由")
    expect = [
        ("/", "首页"),
        ("/about/", "关于页"),
        ("/rss/", "RSS 模式页"),
        ("/disclaimer/", "免责声明"),
        ("/doodle/mens-ipl-2026-final/", "一个详情页"),
    ]
    for p, label in expect:
        st, h, b = get(base + p)
        if st == 200 and len(b) > 500:
            print("  ✓ %-34s 200  %8.1f KB  %s" % (p, len(b) / 1024, label))
        else:
            bad("期望 200：%s（实际 %s，%d 字节）" % (p, st, len(b)))

    # ---- ② 检索页的三个脚本与顺序 -------------------------------------------
    print("\n② 检索页脚本（顺序决定功能）")
    st, h, b = get(base + "/search/")
    if st != 200:
        bad("检索页拿不到（%s）" % st)
    else:
        t = b.decode("utf-8", "replace")
        i_guard = t.find("/vendor/search-filter-guard.js")
        i_shim = t.find("/vendor/search-api-shim.js")
        i_bundle = t.find("index.min.js")
        print("  guard@%d  shim@%d  bundle@%d" % (i_guard, i_shim, i_bundle))
        if 0 <= i_guard < i_bundle:
            ok("筛选查询守卫已上线且在 bundle 之前")
        else:
            bad("检索页缺少 search-filter-guard.js 或位置在 bundle 之后")
        if 0 <= i_shim < i_bundle:
            ok("检索 shim 已上线且在 bundle 之前")
        else:
            bad("检索页缺少 search-api-shim.js 或位置在 bundle 之后")
        # 筛选面板的词表改动
        n_topic = len(set(re.findall(r'name="topic_tags"[^>]*?value="([^"]*)"', t, re.S)))
        for v in ("other", "birthday", "new year", "easter", "diwali", "badminton"):
            if v not in t:
                bad("筛选面板缺少选项 %r" % v)
        if n_topic >= 156:
            ok("筛选面板话题选项 %d 个（含后补的 6 个）" % n_topic)
        else:
            bad("筛选面板话题选项只有 %d 个，少于预期的 156" % n_topic)

    # ---- ③ 需要边缘能力的路由 ----------------------------------------------
    print("\n③ 只有边缘环境才成立的路由")
    st, h, b = get(base + "/logos/2012/basketball12-hp-anim.png")
    ct = h.get("content-type", "")
    if st == 200 and ct.startswith("image/") and len(b) < 200:
        ok("/logos/** 被 _redirects 兜成 %d 字节的 %s（不是 404）" % (len(b), ct))
        print("  ✓ /logos/** → %s  %s  %d 字节" % (st, ct, len(b)))
    else:
        bad("/logos/** 兜底未生效：%s %s %d 字节" % (st, ct, len(b)))

    st, h, _ = get(base + "/vendor/search-api-shim.js")
    cc = h.get("cache-control", "")
    # 【为什么容忍 14400 而不是要求 0】
    # 上传的 _headers 写的是 max-age=0，但线上实测是 max-age=14400 ——
    # zone 的 Browser Cache TTL（zvi.onl 上是 14400 秒）会把低于它的 max-age
    # 抬上去，且只抬「会被边缘缓存的静态资源」（HTML / .xml / .json 是 DYNAMIC
    # 不缓存，所以那些路径原样保留；比它大的值如 /img/* 的 31536000 也不会被改小）。
    # 这里守住「不比 zone 上限更差」，否则离线化层会长时间不生效。
    m = re.search(r"max-age\s*=\s*(\d+)", cc)
    if m and "must-revalidate" in cc and int(m.group(1)) <= 14400:
        ok("/vendor/* 缓存不超过 zone 上限且强制回源校验（%s）" % cc)
        print("  ✓ /vendor/search-api-shim.js → %s" % cc)
        if int(m.group(1)) > 0:
            print("    · 被 zone 的 Browser Cache TTL 抬到了 %s 秒（源站写的是 0）；"
                  "想拉到「改完即刻生效」可在 zone 设置里把 Browser Cache TTL 改成 "
                  "\"Respect Existing Headers\"" % m.group(1))
    else:
        bad("/vendor/* 缓存策略不对：%r（离线化层会长时间不生效）" % cc)

    st, h, _ = get(base + "/img/mens-ipl-2026-final.gif")
    cc = h.get("cache-control", "")
    if "immutable" in cc:
        ok("/img/* 长缓存就位（%s）" % cc)
        print("  ✓ /img/* → %s" % cc)
    else:
        bad("/img/* 缺少 immutable：%r" % cc)

    st, h, _ = get(base + "/")
    sec = [k for k in ("x-content-type-options", "referrer-policy",
                       "x-frame-options", "permissions-policy") if k in h]
    if len(sec) >= 4:
        ok("安全响应头 4 项齐全")
        print("  ✓ 安全头：%s" % ", ".join(sec))
    else:
        bad("安全响应头不齐（只有 %s）" % sec)

    # ---- ④ 订阅源 -----------------------------------------------------------
    print("\n④ 订阅源与元数据")
    st, h, b = get(base + "/rss.xml", timeout=180)
    ct = h.get("content-type", "")
    if st == 200 and "xml" in ct:
        try:
            root = ET.fromstring(b)
            n = len(root.findall(".//item"))
            if n >= 600:
                ok("/rss.xml 可解析，%d 条 item（%s，%.1f MB）" % (n, ct, len(b) / 1024 / 1024))
                print("  ✓ /rss.xml  %d 条  %.1f MB  %s" % (n, len(b) / 1024 / 1024, ct))
            else:
                bad("/rss.xml 只有 %d 条 item，偏少" % n)
        except ET.ParseError as e:
            bad("/rss.xml 不是合法 XML：%s" % e)

        # 【订阅源里的绝对地址必须是公开可达的】
        # 阅读器在用户自己的机器上解析订阅源，所以里面只能写对外地址。
        # 早期这里是构建期的本地地址（http://127.0.0.1:8765）—— 本地跑 serve.py
        # 看不出来（它会按请求 Host 改写），但纯静态部署没有那一步，
        # 于是 6,375 条链接带着 127.0.0.1 上了线，订阅者点开指向他自己的电脑。
        text = b.decode("utf-8", "replace")
        local = re.findall(r'https?://(?:127\.0\.0\.1|localhost)[^"<\s]*', text)
        if local:
            bad("/rss.xml 里有 %d 处本地地址（例如 %s）——订阅者会指向自己的机器"
                % (len(local), local[0][:60]))
        else:
            ok("/rss.xml 里没有 127.0.0.1 / localhost 这类本地地址")
            print("  ✓ 订阅源无本地地址")
        # 抽查 <link> / <enclosure> 是不是都以公开基址开头
        urls = re.findall(r'<(?:link|enclosure[^>]*url)="?([^"<\s]+)', text)
        urls = [u for u in urls if u.startswith("http")]
        off = [u for u in urls if not u.startswith(base)]
        if off:
            bad("订阅源里有 %d 个地址不以 %s 开头（例如 %s）" % (len(off), base, off[0][:70]))
        else:
            ok("订阅源里 %d 个绝对地址全部指向 %s" % (len(urls), base))
            print("  ✓ %d 个绝对地址全部指向 %s" % (len(urls), base))
    else:
        bad("/rss.xml 异常：%s %s" % (st, ct))

    st, h, b = get(base + "/rss/doodles.json", timeout=180)
    if st == 200:
        try:
            j = json.loads(b)
            n = len(j.get("items", []))
            if n == 6375:
                ok("/rss/doodles.json 可解析，%d 条（与本地一致）" % n)
                print("  ✓ /rss/doodles.json  %d 条  %.1f MB" % (n, len(b) / 1024 / 1024))
            else:
                bad("/rss/doodles.json 条数 %d，本地是 6375" % n)
        except Exception as e:
            bad("/rss/doodles.json 解析失败：%s" % e)
    else:
        bad("/rss/doodles.json 拿不到（%s）" % st)

    # ---- ⑤ 反向检查 ---------------------------------------------------------
    print("\n⑤ 反向检查（不该出现的 / 该是 404 的）")
    # 没有 404.html 时，Pages 会把**根 index.html 当兜底**用 200 返回（软 404）。
    # 所以这两条要断言真正的 404，而不是「能打开就行」。
    for p, label in (("/__nonexistent__/", "编造的路径"),
                     ("/creating-a-doodle/", "故意移除的创作流程页")):
        st, _, _ = get(base + p)
        if st == 404:
            ok("%s 返回真 404（%s）" % (p, label))
            print("  ✓ %-22s → 404  %s" % (p, label))
        else:
            bad("%s 返回 %s，应为 404（%s）——部署包里缺 404.html？" % (p, st, label))

    # ---- ⑥ 图片变体（方案 A+B 的线上确认）----------------------------------
    # 【为什么要单独查这一节】变体是构建期**新增**的一批文件，而卡片图的 URL 由
    # shim 在浏览器里拼出来（详情页/首页则是构建期烘进 HTML 的）。两者任一没上传，
    # 线上表现都是「卡片图 404、图裂」；而 ①-⑤ 全都发现不了 —— 首页 HTML 是 200，
    # 只是里面的图打不开。图片优化这一层必须自己有一道闸门。
    print("\n⑥ 图片变体（卡片图是不是真的变小了）")
    st, _, b = get(base + "/index.html", timeout=180)
    if st != 200:
        bad("拿不到首页，无法核对图片变体（%s）" % st)
    else:
        html = b.decode("utf-8", "ignore")
        var_imgs = re.findall(r'<img[^>]*src="(/img-c/[^"]+)"', html)
        raw_imgs = re.findall(r'<img[^>]*src="(/img/[^"]+)"', html)
        if len(var_imgs) >= 8:
            ok("首页 %d 张卡片图指向变体（另 %d 张因原图 ≤150 KB 仍用原图）"
               % (len(var_imgs), len(raw_imgs)))
            print("  ✓ 首页卡片图  %d 张指向 /img-c/，%d 张仍用 /img/"
                  % (len(var_imgs), len(raw_imgs)))
        else:
            bad("首页只有 %d 张卡片图指向变体，构建期改写疑似没上线" % len(var_imgs))

        # 抽 3 个变体：能打开、是 WebP、且带 immutable 长缓存
        good = 0
        for u in var_imgs[:3]:
            st2, h2, b2 = get(base + u, timeout=120)
            ct2 = h2.get("content-type", "")
            cc2 = h2.get("cache-control", "")
            if st2 == 200 and "webp" in ct2 and "immutable" in cc2:
                good += 1
                print("  ✓ %-52s %5d KB  %s" % (u[:51], len(b2) // 1024, ct2))
        if good == min(3, len(var_imgs)):
            ok("抽检 %d 个变体：200 + image/webp + immutable 全部就位" % good)
        else:
            bad("抽检变体只有 %d 个正常（变体没上传？or _headers 的 /img-c/* 失效？）" % good)

        # 量一对（原图 vs 变体），证明线上真的拿到的是缩小版
        if var_imgs:
            u = var_imgs[0]
            stem = re.sub(r"\.[^.]+$", "", u.rsplit("/", 1)[-1])
            for ext in (".gif", ".png", ".jpg", ".jpeg"):
                st3, _, b3 = get(base + "/img/" + stem + ext, timeout=180)
                if st3 != 200:
                    continue
                st4, _, b4 = get(base + u, timeout=120)
                if st4 == 200 and len(b4) < len(b3):
                    ok("线上实测一对：%s%s %.0f KB → 变体 %.0f KB（%.0f%%）"
                       % (stem[:20], ext, len(b3) / 1024, len(b4) / 1024,
                          100.0 * len(b4) / len(b3)))
                    print("  ✓ 原图 %.0f KB → 变体 %.0f KB（%.0f%%）"
                          % (len(b3) / 1024, len(b4) / 1024, 100.0 * len(b4) / len(b3)))
                break

        # 详情页：卡片指向变体、主图仍是原图（主图换成 480px 会在大屏上糊）
        st5, _, b5 = get(base + "/doodle/mens-ipl-2026-final/", timeout=120)
        if st5 == 200:
            h5 = b5.decode("utf-8", "ignore")
            v5 = re.findall(r'<img[^>]*src="(/img-c/[^"]+)"', h5)
            r5 = re.findall(r'<img[^>]*src="(/img/[^"]+)"', h5)
            if v5 and r5:
                ok("详情页：卡片 %d 张指向变体、主图 %d 张仍用原图" % (len(v5), len(r5)))
                print("  ✓ 详情页  卡片 %d 张指向变体 / 主图 %d 张用原图"
                      % (len(v5), len(r5)))
            elif v5:
                bad("详情页主图也被换成了变体（主图应保留原图，否则宽屏会糊）")
            else:
                bad("详情页没有卡片图指向变体，构建期改写疑似没上线")

    # ---- 结果 ---------------------------------------------------------------
    print("\n" + "=" * 78)
    print("结果")
    print("=" * 78)
    for m in OK:
        print("  ✓ " + m)
    for m in BAD:
        print("  ✗ " + m)
    print("\n通过 %d 项，失败 %d 项" % (len(OK), len(BAD)))
    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main())
