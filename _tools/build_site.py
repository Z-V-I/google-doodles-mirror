#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_site.py —— 把抓取到的 doodles.google 页面改造成完全离线的本地镜像。

流程：
  1. 扫描源页面，收集所有远程静态资源 URL
  2. 下载可获取的资源到 site/assets/ 与 site/static/
  3. 为无法直连的资源生成等价占位图
  4. 为第三方脚本生成本地桩件
  5. 重写 HTML/CSS 中所有 URL 为本地路径
"""
import os
import re
import io
import sys
import json
import zlib
import hashlib
import urllib.request
import urllib.error
from urllib.parse import urlparse, unquote

import PIL
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

SITE = "site"
ASSETS = os.path.join(SITE, "assets")
STATIC = os.path.join(SITE, "static")

# 源页面 -> 本地路由
PAGES = {
    "_google_clone/index_local.html": "site/index.html",
    "_clone_search/page.html":        "site/search/index.html",
    "_clone_search/about-page.html":  "site/about/index.html",
    # /creating-a-doodle/ 已按需求整站移除。映射摘掉后，
    # _clone_search/creating-page.html 不再参与任何构建（源料保留，未删）。
}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

manifest = {"downloaded": {}, "placeholder": {}, "failed": {}, "stubs": {}}


# ---------------------------------------------------------------- 工具
def log(msg):
    print(msg, flush=True)


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), r.headers.get("Content-Type", "")


def safe_name(url, prefix_len=0):
    """把 URL 变成稳定的本地文件名，保留原扩展名。"""
    p = urlparse(url)
    path = unquote(p.path)
    base = os.path.basename(path)
    stem, ext = os.path.splitext(base)
    if not ext:
        ext = ".bin"
    # 用 URL 的目录部分做短哈希，避免同名冲突
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    return f"{h}{ext.lower()}"


def placeholder_image(w, h, label, out_path, bg=(32, 33, 36), fg=(154, 160, 166)):
    """生成一张带文字的占位图，视觉上不刺眼。"""
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    # 斜线纹理
    step = 28
    for x in range(-h, w, step):
        d.line([(x, h), (x + h, 0)], fill=(45, 47, 51), width=2)
    try:
        from PIL import ImageFont
        font = ImageFont.load_default()
    except Exception:
        font = None
    txt = label[:60]
    bbox = d.textbbox((0, 0), txt, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(((w - tw) / 2, (h - th) / 2), txt, fill=fg, font=font)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    img.save(out_path, "JPEG" if out_path.lower().endswith((".jpg", ".jpeg")) else "PNG")
    return out_path


# ---------------------------------------------------------------- 步骤 1：收集远程资源
URL_RE = re.compile(r"""https?://[^\s"'<>\\)]+""")

def collect_urls():
    urls = set()
    for src in PAGES:
        text = open(src, encoding="utf-8", errors="replace").read()
        for m in URL_RE.finditer(text):
            urls.add(m.group(0).rstrip(").,;'\"&"))
    for src in ["_google_clone/*.css"]:
        pass
    # 也扫描 CSS
    for css in os.listdir(STATIC):
        if css.endswith(".css"):
            text = open(os.path.join(STATIC, css), encoding="utf-8", errors="replace").read()
            for m in URL_RE.finditer(text):
                urls.add(m.group(0).rstrip(").,;'\"&"))
    return sorted(urls)


# ---------------------------------------------------------------- 步骤 2：下载 & 归类
def asset_local_path(url):
    """决定远程资源的本地存放路径（返回站内绝对路径）。"""
    p = urlparse(url)
    host, path = p.netloc.lower(), unquote(p.path)

    if host == "www.gstatic.com" and path.startswith("/marketing-cms/"):
        return "/assets/cms" + path[len("/marketing-cms"):]
    if host == "www.gstatic.com" and path.startswith("/images/branding/"):
        return "/assets/brand" + path[len("/images"):]
    if host == "www.gstatic.com" and path.startswith("/glue/"):
        return "/vendor/glue" + path[len("/glue"):]
    if host == "www.gstatic.com" and path.startswith("/charts/"):
        return "/vendor/charts" + path[len("/charts"):]
    if host in ("img.youtube.com", "i.ytimg.com"):
        # /vi/<id>/maxresdefault.jpg
        m = re.match(r"/vi/([\w-]+)/", path)
        vid = m.group(1) if m else "unknown"
        return f"/assets/yt/{vid}.jpg"
    if host.endswith("googleusercontent.com"):
        return f"/assets/brand/guc-{safe_name(url)}"
    if host == "www.googletagmanager.com":
        return f"/vendor/gtm{safe_name(url)}"
    if host.endswith("googleplex.com"):
        return f"/assets/misc/{safe_name(url)}"
    if host in ("fonts.googleapis.com", "fonts.gstatic.com"):
        return "/fonts/fonts.css"
    return None


