#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
serve.py —— doodles.google 离线镜像的本地服务器。

用法：
    python serve.py                    # 默认 http://127.0.0.1:8765/
    python serve.py --port 9000
    python serve.py --no-open          # 不自动打开浏览器

它同时承担两件事：

1. 静态托管 site/ 目录，完整复刻 doodles.google 的路由形状：
       /                        -> site/index.html
       /search/                 -> site/search/index.html
       /about/                  -> site/about/index.html
       /doodle/<slug>/          -> site/doodle/<slug>/index.html  （6375 个）
       /games/、/storybooks/     -> 本地已抓取的互动涂鸦

2. 补齐原站的检索接口 /live/api/v1/doodles
   原站前端从谷歌云上的 appspot 服务取数据，离线必然失败。
   这里用本地 doodles_data.js 复刻同样的接口语义（同样的查询参数、同样的
   响应字段），因此 /search/ 页面的筛选、搜索、分页、Show More 都能真正跑起来。

3. 支撑 /rss/（RSS 模式）
   · /rss.xml、/rss/latest.xml 由 gen_rss.py 静态生成落盘，这里只是在响应时
     把里面的绝对地址换成请求头里的真实 Host —— 换端口访问也不会拿到错地址。
   · /rss/images.zip 必须现算：全量包 1.7 GB，不可能预先落盘。这里用
     HTTP/1.1 chunked 编码边打边发，zipfile 写给一个只能顺序写、不能 seek 的
     流对象（它会自动给每个条目补 data descriptor，产物依然能被标准工具解开）。

