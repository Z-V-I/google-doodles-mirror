#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_rss.py —— 生成 RSS 订阅源、元数据导出与「RSS 模式」页面，并给全站页眉加上入口。

产物：
    site/rss.xml              全量订阅源（6,375 条，标准 RSS 2.0）
    site/rss/latest.xml       最新 100 条（给阅读器日常订阅）
    site/rss/doodles.json     结构化元数据（页面前端也用它渲染订阅视图）
    site/rss/doodles.csv      同一份数据的表格版（UTF-8 BOM，Excel 直开）
    site/rss/index.html       RSS 模式页：订阅视图 + 筛选 + 下载区
    页眉导航                   在 Library / About 之后插入第三项 RSS

【为什么导航要「双写」】
页眉是全站共用的一份 DOM，但它的来源有两处：
  · 4 个源模板（_google_clone/index_local.html、_clone_search/page.html、
    about-page.html、doodle-slideshow.html）—— 重建时 build_site.py 与
    gen_doodle_pages.py 会从这里重新生成页面；
  · site/ 下已经生成的 6,542 个 HTML —— 本次要立刻生效的那份。
只改一边的后果是「本次看着生效了，下次重建又没了」（或反过来）。所以两边都写，
并且本脚本是幂等的（按 href="/rss/" 判重）。

【为什么导航序号要从 N/2 收敛到 N/3】
页眉埋点用 data-g-template_position="1/2"、"2/2" 记录「第几项 / 共几项」。
加进第三项后必须同步改成 /3，否则埋点数字对不上。收敛**只在本 <ul> 内**进行 ——
导航之外的 hero-tag-carousel、glue-jumplinks 也用 N/3、N/9、N/12 这类序号，
全文件替换会把它们一起改坏（这个坑 remove_creating_doodle.py 已经踩过一次）。

用法：
    python _tools/gen_rss.py            # 生成（幂等）
    python _tools/gen_rss.py --check    # 只报告状态，不写盘
    python _tools/gen_rss.py --revert   # 撤销：删产物 + 撤下导航入口
    python _tools/gen_rss.py --skip-nav # 只生成文件，不动页眉
