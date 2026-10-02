#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rss_common.py —— RSS 订阅源 / 元数据导出 / 图片包清单的共享数据层。

为什么单独抽一层：
  同一份「涂鸦 -> 订阅条目」的转换被三处消费，任何一处各写一遍都会漂移：
    1. _tools/gen_rss.py      构建期落盘 rss.xml / doodles.json / doodles.csv
    2. serve.py               /rss/images.zip 打包时生成包内 manifest
    3. _tools/verify_rss.py   验收时独立复算一份做交叉比对

排序规则：
  按「可读的年份」倒序 —— 有确切上线日期的排在最前，其余用 slug 里的年份
  （涂鸦 slug 普遍带上线年份，doodle_data 已经用它做过日期推定）作为次级排序，
  两条线索都没有的 2,246 条按标题升序垫底。
  注意这与站点其它页面的排序（只看 run_date）不同：检索页的「无日期」条目会
  整块沉底，对列表浏览没问题，但一个订阅源如果 90% 的条目按字母序排，读起来
  完全没有时间感，所以这里额外借用了 slug 年份。有确切日期的条目才会写
  <pubDate>，不拿推定年份伪造一个日期。

介绍（description）取 doodles_data.js 里的官方元描述；分类取
topics（话题）/ color（主色）/ format（格式）/ regions（地区）四个维度。
"""
import os
import re
import io
import csv
import json
import html
from email.utils import formatdate
import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 构建期落盘的订阅源里，链接必须是绝对地址（阅读器没有「当前页」概念）。
#
# 【这里必须是**对外**基址，不能是本地地址】
# 订阅源是会被**下载走**的产物 —— 阅读器在用户自己的机器上解析它，
# 所以里面只能写公开可达的地址。早期这里写的是 http://127.0.0.1:8765，
# 在本地跑 serve.py 时看起来一切正常（serve.py 会按请求头的 Host 把基址
# 再改写一次，见 serve.py 的 _origin()/_send_feed()），
# 但**纯静态部署没有那个改写步骤** —— 于是 6,375 条 <link> 和 <enclosure>
# 带着 127.0.0.1 上了线，订阅者点任何一条都指向他自己的电脑。
#
# 现在默认取对外域名；serve.py 仍会把它改写成请求实际到达的 Host，
# 所以本地换端口访问照样拿到正确地址。换域名时用环境变量覆盖即可。
DEFAULT_BASE = os.environ.get("ZVI_PUBLIC_BASE", "https://google.zvi.onl")

FEED_TITLE = "Google Doodles 离线镜像 · 全集订阅"
FEED_DESC = ("doodles.google 离线镜像收录的全部涂鸦。每条包含图片（enclosure）、"
             "官方介绍、上线日期，以及话题 / 主色 / 格式 / 地区分类。")

# ------------------------------------------------------------------ 数据


def _sort_key(it):
    """有年份的按年份倒序在前；完全没有年份线索的按标题升序垫底。"""
    y = it["year"]
    if y is None:
        return (1, 0, 0, 0, it["title"].lower())
    m, d = it["date"][1:] if it["date"] else (0, 0)
    return (0, -y, -m, -d, it["title"].lower())


def load_items(root=None):
    """载入并转换（独立进程用，例如构建期与验收脚本）。"""
    root = root or ROOT
    import sys
    if os.path.join(root, "_tools") not in sys.path:
        sys.path.insert(0, os.path.join(root, "_tools"))
    import doodle_data
    return from_normalized(doodle_data.load(root), root)


def from_normalized(rows, root=None):
    """把 doodle_data 的规范化记录转成订阅条目。

    serve.py 启动时已经把这份数据载进内存了，单独开这个入口是为了不让它
    为了 RSS 再解析一遍 2.8 MB 的 doodles_data.js。
    """
    root = root or ROOT
    import sys
    if os.path.join(root, "_tools") not in sys.path:
        sys.path.insert(0, os.path.join(root, "_tools"))
    import doodle_data

    img_dir = os.path.join(root, "site", "img")
    out = []
    for r in rows:
        img = os.path.basename(r.get("image_file") or "")
        path = os.path.join(img_dir, img) if img else ""
        size = os.path.getsize(path) if path and os.path.isfile(path) else 0
        d = r.get("run_date")
        # 年份线索：先取确切上线年份，没有再退回 slug 里的年份（同一份正则，
        # 与详情页/检索页的日期推定逻辑保持一致）。
        slug_years = doodle_data.YEAR_IN_SLUG.findall(r.get("slug") or "")
        year = d[0] if d else (int(slug_years[0]) if slug_years else None)
        out.append({
            "slug": r.get("slug") or "",
            # 原始数据里混着 &#39; 这类实体（有的字段有、有的没有），
            # 这里统一还原成纯文本，序列化时再由各自的目标格式决定怎么转义。
            "title": html.unescape(r.get("title") or (r.get("slug") or "")),
            "date": list(d) if d else None,
            "date_label": r.get("run_date_label") or "",
            "original_date": r.get("date") or "",
            "year": year,
            "year_exact": bool(d),
            "description": html.unescape(r.get("description") or ""),
            "color": r.get("color") or "",
            "topics": list(r.get("topics") or []),
            "format": r.get("format") or "",
            "regions": list(r.get("regions") or []),
            "image": img,
            "image_url": "/img/" + img if img else "",
            "bytes": size,
            "page_url": "/doodle/%s/" % (r.get("slug") or ""),
        })
    out.sort(key=_sort_key)
    return out


def _ts(it):
    """条目的发布时间戳（当天 00:00:00 UTC）；无日期返回 None。"""
    if not it["date"]:
        return None
    y, m, d = it["date"]
    return datetime.datetime(y, m, d, tzinfo=datetime.timezone.utc).timestamp()


def _esc(s):
    """先还原源数据里已有的 HTML 实体再转义 —— 官方描述里本来就有 &#39; 这类
    写法，直接 escape 会变成 &amp;#39;（页面上显示成字面量的 &#39;）。"""
    return html.escape(html.unescape(s or ""), quote=True)