整个过程不发起任何外部网络请求。
"""
import os
import re
import sys
import html
import json
import time
import shutil
import zipfile
import argparse
import posixpath
import webbrowser
import threading
import mimetypes
from urllib.parse import urlparse, parse_qs, unquote
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from functools import partial

ROOT = os.path.dirname(os.path.abspath(__file__))
DOCROOT = os.path.join(ROOT, "site")
IMAGES = os.path.join(DOCROOT, "img")
sys.path.insert(0, os.path.join(ROOT, "_tools"))

mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("font/woff", ".woff")
mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("image/avif", ".avif")
mimetypes.add_type("application/manifest+json", ".webmanifest")

API_PATH = "/live/api/v1/doodles"

# 卡片图变体（见 _tools/gen_image_variants.py）。
# 阈值与目录必须与另外两处保持一致，否则卡片图会 404：
#   _tools/gen_image_variants.py 的 THRESHOLD / WIDTH
#   _deploy_src/vendor/search-api-shim.js 的 CARD_VARIANT_MIN / CARD_VARIANT_PREFIX
CARD_VARIANT_MIN = 150 * 1024
CARD_VARIANT_PREFIX = "/img-c/card/"
ZIP_PATH = "/rss/images.zip"

# RSS 模式：静态落盘的订阅源（响应时按真实 Host 改写其中的绝对地址）
FEEDS = {
    "/rss.xml":        os.path.join(DOCROOT, "rss.xml"),
    "/rss/latest.xml": os.path.join(DOCROOT, "rss", "latest.xml"),
}
# 改写后的订阅源字节流按 (文件, 来源) 缓存，避免每次请求都读一遍 5.7 MB
_FEED_CACHE = {}

# ------------------------------------------------------------------ 降级资源
def _blank_png():
    """就地生成 1x1 全透明 PNG，不依赖任何外部文件。"""
    import struct
    import zlib

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)   # 1x1 RGBA
    idat = zlib.compress(b"\x00\x00\x00\x00\x00")          # filter 0 + RGBA(0,0,0,0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", idat) + chunk(b"IEND", b""))


BLANK_PNG = _blank_png()

# 首页抓取于较晚时点，个别卡片指向比本地数据集更新的涂鸦（数据集最新为 2026-05）。
# 这些 slug 在 site/doodle/ 下不存在。与其抛裸 404，不如给一个与站点风格一致的说明页。
MISSING_DOODLE_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>暂无本地数据 - Google Doodles 离线镜像</title>
<style>
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
    background: #f8f9fa; padding: 24px;
    font-family: "Google Sans", "Product Sans", Roboto, "Segoe UI", "PingFang SC",
                 "Microsoft YaHei", Arial, sans-serif;
    color: #202124;
  }
  .card {
    max-width: 620px; width: 100%; background: #fff; border: 1px solid #dadce0;
    border-radius: 16px; padding: 40px 36px; box-shadow: 0 1px 3px rgba(60,64,67,.12);
  }
  .logo { font-size: 26px; font-weight: 500; letter-spacing: -.5px; margin-bottom: 26px; }
  .logo span:nth-child(1) { color: #4285f4; } .logo span:nth-child(2) { color: #ea4335; }
  .logo span:nth-child(3) { color: #fbbc05; } .logo span:nth-child(4) { color: #4285f4; }
  .logo span:nth-child(5) { color: #34a853; } .logo span:nth-child(6) { color: #ea4335; }
  .logo em { font-style: normal; color: #5f6368; font-weight: 400; margin-left: 10px; font-size: 17px; }
  h1 { font-size: 24px; font-weight: 500; margin: 0 0 14px; }
  p { font-size: 15px; line-height: 1.7; color: #5f6368; margin: 0 0 14px; }
  .slug {
    margin: 22px 0 26px; padding: 14px 16px; border-radius: 10px;
    background: #f1f3f4; border: 1px solid #e0e0e0;
    font-family: ui-monospace, Consolas, "Courier New", monospace;
    font-size: 13px; color: #3c4043; word-break: break-all;
  }
  .actions { display: flex; gap: 12px; flex-wrap: wrap; }
  a.btn { display: inline-block; padding: 11px 22px; border-radius: 100px;
          text-decoration: none; font-size: 14.5px; font-weight: 500; }
  a.primary { background: #1a73e8; color: #fff; }
  a.primary:hover { background: #1765cc; }
  a.ghost { color: #1a73e8; border: 1px solid #dadce0; }
  a.ghost:hover { background: #f8f9fa; }
  .foot { margin-top: 26px; padding-top: 18px; border-top: 1px solid #e8eaed;
          font-size: 12.5px; color: #80868b; }
</style>
</head>
<body>
  <div class="card">
    <div class="logo">
      <span>G</span><span>o</span><span>o</span><span>g</span><span>l</span><span>e</span>
      <em>Doodles 离线镜像</em>
    </div>
    <h1>这个涂鸦不在本地数据集中</h1>
    <p>本镜像的涂鸦清单来自一次全局导出（最新一条为 2026 年 5 月），
       而首页是稍晚时点抓取的，因此个别卡片指向的涂鸦尚未收录。</p>
    <div class="slug">__SLUG__</div>
    <div class="actions">
      <a class="primary" href="/">回到 Doodles 首页</a>
      <a class="ghost" href="/search/">浏览已收录的全部 Doodles</a>
    </div>
    <div class="foot">
      离线镜像 · 仅供学习与研究 · 其余页面与原站一致。
    </div>
  </div>
</body>
</html>
"""

# ------------------------------------------------------------------ 数据层
DATA = []


def load_data():
    """载入规范化后的涂鸦数据（与详情页生成器共用同一份逻辑）。"""
    global DATA
    try:
        import doodle_data
        DATA = doodle_data.load(ROOT)
    except Exception as e:
        print(f"⚠ 无法通过 doodle_data 载入，回退到直接解析：{e}")
        t = open(os.path.join(ROOT, "doodles_data.js"), encoding="utf-8").read()
        DATA = json.loads(t[t.index("["):].rstrip().rstrip(";"))
    return DATA


# ------------------------------------------------------------------ RSS 数据
RSS_ITEMS = []