"""
import os
import re
import sys
import json
import glob
import html

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "_tools"))

import rss_common as R   # noqa: E402

DOC = "site"
RSS_DIR = os.path.join(DOC, "rss")
FEED_FULL = os.path.join(DOC, "rss.xml")
FEED_LATEST = os.path.join(DOC, "rss", "latest.xml")
DATA_JSON = os.path.join(DOC, "rss", "doodles.json")
DATA_CSV = os.path.join(DOC, "rss", "doodles.csv")
PAGE = os.path.join(DOC, "rss", "index.html")

LATEST_N = 100

# 页眉导航要插入的那一项（相对缩进，渲染时按目标文件的实际缩进展开）。
# 缩进不能写死：主页与详情页是两套模板生成的，详情页的页眉还经过
# gen_doodle_pages.py 重排，各页 <li> 的缩进并不一致。
_NAV_REL = [
    (0, '<li class="glue-header__item glue-header__item">'),
    (2, '<a class="glue-header__link" href="/rss/"'),
    (6, 'data-g-event-trigger="click"'),
    (6, 'data-g-event="module_header"'),
    (6, 'data-g-module_id=""'),
    (6, 'data-g-module_name="header"'),
    (6, 'data-g-module_postion=""'),
    (6, 'data-g-cta_type="button"'),
    (6, 'data-g-cta_text="RSS"'),
    (6, 'data-g-cta_url="/rss/"'),
    (6, 'data-g-template_id="header_item"'),
    (6, 'data-g-template_position="3/3">'),
    (4, 'RSS'),
    (2, '</a>'),
    (0, '</li>'),
]

NAV_MARK = 'href="/rss/"'
UL_OPEN = '<ul class="glue-header__list">'
ABOUT_LINK = 'href="/about/"'


def nav_item_html(indent):
    return "\n".join(" " * (indent + rel) + line for rel, line in _NAV_REL)


def _line_indent(block, pos):
    """pos 所在行的缩进宽度。"""
    return pos - (block.rfind("\n", 0, pos) + 1)


def _item_sep(block, li_open):
    """取导航项之间的原始分隔文本（换行 + 空行的缩进）。

    注意「到本项行首为止」—— 本项自己的缩进不算在内，否则插入时会叠两份
    （实测会变成 28 空格，比相邻项深一倍）。

    就地取用而不是写死：主页与详情页来自两套模板，详情页的页眉还被
    gen_doodle_pages.py 重排过，缩进并不统一；复用文件自己的写法，
    插入与撤销才能逐字节对齐。
    """
    line_start = block.rfind("\n", 0, li_open) + 1        # 本项行首
    prev = block.rfind("</li>", 0, li_open)
    if prev >= 0 and prev + len("</li>") <= line_start:
        return block[prev + len("</li>"):line_start]
    # About 就是第一项：退回到 <ul ...> 标签之后
    gt = block.find(">")
    if 0 <= gt < line_start:
        return block[gt + 1:line_start]
    return "\n"


# ---------------------------------------------------------------- 导航改写
def _balanced_end(text, start, tag):
    """从 start（指向 <tag 的 < 处）开始做深度配平，返回闭合标签之后的下标。

    不用非贪婪正则：导航里可能有嵌套 <ul>，非贪婪会在第一个 </ul> 提前收尾。
    """
    open_re = re.compile(r"<%s\b" % tag, re.I)
    close_re = re.compile(r"</%s\s*>" % tag, re.I)
    depth = 0
    i = start
    while True:
        o = open_re.search(text, i)
        c = close_re.search(text, i)
        if not c:
            return -1
        if o and o.start() < c.start():
            depth += 1
            i = o.end()
        else:
            depth -= 1
            i = c.end()
            if depth == 0:
                return c.end()


def _ul_blocks(text):
    """返回所有页眉导航 <ul class="glue-header__list"> 的 (start, end)。"""
    out = []
    i = 0
    while True:
        i = text.find(UL_OPEN, i)
        if i < 0:
            return out
        end = _balanced_end(text, i, "ul")
        if end < 0:
            return out
        out.append((i, end))
        i = end


def patch_nav(text):
    """给页眉导航插入 RSS 项，返回 (新文本, 改动数)。"""
    changed = 0
    # 从后往前改，避免下标失效
    for start, end in reversed(_ul_blocks(text)):
        block = text[start:end]
        if NAV_MARK in block or ABOUT_LINK not in block:
            continue

        # 定位 About 那一项：<a ...href="/about/"...>…</a> 外面包着 <li>
        a = re.search(r'<a\b[^>]*href="/about/"[^>]*>.*?</a>', block, re.S)
        if not a:
            continue
        li_close = block.find("</li>", a.end())
        if li_close < 0:
            continue
        li_close += len("</li>")
        li_open = block.rfind("<li", 0, a.start())
        if li_open < 0:
            continue

        block = (block[:li_close]
                 + _item_sep(block, li_open)
                 + nav_item_html(_line_indent(block, li_open))
                 + block[li_close:])
        # 序号只在被改的这个 <ul> 内收敛：1/2 -> 1/3、2/2 -> 2/3
        block = re.sub(r'data-g-template_position="([12])/2"',
                       lambda m: 'data-g-template_position="%s/3"' % m.group(1), block)
        text = text[:start] + block + text[end:]
        changed += 1
    return text, changed


def revert_nav(text):
    """撤下导航里的 RSS 项：删掉含 href="/rss/" 的那个 <li>，序号回到 /2。"""
    changed = 0
    for start, end in reversed(_ul_blocks(text)):
        block = text[start:end]
        if NAV_MARK not in block:
            continue
        a = re.search(r'<a\b[^>]*href="/rss/"[^>]*>.*?</a>', block, re.S)
        if not a:
            continue
        li_close = block.find("</li>", a.end())
        li_open = block.rfind("<li", 0, a.start())
        if li_close < 0 or li_open < 0:
            continue
        # 从「上一项的 </li> 之后」删到「本项的 </li> 之后」——
        # 连插入时带上的分隔一起去掉，因此是逐字节还原。
        prev = block.rfind("</li>", 0, li_open)
        cut = prev + len("</li>") if prev >= 0 else block.find(">") + 1
        block = block[:cut] + block[li_close + len("</li>"):]
        block = re.sub(r'data-g-template_position="([12])/3"',
                       lambda m: 'data-g-template_position="%s/2"' % m.group(1), block)
        text = text[:start] + block + text[end:]
        changed += 1
    return text, changed


def nav_targets():
    """要先改的源模板 + 要立刻生效的产物页面。"""
    src = sorted(glob.glob("_google_clone/*.html") + glob.glob("_clone_search/*.html"))
    built = sorted(glob.glob(os.path.join(DOC, "**", "*.html"), recursive=True))
    return src, built


def apply_nav(revert=False, check=False):
    src, built = nav_targets()
    stats = {"src": 0, "built": 0, "hit_src": 0, "hit_built": 0, "skip": 0}
    fn = revert_nav if revert else patch_nav
    for group, paths in (("src", src), ("built", built)):
        for p in paths:
            try:
                with open(p, encoding="utf-8", newline="") as f:
                    text = f.read()
            except (UnicodeDecodeError, OSError):
                stats["skip"] += 1
                continue
            if UL_OPEN not in text:
                continue
            new, n = fn(text)
            if n:
                stats["hit_src" if group == "src" else "hit_built"] += 1
            if check or new == text:
                continue
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write(new)
            stats[group] += 1
    return stats


def nav_status():
    """报告导航入口的落地情况。"""
    src, built = nav_targets()
    def count(paths):
        has = miss = 0
        for p in paths:
            try:
                with open(p, encoding="utf-8", errors="replace") as f:
                    t = f.read()
            except OSError:
                continue
            if UL_OPEN not in t:
                continue
            if NAV_MARK in t:
                has += 1
            else:
                miss += 1
        return has, miss
    s_has, s_miss = count(src)
    b_has, b_miss = count(built)
    return {"src": (s_has, s_miss), "built": (b_has, b_miss)}


# ---------------------------------------------------------------- 页面
def _page_css():
    return """<style>
