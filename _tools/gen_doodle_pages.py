#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_doodle_pages.py —— 以真实涂鸦详情页为骨架，批量生成 6375 个 /doodle/<slug>/ 页面。

骨架来源：_clone_search/doodle-slideshow.html（真实站点抓取页）
数据来源：doodles_data.js
交互来源：iframe_map.js（本地已抓取的 games / storybooks）
"""
import os
import re
import json
import html
import random
import shutil
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "_tools"))

import doodle_data  # noqa: E402  统一的日期规范化层

SITE = "site"
TEMPLATE = "_clone_search/doodle-slideshow.html"

# ---------------------------------------------------------------- 主题映射
THEME = {
    "pink": "theme-pink", "blue": "theme-blue", "red": "theme-red",
    "green": "theme-green", "cyan": "theme-teal", "orange": "theme-orange",
    "grey": "theme-gray", "gray": "theme-gray", "yellow": "theme-yellow",
    "white": "theme-tan", "purple": "theme-purple", "black": "theme-gray",
    "brown": "theme-brown", "teal": "theme-teal",
}

# 真实站点 date 字段里带星期/月份缩写，用于 <p class="date">
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def esc(s):
    return html.escape(s or "", quote=True)


def theme_of(color):
    return THEME.get((color or "").lower(), "theme-terracotta")


def date_label(rec):
    """上线日期标签，形如 'Oct 19, 2024'。

    直接采用 doodle_data 规范化后的 run_date —— 它会用 slug 里的年份
    纠正原始 date 字段（原始值是历史事件日期，例如 7 月 4 日被记成 1776 年）。
    无法推导时返回空串，页面就不渲染日期行，而不是显示一个错的日期。
    """
    rd = rec.get("run_date")
    if not rd:
        return ""
    y, mo, d = rd
    return f"{MONTHS[mo-1]} {d}, {y}"


# ---------------------------------------------------------------- 抽取骨架
def load_template():
    t = open(TEMPLATE, encoding="utf-8", errors="replace").read()

    # header：从 <header 到 <div id="content">
    h_start = t.index('<header class="glue-header')
    h_end = t.index('<div id="content">')
    header = t[h_start:h_end]

    # footer：从 <footer 到 </html>
    f_start = t.index('<footer class="glue-footer"')
    f_end = t.index("</html>")
    footer = t[f_start:f_end]

    # head 模板：<head> 段
    head_start = t.index("<head>")
    head_end = t.index("</head>") + len("</head>")
    head = t[head_start:head_end]

    # 结尾脚本：</html> 之前的 script 标签（保留原站运行时）
    scripts = "\n".join(re.findall(r'<script[^>]*src="[^"]*"[^>]*>\s*</script>', t))
    return head, header, footer, scripts


def extract_share_block(t):
    """社交分享容器 —— 已按需求整站移除，生成阶段不再产出，固定返回空串。

    原实现是从模板抠出 `<div class="share-container vertical">` 整块，再逐页把
    slug / 标题实例化进去。该组件（复制链接 + LinkedIn + X + Facebook）指向外部
    社交站点，已由 _tools/strip_share_icons.py 全站摘除，模板里也不再有它。
    函数签名保留，是为了不牵动 build_main / main 的调用点。

    日后若要恢复这一块：从抓取源里取回该 <div>，改回原实现即可。
    """
    return ""


# 「Explore a Random Theme」胶囊里挂的候选主题条数（与原始抓取页一致）
CTA_LINKS = 10
CTA_LABEL = "Explore a Random Theme"
CTA_ARROW_SVG = (
    '<svg width="21" height="16" viewBox="0 0 21 16" fill="none" alt="" role="img">'
    '<use xlink:href="/static/'
    '37ae077da8a25b6950c926233ed752617048c50792204bc7b9d6332bbb7a3a5f'
    '.icons.svg#right-arrow-doodles"></use></svg>'
)


def theme_pool(by_topic):
    """随机主题池：取本地主题分类，去掉无所指的 Other 与含 & 的项。

    `&` 会同时破坏 URL 查询串和 HTML 属性，本地那两个（Plants & Flowers）
    在检索页里本来就是靠参数解析兜住的，没必要再塞进随机池。
    排序固定为「热度降序 → 名称升序」，保证同一 slug 每次抽到的是同一批。
    结果缓存，避免 6375 个页面各重排一次。
    """
    global _POOL
    if _POOL is None:
        p = [k for k, v in by_topic.items()
             if k.lower() != "other" and "&" not in k and v]
        p.sort(key=lambda k: (-len(by_topic[k]), k))
        _POOL = p
    return _POOL


_POOL = None


def key_themes_cta(slug, pool):
    """key-themes 模块底部的「Explore a Random Theme」胶囊。

    这个胶囊不是装饰，是原站 bundle 里 KeyThemes 类的构造前提：类中第一句就是
    `el.querySelector(".key-themes-cta-link")`，紧接着读它的 `dataset.links`。
    少了它，构造函数抛 "Cannot read properties of null (reading 'dataset')"，
    而且这个异常位于主初始化函数的 forEach 回调里，会连带掐断排在它后面的
    AddLinkClass / ContentBlock / SocialShare / ToutCarousel 四个模块的初始化。
    原页本来就有这个胶囊，之前是生成时漏了。
    """
    if not pool:
        return ""
    picks = random.Random("key-themes/" + slug).sample(pool, min(CTA_LINKS, len(pool)))
    links = ["/search/?topic_tags=" + p for p in picks]
    # 属性值里的双引号必须转实体，否则 JSON 的引号会把属性截断（原页也是 &#34;）
    data_links = json.dumps(links, ensure_ascii=False).replace('"', "&#34;")
    return (
        f'<a aria-label="{CTA_LABEL}" class="key-themes-cta-link" '
        f'data-links="{data_links}" href="{links[0]}">'
        f'<div class="key-themes-cta-wrapper">'
        f'<div class="key-themes-cta">{CTA_LABEL}</div>'
        f'{CTA_ARROW_SVG}'
        f'</div></a>'
    )


TPL_SLUG = "celebrating-the-staurikosaurus"
TPL_TITLE = "Celebrating the Staurikosaurus"


def instantiate_share(block, slug, title):
    """把模板里的示例 slug / 标题换成当前涂鸦。"""
    from urllib.parse import quote
    out = block.replace(TPL_SLUG, slug)
    out = out.replace(quote(TPL_TITLE), quote(title))
    out = out.replace(TPL_TITLE, title)
    # 兜底：直接替换剩余的 doodles.google 详情链接为本地路由
    out = re.sub(r"https?://doodles\.google/doodle/[^\"'&<>\s]*", f"/doodle/{slug}/", out)
    return out


# ---------------------------------------------------------------- 生成 body
def build_main(rec, share_block, iframe_src, all_slugs, by_topic):
    slug = rec["slug"]
    title = rec.get("title") or slug
    img = rec.get("image_file") or ""
    color = theme_of(rec.get("color"))
    desc = (rec.get("description") or "").strip()
    topics = rec.get("topics") or []
    dlabel = date_label(rec)
    is_interactive = bool(iframe_src)
    share_block = instantiate_share(share_block, slug, title)

    media_cls = "media-container has-modal" if is_interactive else "media-container"

    # —— section-1 hero
    modal = ""
    if is_interactive:
        modal = (
            '<div class="glue-modal hero-doodle-modal is-interactive" aria-labelledby="modal-label" '
            'aria-describedby="modal-content">'
            '<div class="modal-content">'
            '<div class="modal-close-btn-wrapper">'
            '<button id="modal-close-btn" class="glue-button glue-button--low-emphasis '
            'glue-modal__close-btn" aria-label="Close the modal"></button>'
            '</div>'
            '<div class="modal-content-wrapper">'
            f'<iframe allow="web-share" src="{iframe_src}" loading="lazy" '
            'class="interactive-module" frameborder="0"></iframe>'
            '</div></div></div>'
        )

    # —— section-3 主题标签
    theme_links = ""
    for tp in topics:
        theme_links += (
            f'<a aria-label="" class="theme surface-4-outline" '
            f'href="/search/?topic_tags={esc(tp)}" '
            f'data-g-event-trigger="click" data-g-event="module_key_themes" '
            f'data-g-module_name="key_themes" data-g-cta_type="module" '
            f'data-g-cta_text="{esc(tp)}" data-g-cta_url="/search/?topic_tags={esc(tp)}"> {esc(tp)} </a>'
        )
    key_themes = ""
    if theme_links:
        key_themes = (
            f'<div class="doodle-module {color} key-themes" id="section-3">'
            '<div class="glue-page">'
            "<h2 class=\"key-themes-title\">This Doodle\u2019s Key Themes</h2>"
            '<div class="themes-cta-wrapper"><div class="themes-wrapper">'
            f"{theme_links}"
            "</div>"
            # 原页在这后面还有一个「Explore a Random Theme」胶囊。
            # 早期生成时漏了它，导致全站 6375 页的 KeyThemes 构造函数读 null.dataset
            # 抛异常，并把同一初始化函数里排在后面的四个模块一起掐断。
            f"{key_themes_cta(slug, theme_pool(by_topic))}"
            "</div></div></div>"
        )

    # —— 相关推荐：优先同主题，不足补最近日期
    related = []
    for tp in topics:
        for s in by_topic.get(tp, []):
            if s != slug and s not in related:
                related.append(s)
            if len(related) >= 8:
                break
        if len(related) >= 8:
            break

    cards = ""
    for s in related[:8]:
        r2 = all_slugs[s]
        cards += (
            '<div class="doodle-card-wrapper">'
            f'<a href="/doodle/{esc(s)}/" class="doodle-card-link">'
            f'<img loading="lazy" class="doodle-card-img" '
            f'src="{esc(r2.get("image_path") or "")}" '
            f'alt="{esc(r2.get("title") or s)}">'
            f'<div class="doodle-card-title">{esc(r2.get("title") or s)}</div>'
            "</a></div>"
        )
    related_block = ""
    if cards:
        related_block = (
            f'<div class="doodle-module {color} cards-stack" id="section-11">'
            '<div class="glue-page">'
            '<h2 class="cards-stack-title">Discover more Doodles</h2>'
            f'<div class="cards-stack-grid">{cards}</div>'
            "</div></div>"
        )

    opener = ""
    if desc:
        opener = (
            f'<div class="doodle-module {color}" id="section-2">'
            '<div class="glue-page">'
            '<div class="opener neutral-surface-1 main-outline add_link_class">'
            f"<p>{esc(desc)}</p>"
            "</div></div></div>"
        )

    title_block = (
        '<div class="display-flex">'
        '<div class="background-padding surface-1"></div>'
        '<div class="glue-page no-margin">'
        '<section class="content-container surface-1">'
        + (f'<p class="date">{esc(dlabel)}</p>' if dlabel else "")
        + f'<h1 class="title">{esc(title)}</h1>'
        "</section></div>"
        '<div class="background-padding surface-1"></div>'
        "</div>"
    )

    return (
        '<div id="content"><div class="main-page">'
        f'<div class="hero-doodle doodle-module {color}" id="section-1">'
        '<div class="display-flex border-bottom">'
        '<div class="background-padding"></div>'
        '<div class="glue-page no-margin">'
        '<section class="img-container">'
        f'<div class="{media_cls}">'
        f'<img loading="lazy" src="{esc(rec.get("image_path") or "")}" '
        f'class="doodle-image{" clickable" if is_interactive else ""}" alt="{esc(title)}">'
        "</div>"
        f"{modal}{share_block}"
        "</section></div>"
        '<div class="background-padding"></div>'
        "</div>"
        f"{title_block}"
        "</div>"
        f"{opener}{key_themes}{related_block}"
        "</div></div>"
    )


def build_head(head_tpl, rec):
    title = rec.get("title") or rec["slug"]
    desc = (rec.get("description") or "").strip()
    slug = rec["slug"]
    img = os.path.basename(rec.get("image_file") or "")
    head = head_tpl
    head = re.sub(r"<title>.*?</title>", f"<title>{esc(title)} - Google Doodles</title>",
                  head, flags=re.S)
    head = re.sub(r'<meta name="description" content="[^"]*">',
                  f'<meta name="description" content="{esc(desc)}">', head)
    head = re.sub(r'<link rel="canonical" href="[^"]*">',
                  f'<link rel="canonical" href="/doodle/{esc(slug)}/">', head)
    for prop in ["og:url", "twitter:url"]:
        head = re.sub(rf'<meta property="{prop}" content="[^"]*">',
                      f'<meta property="{prop}" content="/doodle/{esc(slug)}/">', head)
    for prop in ["itemprop\" image", "og:image", "twitter:image"]:
        pass
    head = re.sub(r'content="https?://[^"]*\.(png|jpg|jpeg|gif|webp)"',
                  f'content="/img/{esc(img)}"', head)
    return head


# ---------------------------------------------------------------- 主流程
def main():
    data = doodle_data.load()            # 已规范化（含 run_date / image_path）
    print(f"数据: {len(data)} 条，其中 {sum(1 for r in data if r['run_date'])} 条有确切日期")

    # iframe 映射
    iframe_map = {}
    if os.path.exists("iframe_map.js"):
        txt = open("iframe_map.js", encoding="utf-8").read()
        txt = txt[txt.index("{"):].rsplit("}", 1)[0] + "}"
        txt = re.sub(r"(\w[\w-]*)\s*:", r'"\1":', txt)
        try:
            iframe_map = json.loads(txt)
        except Exception as e:
            print("iframe_map 解析失败:", e)

    all_slugs = {r["slug"]: r for r in data}
    by_topic = defaultdict(list)
    for r in data:
        for t in (r.get("topics") or []):
            by_topic[t].append(r["slug"])

    head_tpl, header, footer, scripts = load_template()
    tpl_text = open(TEMPLATE, encoding="utf-8", errors="replace").read()
    share_block = extract_share_block(tpl_text)
    print(f"骨架: header {len(header)}B, footer {len(footer)}B, "
          f"分享条已移除(share={len(share_block)}B)")

    # 就地覆盖生成（不删除目录：批量删除会被安全策略拦截，而且没必要）
    out_root = os.path.join(SITE, "doodle")
    os.makedirs(out_root, exist_ok=True)
    stale = set()
    if os.path.isdir(out_root):
        stale = {d for d in os.listdir(out_root)
                 if os.path.isdir(os.path.join(out_root, d))}

    n_iframe = 0
    for i, rec in enumerate(data):
        slug = rec["slug"]
        iframe_src = iframe_map.get(slug, "")
        if iframe_src:
            # iframe_map 里是 "games/<slug>/x.html" 形式，转成站点绝对路径
            iframe_src = "/" + iframe_src.lstrip("/")
            n_iframe += 1
        head = build_head(head_tpl, rec)
        main_html = build_main(rec, share_block, iframe_src, all_slugs, by_topic)
        page = (
            "<!doctype html>\n<html lang=\"en\">\n"
            + head + "\n<body>\n"
            + header
            + main_html
            + footer
            + "\n</body>\n</html>\n"
        )
        d = os.path.join(out_root, slug)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "index.html"), "w", encoding="utf-8", newline="") as f:
            f.write(page)
        stale.discard(slug)
        if (i + 1) % 1000 == 0:
            print(f"  已生成 {i+1}/{len(data)}")

    if stale:
        print(f"注意：有 {len(stale)} 个旧目录不再对应任何涂鸦，未自动删除：{sorted(stale)[:5]}")

    print(f"完成: {len(data)} 个详情页，其中 {n_iframe} 个挂载了本地互动模块")


if __name__ == "__main__":
    main()