def load_rss_items():
    """载入订阅条目。

    复用已经载入内存的 DATA（rss_common 只做形状转换，不会再解析一遍
    2.8 MB 的 doodles_data.js）。
    """
    global RSS_ITEMS
    try:
        import rss_common
        RSS_ITEMS = rss_common.from_normalized(DATA, ROOT)
    except Exception as e:
        print(f"⚠ RSS 数据载入失败（/rss/images.zip 将不可用）：{e}")
    return RSS_ITEMS


def _norm_key(s):
    """标签取值的归一化：非字母数字一律折叠成空格，再转小写、去首尾。

    【为什么不只是大小写与空白】
    检索页筛选面板里每个选项的 value 是**原站 API 的词表**，规则正是
    「非字母数字折叠成空格」：Football/Soccer 在面板里叫 `football soccer`，
    Plants & Flowers 叫 `plants flowers`，Children's Day 叫 `children s day`。
    而详情页上的主题 chip 与本地数据用的是原始标签（带 / 与 &）。
    两边不一致的后果是「筛选条件静默失效」：实测筛选 Football/Soccer 时
    条数对不上，且没有任何报错。

    所以这里对**两侧**都套同一条规则 —— 参数值一套、数据值一套，比较自然相等。
    注意本函数只用于标签类字段（topic/color/form/region）；
    标题子串匹配（title_like）用的是另一套更宽松的处理，见 query_doodles()。
    """
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _same_number(value, wanted):
    """按数值比较两个十进制字符串（'05' 与 '5' 等价）。

    【为什么不能写成 str(v) not in (wanted, "%02d" % v)】
    那样写在 v >= 10 时 "%02d" % v 就等于 str(v) 本身，等号恒成立 ——
    筛选会整个失效。实测「筛选月份 = 10」会把 11、12 月一起放进来
    （19 条结果里全是 11/12 月的涂鸦），而界面上完全看不出异常。
    正确做法是把**参数**去掉前导零再比，而不是把实际值补齐两位。
    """
    w = (wanted or "").lstrip("0")
    return str(value) == (w or "0")


def _matches(rec, field, wanted):
    """field 取值是否命中 wanted 集合（由 _norm_key 归一化，标点折叠、大小写不敏感）。"""
    if not wanted:
        return True
    if field == "color":
        have = [_norm_key(rec.get("color"))]
    elif field == "topic":
        have = [_norm_key(t) for t in (rec.get("topics") or [])]
    elif field == "form":
        have = [_norm_key(rec.get("format"))]
    elif field == "region":
        have = [_norm_key(t) for t in (rec.get("regions") or [])]
    else:
        have = []
    return bool(set(have) & wanted)


