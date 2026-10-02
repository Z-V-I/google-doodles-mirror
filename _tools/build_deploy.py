#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建 Cloudflare Pages 部署包。

产物：`_deploy/` —— 可直接 `wrangler pages deploy _deploy` 的目录。

【铁律】site/ 一个字节都不动。
所有与上线相关的改造（排除无引用资源、注入检索页 shim、加 _redirects/_headers/
robots.txt/免责声明）都只发生在本脚本的产物里。site/ 仍然是一个「原样镜像」，
随时可以继续用 serve.py 跑。

【为什么要有这个中间层，而不是直接传 site/】
1. site/ 里有 123 个无引用资源（273 MB），上传上去纯属浪费 —— 详见 build 报告。
2. 纯静态托管缺三样东西（检索接口、/logos 降级、图片包），需要就地补齐。
3. 上传包要能被反复重建，而不是手工在 site/ 里东改西改。

用法：
    python _tools/build_deploy.py                # 增量重建（硬链接，秒级）
    python _tools/build_deploy.py --copy         # 强制真实复制（跨盘/网络盘时用）
    python _tools/build_deploy.py --check        # 只校验现有 _deploy，不重建
"""

import argparse
import json
import os
import re
import shutil
import struct
import sys
import time
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(ROOT, "site")
SRCDIR = os.path.join(ROOT, "_deploy_src")
OUT = os.path.join(ROOT, "_deploy")

# Cloudflare Pages 的单文件上限。超了整次部署会被拒，所以在构建期就断言。
MAX_FILE_BYTES = 25 * 1024 * 1024

# 只有这些目录参与「无引用即回收」。范围刻意开得很窄：
# 全站扫描 + 无条件回收太危险，一旦引用扫描有盲区就会删掉真正在用的东西。
RECLAIM_ROOTS = ("assets/cms",)

# 参与引用扫描的文本类型。图片等二进制不扫（也不该扫：里面出现路径是巧合）。
TEXT_EXT = {".html", ".htm", ".js", ".mjs", ".css", ".json", ".xml",
            ".txt", ".svg", ".webmanifest", ".map", ".csv"}

# overlay 时跳过的文件（它们是这一层自己的说明，不该被传到站点根目录）
OVERLAY_SKIP = {"README.md"}

SHIM_REL = "vendor/search-api-shim.js"
SHIM_TAG = '  <script src="/vendor/search-api-shim.js"></script>\n'
SHIM_ANCHOR = '<script src="/vendor/recaptcha-stub.js">'
SHIM_TARGET = "search/index.html"

BLANK_REL = "assets/__blank.png"

# ---------------------------------------------------------------- 卡片图变体
# /img-c/card/<原名>.webp 是构建期用 _tools/gen_image_variants.py 生成的
# 480px WebP。这三处常量必须与下面两个文件保持一致，否则卡片图会 404：
#   _tools/gen_image_variants.py 的 THRESHOLD / WIDTH
#   _deploy_src/vendor/search-api-shim.js 的 CARD_VARIANT_MIN / CARD_VARIANT_PREFIX
#   serve.py 的 CARD_VARIANT_MIN / CARD_VARIANT_PREFIX
VARIANT_DIR_REL = "img-c/card"
VARIANT_PREFIX = "/img-c/card/"
VARIANT_MIN = 150 * 1024

# 需要注入懒加载与变体路径的页面。刻意只开这两类：首页（hero 轮播 16 张图
# 里 15 张是隐藏的却照样被下载）与详情页（每页 8 张相关推荐）。其余页面
# （about/404/disclaimer…）图片少且语义不同，不碰，避免误改。
IMG_TAG_RE = re.compile(r"<img\b[^>]*>")
IMG_SRC_RE = re.compile(r'src="(/img/[^"]+)"')
LAZY_ATTRS = ' loading="lazy" decoding="async"'

REPORT = os.path.join(ROOT, "_tools", "deploy_report.json")


# --------------------------------------------------------------------- 基础

def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.2f %s" % (n, unit) if unit != "B" else "%d B" % n
        n /= 1024.0


def iter_site_files(root=None):
    """遍历站点，产出 (绝对路径, 以 / 开头的站内路径)。"""
    root = root or SITE
    for r, _dirs, files in os.walk(root):
        for x in files:
            p = os.path.join(r, x)
            yield p, "/" + os.path.relpath(p, root).replace(os.sep, "/")


def in_reclaim(rel):
    return any(rel.startswith("/" + x + "/") for x in RECLAIM_ROOTS)


# ------------------------------------------------------- 无引用资源（回收）

def scan_refs():
    """扫描全站文本文件，收集对可回收目录的引用（含 CSS url()、JS 拼接前缀）。"""
    pat = re.compile(r"/(?:" + "|".join(re.escape(x) for x in RECLAIM_ROOTS)
                     + r")/[A-Za-z0-9_\-./]+")
    refs = set()
    scanned = 0
    for p, rel in iter_site_files():
        if in_reclaim(rel):
            continue
        if os.path.splitext(p)[1].lower() not in TEXT_EXT:
            continue
        try:
            if os.path.getsize(p) > 20 * 1024 * 1024:
                continue
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                t = f.read()
        except OSError:
            continue
        scanned += 1
        for m in pat.finditer(t):
            refs.add(m.group(0))
    return refs, scanned


def find_orphans():
    """可回收目录里、全站文本找不到任何引用的文件。

    这批文件的历史成因见 README「已知缺口」：它们原本服务于 /creating-a-doodle/
    页面，而那个页面没有进入交付范围、只停在暂存区，资源却已经落盘，
    于是引用方一消失就成了死重。按「引用方是否还在站内」判，比按「内容是否重复」判可靠。
    """
    refs, scanned = scan_refs()
    orphans = []
    for p, rel in iter_site_files():
        if not in_reclaim(rel):
            continue
        if rel in refs:
            continue
        try:
            orphans.append((rel, os.path.getsize(p)))
        except OSError:
            pass
    orphans.sort(key=lambda x: -x[1])
    return orphans, refs, scanned


# ------------------------------------------------------- 卡片图变体 + 懒加载

def html_kind(rel):
    """返回需要改写的页面类型："home" / "detail" / None。"""
    if rel == "/index.html":
        return "home"
    if rel.startswith("/doodle/") and rel.endswith("/index.html"):
        return "detail"
    return None


def variant_url(url, cache):
    """`/img/<name>` → `/img-c/card/<stem>.webp`，没有合适变体时返回 None。

    三条同时成立才换算，避免给出一个打不开或更胖的地址：
      · 原图 > VARIANT_MIN（阈值以下的根本不做变体）
      · 变体文件存在
      · 变体确实比原图小
    判定口径与 gen_image_variants.py、shim 完全一致（都按 150 KB）。

    【必须读源站 site/，不能读产物 _deploy/】改写是在 mirror 遍历过程中做的，
    而 os.walk 会先走到根目录的 index.html 和 doodle/，那时 _deploy/img/ 还没
    链接过来 —— 第一版就是这么写的，结果首页与全部详情页的变体判定全部落空
    （src 仍是原图，白改）。读 site/ 既没有这个时序问题，也更快。
    """
    if url in cache:
        return cache[url]
    res = None
    if url.startswith("/img/"):
        name = url[len("/img/"):]
        stem = os.path.splitext(name)[0]
        var_rel = "%s/%s.webp" % (VARIANT_DIR_REL, stem)
        try:
            src_p = os.path.join(SITE, "img", name.replace("/", os.sep))
            var_p = os.path.join(SITE, var_rel.replace("/", os.sep))
            if os.path.getsize(src_p) > VARIANT_MIN:
                if os.path.getsize(var_p) < os.path.getsize(src_p):
                    res = "/" + var_rel
        except OSError:
            res = None
    cache[url] = res
    return res


def transform_html(text, kind, slug, cache):
    """把卡片语境的 <img> 指向 480px 变体，并加懒加载（方案 A+B）。

    两条判据，都已全站核实：
      · 详情页主图 —— src 的文件名（去扩展名）等于页面 slug —— 保留原图、
        保持 eager。它是 LCP 元素，且宽屏下 .media-container 宽达 940px，
        塞 480px 变体会糊。（6375 个详情页里「第一张即主图」与
        「主图在 .media-container 内」两种判据 100% 一致。）
      · 其余 /img/ 的 <img> 都是卡片/缩略图语境（首页 hero 轮播、详情页
        相关推荐），一律换算变体并 lazy。
    首页首张（轮播里唯一可见的那张）保持 eager，免得压后 LCP。
    """
    n = [0]

    def repl(m):
        tag = m.group(0)
        sm = IMG_SRC_RE.search(tag)
        if not sm:
            return tag                      # 模板占位 / loading.gif / 非 /img/ 资源
        url = sm.group(1)
        if kind == "detail" and os.path.splitext(os.path.basename(url))[0] == slug:
            # 详情页主图：保留原图，但原站给它标了 loading="lazy" —— 它是首屏
            # 主视觉、也是 LCP 元素，lazy 只会把它推后。改成 eager 并提优先级。
            return tag.replace('loading="lazy"',
                               'loading="eager" fetchpriority="high"', 1)
        n[0] += 1
        var = variant_url(url, cache)
        if var:
            tag = tag.replace('src="%s"' % url, 'src="%s"' % var, 1)
        # 首页第 1 张、详情页主图之外的都 lazy；已带 loading 的不重复插
        if not (kind == "home" and n[0] == 1) and "loading=" not in tag:
            tag = tag[:4] + LAZY_ATTRS + tag[4:]
        return tag

    return IMG_TAG_RE.sub(repl, text)


# --------------------------------------------------------------------- 镜像

def mirror(orphan_rels, hardlink=True):
    """把 site/ 镜像到 _deploy/，跳过孤儿。

    默认硬链接：2.1 GB / 14k 个文件如果每次真复制要一两分钟，硬链接是秒级，
    且不额外占盘。代价是 _deploy 与 site 共享文件实体 —— 若之后 site/ 里的文件
    被「原地改写」，_deploy 会跟着变。实际不会发生：site/ 只由 rebuild.py 整体
    重建（新 inode），而重建后本来就该重跑本脚本。

    例外是首页与详情页：它们要在产物里注入懒加载与卡片变体路径，必须与 site/
    断开。做法是「读—改写—写」，一次 IO 落成独立 inode —— 既不改动 site/，
    也不产生任何删除动作（这点很实际：本环境对批量删除有闸门，而
    「临时文件 + os.rename」那种原子写会被计成删除，几千个文件必被拦下）。
    """
    if os.path.isdir(OUT):
        # 安全阀：只允许删我们自己的产物目录
        if os.path.basename(OUT) != "_deploy" or os.path.dirname(OUT) != ROOT:
            raise RuntimeError("拒绝删除非预期路径：%s" % OUT)
        shutil.rmtree(OUT)
    os.makedirs(OUT)

    n_link = n_copy = n_skip = n_rewrite = 0
    cache = {}
    for p, rel in iter_site_files():
        if rel in orphan_rels:
            n_skip += 1
            continue
        dst = os.path.join(OUT, rel.lstrip("/").replace("/", os.sep))
        d = os.path.dirname(dst)
        if d:
            os.makedirs(d, exist_ok=True)

        kind = html_kind(rel)
        if kind:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    text = f.read()
            except (OSError, UnicodeDecodeError):
                kind = None          # 读不动就退化为普通镜像
            else:
                slug = os.path.basename(os.path.dirname(rel)) if kind == "detail" else ""
                with open(dst, "w", encoding="utf-8", newline="") as f:
                    f.write(transform_html(text, kind, slug, cache))
                n_rewrite += 1
                continue

        if hardlink:
            try:
                os.link(p, dst)
                n_link += 1
                continue
            except OSError:
                pass  # 跨卷等原因退化为复制
        shutil.copy2(p, dst)
        n_copy += 1
    return n_link, n_copy, n_skip, n_rewrite


def overlay():
    """把 _deploy_src/ 里的部署专用文件铺到产物上（会覆盖同名文件）。"""
    n = 0
    for r, _dirs, files in os.walk(SRCDIR):
        for x in files:
            if x in OVERLAY_SKIP:
                continue
            p = os.path.join(r, x)
            rel = os.path.relpath(p, SRCDIR)
            dst = os.path.join(OUT, rel)
            d = os.path.dirname(dst)
            if d:
                os.makedirs(d, exist_ok=True)
            shutil.copy2(p, dst)
            n += 1
    return n


# ------------------------------------------------------------------ 生成物

def blank_png():
    """1x1 全透明 PNG，与 serve.py 里那份逐字节一致（同一段逻辑）。"""
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)   # 1x1 RGBA
    idat = zlib.compress(b"\x00\x00\x00\x00\x00")          # filter 0 + RGBA(0,0,0,0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", idat) + chunk(b"IEND", b""))


def write_blank():
    dst = os.path.join(OUT, BLANK_REL.replace("/", os.sep))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "wb") as f:
        f.write(blank_png())
    return len(blank_png())


def rewrite_in_place(path, fn):
    """改写产物里的文件。

    【关键】必须先 os.remove 断开硬链接。直接 open(...,'w') 会就地改掉与 site/
    共享的那个 inode，等于把 site/ 也改了 —— 那正是本脚本承诺绝不做的事。
    """
    with open(path, "rb") as f:
        raw = f.read()
    text = raw.decode("utf-8")
    new = fn(text)
    if new is None or new == text:
        return False
    os.remove(path)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(new)
    return True


def inject_shim():
    """把 search-api-shim.js 挂到检索页上。

    只挂检索页：全站 6,379 个页面都引用了同一份 bundle，但只有 /search/ 会去调
    /live/api/v1/doodles。全站注入会让每个访客在首页就白下 3.2 MB 数据。

    位置放在 recaptcha-stub.js 之前 —— 那一行是页面里第一个 vendor 脚本，
    而 bundle 在它后面几个标签，同步加载，所以 shim 一定先于 APIDoodles 执行。
    """
    src = os.path.join(SRCDIR, SHIM_REL.replace("/", os.sep))
    if not os.path.isfile(src):
        return "missing-source", ""
    dst = os.path.join(OUT, SHIM_REL.replace("/", os.sep))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)

    page = os.path.join(OUT, SHIM_TARGET.replace("/", os.sep))
    if not os.path.isfile(page):
        return "missing-page", SHIM_TARGET

    def fn(text):
        changed = False
        if "search-api-shim.js" not in text:
            i = text.find(SHIM_ANCHOR)
            if i >= 0:
                text = text[:i] + SHIM_TAG + text[i:]
                changed = True
        # 卡片模板里的 `<img src="" alt="">` 是 JS 渲染结果卡的骨架，src 由脚本
        # 事后填。给它加 lazy：只有滚进视口的那几张卡片图会被下载（模板是共用的，
        # 所以首屏可见的那几张也会被浏览器立即加载，不影响 LCP）。
        if '<img src="" alt="">' in text:
            text = text.replace(
                '<img src="" alt="">',
                '<img src="" alt=""%s>' % LAZY_ATTRS)
            changed = True
        return text if changed else None

    ok = rewrite_in_place(page, fn)
    return ("injected" if ok else "already"), SHIM_TARGET


DISCLAIMER_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>免责声明与来源说明 · Google Doodles 离线镜像</title>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 40px 20px 72px;
    font: 16px/1.75 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
          "Helvetica Neue", "PingFang SC", "Microsoft YaHei", sans-serif;
    color: #202124; background: #fff;
    max-width: 760px; margin-inline: auto;
  }}
  h1 {{ font-size: 28px; line-height: 1.35; margin: 0 0 8px; }}
  h2 {{ font-size: 18px; margin: 36px 0 10px; }}
  .sub {{ color: #5f6368; font-size: 14px; margin: 0 0 28px; }}
  .lead {{
    background: #f8f9fa; border-left: 4px solid #4285f4;
    padding: 14px 18px; border-radius: 0 8px 8px 0; margin: 0 0 8px;
  }}
  ul {{ padding-left: 22px; }}
  li {{ margin: 6px 0; }}
  code {{
    background: #f1f3f4; padding: 1px 6px; border-radius: 4px;
    font-size: 13.5px;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  }}
  a {{ color: #1a73e8; }}
  footer {{ margin-top: 44px; padding-top: 18px; border-top: 1px solid #dadce0;
            color: #5f6368; font-size: 13.5px; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #202124; color: #e8eaed; }}
    .sub, footer {{ color: #9aa0a6; }}
    .lead {{ background: #292a2d; }}
    code {{ background: #303134; }}
    a {{ color: #8ab4f8; }}
    footer {{ border-color: #3c4043; }}
  }}
</style>
</head>
<body>
  <h1>免责声明与来源说明</h1>
  <p class="sub">最后更新：{date}</p>

  <p class="lead">
    这是一个<strong>供学习与离线演示使用的网站镜像副本</strong>，与 Google LLC
    没有任何隶属、赞助或授权关系，也不是官方站点。官方站点是
    <a href="https://doodles.google" rel="noopener nofollow">doodles.google</a>。
  </p>

  <h2>权利归属</h2>
  <ul>
    <li>站内全部涂鸦图片、动画、介绍文案及其它美术素材，著作权属于
        Google LLC 及相关创作者、插画师。</li>
    <li>「Google」「Doodles」名称与相关标识是 Google LLC 的商标。</li>
    <li>本站对这些内容<strong>不主张任何权利，也未获得任何形式的授权</strong>。
        内容的呈现方式仅供技术研究与离线浏览之用。</li>
  </ul>

  <h2>我们自行编写的部分</h2>
  <ul>
    <li>把原站改造成可离线运行所需的全部构建、修补与验收脚本；</li>
    <li>检索页在无服务端环境下的数据加载适配层；</li>
    <li>RSS 订阅源、元数据导出与图片打包工具；</li>
    <li>本页与站点的部署配置。</li>
  </ul>

  <h2>数据与隐私</h2>
  <ul>
    <li>本站<strong>不收集、不存储、不传输任何用户数据</strong>：没有账号体系、
        没有分析埋点、没有第三方统计脚本。</li>
    <li>原站的 reCAPTCHA、广告与埋点脚本均已移除或替换为本地桩件，
        页面不会向你之外的任何服务器发起请求。</li>
    <li>本站不参与搜索引擎收录（见 <code>/robots.txt</code>），以避免与官方站点混淆。</li>
  </ul>

  <h2>权利人的请求</h2>
  <p>
    若你是上述内容的权利人，且不希望本副本继续存在，请通过站点所有者公开的联系方式
    提出，会尽快下线处理。
  </p>

  <footer>
    本站为个人学习用途的非商业项目 · 不含任何形式的广告或付费内容 ·
    <a href="/">返回首页</a>
  </footer>
</body>
</html>
"""