# 当前网络环境下已知不可达的主机 —— 直接跳到占位，避免逐个超时
BLOCKED_HOSTS = (
    "img.youtube.com", "i.ytimg.com", "ytimg.com", "www.youtube.com", "youtube.com",
    "youtu.be", "lh3.googleusercontent.com", "googleusercontent.com",
    "googleplex.com", "doodles.google", "www.doodles.google",
)

def do_download():
    urls = collect_urls()
    log(f"[收集] 共 {len(urls)} 个远程 URL")
    os.makedirs(os.path.join(ASSETS, "yt"), exist_ok=True)

    for url in urls:
        local = asset_local_path(url)
        if not local:
            continue
        dest = os.path.join(SITE, local.lstrip("/"))
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            manifest["downloaded"][url] = local
            continue

        host = urlparse(url).netloc.lower()
        if any(host == h or host.endswith("." + h) for h in BLOCKED_HOSTS):
            manifest["failed"][url] = "host blocked in this network"
            continue

        os.makedirs(os.path.dirname(dest), exist_ok=True)
        try:
            body, ctype = fetch(url, timeout=12)
            with open(dest, "wb") as f:
                f.write(body)
            manifest["downloaded"][url] = local
            log(f"  ✓ {url[:96]}  ({len(body)}B)")
        except Exception as e:
            manifest["failed"][url] = str(e)[:120]
            log(f"  ✗ {url[:96]}  -> {str(e)[:60]}")

    log(f"[下载] 成功 {len(manifest['downloaded'])}，失败 {len(manifest['failed'])}")


def do_placeholders():
    """为下载失败 / 无法直连的资源生成等价占位。"""
    for url, local in list(manifest["downloaded"].items()):
        pass
    # 失败项 & 未处理的 youtube / googleusercontent
    targets = dict(manifest["failed"])
    for url in collect_urls():
        l = asset_local_path(url)
        if l and not os.path.exists(os.path.join(SITE, l.lstrip("/"))):
            targets.setdefault(url, "missing")
    for url, _err in targets.items():
        local = asset_local_path(url)
        if not local:
            continue
        dest = os.path.join(SITE, local.lstrip("/"))
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            continue
        if "/assets/yt/" in local:
            placeholder_image(1280, 720, "Video thumbnail (offline)", dest)
        elif local.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
            placeholder_image(640, 360, "Image (offline)", dest)
        else:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            open(dest, "w", encoding="utf-8").write("/* offline placeholder */\n")
        manifest["placeholder"][url] = local
    log(f"[占位] 生成 {len(manifest['placeholder'])} 个占位资源")