def query_doodles(qs):
    """复刻原站 /live/api/v1/doodles 的查询语义。"""
    def multi(*keys):
        out = []
        for k in keys:
            for v in qs.get(k, []):
                for part in str(v).split(","):
                    part = _norm_key(part)
                    if part:
                        out.append(part)
        return set(out)

    title_like = (qs.get("title_like") or qs.get("q") or [""])[0].strip().lower()
    colors = multi("color_tags")
    topics = multi("topic_tags")
    forms = multi("form_tags")
    regions = multi("nationality_tags")

    year = (qs.get("date_like_year") or [""])[0].strip()
    month = (qs.get("date_like_month") or [""])[0].strip()
    day = (qs.get("date_like_day") or [""])[0].strip()

    # art_style_tags / sort_tags 在本地数据里没有对应维度，
    # 一旦用户勾选，这里保持「不缩小结果集」而不是返回空，避免看起来像坏了。
    try:
        page = max(1, int((qs.get("page") or ["1"])[0]))
    except ValueError:
        page = 1
    try:
        limit = int((qs.get("limit") or ["16"])[0])
        limit = max(1, min(limit, 200))
    except ValueError:
        limit = 16

    direction = ((qs.get("sort_direction") or ["desc"])[0] or "desc").lower()

    rows = []
    for r in DATA:
        if title_like and title_like not in (r.get("title") or "").lower() \
                and title_like not in (r.get("slug") or "").lower():
            continue
        if not _matches(r, "color", colors):
            continue
        if not _matches(r, "topic", topics):
            continue
        if not _matches(r, "form", forms):
            continue
        if not _matches(r, "region", regions):
            continue
        if year or month or day:
            rd = r.get("run_date")
            if not rd:
                continue
            y, mo, d = rd
            if year and str(y) != year:
                continue
            # 前端 select 的 value 可能是 1..12 或 01..12，按数值比较
            if month and not _same_number(mo, month):
                continue
            if day and not _same_number(d, day):
                continue
        rows.append(r)

    # 排序：有确切日期的按日期排在前，无日期的按标题排在后面。
    # 这样无论升序降序，结果都稳定可复现，且不会让「无日期」条目插到前面。
    dated = [r for r in rows if r.get("run_date")]
    undated = sorted((r for r in rows if not r.get("run_date")),
                     key=lambda r: (r.get("title") or r.get("slug") or "").lower())
    dated.sort(key=lambda r: r["run_date"], reverse=(direction != "asc"))
    rows = dated + undated

    total = len(rows)
    start = (page - 1) * limit
    chunk = rows[start:start + limit]
    has_next = start + limit < total

    def card_image(r):
        """卡片场景用的图：超过阈值的原图改用 480px WebP 变体。

        【为什么 high_res_url 返回的是「卡片尺寸」而不是原图】
        前端只用 high_res_url 渲染卡片 —— bundle 里它只出现 2 次，都在卡片场景
        （检索结果的 appendResults、首页 discover 卡片），而 low_res_url 一次都没用到。
        卡片在 3 列布局下显示宽度只有约 400px，下 1000–2000px 宽的原图纯属浪费：
        检索页首屏 16 张要 7.04 MB，换成 480px WebP 后实测约 1.7 MB。
        详情页不走这个接口，仍然用 /img/ 下的原图，画质不受影响。
        """
        name = os.path.basename(r.get("image_file") or "")
        orig = r.get("image_path") or ("/img/" + name)
        if not name:
            return orig
        try:
            if os.path.getsize(os.path.join(IMAGES, name)) <= CARD_VARIANT_MIN:
                return orig
        except OSError:
            return orig
        return CARD_VARIANT_PREFIX + os.path.splitext(name)[0] + ".webp"

    def item(r):
        rd = r.get("run_date")
        card = card_image(r)
        return {
            "name": r.get("slug"),
            "title": r.get("title"),
            "alternativeTitle": "",
            "high_res_url": card,
            "low_res_url": card,
            # 检索结果列表读的是 run_date_array
            "run_date_array": list(rd) if rd else None,
            # 颜色轮那个模块读的是三个标量
            "run_date_year": rd[0] if rd else None,
            "run_date_month": rd[1] if rd else None,
            "run_date_day": rd[2] if rd else None,
            "color_tags": [r.get("color")] if r.get("color") else [],
            "topic_tags": r.get("topics") or [],
            "form_tags": [r.get("format")] if r.get("format") else [],
        }

    return {"result": [item(r) for r in chunk], "next": has_next,
            "total": total, "page": page, "limit": limit}