def _cdata(s):
    """CDATA 里唯一需要处理的就是 ]]> 序列。"""
    return "<![CDATA[%s]]>" % (s or "").replace("]]>", "]]&gt;")


def _cats(it):
    """话题用裸 <category>；另外三个维度带 domain 标注，便于下游按维度分辨。"""
    for t in it["topics"]:
        yield "<category>%s</category>" % _esc(t)
    if it["color"]:
        yield '<category domain="zvi:color">%s</category>' % _esc(it["color"])
    if it["format"]:
        yield '<category domain="zvi:format">%s</category>' % _esc(it["format"])
    for rg in it["regions"]:
        yield '<category domain="zvi:region">%s</category>' % _esc(rg)


MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
        ".mp4": "video/mp4", ".webm": "video/webm"}


def _mime(name):
    return MIME.get(os.path.splitext(name)[1].lower(), "application/octet-stream")


def _body_html(it, base):
    """把介绍与分类摊平成一段 HTML —— 纯 <description> 文本在阅读器里太素。"""
    rows = [("上线日期", it["date_label"] or it["original_date"] or ""),
            ("年份", "" if it["year_exact"] else ("约 %d（由名称推定）" % it["year"]
                                                 if it["year"] else "")),
            ("主色", it["color"]),
            ("话题", "、".join(it["topics"])),
            ("格式", it["format"]),
            ("地区", "、".join(it["regions"]))]
    meta = "".join(
        "<li><b>%s</b>：%s</li>" % (_esc(k), _esc(v))
        for k, v in rows if v)
    img = ""
    if it["image"]:
        img = ('<p><img src="%s%s" alt="%s"></p>'
               % (_esc(base), _esc(it["image_url"]), _esc(it["title"])))
    return ("%s<p>%s</p><ul>%s</ul>"
            % (img, _esc(it["description"]), meta))