def make_disclaimer():
    dst = os.path.join(OUT, "disclaimer", "index.html")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    html = DISCLAIMER_HTML.format(date=time.strftime("%Y-%m-%d"))
    with open(dst, "w", encoding="utf-8", newline="") as f:
        f.write(html)
    return len(html.encode("utf-8"))


# --------------------------------------------------------------- 404 兜底页
# 【为什么必须自己放一个 404.html】
# Cloudflare Pages 在项目里找不到 404.html 时，会把**根 index.html 当成兜底**
# 返回，状态码还是 200。实测：`/creating-a-doodle/` 和随便编的 `/__nonexistent__/`
# 都返回 200 + 首页那份 HTML。
#
# 对本项目有两个具体坏处：
#   1. `/creating-a-doodle/` 是**故意移除**的营销页，回退到首页等于那条路由
#      看起来"还在"（虽然是别的内容），验收里也不好判定；
#   2. 软 404 会让爬虫把大量乱七八糟的地址都当成正常页（虽然 robots.txt 已禁索引，
#      但那是另一道闸门，不该互相顶替）。
#
# 放上 404.html 之后，Pages 会用这个文件、并以真正的 404 状态码返回。
NOTFOUND_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>页面不存在 · Google Doodles 离线镜像</title>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; min-height: 100vh;
    display: flex; align-items: center; justify-content: center;
    padding: 40px 20px;
    font: 16px/1.75 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
          "Helvetica Neue", "PingFang SC", "Microsoft YaHei", sans-serif;
    color: #202124; background: #fff;
  }}
  .box {{ max-width: 560px; }}
  .code {{
    font-size: 64px; font-weight: 700; letter-spacing: -2px;
    color: #dadce0; margin: 0 0 4px;
  }}
  h1 {{ font-size: 24px; margin: 0 0 12px; }}
  p {{ margin: 0 0 10px; color: #5f6368; }}
  ul {{ padding-left: 20px; color: #5f6368; }}
  a {{ color: #1a73e8; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #202124; color: #e8eaed; }}
    .code {{ color: #3c4043; }}
    p, ul {{ color: #9aa0a6; }}
    a {{ color: #8ab4f8; }}
  }}
</style>
</head>
<body>
  <div class="box">
    <p class="code">404</p>
    <h1>没有这个页面</h1>
    <p>这个地址在本站不存在。本站是 <code>doodles.google</code> 的离线镜像，
       只收录原站的公开页面，因此被移除的路径（例如创作流程页）不会在这里出现。</p>
    <p>可以试试：</p>
    <ul>
      <li><a href="/">首页</a> · <a href="/search/">检索全部涂鸦</a> · <a href="/about/">关于</a></li>
      <li><a href="/rss/">RSS 模式</a>（可按分类查看与整包下载）</li>
      <li><a href="/disclaimer/">免责声明与权利说明</a></li>
    </ul>
  </div>
</body>
</html>
"""


def make_404():
    dst = os.path.join(OUT, "404.html")
    with open(dst, "w", encoding="utf-8", newline="") as f:
        f.write(NOTFOUND_HTML)
    return len(NOTFOUND_HTML.encode("utf-8"))


# --------------------------------------------------------------------- 校验

REQUIRED = [
    "index.html",
    "search/index.html",
    "about/index.html",
    "rss/index.html",
    "rss.xml",
    "rss/latest.xml",
    "rss/doodles.json",
    "rss/doodles.csv",
    "_redirects",
    "_headers",
    "robots.txt",
    "404.html",
    "disclaimer/index.html",
    "vendor/search-api-shim.js",
    BLANK_REL,
]


def verify(keep_cms):
    problems = []
    files = list(iter_site_files(OUT))
    total = 0
    oversized = []
    for p, rel in files:
        try:
            s = os.path.getsize(p)
        except OSError:
            continue
        total += s
        if s > MAX_FILE_BYTES:
            oversized.append((rel, s))

    if oversized:
        problems.append("有 %d 个文件超过 25 MiB 上限：%s"
                        % (len(oversized), ", ".join(r for r, _ in oversized[:5])))

    have = set(rel for _, rel in files)
    for rel in REQUIRED:
        if "/" + rel not in have:
            problems.append("缺少必需文件：/%s" % rel)

    if "/" + SHIM_REL in have:
        page = os.path.join(OUT, SHIM_TARGET.replace("/", os.sep))
        try:
            with open(page, "r", encoding="utf-8", errors="ignore") as f:
                if "search-api-shim.js" not in f.read():
                    problems.append("检索页上没有挂上 shim")
        except OSError:
            problems.append("读不到检索页")

    missing_cms = [r for r in keep_cms if "/" + r not in have]
    if missing_cms:
        problems.append("被引用的 cms 资源被误删了：%s" % ", ".join(missing_cms[:5]))

    # 兜底 PNG 必须是合法 PNG 且真的 1x1
    bp = os.path.join(OUT, BLANK_REL.replace("/", os.sep))
    try:
        with open(bp, "rb") as f:
            d = f.read()
        if not d.startswith(b"\x89PNG\r\n\x1a\n"):
            problems.append("兜底图不是 PNG")
        elif d[16:24] != struct.pack(">II", 1, 1):
            problems.append("兜底图尺寸不是 1x1")
    except OSError:
        pass

    # ---- 图片性能闸门（方案 A+B）-----------------------------------------
    # 这几条不是功能正确性，而是防止后续改动把这次优化悄悄改回去。
    # 关键的一条是「源站未被污染」：所有 /img/ 必须是硬链接（与 site/ 同 inode），
    # 一旦有人写了 _deploy 里的原图，site/ 也会跟着变 —— 这正是本脚本承诺不做的事。
    opt = {"variants": 0, "variant_bytes": 0, "home_card_imgs": 0,
           "home_variant": 0, "detail_checked": 0, "detail_card_variant": 0,
           "detail_main_intact": 0, "lazy": 0, "srcPinned": True}

    vdir = os.path.join(OUT, VARIANT_DIR_REL.replace("/", os.sep))
    if not os.path.isdir(vdir):
        problems.append("缺少卡片图变体目录：/%s" % VARIANT_DIR_REL)
    else:
        vs = [f for f in os.listdir(vdir) if f.endswith(".webp")]
        opt["variants"] = len(vs)
        opt["variant_bytes"] = sum(os.path.getsize(os.path.join(vdir, f)) for f in vs)
        if len(vs) < 3000:
            problems.append("卡片图变体只有 %d 个，疑似未生成或生成中断" % len(vs))

    def read_page(rel):
        try:
            with open(os.path.join(OUT, rel.lstrip("/").replace("/", os.sep)),
                      encoding="utf-8", errors="ignore") as f:
                return f.read()
        except OSError:
            return ""

    home = read_page("/index.html")
    home_imgs = IMG_TAG_RE.findall(home)
    home_card = [t for t in home_imgs if "/img/" in t or "/img-c/" in t]
    opt["home_card_imgs"] = len(home_card)
    opt["home_variant"] = len([t for t in home_imgs if 'src="/img-c/' in t])
    opt["lazy"] += home.count(LAZY_ATTRS.strip().split()[0])
    if opt["home_variant"] < 8:
        problems.append("首页只有 %d 张图指向变体，改写疑似未生效" % opt["home_variant"])

    # 抽样详情页（确定性抽样：按路径排序后等距取 5 个）
    dood = os.path.join(OUT, "doodle")
    pages = []
    if os.path.isdir(dood):
        cands = sorted(d for d in os.listdir(dood)
                       if os.path.isfile(os.path.join(dood, d, "index.html")))
        if cands:
            step = max(1, len(cands) // 5)
            pages = [cands[i] for i in range(0, len(cands), step)][:5]

    for slug in pages:
        h = read_page("/doodle/%s/index.html" % slug)
        tags = IMG_TAG_RE.findall(h)
        if not tags:
            continue
        opt["detail_checked"] += 1
        first = IMG_SRC_RE.search(tags[0])
        # 主图必须还是 /img/ 原图，且改成 eager（它是 LCP，不能 lazy）
        if (first and first.group(1).startswith("/img/")
                and 'loading="eager"' in tags[0]):
            opt["detail_main_intact"] += 1
        else:
            problems.append("详情页 %s 的主图被动过了：%s" % (slug, tags[0][:90]))
        # 其余卡片图必须指向变体（除非原图本来就在阈值以下）
        for t in tags[1:]:
            sm = IMG_SRC_RE.search(t)
            if sm and 'src="/img-c/' not in t:
                src_p = os.path.join(OUT, "img",
                                     sm.group(1)[len("/img/"):].replace("/", os.sep))
                try:
                    if os.path.getsize(src_p) > VARIANT_MIN:
                        problems.append("详情页 %s 的卡片图未指向变体：%s"
                                        % (slug, sm.group(1)[:80]))
                        break
                except OSError:
                    pass
        opt["detail_card_variant"] += sum(1 for t in tags[1:] if 'src="/img-c/' in t)
        opt["lazy"] += sum(1 for t in tags if "loading=\"lazy\"" in t)

    # 源站未被污染：抽查 5 个 /img/ 是否仍与 site/ 同 inode
    idir = os.path.join(OUT, "img")
    if os.path.isdir(idir):
        names = sorted(os.listdir(idir))[:5]
        for nm in names:
            a = os.path.join(idir, nm)
            b = os.path.join(SITE, "img", nm)
            try:
                if os.stat(a).st_ino != os.stat(b).st_ino:
                    opt["srcPinned"] = False
                    problems.append("原图 %s 在产物里被改写了（site/ 可能被污染）" % nm)
            except OSError:
                pass

    # 检索页首屏 16 张卡片的字节预算。顺序即 shim 返回的 items 顺序，
    # 所以直接取 doodles.json 前 16 条 —— 这是访客进检索页真正要先下完的那批图。
    dj = os.path.join(OUT, "rss", "doodles.json")
    if os.path.isfile(dj):
        try:
            with open(dj, encoding="utf-8") as f:
                items = json.load(f).get("items", [])
        except (OSError, ValueError):
            items = []
        o_b = v_b = 0
        for it in items[:16]:
            b = it.get("bytes") or 0
            u = it.get("image_url") or ""
            o_b += b
            v = None
            if b > VARIANT_MIN and u.startswith("/img/"):
                stem = os.path.splitext(u[len("/img/"):])[0]
                vp = os.path.join(SITE, VARIANT_DIR_REL, stem + ".webp")
                try:
                    if os.path.getsize(vp) < b:
                        v = os.path.getsize(vp)
                except OSError:
                    v = None
            v_b += v if v is not None else b
        opt["first_screen_orig"] = o_b
        opt["first_screen_opt"] = v_b
        if o_b and v_b > o_b * 0.5:
            problems.append("检索页首屏图片只降到 %.0f%%（%.2f MB → %.2f MB），变体疑似没接上"
                            % (100.0 * v_b / o_b, o_b / 1024**2, v_b / 1024**2))

    return {"files": len(files), "bytes": total, "oversized": oversized,
            "problems": problems, "image_opt": opt}


# ---------------------------------------------------------------------- 主流程

def build(hardlink=True, do_verify=True):
    t0 = time.time()
    if not os.path.isdir(SITE):
        print("找不到 site/ 目录：%s" % SITE)
        return 2

    print("=" * 78)
    print("构建 Cloudflare Pages 部署包")
    print("=" * 78)

    orphans, refs, scanned = find_orphans()
    orphan_rels = set(r for r, _ in orphans)
    orphan_bytes = sum(s for _, s in orphans)
    keep_cms = sorted(r for r in refs if in_reclaim(r))
    keep_cms_rel = sorted(r.lstrip("/") for r in keep_cms)

    print("\n▸ 扫描引用      : 文本文件 %d 个，命中可回收目录的引用 %d 条"
          % (scanned, len(refs)))
    print("▸ 无引用资源    : %d 个 / %s（引用方不在交付范围内，回收零风险）"
          % (len(orphans), human(orphan_bytes)))
    print("▸ 保留的 cms    : %d 个（被 about 等页面真实引用）" % len(keep_cms))
    if orphans:
        print("    体积最大的 5 个：")
        for r, s in orphans[:5]:
            print("      %9s  %s" % (human(s), r))

    print("\n▸ 镜像 site/ → _deploy/ ...")
    t1 = time.time()
    n_link, n_copy, n_skip, n_rewrite = mirror(orphan_rels, hardlink=hardlink)
    print("    硬链接 %d / 复制 %d / 跳过 %d / 图片优化改写 %d（%.1fs）"
          % (n_link, n_copy, n_skip, n_rewrite, time.time() - t1))

    print("▸ 铺设部署专用文件 ...")
    n_overlay = overlay()
    n_png = write_blank()
    state, page = inject_shim()
    n_dis = make_disclaimer()
    n_404 = make_404()
    print("    覆盖层 %d 个 · 兜底图 %d B · 检索页 shim: %s · 免责声明 %d B · 404 页 %d B"
          % (n_overlay, n_png, state, n_dis, n_404))

    report = {"orphans": [{"path": r, "bytes": s} for r, s in orphans],
              "keep_cms": keep_cms_rel,
              "files": n_link + n_copy, "built_at": time.time()}

    code = 0
    if do_verify:
        v = verify(keep_cms_rel)
        report.update(v)
        print("\n" + "=" * 78)
        print("校验")
        print("=" * 78)
        print("  文件数        : %d" % v["files"])
        print("  总体积        : %s" % human(v["bytes"]))
        print("  超 25 MiB     : %d 个" % len(v["oversized"]))
        print("  单文件最大    : %s" % human(max(
            (os.path.getsize(p) for p, _ in iter_site_files(OUT)), default=0)))
        o = v.get("image_opt") or {}
        print("  ── 图片（方案 A+B）")
        print("  卡片变体      : %d 个 / %s" % (o.get("variants", 0),
                                               human(o.get("variant_bytes", 0))))
        print("  首页卡片图    : %d 张，其中指向变体 %d 张"
              % (o.get("home_card_imgs", 0), o.get("home_variant", 0)))
        print("  抽检详情页    : %d 个，卡片指向变体 %d 张，主图保持原图 %d 个"
              % (o.get("detail_checked", 0), o.get("detail_card_variant", 0),
                 o.get("detail_main_intact", 0)))
        print("  懒加载属性    : %d 处" % o.get("lazy", 0))
        print("  源站未被污染  : %s" % ("是" if o.get("srcPinned") else "**否**"))
        if o.get("first_screen_orig"):
            print("  检索页首屏    : %.2f MB → %.2f MB（%.0f%%）"
                  % (o["first_screen_orig"] / 1024**2, o["first_screen_opt"] / 1024**2,
                     100.0 * o["first_screen_opt"] / o["first_screen_orig"]))
        if v["problems"]:
            print("\n  ✗ 发现 %d 个问题：" % len(v["problems"]))
            for x in v["problems"]:
                print("      " + x)
            code = 1
        else:
            print("\n  ✓ 全部检查通过")

    with open(REPORT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("\n用时 %.1fs ·  产物 %s" % (time.time() - t0, OUT.replace(os.sep, "/")))
    print("报告 %s" % REPORT.replace(os.sep, "/"))
    return code


def main():
    ap = argparse.ArgumentParser(description="构建 Cloudflare Pages 部署包")
    ap.add_argument("--copy", action="store_true",
                    help="真实复制文件而不是硬链接（跨盘/网络盘时用）")
    ap.add_argument("--check", action="store_true",
                    help="只校验现有的 _deploy，不重建")
    args = ap.parse_args()

    if args.check:
        if not os.path.isdir(OUT):
            print("_deploy 不存在，先跑一次构建")
            return 2
        # 重建时会重算，这里就按当前产物里实际存在的 cms 文件当基准
        keep = sorted(r.lstrip("/") for r, _ in iter_site_files(OUT)
                      if in_reclaim(r))
        v = verify(keep)
        for k, val in v.items():
            print("  %-12s %s" % (k, val))
        return 1 if v["problems"] else 0

    return build(hardlink=not args.copy)


if __name__ == "__main__":
    sys.exit(main())