# ------------------------------------------------------------------ 流式打包
class ChunkedSink:
    """把 zipfile 的输出按 HTTP/1.1 chunked 编码推给 socket。

    【为什么不用 Content-Length】
    全量图片包 1.7 GB，不可能先落盘算长度再发；边打边发就只能用 chunked。

    【为什么故意不实现 seek/tell】
    zipfile 拿到一个「没有 tell()」的流对象时，会自己套一层 _Tellable 记录
    偏移，并把每个条目的大小写进 data descriptor（而不是回填到 local header）。
    这正是顺序流需要的形态。反过来，如果这里假装可寻址，zipfile 会去 seek
    回填，socket 上必然失败。产物依然是标准 zip，Python / 7-Zip / 资源管理器
    都能正常解开（_tools/verify_rss.py 会实际回读校验一遍）。
    """

    def __init__(self, wfile):
        self.wfile = wfile
        self.total = 0

    def write(self, data):
        if not data:
            return 0
        self.wfile.write(b"%x\r\n" % len(data))
        self.wfile.write(data)
        self.wfile.write(b"\r\n")
        self.total += len(data)
        return len(data)

    def flush(self):
        self.wfile.flush()

    def close(self):
        """写终止块并落盘。zipfile 因为拿到的是外部 fileobj，不会替我们调它。"""
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


ZIP_README = """\
doodles.google 离线镜像 · 图片包
================================

包内结构
  images/        {n} 张涂鸦图片，文件名与订阅源里 <enclosure url> 的最后一段一致
  manifest.csv   每张图对应的标题、上线日期、年份、主色、话题、格式、地区、详情页地址、介绍
  manifest.json  同一份数据的结构化版本（topics 为数组）

怎么用
  · manifest.csv 可直接用 Excel 打开（UTF-8 BOM，中文列名不乱码）。
  · 想按分类挑图：用「话题」「主色」「格式」三列筛选即可。
  · 想按名称找图：用「图片文件」列，和 images/ 下的文件名一一对应。

数据说明
  · 「上线日期」仅 {dated} 条为官方确切日期，其余留空。
  · 「年份」另有 {approx} 条由涂鸦名称推定，manifest.csv 的「年份来源」列已逐条标注。
  · 全部内容来自本地离线镜像，打包过程不发起任何联网请求。

生成时间：{when}
"""