.zvi-rss{--rs-line:#dadce0;--rs-line2:#e8eaed;--rs-dim:#5f6368;--rs-dim2:#80868b;--rs-acc:#1a73e8}
.zvi-rss code{background:#f1f3f4;border-radius:4px;padding:1px 5px;font-size:.9em;font-family:ui-monospace,Consolas,monospace;color:#3c4043}
.zvi-rss-hero{background:#f8f9fa;border-bottom:1px solid var(--rs-line);padding:52px 0 44px}
.zvi-rss-hero .glue-page{max-width:1180px}
.zvi-rss-badge{display:inline-block;background:#e8f0fe;color:#1967d2;font-size:12px;font-weight:700;letter-spacing:.1em;padding:6px 14px;border-radius:100px;margin-bottom:18px}
.zvi-rss-hero h1{font-size:34px;line-height:1.25;font-weight:500;letter-spacing:-.4px;margin:0 0 16px;color:#202124}
.zvi-rss-hero > .glue-page > p{font-size:16px;line-height:1.75;color:var(--rs-dim);max-width:780px;margin:0}
.zvi-rss-stats{display:flex;flex-wrap:wrap;gap:30px;margin:30px 0 0}
.zvi-rss-stat b{display:block;font-size:25px;font-weight:500;color:#202124;line-height:1.2}
.zvi-rss-stat span{font-size:12.5px;color:var(--rs-dim2)}
.zvi-rss-sub{margin-top:32px;max-width:720px}
.zvi-rss-sub label{display:block;font-size:12.5px;font-weight:500;color:var(--rs-dim2);margin-bottom:8px}
.zvi-rss-subrow{display:flex;gap:10px;flex-wrap:wrap}
.zvi-rss-subrow input{flex:1 1 320px;min-width:0;font:inherit;font-size:14px;padding:11px 14px;border:1px solid var(--rs-line);border-radius:9px;background:#fff;color:#202124;font-family:ui-monospace,Consolas,monospace}
.zvi-rss-btn{display:inline-flex;align-items:center;justify-content:center;font:inherit;font-size:14px;font-weight:500;padding:11px 22px;border-radius:100px;border:1px solid var(--rs-line);background:#fff;color:var(--rs-acc);cursor:pointer;text-decoration:none;white-space:nowrap}
.zvi-rss-btn:hover{background:#f8f9fa}
.zvi-rss-btn.primary{background:var(--rs-acc);border-color:var(--rs-acc);color:#fff}
.zvi-rss-btn.primary:hover{background:#1765cc}
.zvi-rss-section{padding:46px 0;border-bottom:1px solid var(--rs-line2)}
.zvi-rss-section .glue-page{max-width:1180px}
.zvi-rss-section h2{font-size:22px;font-weight:500;margin:0 0 10px;color:#202124}
.zvi-rss-secnote{font-size:14px;line-height:1.75;color:var(--rs-dim);margin:0 0 24px;max-width:900px}
.zvi-rss-cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:16px}
a.zvi-rss-card{display:block;background:#fff;border:1px solid var(--rs-line);border-radius:14px;padding:20px;text-decoration:none;color:inherit;transition:border-color .16s,box-shadow .16s}
a.zvi-rss-card:hover{border-color:var(--rs-acc);box-shadow:0 1px 2px rgba(60,64,67,.18),0 2px 8px 2px rgba(60,64,67,.12)}
.zvi-rss-card .t{display:flex;align-items:center;gap:9px;font-size:16px;font-weight:500;color:var(--rs-acc);margin-bottom:9px}
.zvi-rss-card .d{font-size:13.5px;line-height:1.68;color:var(--rs-dim);margin-bottom:15px}
.zvi-rss-card .m{font-size:12.5px;color:var(--rs-dim2);font-family:ui-monospace,Consolas,monospace}
.zvi-rss-card .pill{font-size:11px;font-weight:700;letter-spacing:.04em;padding:3px 10px;border-radius:100px;background:#e6f4ea;color:#137333}
.zvi-rss-filters{display:flex;flex-wrap:wrap;gap:14px;align-items:flex-end;margin-bottom:16px}
.zvi-rss-field{display:flex;flex-direction:column;gap:6px}
.zvi-rss-field label{font-size:12px;font-weight:500;color:var(--rs-dim2)}
.zvi-rss-field select,.zvi-rss-field input{font:inherit;font-size:14px;padding:10px 12px;border:1px solid var(--rs-line);border-radius:9px;background:#fff;color:#202124}
.zvi-rss-field select{min-width:158px}
.zvi-rss-field input{min-width:252px}
.zvi-rss-field select:focus,.zvi-rss-field input:focus{outline:2px solid #a8c7fa;outline-offset:-1px;border-color:var(--rs-acc)}
.zvi-rss-count{font-size:13.5px;color:var(--rs-dim);padding:12px 0 4px;border-top:1px solid var(--rs-line2);margin-top:6px}
.zvi-rss-list{list-style:none;margin:0;padding:0}
.zvi-rss-item{display:flex;gap:22px;padding:22px 0;border-top:1px solid var(--rs-line2)}
.zvi-rss-item:first-child{border-top:0}
.zvi-rss-thumb{flex:0 0 172px;width:172px;height:115px;border-radius:10px;background:#f1f3f4;overflow:hidden;display:block}
.zvi-rss-thumb img{width:100%;height:100%;object-fit:contain;display:block}
.zvi-rss-body{flex:1 1 auto;min-width:0}
.zvi-rss-body h3{margin:0 0 6px;font-size:17px;font-weight:500;line-height:1.4}
.zvi-rss-body h3 a{color:#202124;text-decoration:none}
.zvi-rss-body h3 a:hover{color:var(--rs-acc);text-decoration:underline}
.zvi-rss-when{font-size:12.5px;color:var(--rs-dim2);margin-bottom:8px}
.zvi-rss-desc{font-size:14px;line-height:1.7;color:var(--rs-dim);margin:0 0 11px}
.zvi-rss-chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:11px}
.zvi-rss-chip{display:inline-flex;align-items:center;gap:6px;font-size:12px;padding:4px 11px;border-radius:100px;background:#f1f3f4;color:#3c4043;white-space:nowrap}
.zvi-rss-chip i{width:10px;height:10px;border-radius:50%;display:inline-block;border:1px solid rgba(0,0,0,.12)}
.zvi-rss-chip.year{background:#e8f0fe;color:#1967d2}
.zvi-rss-acts{display:flex;gap:18px;font-size:13px}
.zvi-rss-acts a{color:var(--rs-acc);text-decoration:none}
.zvi-rss-acts a:hover{text-decoration:underline}
.zvi-rss-sentinel{display:flex;align-items:center;gap:10px;justify-content:center;padding:26px 0;font-size:13.5px;color:var(--rs-dim2)}
.zvi-rss-sentinel.done{color:#bdc1c6}
.zvi-rss-spin{width:16px;height:16px;border:2px solid #dadce0;border-top-color:var(--rs-acc);border-radius:50%;animation:zvi-rss-spin .7s linear infinite}
.zvi-rss-sentinel.done .zvi-rss-spin,.zvi-rss-sentinel.error .zvi-rss-spin{display:none}
.zvi-rss-sentinel.error{color:#d93025}
@keyframes zvi-rss-spin{to{transform:rotate(360deg)}}
@media (max-width:760px){
  .zvi-rss-hero h1{font-size:26px}
  .zvi-rss-stats{gap:20px}
  .zvi-rss-item{flex-direction:column;gap:14px}
  .zvi-rss-thumb{flex:0 0 auto;width:100%;height:190px}
  .zvi-rss-field input,.zvi-rss-field select{min-width:0;width:100%}
  .zvi-rss-field{flex:1 1 44%}
}
</style>"""


def _page_body(items, feed_bytes, total_bytes, topics_n, years_n):
    return """  <div id="content">
<div class="main-page zvi-rss">

  <section class="zvi-rss-hero">
    <div class="glue-page">
      <span class="zvi-rss-badge">RSS 模式</span>
      <h1>把整个 Doodles 全集当成一个订阅源</h1>
      <p>__COUNT__ 个涂鸦 —— 原始图片、官方介绍、上线日期，以及话题 / 主色 / 格式 / 地区四类标签，
         全部装在一个标准 RSS 2.0 订阅源里。可以直接粘进阅读器订阅，也可以在这里按需筛选、整包下载。</p>
      <div class="zvi-rss-stats">
        <div class="zvi-rss-stat"><b>__COUNT__</b><span>涂鸦条目</span></div>
        <div class="zvi-rss-stat"><b>__YEARN__</b><span>有年份可查</span></div>
        <div class="zvi-rss-stat"><b>__TOPICN__</b><span>话题分类</span></div>
        <div class="zvi-rss-stat"><b>__SIZE__</b><span>图片总量</span></div>
      </div>
      <div class="zvi-rss-sub">
        <label for="feedUrl">订阅地址（粘进任意 RSS 阅读器）</label>
        <div class="zvi-rss-subrow">
          <input id="feedUrl" type="text" readonly value="/rss.xml" spellcheck="false">
          <button type="button" class="zvi-rss-btn" id="copyFeed">复制</button>
          <a class="zvi-rss-btn primary" id="openFeed" href="/rss.xml" target="_blank" rel="noopener">打开订阅源</a>
        </div>
      </div>
    </div>
  </section>

  <section class="zvi-rss-section" id="download">
    <div class="glue-page">
      <h2>下载</h2>
      <p class="zvi-rss-secnote">
        订阅源是标准 RSS 2.0：每条 <code>&lt;item&gt;</code> 都带
        <code>&lt;enclosure&gt;</code>（图片本体，阅读器可直接抓取）、
        <code>&lt;description&gt;</code>（介绍 + 分类明细）、
        <code>&lt;category&gt;</code>（话题、主色、格式、地区）与
        <code>&lt;guid&gt;</code>。
        图片包把图片和 <code>manifest.csv</code> 一起打进去，离线解压后也能一张张对上号。
      </p>
      <div class="zvi-rss-cards">
        <a class="zvi-rss-card" href="/rss.xml" download>
          <div class="t">完整订阅源 <span class="pill">全量</span></div>
          <div class="d">全部 __COUNT__ 条，含图片 enclosure、介绍与四类标签。体积不小，适合归档。</div>
          <div class="m">rss.xml · __FEEDSIZE__</div>
        </a>
        <a class="zvi-rss-card" href="/rss/latest.xml" download>
          <div class="t">最新 100 条</div>
          <div class="d">按上线时间倒序的前 100 条，给阅读器日常订阅用，体积几乎可忽略。</div>
          <div class="m">latest.xml</div>
        </a>
        <a class="zvi-rss-card" href="/rss/doodles.csv" download>
          <div class="t">元数据 CSV</div>
          <div class="d">标题、上线日期、年份、主色、话题、格式、地区、图片文件名、详情页地址、介绍。</div>
          <div class="m">doodles.csv</div>
        </a>
        <a class="zvi-rss-card" href="/rss/doodles.json" download>
          <div class="t">元数据 JSON</div>
          <div class="d">同一份数据的结构化版本，<code>topics</code> 为数组，另含图片字节数便于核账。</div>
          <div class="m">doodles.json</div>
        </a>
        <a class="zvi-rss-card" id="zipCard" href="/rss/images.zip" download>
          <div class="t">图片包 ZIP <span class="pill">含清单</span></div>
          <div class="d">图片 + <code>manifest.csv</code>（每张图对应的介绍与分类）+ <code>README.txt</code>。
             服务端实时打包，点下去会先转几秒才开始进下载。</div>
          <div class="m"><span id="zipScope">全部 __COUNT__ 张</span> · <span id="zipSize">__SIZE__</span></div>
        </a>
      </div>
    </div>
  </section>

  <section class="zvi-rss-section" id="browse">
    <div class="glue-page">
      <h2>订阅视图</h2>
      <p class="zvi-rss-secnote">
        下面就是订阅源的内容，滚到底自动续载。筛选之后，上面的图片包会跟着一起收窄 ——
        只下载你要的那一批。
      </p>
      <div class="zvi-rss-filters">
        <div class="zvi-rss-field">
          <label for="fq">关键字</label>
          <input id="fq" type="search" placeholder="标题或介绍包含…" autocomplete="off">
        </div>
        <div class="zvi-rss-field">
          <label for="ftopic">话题</label>
          <select id="ftopic"><option value="">全部话题</option></select>
        </div>
        <div class="zvi-rss-field">
          <label for="fcolor">主色</label>
          <select id="fcolor"><option value="">全部主色</option></select>
        </div>
        <div class="zvi-rss-field">
          <label for="fform">格式</label>
          <select id="fform"><option value="">全部格式</option></select>
        </div>
        <div class="zvi-rss-field">
          <label for="fyear">年份</label>
          <select id="fyear"><option value="">全部年份</option></select>
        </div>
        <button type="button" class="zvi-rss-btn" id="freset">重置</button>
      </div>
      <div class="zvi-rss-count" id="count">正在载入订阅源…</div>
      <ul class="zvi-rss-list" id="list"></ul>
      <div class="zvi-rss-sentinel" id="sentinel"><span class="zvi-rss-spin"></span><span id="sentinelText">正在载入…</span></div>
    </div>
  </section>

</div>
  </div>
""".replace("__COUNT__", "{:,}".format(len(items))) \
   .replace("__YEARN__", "{:,}".format(years_n)) \
   .replace("__TOPICN__", "{:,}".format(topics_n)) \
   .replace("__SIZE__", R.human_size(total_bytes)) \
   .replace("__FEEDSIZE__", R.human_size(feed_bytes))


def _page_js():
    return """<script>
(function () {
  "use strict";
  var PAGE = 24;
  var all = [], view = [], shown = 0, io = null;
  function $(id) { return document.getElementById(id); }
  var list = $("list"), sentinel = $("sentinel"), sentinelText = $("sentinelText");

  var COLORS = { red:"#ea4335", orange:"#fa903e", yellow:"#fbbc05", green:"#34a853",
                 cyan:"#24c1e0", blue:"#4285f4", purple:"#a142f4", pink:"#f439a0",
                 brown:"#a15c25", grey:"#9aa0a6", gray:"#9aa0a6",
                 black:"#202124", white:"#f1f3f4" };

  function nfmt(n) { return n.toLocaleString("en-US"); }
  function sizeText(n) {
    var u = ["B", "KB", "MB", "GB"], i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return i === 0 ? n + " B" : n.toFixed(1) + " " + u[i];
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function fill(sel, values) {
    values.forEach(function (v) {
      var o = document.createElement("option");
      o.value = v;
      o.textContent = v;
      sel.appendChild(o);
    });
  }

  function buildFilters() {
    var tc = {}, cc = {}, fc = {}, yc = {};
    all.forEach(function (it) {
      (it.topics || []).forEach(function (t) { tc[t] = (tc[t] || 0) + 1; });
      if (it.color) { cc[it.color] = (cc[it.color] || 0) + 1; }
      if (it.format) { fc[it.format] = (fc[it.format] || 0) + 1; }
      if (it.year) { yc[it.year] = (yc[it.year] || 0) + 1; }
    });
    function byCount(m) {
      return Object.keys(m).sort(function (a, b) {
        return m[b] - m[a] || (a < b ? -1 : 1);
      });
    }
    fill($("ftopic"), byCount(tc));
    fill($("fcolor"), byCount(cc));
    fill($("fform"), byCount(fc));
    fill($("fyear"), Object.keys(yc).sort(function (a, b) { return b - a; }));
  }

  function filters() {
    return {
      q: $("fq").value.trim().toLowerCase(),
      topic: $("ftopic").value,
      color: $("fcolor").value,
      form: $("fform").value,
      year: $("fyear").value
    };
  }

  function match(it, f) {
    if (f.topic && (it.topics || []).indexOf(f.topic) < 0) { return false; }
    if (f.color && it.color !== f.color) { return false; }
    if (f.form && it.format !== f.form) { return false; }
    if (f.year && String(it.year || "") !== f.year) { return false; }
    if (f.q) {
      var hay = (it.title + " " + it.description).toLowerCase();
      if (hay.indexOf(f.q) < 0) { return false; }
    }
    return true;
  }

  function whenText(it) {
    if (it.date_label) { return it.date_label; }
    if (it.year) { return "约 " + it.year + " 年（由名称推定）"; }
    return "上线日期不详";
  }

  function itemHtml(it) {
    var chips = "";
    (it.topics || []).forEach(function (t) {
      chips += '<span class="zvi-rss-chip">' + esc(t) + "</span>";
    });
    if (it.color) {
      chips += '<span class="zvi-rss-chip"><i style="background:' +
               (COLORS[it.color] || "#dadce0") + '"></i>' + esc(it.color) + "</span>";
    }
    if (it.format) { chips += '<span class="zvi-rss-chip">' + esc(it.format) + "</span>"; }
    if (it.year) { chips += '<span class="zvi-rss-chip year">' + esc(it.year) + "</span>"; }
    (it.regions || []).forEach(function (rg) {
      chips += '<span class="zvi-rss-chip">' + esc(rg) + "</span>";
    });
    return '<li class="zvi-rss-item">' +
      '<a class="zvi-rss-thumb" href="' + esc(it.page_url) + '" tabindex="-1">' +
        '<img loading="lazy" src="' + esc(it.image_url) + '" alt="' + esc(it.title) + '">' +
      "</a>" +
      '<div class="zvi-rss-body">' +
        '<h3><a href="' + esc(it.page_url) + '">' + esc(it.title) + "</a></h3>" +
        '<div class="zvi-rss-when">' + esc(whenText(it)) + "</div>" +
        '<p class="zvi-rss-desc">' + esc(it.description) + "</p>" +
        '<div class="zvi-rss-chips">' + chips + "</div>" +
        '<div class="zvi-rss-acts">' +
          '<a href="' + esc(it.image_url) + '" download>下载图片</a>' +
          '<a href="' + esc(it.page_url) + '">详情页</a>' +
        "</div>" +
      "</div></li>";
  }

  function renderMore() {
    var end = Math.min(shown + PAGE, view.length);
    if (end <= shown) { return false; }
    var buf = "";
    for (var i = shown; i < end; i++) { buf += itemHtml(view[i]); }
    list.insertAdjacentHTML("beforeend", buf);
    shown = end;
    return true;
  }

  function sumBytes() {
    var n = 0;
    for (var i = 0; i < view.length; i++) { n += view[i].bytes || 0; }
    return n;
  }

  function updateSummary() {
    var left = view.length - shown;
    // 给自动化验收留的可读锚点：脚本不必去解析「共 1,234 条」这种中文文案
    list.dataset.count = String(view.length);
    $("count").textContent = "共 " + nfmt(view.length) + " 条 · 图片 " +
      sizeText(sumBytes()) +
      (left > 0 ? " · 已显示 " + nfmt(shown) + " 条，继续下滑自动载入" : " · 已全部列出");
  }

  function updateZip(f) {
    var qs = [];
    if (f.topic) { qs.push("topic=" + encodeURIComponent(f.topic)); }
    if (f.color) { qs.push("color=" + encodeURIComponent(f.color)); }
    if (f.form) { qs.push("form=" + encodeURIComponent(f.form)); }
    if (f.year) { qs.push("year=" + encodeURIComponent(f.year)); }
    if (f.q) { qs.push("q=" + encodeURIComponent(f.q)); }
    $("zipCard").href = "/rss/images.zip" + (qs.length ? "?" + qs.join("&") : "");
    $("zipScope").textContent = (qs.length ? "筛选后 " : "全部 ") +
      nfmt(view.length) + " 张";
    $("zipSize").textContent = sizeText(sumBytes());
  }

  function apply() {
    var f = filters();
    view = all.filter(function (it) { return match(it, f); });
    shown = 0;
    list.innerHTML = "";
    sentinel.classList.remove("done", "error");
    sentinelText.textContent = "正在载入…";
    renderMore();
    updateSummary();
    updateZip(f);
    if (io) { io.disconnect(); io.observe(sentinel); }
    maybeMore();
  }

  // 滚到底自动续载 —— 与检索页同一套思路：不用按钮，只靠位置触发。
  function maybeMore() {
    if (shown >= view.length) {
      sentinel.classList.add("done");
      sentinelText.textContent = "已经到底了 —— 共 " + nfmt(view.length) + " 条";
      return;
    }
    if (sentinel.getBoundingClientRect().top < window.innerHeight + 900) {
      if (renderMore()) {
        updateSummary();
        window.setTimeout(maybeMore, 0);
      }
    }
  }

  if ("IntersectionObserver" in window) {
    io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { if (e.isIntersecting) { maybeMore(); } });
    }, { rootMargin: "900px 0px" });
  }
  window.addEventListener("scroll", maybeMore, { passive: true });
  window.addEventListener("resize", maybeMore);

  var t = null;
  $("fq").addEventListener("input", function () {
    window.clearTimeout(t);
    t = window.setTimeout(apply, 180);
  });
  ["ftopic", "fcolor", "fform", "fyear"].forEach(function (id) {
    $(id).addEventListener("change", apply);
  });
  $("freset").addEventListener("click", function () {
    $("fq").value = "";
    $("ftopic").value = "";
    $("fcolor").value = "";
    $("fform").value = "";
    $("fyear").value = "";
    apply();
  });

  var feed = location.origin + "/rss.xml";
  $("feedUrl").value = feed;
  $("openFeed").href = feed;
  $("copyFeed").addEventListener("click", function () {
    var btn = this, old = btn.textContent;
    function ok() { btn.textContent = "已复制"; window.setTimeout(function () { btn.textContent = old; }, 1400); }
    function fallback() {
      var inp = $("feedUrl");
      inp.removeAttribute("readonly");
      inp.select();
      try { document.execCommand("copy"); ok(); } catch (e) { btn.textContent = "请手动复制"; }
      inp.setAttribute("readonly", "readonly");
      window.getSelection().removeAllRanges();
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(feed).then(ok, fallback);
    } else { fallback(); }
  });

  fetch("doodles.json").then(function (r) {
    if (!r.ok) { throw new Error("HTTP " + r.status); }
    return r.json();
  }).then(function (d) {
    all = d.items || [];
    buildFilters();
    apply();
  }).catch(function (e) {
    sentinel.classList.add("error");
    sentinelText.textContent = "订阅源载入失败：" + e.message;
    $("count").textContent = "无法载入 doodles.json";
  });
})();
</script>"""


def build_page(items, stats):
    donor_path = os.path.join(DOC, "about", "index.html")
    if not os.path.isfile(donor_path):
        raise SystemExit("找不到骨架页 %s（需要先跑 build_site.py）" % donor_path)
    with open(donor_path, encoding="utf-8", newline="") as f:
        donor = f.read()

    h_end = donor.find("</header>") + len("</header>")
    f_start = donor.find("<footer")
    if h_end <= 0 or f_start < 0:
        raise SystemExit("骨架页里找不到 </header> / <footer> 边界")

    head = donor[donor.find("<head>"):donor.find("</head>") + 7]
    head = _retarget_head(head)
    body = _page_body(items, stats["feed_bytes"], stats["total_bytes"],
                      stats["topics"], stats["years"])

    # 骨架的头(fonts/css) + 自己的 style，然后 header + 自己的正文 + 原页脚与尾部脚本
    head = head.replace("</head>", _page_css() + "\n</head>")
    tail = donor[f_start:]
    # 自己的脚本放在原站脚本之后、</html> 之前 —— 追加到 </html> 后面就是坏标记了
    tail = tail.replace("</html>", _page_js() + "\n</html>", 1)
    if _page_js() not in tail:            # 万一骨架没有 </html>，兜底直接追加
        tail += "\n" + _page_js()
    return ("<!doctype html>\n<html lang=\"zh-CN\">\n" + head
            + donor[donor.find("<body"):h_end] + "\n"
            + body
            + "  " + tail + "\n")


def _retarget_head(head):
    """把骨架页的标题/描述/社交卡片改成 RSS 模式页自己的。"""
    title = "RSS 模式 - Google Doodles 离线镜像"
    desc = ("把 doodles.google 离线镜像的 6,375 个涂鸦当成一个订阅源："
            "标准 RSS 2.0，每条含图片、官方介绍与话题/主色/格式/地区分类。")

    def sub(pattern, repl, text, count=0):
        new, n = re.subn(pattern, repl, text, count=count, flags=re.S)
        return new if n else text

    head = sub(r"<title>.*?</title>", "<title>%s</title>" % html.escape(title), head)
    head = sub(r'<meta\s+name="description"\s+content="[^"]*">',
               '<meta name="description" content="%s">' % html.escape(desc, quote=True), head)
    head = sub(r'<meta\s+property="og:title"\s+content="[^"]*">',
               '<meta property="og:title" content="%s">' % html.escape(title, quote=True), head)
    head = sub(r'<meta\s+property="og:description"\s+content="[^"]*">',
               '<meta property="og:description" content="%s">' % html.escape(desc, quote=True), head)
    head = sub(r'<meta\s+property="og:url"\s+content="[^"]*">',
               '<meta property="og:url" content="/rss/">', head)
    head = sub(r'<meta\s+property="twitter:title"\s+content="[^"]*">',
               '<meta property="twitter:title" content="%s">' % html.escape(title, quote=True), head)
    head = sub(r'<meta\s+property="twitter:description"\s+content="[^"]*">',
               '<meta property="twitter:description" content="%s">' % html.escape(desc, quote=True), head)
    head = sub(r'<meta\s+property="twitter:url"\s+content="[^"]*">',
               '<meta property="twitter:url" content="/rss/">', head)
    head = sub(r'<link\s+rel="canonical"\s+href="[^"]*">',
               '<link rel="canonical" href="/rss/">', head)
    # 这一页没有代表性大图，去掉从 about 页继承来的图片卡片，免得社交预览拿错图
    for pat in (r'<meta\s+itemprop="image"[^>]*>\s*',
                r'<meta\s+property="og:image"[^>]*>\s*',
                r'<meta\s+property="twitter:image"[^>]*>\s*'):
        head = sub(pat, "", head)
    head = sub(r"pageType:\s*'about'", "pageType: 'rss'", head)
    return head


# ---------------------------------------------------------------- 主流程
def revert(generated_only=False):
    removed = []
    for p in (FEED_FULL, FEED_LATEST, DATA_JSON, DATA_CSV, PAGE):
        if os.path.isfile(p):
            os.remove(p)
            removed.append(p)
    if os.path.isdir(RSS_DIR) and not os.listdir(RSS_DIR):
        os.rmdir(RSS_DIR)
    st = {} if generated_only else apply_nav(revert=True)
    return removed, st


def main():
    args = set(sys.argv[1:])
    check = "--check" in args
    do_revert = "--revert" in args
    skip_nav = "--skip-nav" in args

    if do_revert:
        removed, st = revert(generated_only=skip_nav)
        print("已删除产物：")
        for p in removed:
            print("  -", p)
        if not removed:
            print("  （无）")
        if not skip_nav:
            print("\n导航入口撤销：源模板 %d 个、产物 %d 个"
                  % (st.get("src", 0), st.get("built", 0)))
        print("\n注意：重新生成请再跑一次不带 --revert 的本脚本。")
        return

    if check:
        n = nav_status()
        print("导航入口状态（含 href=\"/rss/\" 的页眉 / 缺该项的页眉）：")
        print("  源模板  %d 已加 / %d 待加" % n["src"])
        print("  产物页  %d 已加 / %d 待加" % n["built"])
        for p in (FEED_FULL, FEED_LATEST, DATA_JSON, DATA_CSV, PAGE):
            print("  [%s] %s" % ("有" if os.path.isfile(p) else "无", p))
        return

    items = R.load_items()
    with_topics = sum(1 for it in items if it["topics"])
    topics = len({t for it in items for t in it["topics"]})
    years = sum(1 for it in items if it["year"])
    total_bytes = R.total_bytes(items)

    os.makedirs(RSS_DIR, exist_ok=True)

    # 先加导航入口，再生成页面 —— RSS 模式页的页眉是从 site/about 骨架里取的，
    # 骨架先改好，新页面才自带这个入口（否则它自己的页眉里反而没有 RSS）。
    st = None
    if not skip_nav:
        st = apply_nav()

    full_xml = R.feed_xml(items, feed_path="/rss.xml")
    latest_xml = R.feed_xml(
        items, feed_path="/rss/latest.xml", limit=LATEST_N,
        title=R.FEED_TITLE + " · 最新 %d 条" % LATEST_N,
        desc="按上线时间倒序的最新 %d 条 Google Doodles（离线镜像）。" % LATEST_N)

    wrote = []

    def put(path, text):
        """只在内容有变化时写盘 —— 便于识别「本次真的生成了新东西」。"""
        old = None
        if os.path.isfile(path):
            with open(path, encoding="utf-8", newline="") as f:
                old = f.read()
        if old == text:
            return False
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        wrote.append(path)
        return True

    put(FEED_FULL, full_xml)
    put(FEED_LATEST, latest_xml)
    put(DATA_JSON, json.dumps(
        {"count": len(items), "generated_for": "doodles.google 离线镜像",
         "items": items}, ensure_ascii=False, separators=(",", ":")))
    put(DATA_CSV, R.manifest_csv(items))

    stats = {"feed_bytes": len(full_xml.encode("utf-8")),
             "total_bytes": total_bytes, "topics": topics, "years": years}
    put(PAGE, build_page(items, stats))

    print("订阅条目        : %d（其中 %d 条带话题分类，%d 条有年份线索）"
          % (len(items), with_topics, years))
    print("图片合计        : %s" % R.human_size(total_bytes))
    print("rss.xml         : %s（%d 条）"
          % (R.human_size(stats["feed_bytes"]), len(items)))
    print("latest.xml      : %d 条" % min(LATEST_N, len(items)))
    print("doodles.csv     : %s" % R.human_size(os.path.getsize(DATA_CSV)))
    print("doodles.json    : %s" % R.human_size(os.path.getsize(DATA_JSON)))
    print("rss/index.html  : %s" % R.human_size(os.path.getsize(PAGE)))
    if wrote:
        print("\n本次写入 %d 个文件：" % len(wrote))
        for p in wrote:
            print("  +", p)
    else:
        print("\n全部产物内容未变（幂等）。")

    if skip_nav:
        print("\n[--skip-nav] 未改动页眉导航。")
        return

    print("\n页眉导航加 RSS 入口：源模板 %d 个、产物页 %d 个"
          % (st["hit_src"], st["hit_built"]))
    n = nav_status()
    print("  复核：源模板 %d/%d 已加，产物页 %d/%d 已加"
          % (n["src"][0], sum(n["src"]), n["built"][0], sum(n["built"])))


if __name__ == "__main__":
    main()