# ---------------------------------------------------------------- 步骤 3：第三方桩件
STUB_JS = {
"youtube-api.js": """/* 本地桩件：替代 https://www.youtube.com/player_api 与 /iframe_api
   目标：页面在不联网的情况下不抛错，视频位显示为占位。 */
(function () {
  if (window.YT) return;
  function Player(el) {
    this.el = el; this._state = -1;
    // 用占位图替换 iframe 区域
    try {
      var host = typeof el === 'string' ? document.getElementById(el) : el;
      if (host) {
        host.innerHTML = '<div style="width:100%;height:100%;display:flex;align-items:center;'
          + 'justify-content:center;background:#202124;color:#9aa0a6;font:14px/1.4 Roboto,Arial,sans-serif;'
          + 'text-align:center;padding:12px;box-sizing:border-box;">视频不可用（离线镜像）</div>';
      }
    } catch (e) {}
  }
  Player.prototype.playVideo = function () {};
  Player.prototype.pauseVideo = function () {};
  Player.prototype.stopVideo = function () {};
  Player.prototype.seekTo = function () {};
  Player.prototype.getPlayerState = function () { return -1; };
  Player.prototype.getCurrentTime = function () { return 0; };
  Player.prototype.getDuration = function () { return 0; };
  Player.prototype.setVolume = function () {};
  Player.prototype.mute = function () {};
  Player.prototype.unMute = function () {};
  Player.prototype.destroy = function () {};
  Player.prototype.addEventListener = function () {};
  Player.prototype.removeEventListener = function () {};
  var YT = window.YT = window.YT || {};
  YT.Player = Player;
  YT.PlayerState = { UNSTARTED: -1, ENDED: 0, PLAYING: 1, PAUSED: 2, BUFFERING: 3, CUED: 5 };
  YT.loaded = 1;
  var cbs = [];
  YT.ready = function (fn) { if (typeof fn === 'function') { cbs.push(fn); } };
  YT._fireReady = function () { cbs.forEach(function (f) { try { f(); } catch (e) {} }); };
  setTimeout(function () { YT._fireReady(); }, 0);
  if (typeof window.onYouTubeIframeAPIReady === 'function') {
    try { window.onYouTubeIframeAPIReady(); } catch (e) {}
  }
})();
""",
"google-charts-loader.js": """/* 本地桩件：替代 https://www.gstatic.com/charts/loader.js */
(function () {
  var google = window.google = window.google || {};
  google.charts = google.charts || {};
  google.charts.load = function () {};
  google.charts.setOnLoadCallback = function (fn) { if (typeof fn === 'function') { setTimeout(fn, 0); } };
  google.visualization = google.visualization || {};
  google.visualization.LineChart = function () {};
  google.visualization.ColumnChart = function () {};
  google.visualization.PieChart = function () {};
  google.visualization.DataTable = function () {};
})();
""",
"gtm-stub.js": """/* 本地桩件：替代 Google Tag Manager —— 本地镜像不做任何埋点上报。 */
window.dataLayer = window.dataLayer || [];
window.dataLayer.push = function () { return 0; };
window.google_tag_manager = window.google_tag_manager || {};
window.google_tag_manager._stub = true;
""",
"glue-cookienotificationbar.js": """/* 本地桩件：替代 Glue  Cookie 通知条。
   离线镜像无需征求 Cookie 同意，因此直接空实现，避免弹条遮挡页面。 */
(function () { window.glueCookieNotificationBarLoaded = true; })();
""",
# 这张样式表被 about / search 两页用 <link rel="stylesheet"> 引入。
# JS 已经桩掉、通知条不会渲染，所以样式留空即可 —— 但文件必须存在，
# 否则浏览器会多出一次失败的资源请求。
"glue-cookienotificationbar.css": """/* 本地桩件：替代 Glue Cookie 通知条样式（离线镜像不展示通知条）。 */
""",
"recaptcha-stub.js": """/* 本地桩件：替代 https://www.google.com/recaptcha/enterprise.js
   离线镜像不接入 reCAPTCHA。前端调用的是 grecaptcha.enterprise.ready / .execute，
   因此这两层命名空间都要补齐，否则检索页的 fetch 链会直接断掉。 */
(function () {
  function ready(fn) { if (typeof fn === 'function') { setTimeout(fn, 0); } }
  function execute() { return Promise.resolve('offline-token'); }
  var ns = {
    ready: ready,
    execute: execute,
    render: function () { return 0; },
    reset: function () {},
    getResponse: function () { return 'offline-token'; }
  };
  var g = window.grecaptcha = window.grecaptcha || {};
  g.ready = ready;
  g.execute = execute;
  g.render = ns.render;
  g.reset = ns.reset;
  g.getResponse = ns.getResponse;
  g.enterprise = Object.assign({}, ns, g.enterprise || {});
})();
""",
'''google-ads-stub.js''': """/* 本地桩件：屏蔽任何 Google Ads / 转化脚本。 */
(function () { window.__adsOffline = true; })();
""",
}