def feed_xml(items, base=DEFAULT_BASE, feed_path="/rss.xml",
             title=FEED_TITLE, desc=FEED_DESC, limit=None):
    """生成 RSS 2.0 订阅源。limit=None 表示全量。"""
    base = base.rstrip("/")
    picked = items if limit is None else items[:limit]
    newest = next((_ts(i) for i in picked if _ts(i)), None)
    build_date = formatdate(newest if newest else None, usegmt=True)

    L = ['<?xml version="1.0" encoding="UTF-8"?>',
         '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
         '  <channel>',
         '    <title>%s</title>' % _esc(title),
         '    <link>%s/</link>' % _esc(base),
         '    <description>%s</description>' % _esc(desc),
         '    <language>en</language>',
         '    <lastBuildDate>%s</lastBuildDate>' % build_date,
         '    <generator>doodles-offline-mirror/1.0</generator>',
         '    <atom:link href="%s%s" rel="self" type="application/rss+xml"/>'
         % (_esc(base), _esc(feed_path)),
         '    <ttl>1440</ttl>']

    for it in picked:
        ts = _ts(it)
        L.append('    <item>')
        L.append('      <title>%s</title>' % _esc(it["title"]))
        L.append('      <link>%s%s</link>' % (_esc(base), _esc(it["page_url"])))
        L.append('      <guid isPermaLink="false">doodles.google/doodle/%s</guid>'
                 % _esc(it["slug"]))
        if ts:
            L.append('      <pubDate>%s</pubDate>' % formatdate(ts, usegmt=True))
        for c in _cats(it):
            L.append('      ' + c)
        if it["image"]:
            L.append('      <enclosure url="%s%s" type="%s" length="%d"/>'
                     % (_esc(base), _esc(it["image_url"]), _mime(it["image"]),
                        it["bytes"]))
        L.append('      <description>%s</description>'
                 % _cdata(_body_html(it, base)))
        L.append('    </item>')

    L += ['  </channel>', '</rss>', '']
    return "\n".join(L)


# ------------------------------------------------------------------ 导出

CSV_HEADER = ["序号", "标题", "上线日期", "年份", "年份来源", "原始日期", "主色",
              "话题", "格式", "地区", "图片文件", "图片字节", "详情页", "介绍"]


def manifest_rows(items):
    for i, it in enumerate(items, 1):
        yield [i, it["title"], it["date_label"] or "", it["year"] or "",
               "确切日期" if it["year_exact"] else ("名称推定" if it["year"] else ""),
               it["original_date"], it["color"], "|".join(it["topics"]),
               it["format"], "|".join(it["regions"]), it["image"], it["bytes"],
               it["page_url"], it["description"]]


def manifest_csv(items):
    """带 UTF-8 BOM —— 否则 Excel 打开中文列名是乱码。"""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(CSV_HEADER)
    for row in manifest_rows(items):
        w.writerow(row)
    return "\ufeff" + buf.getvalue()


def manifest_json(items):
    return json.dumps(
        {"generated_for": "doodles.google 离线镜像",
         "count": len(items),
         "fields": ["slug", "title", "date", "date_label", "original_date",
                    "year", "year_exact", "description", "color",
                    "topics", "format", "regions", "image", "image_url",
                    "bytes", "page_url"],
         "items": items},
        ensure_ascii=False, indent=1)


def total_bytes(items):
    return sum(it["bytes"] for it in items)


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.1f %s" % (n, unit) if unit != "B" else "%d B" % n
        n /= 1024.0


# ------------------------------------------------------------------ 筛选
# serve.py 的图片打包路由与页面前端共用同一套筛选语义，避免「页面显示 12 张、
# 实际打包 15 张」这种对不上的情况。

def _norm(s):
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def select(items, topic="", color="", form="", year="", q=""):
    tb = {_norm(x) for x in topic.split(",") if x.strip()}
    cb = {_norm(x) for x in color.split(",") if x.strip()}
    fb = {_norm(x) for x in form.split(",") if x.strip()}
    yb = {x.strip() for x in year.split(",") if x.strip()}
    kw = (q or "").strip().lower()
    out = []
    for it in items:
        if tb and not (tb & {_norm(t) for t in it["topics"]}):
            continue
        if cb and _norm(it["color"]) not in cb:
            continue
        if fb and _norm(it["format"]) not in fb:
            continue
        if yb:
            if str(it["year"] or "") not in yb:
                continue
        if kw and kw not in it["title"].lower() and kw not in it["description"].lower():
            continue
        out.append(it)
    return out


if __name__ == "__main__":
    its = load_items()
    print("条目 %d，图片合计 %s" % (len(its), human_size(total_bytes(its))))
    print("前 3 条：")
    for it in its[:3]:
        print("  %-14s %-50s %s" % (it["date_label"], it["title"][:50],
                                    ",".join(it["topics"])))
    print("无图片条目:", sum(1 for i in its if not i["bytes"]))