# ------------------------------------------------------------------ HTTP 层
class Handler(SimpleHTTPRequestHandler):
    server_version = "DoodlesOfflineMirror/1.0"
    protocol_version = "HTTP/1.1"

    # ---------- 工具
    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    # ---------- RSS 模式
    def _origin(self):
        """请求实际到达的基址，用来改写订阅源里的绝对链接。

        <link> / <enclosure> 必须是绝对地址（阅读器没有「当前页」概念）。
        构建期只能先写一个默认基址，这里按请求头里的 Host 再改一次 ——
        这样换端口、换主机名访问，订阅源里的地址都还是对的。
        """
        host = self.headers.get("Host")
        if not host:
            addr = self.server.server_address
            host = "%s:%s" % (addr[0], addr[1])
        return "http://" + host

    def _send_feed(self, local, origin):
        import rss_common
        key = (local, origin)
        body = _FEED_CACHE.get(key)
        if body is None:
            with open(local, "rb") as f:
                body = f.read()
            if origin != rss_common.DEFAULT_BASE:
                body = body.replace(rss_common.DEFAULT_BASE.encode("utf-8"),
                                    origin.encode("utf-8"))
            _FEED_CACHE[key] = body
        self.send_response(200)
        self.send_header("Content-Type", "application/rss+xml; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_image_zip(self, items):
        """边打边发：images/ + manifest.csv + manifest.json + README.txt。"""
        import rss_common
        readme = ZIP_README.format(
            n=len(items),
            dated=sum(1 for it in items if it["year_exact"]),
            approx=sum(1 for it in items if it["year"] and not it["year_exact"]),
            when=time.strftime("%Y-%m-%d %H:%M:%S"))
        # 全量包用固定名字，筛选过的包把条数写进名字，避免下载完分不清
        full = bool(RSS_ITEMS) and len(items) == len(RSS_ITEMS)
        name = "doodles-images.zip" if full else "doodles-images-%d.zip" % len(items)

        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", 'attachment; filename="%s"' % name)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        sink = ChunkedSink(self.wfile)
        packed = skipped = 0
        try:
            with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_STORED,
                                 allowZip64=True) as zf:
                # 清单先写，这样即使后面下载中断，解出来的包里也有目录
                zf.writestr("README.txt", readme)
                zf.writestr("manifest.csv", rss_common.manifest_csv(items))
                zf.writestr("manifest.json", rss_common.manifest_json(items))
                for it in items:
                    src = os.path.join(IMAGES, it["image"]) if it["image"] else ""
                    if not src or not os.path.isfile(src):
                        skipped += 1
                        continue
                    zi = zipfile.ZipInfo("images/" + it["image"], (2026, 1, 1, 0, 0, 0))
                    zi.compress_type = zipfile.ZIP_STORED
                    zi.external_attr = 0o644 << 16
                    with zf.open(zi, "w") as dst, open(src, "rb") as fsrc:
                        shutil.copyfileobj(fsrc, dst, 1024 * 256)
                    packed += 1
            sink.close()
        except (BrokenPipeError, ConnectionResetError):
            # 用户中途取消下载：正常现象，不该刷一屏堆栈
            self.close_connection = True
            self.log_message("ZIP 打包被客户端中断（已发出 %s）",
                             rss_common.human_size(sink.total))
            return
        self.log_message("ZIP 打包完成 %d 张 / %s（跳过 %d）",
                         packed, rss_common.human_size(sink.total), skipped)

    def translate_path(self, path):
        path = path.split("?", 1)[0].split("#", 1)[0]
        path = posixpath.normpath(path)
        parts = [p for p in path.split("/") if p and p not in (".", "..")]
        local = os.path.join(self.directory, *parts)

        if os.path.isdir(local):
            idx = os.path.join(local, "index.html")
            if os.path.isfile(idx):
                return idx
        if not os.path.splitext(local)[1] and os.path.isdir(local):
            idx = os.path.join(local, "index.html")
            if os.path.isfile(idx):
                return idx
        if not os.path.exists(local) and not os.path.splitext(local)[1]:
            if os.path.isfile(local + ".html"):
                return local + ".html"
        return local

    # ---------- 路由
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.rstrip("/") == API_PATH.rstrip("/"):
            qs = parse_qs(parsed.query, keep_blank_values=True)
            try:
                payload = query_doodles(qs)
            except Exception as e:
                self._send_json({"error": str(e), "result": [], "next": False}, 500)
                return
            self.log_message('API %s -> %d 条 (page %s)', parsed.query[:90],
                             len(payload["result"]), payload["page"])
            self._send_json(payload)
            return

        # ---- RSS 模式 ----
        # 订阅源是构建期落盘的静态文件，这里只把里面的默认基址换成请求实际用的
        # 地址（见 _origin 的说明）。
        if parsed.path in FEEDS:
            local = FEEDS[parsed.path]
            if os.path.isfile(local):
                self._send_feed(local, self._origin())
                return

        # 图片包必须现算：全量 1.7 GB。筛选参数与页面 / rss/ 上的控件同一套语义
        # （共用 rss_common.select），所以「页面显示 128 张」和「包里 128 张」必定一致。
        if parsed.path == ZIP_PATH:
            if not RSS_ITEMS:
                self._send_json({"error": "RSS 数据未载入"}, 503)
                return
            import rss_common
            qs = parse_qs(parsed.query, keep_blank_values=True)

            def one(k):
                return (qs.get(k) or [""])[0]

            try:
                picked = rss_common.select(
                    RSS_ITEMS, topic=one("topic"), color=one("color"),
                    form=one("form"), year=one("year"), q=one("q"))
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
                return
            self.log_message('ZIP %s -> %d 张 (%s)',
                             parsed.query[:70] or "(全量)", len(picked),
                             rss_common.human_size(rss_common.total_bytes(picked)))
            self._send_image_zip(picked)
            return

        # 浏览器会在解析出文档里的 <link rel="icon"> 之前，先对根路径探测一次
        # /favicon.ico。原站的图标声明的是 /static/favicon-*.png 与
        # /assets/brand/branding/... 这几个名字，没有 /favicon.ico 这个「约定回退名」，
        # 于是控制台会稳定留下一条 Failed to load resource: 404（headless 下尤其容易复现）。
        # 这里把回退名指向站点自己声明的那个 32px 图标 —— 同一份图片，不新增素材，
        # 也不改变任何页面的 <link> 声明。
        if parsed.path == "/favicon.ico":
            icon = os.path.join(DOCROOT, "static", "favicon-32.png")
            if os.path.isfile(icon):
                with open(icon, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

        # 互动涂鸦的 JS 里写死了 /logos/<年份>/<项目>/<rc>/xxx.png 这类绝对路径。
        # 当年抓取时只落盘了其中一部分，且本网络已无法直连 www.google.com 补齐
        # （见 README「已知缺口」）。这里对未落盘的 /logos/ 资源返回 1x1 透明 PNG：
        # 游戏会静默降级（缺图处为空白），而不是抛一屏 404 打断渲染。
        if parsed.path.startswith("/logos/"):
            if not os.path.isfile(self.translate_path(parsed.path)):
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(BLANK_PNG)))
                self.end_headers()
                self.wfile.write(BLANK_PNG)
                return

        # 首页快照指向、但不在本地数据集里的涂鸦详情页
        m = re.match(r"^/doodle/([^/]+)/?$", parsed.path)
        if m and not os.path.isfile(self.translate_path(parsed.path)):
            body = MISSING_DOODLE_HTML.replace(
                "__SLUG__", html.escape("/doodle/%s/" % m.group(1))).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        super().do_GET()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("X-Offline-Mirror", "doodles.google")
        super().end_headers()

    def guess_type(self, path):
        """给文本类资源补上 charset，避免中文/弯引号乱码。"""
        ctype = super().guess_type(path)
        if ctype in ("text/html", "text/css", "text/javascript",
                     "application/javascript", "image/svg+xml",
                     "application/json"):
            return ctype + "; charset=utf-8"
        return ctype

    def log_message(self, fmt, *args):
        msg = fmt % args
        if "favicon" in msg:
            return
        sys.stderr.write("  " + msg + "\n")