def write_stubs():
    for name, body in STUB_JS.items():
        p = os.path.join(SITE, "vendor", name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w", encoding="utf-8").write(body)
        manifest["stubs"][name] = "/vendor/" + name
    log(f"[桩件] 写出 {len(STUB_JS)} 个本地桩件")

# ---------------------------------------------------------------- 步骤 3c：视频封面
# glue-yt-video 组件在缩略图 src 为空时，会把 src 设成 i.ytimg.com 的在线地址。
# patch_runtime 已把那段模板改指本地 /assets/yt/<videoId>.jpg，所以这里要按 id
# 预先铺好本地占位图，避免改完之后引用落空。
YT_VID_RE = re.compile(r'glue-yt-video-vid="([\w-]{6,})"')


def do_yt_posters():
    ids = set()
    for src in PAGES:
        try:
            text = open(src, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        ids.update(YT_VID_RE.findall(text))
    out_dir = os.path.join(ASSETS, "yt")
    os.makedirs(out_dir, exist_ok=True)
    made = 0
    for vid in sorted(ids):
        dest = os.path.join(out_dir, vid + ".jpg")
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            continue
        placeholder_image(1280, 720, f"YouTube video cover (offline)  {vid}", dest)
        made += 1
    log(f"[视频封面] 页面用到 {len(ids)} 个视频，新生成 {made} 张本地封面")


# ---------------------------------------------------------------- 步骤 3b：共享素材目录
# 这几份素材体量很大（图片 ~1.9 GB、游戏上千文件），且已经是「站内自洽」的形态，
# 直接按目录做硬链接共享即可：同一 inode 挂两个名字，不额外占磁盘，改动也是同步的。
#
# ⚠ 两张表踩过的坑，务必保留：
#   * fonts/   fonts.css 里写的是相对文件名 url(4UasrENH...woff2)，解析后落在 /fonts/ 下。
#              只带 fonts.css 不带 woff2，页面会静默回退到系统字体，字重字距全不对。
#   * vendor/  互动涂鸦里写的是 ../../vendor/cannon.min.js（pixi / createjs / firebase …），
#              从 /games/<slug>/ 往上两级正是 /vendor/。缺了这些库，互动模块直接白屏。
LINK_TREES = ["img", "fonts", "vendor", "games", "storybooks"]


def link_tree(name):
    src = name
    dst = os.path.join(SITE, name)
    if not os.path.isdir(src):
        log(f"[素材] 未找到 {src}/，跳过")
        return 0
    made = 0
    for dp, _dn, fns in os.walk(src):
        rel = os.path.relpath(dp, src)
        out_dir = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(out_dir, exist_ok=True)
        for fn in fns:
            s = os.path.join(dp, fn)
            d = os.path.join(out_dir, fn)
            if os.path.exists(d):
                continue
            try:
                os.link(s, d)          # 硬链接：零拷贝
            except OSError:
                import shutil
                shutil.copy2(s, d)     # 跨卷等场景退化
            made += 1
    return made


def link_shared_dirs():
    for name in LINK_TREES:
        n = link_tree(name)
        if n:
            log(f"[素材] {name}/ -> {SITE}/{name}/  新增 {n} 个硬链接")
    # 涂鸦主图没有单独的 img/ 目录，是直接平铺在工程根下的（约 6300 个）。
    # 站点路由把它们统一映射到 /img/<文件名>，所以要在这里做一层收拢。
    n = link_root_images()
    if n:
        log(f"[素材] 根目录图片 -> {SITE}/img/  新增 {n} 个硬链接")
    log("[素材] 共享目录就绪")


IMG_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg")


def link_root_images():
    dst = os.path.join(SITE, "img")
    os.makedirs(dst, exist_ok=True)
    made = 0
    for fn in os.listdir("."):
        if not os.path.isfile(fn) or not fn.lower().endswith(IMG_EXT):
            continue
        d = os.path.join(dst, fn)
        if os.path.exists(d):
            continue
        try:
            os.link(fn, d)
        except OSError:
            import shutil
            shutil.copy2(fn, d)
        made += 1
    return made


# ---------------------------------------------------------------- 步骤 4：重写 URL
GOOGLE_SRC_HOSTS = re.compile(
    r"(google|gstatic|googleapis|youtube|ytimg|ggpht|googleusercontent|googleplex|withgoogle)\.", re.I)

DOODLES_ROUTE = re.compile(r"^https?://(?:www\.)?doodles\.google(?::\d+)?(/.*)?$", re.I)

def to_local_route(url):
    m = DOODLES_ROUTE.match(url)
    if not m:
        return None
    path = m.group(1) or "/"
    path = path.split("#")[0].split("?")[0]
    if path in ("/", ""):
        return "/"
    if not path.endswith("/"):
        path += "/"
    return path  # 已按 site/ 根目录布局


def rewrite_text(text, src_dir):
    stats = {}

    # 4.-1 补 charset：抓取时丢掉了 <meta charset>，原站靠 HTTP 头声明。
    #       静态托管时不补会出现「Doodleâ€™s」这类乱码。
    if "<head>" in text and not re.search(r"<meta[^>]*charset", text[:4000], re.I):
        text = text.replace("<head>", '<head>\n<meta charset="utf-8">', 1)

    # 4.0 去掉 preconnect / dns-prefetch 到谷歌主机（离线无意义且会触发连接）
    text = re.sub(
        r'<link[^>]*rel="(?:preconnect|dns-prefetch)"[^>]*href="https?://(?:fonts\.googleapis\.com|fonts\.gstatic\.com|www\.gstatic\.com|csp\.withgoogle\.com|www\.googletagmanager\.com|www\.youtube\.com|img\.youtube\.com|www\.google\.com|doodles\.google|google\.com|about\.google|lh3\.googleusercontent\.com)[^"]*"[^>]*>\s*',
        "", text, flags=re.I)

    # 4.1 先处理属性里的 URL
    #     注意：content 不在此列 —— <meta name="description" content="散文">
    #     的 content 并不是 URL，按 URL 处理会把描述文字改成 /static/xxx 这种垃圾。
    #     真正携带 URL 的 meta 单独在 4.1b 处理。
    def repl_attr(m):
        attr, quote, url = m.group(1), m.group(2), m.group(3)
        new = map_url(url, src_dir)
        if new != url:
            stats[url] = new
        return f'{attr}={quote}{new}{quote}'

    text = re.sub(r'\b(src|href|data-src|poster)\s*=\s*(["\'])([^"\']+)\2',
                  repl_attr, text, flags=re.I)

    # 4.1b 只对「URL 类 meta」的 content 做 URL 映射
    #      画像类 og:image / twitter:image 要换成本地图片路径；
    #      og:url / twitter:url 这类「指向本页」的值同样要换成本地路由，
    #      否则会残留 ./../index.html 这种抓取痕迹。
    #      其余 meta（description / keywords / viewport …）的 content 是散文，
    #      一律不碰 —— 这是早期版本踩过的坑。
    META_URL_PROPS = (
        r'(?:og:(?:url|image(?::\w+)?|video(?::\w+)?|audio(?::\w+)?)'
        r'|twitter:(?:url|image)'
        r'|image)'
    )

    META_IMG = re.compile(
        r'(<meta\b[^>]*?(?:property|itemprop|name)\s*=\s*(["\'])'
        + META_URL_PROPS + r'\2[^>]*?content\s*=\s*(["\']))'
        r'([^"\']+)(\3)', re.I)

    def repl_meta_img(m):
        url = m.group(4)
        new = map_url(url, src_dir)
        if new != url:
            stats[url] = new
        return m.group(1) + new + m.group(5)

    text = META_IMG.sub(repl_meta_img, text)

    # 4.1c 反向书写顺序：content 在 property/itemprop/name 之前
    META_IMG2 = re.compile(
        r'(content\s*=\s*(["\']))([^"\']+)(\2[^>]*?(?:property|itemprop|name)\s*=\s*(["\'])'
        + META_URL_PROPS + r'\5)', re.I)

    def repl_meta_img2(m):
        url = m.group(3)
        new = map_url(url, src_dir)
        if new != url:
            stats[url] = new
        return m.group(1) + new + m.group(4)

    text = META_IMG2.sub(repl_meta_img2, text)

    # 4.2 srcset
    def repl_srcset(m):
        vals = []
        for part in m.group(2).split(","):
            part = part.strip()
            if not part:
                continue
            bits = part.split()
            bits[0] = map_url(bits[0], src_dir)
            vals.append(" ".join(bits))
        return f'{m.group(1)}="{", ".join(vals)}"'

    text = re.sub(r'\b(srcset)\s*=\s*(["\'])([^"\']+)\2', repl_srcset, text, flags=re.I)

    # 4.3 CSS url(...)
    def repl_css_url(m):
        url = m.group(1).strip('\'"')
        new = map_url(url, src_dir)
        if new != url:
            stats[url] = new
        return f"url({new})"

    text = re.sub(r'url\(\s*([^)]+?)\s*\)', repl_css_url, text)

    # 4.4 剩下的裸谷歌 URL（如内联脚本里的字符串）→ 尽量本地化
    def repl_bare(m):
        url = m.group(0)
        new = map_url(url, src_dir)
        if new != url:
            stats[url] = new
        return new

    text = re.sub(r'https?://[^\s"\'<>\\)]+', repl_bare, text)

    return text, stats


# ---------------------------------------------------------------- URL 映射
LOCAL_MAP = {}          # 远程 URL -> 本地路径
SLUGS = set()           # 已知 slug

# 「扁平 .html」-> 站点规范路由。见 _norm_rel 里的说明。
FLAT_ROUTE = {
    "index.html": "/",
    "about.html": "/about/",
    "search.html": "/search/",
    # 原站 /library.html 是「互动涂鸦库」入口，本站没有对应页面，
    # 折到「形态 = game」的检索结果上（163 个互动涂鸦）。
    "library.html": "/search/?form_tags=game",
}

def _norm_rel(url, src_dir):
    """把相对 URL 解析成工程内相对路径，再映射到站点路径。"""
    path, _, frag = url.partition("#")
    path, _, query = path.partition("?")
    if path.startswith("/"):
        return None
    # 拼到源目录上再做归一化
    joined = os.path.normpath(os.path.join(src_dir, path)).replace("\\", "/")
    if joined.startswith("../"):
        joined = joined[3:]

    # 抓取到的首页里用的是「扁平 .html」链接（./../about.html、./../library.html），
    # 而本站的规范路由是目录式的（各页 <link rel="canonical"> 写的是 /about/、/search/）。
    # 按相对路径直解会得到 /about.html 这种站内并不存在的地址，
    # 于是首页的导航、CTA 全部 404，所以在这里统一收口到规范路由。
    if "/" not in joined and joined.lower() in FLAT_ROUTE:
        base = FLAT_ROUTE[joined.lower()]
        if query and "?" not in base:
            base += "?" + query
        if frag:
            base += "#" + frag
        return base

    if joined.startswith("_google_clone/"):
        rest = joined[len("_google_clone/"):]
        if rest.startswith("img/"):
            out = "/img/" + rest[len("img/"):]
        elif rest in ("", ".", "index.html", "../index.html"):
            out = "/"
        else:
            out = "/static/" + os.path.basename(rest)
    elif joined.startswith(SITE + "/"):
        # 已经位于站点目录内：相对路径直接换算成站点绝对路径，
        # 例如 site/games/<slug>/assets/a.png -> /games/<slug>/assets/a.png
        out = "/" + joined[len(SITE) + 1:]
    elif joined.startswith("fonts/"):
        out = "/" + joined
    elif joined.startswith("_clone_search/"):
        out = "/static/" + os.path.basename(joined)
    elif joined in ("", ".", "index.html"):
        out = "/"
    else:
        out = "/" + joined
    if query:
        out += "?" + query
    if frag:
        out += "#" + frag
    return out


OLD_DOODLE_RE = re.compile(r"^https?://(?:www\.)?google\.com/doodles/([\w-]+)/?$", re.I)

def map_url(url, src_dir):
    u = url.strip()

    # 锚点 / 空 / data: 协议
    if not u or u.startswith("#") or u.startswith("data:") or u.startswith("blob:") \
       or u.startswith("mailto:") or u.startswith("javascript:") or u.startswith("tel:"):
        return url

    # 协议相对 //host/path
    if u.startswith("//"):
        u_abs = "https:" + u
    else:
        u_abs = u

    # 1) 已在映射表中（下载/占位/桩件）
    key = u_abs.rstrip("/")
    if key in LOCAL_MAP:
        suffix = ""
        if "#" in u_abs:
            suffix = "#" + u_abs.split("#", 1)[1]
        return LOCAL_MAP[key] + suffix

    # 2) doodles.google 路由
    r = to_local_route(u_abs)
    if r:
        frag = ""
        if "#" in u_abs:
            frag = "#" + u_abs.split("#", 1)[1]
        return r + frag

    # 3) 旧版 google.com/doodles/<slug> 链接
    m = OLD_DOODLE_RE.match(u_abs)
    if m and m.group(1) in SLUGS:
        return f"/doodle/{m.group(1)}/"

    # 4) 第三方脚本桩件
    low = u_abs.lower()
    if "youtube.com/player_api" in low or "youtube.com/iframe_api" in low:
        return "/vendor/youtube-api.js"
    if "cookienotificationbar.min.js" in low:
        return "/vendor/glue-cookienotificationbar.js"
    if "charts/loader.js" in low:
        return "/vendor/google-charts-loader.js"
    if "googletagmanager.com/gtm.js" in low or "googletagmanager.com/gtag" in low:
        return "/vendor/gtm-stub.js"
    if "cookienotificationbar.min.css" in low:
        return "/vendor/glue-cookienotificationbar.css"
    if "recaptcha/enterprise.js" in low or "recaptcha/api.js" in low:
        return "/vendor/recaptcha-stub.js"

    # 5) 谷歌字体
    if "fonts.googleapis.com" in low and "css" in low:
        return "/fonts/fonts.css"

    # 6) 其他谷歌静态资源 → 用占位
    host = urlparse(u_abs).netloc.lower()
    if GOOGLE_SRC_HOSTS.search(host + "."):
        lp = asset_local_path(u_abs)
        if lp:
            cand = os.path.join(SITE, lp.lstrip("/"))
            if os.path.exists(cand):
                return lp
        # 资源类但拿不到 → 用占位图（仅图片类）
        ext = os.path.splitext(urlparse(u_abs).path)[1].lower()
        if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"):
            ph = f"/assets/placeholder{ext if ext != '.svg' else '.png'}"
            return ph

    # 7) 相对路径
    if not urllib.parse.urlparse(u_abs).netloc:
        n = _norm_rel(url, src_dir)
        if n:
            return n

    # 8) 其余外链保持原样（政策/支持/社交等锚点）
    return url


# ---------------------------------------------------------------- 主流程
def main():
    global LOCAL_MAP
    write_stubs()
    link_shared_dirs()

    data = json.loads(open("doodles_data.js", encoding="utf-8").read()
                      .split("[", 1)[1].rsplit("]", 1)[0].join(["[", "]"]))
    SLUGS.update(r["slug"] for r in data)
    log(f"[数据] 载入 {len(SLUGS)} 个 slug")

    # 资源已全部落盘后重跑时，没必要再联网：复用上一轮的 manifest 即可。
    # 设置 SKIP_DOWNLOAD=1 让构建变成纯文本重写，秒级完成且完全离线。
    man_path = "_tools/build_manifest.json"
    if os.environ.get("SKIP_DOWNLOAD") == "1" and os.path.exists(man_path):
        old = json.loads(open(man_path, encoding="utf-8").read())
        for k in list(manifest.keys()):
            if k in old:
                manifest[k] = old[k]
        log(f"[下载] SKIP_DOWNLOAD=1，复用 manifest "
            f"(downloaded={len(manifest['downloaded'])}, "
            f"placeholder={len(manifest['placeholder'])})")
    else:
        do_download()
        do_placeholders()

    LOCAL_MAP = {}
    for url, lp in manifest["downloaded"].items():
        LOCAL_MAP[url.rstrip("/")] = lp
    for url, lp in manifest["placeholder"].items():
        LOCAL_MAP[url.rstrip("/")] = lp
    for name, lp in manifest["stubs"].items():
        pass

    # 通用占位图
    ph_png = os.path.join(SITE, "assets", "placeholder.png")
    placeholder_image(640, 360, "Resource unavailable (offline)", ph_png)
    do_yt_posters()

    all_stats = {}
    for src, dst in PAGES.items():
        src_dir = os.path.dirname(src)
        text = open(src, encoding="utf-8", errors="replace").read()
        new, stats = rewrite_text(text, src_dir)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        open(dst, "w", encoding="utf-8", newline="") .write(new)
        all_stats[dst] = stats
        log(f"[重写] {src}  ->  {dst}   ({len(stats)} 处替换)")

    json.dump({k: v for k, v in manifest.items()}, open("_tools/build_manifest.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    log("[完成] manifest -> _tools/build_manifest.json")


if __name__ == "__main__":
    main()