def main():
    ap = argparse.ArgumentParser(description="doodles.google 离线镜像服务器")
    ap.add_argument("--port", type=int, default=8765, help="监听端口（默认 8765）")
    ap.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    ap.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    args = ap.parse_args()

    if not os.path.isdir(DOCROOT):
        sys.exit(f"找不到镜像目录：{DOCROOT}\n请先运行 _tools/build_site.py 生成。")

    load_data()
    load_rss_items()
    with_date = sum(1 for r in DATA if r.get("run_date"))

    handler = partial(Handler, directory=DOCROOT)
    httpd = ThreadingHTTPServer((args.host, args.port), handler)
    url = f"http://{args.host}:{args.port}/"

    print("=" * 64)
    print("  Google Doodles 离线镜像 已启动")
    print("=" * 64)
    print(f"  文档根 : {DOCROOT}")
    print(f"  地址   : {url}")
    print(f"  涂鸦数 : {len(DATA)}（其中 {with_date} 条带确切日期）")
    print()
    print("  路由：")
    print("    /                    首页")
    print("    /search/             检索页（本地接口已复刻，筛选/分页可用）")
    print("    /about/              关于 Doodles")
    print("    /doodle/<slug>/      涂鸦详情页（6375 个）")
    print("    /rss/                RSS 模式（订阅视图 + 筛选 + 打包下载）")
    print("    /rss.xml             全量订阅源（6375 条）")
    print(f"    {ZIP_PATH}   图片包（实时打包，支持 topic/color/form/year/q 筛选）")
    print(f"    {API_PATH}   本地检索接口")
    print()
    print("  按 Ctrl+C 停止")
    print("=" * 64)

    if not args.no_open:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
