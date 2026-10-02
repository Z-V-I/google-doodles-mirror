#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""补回 `key-themes` 模块里缺失的「Explore a Random Theme」CTA，修掉全站 JS 异常。

【报的是什么】
    TypeError: Cannot read properties of null (reading 'dataset')
        at new t.KeyThemes (static/…index.min.js:1:191667)

原站 bundle 里那个类的实现是：

    class KeyThemes {
      constructor(el) {
        this.linkElement = el.querySelector(".key-themes-cta-link");   // ← 这里是 null
        this.links = JSON.parse(this.linkElement.dataset.links);       // ← 于是炸这里
        ...
      }
    }
    调用方：document.querySelectorAll(".key-themes").forEach(el => new KeyThemes(el));

【为什么是 null】
详情页的 `key-themes` 模块是 gen_doodle_pages.py 拼出来的，只写了标题 + 主题标签，
漏掉了原页里那个「Explore a Random Theme」跳转胶囊（`<a class="key-themes-cta-link"
data-links="[…]">`）。于是全站 6,375 个详情页都命中 `.key-themes`、都没有那个子元素，
构造函数第一次访问 dataset 就抛异常。

【为什么必须补回来，而不是绕过它】
那一行异常是在同一个初始化函数里抛的，它后面还排着四个模块：

    …forEach(e => new KeyThemes(e));          ← 抛在这里，后面的全部不执行
    const f = AddLinkClass.getInstance(); …   ← 内部链接样式
    new ContentBlock; new SocialShare; new ToutCarousel

也就是说这个异常把后面四个模块的初始化整条掐断了（同一次 forEach 里前面几个
AnalyticsGlobal / FullWidthCardList / HeroDoodle 已经跑过）。补回 CTA 后链路恢复，
比在 bundle 里加 null 判断更接近原站 —— 而且原页本来就有这个按钮，缺的正是它。

【随机主题从哪来】
原页的 data-links 是每个页面一份的 10 条随机主题（实测两个抓取页的列表并不相同）。
原站那批细分主题（skateboarding / hinamatsuri / …）本地数据里没有，链接会落到空结果，
所以这里改用**本地自己的主题分类**做池子：按 slug 做确定性抽样，每页 10 条，
每次点都落在有内容的检索结果上，且各页不同 —— 机制一致，结果可用。

用法：
    python _tools/fix_key_themes.py --check    # 预演，不落盘
    python _tools/fix_key_themes.py            # 实际执行
"""

import collections
import json
import os
import random
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 与原始抓取页逐字一致的图标引用（该 symbols 文件本地已存在）
ARROW_SVG = ('<svg width="21" height="16" viewBox="0 0 21 16" fill="none" '
             'alt="" role="img"><use xlink:href="/static/'
             '37ae077da8a25b6950c926233ed752617048c50792204bc7b9d6332bbb7a3a5f'
             '.icons.svg#right-arrow-doodles"></use></svg>')

CTA_LABEL = 'Explore a Random Theme'

# 每个页面挂几条候选主题（与原页一致）
LINKS_PER_PAGE = 10

MODULE_RE = re.compile(r'<div class="doodle-module[^"]*\bkey-themes\b[^"]*"[^>]*>')
WRAPPER_OPEN = '<div class="themes-cta-wrapper">'
DIV_TAG_RE = re.compile(r'<(/?)div\b[^>]*>', re.I)

SOURCE_FILES = [
    '_clone_search/doodle-slideshow.html',
    '_clone_search/doodle-pacman-game.html',
]


def topic_pool():
    """本地主题分类池：取 doodles_data.js 的 topics，去掉无所指的 Other 与含 & 的项。

    `&` 会同时破坏 URL 查询串和 HTML 属性，本地那两个（Plants & Flowers）
    在检索页里本来就是靠参数解析兜住的，没必再塞进随机池。
    """
    p = os.path.join(ROOT, 'doodles_data.js')
    t = open(p, encoding='utf-8').read()
    data = json.loads(t[t.index('['):].rstrip().rstrip(';'))
    cnt = collections.Counter()
    for r in data:
        for tp in (r.get('topics') or []):
            cnt[tp] += 1
    pool = [k for k in cnt if k.lower() != 'other' and '&' not in k]
    # 先按热度降序、再按名称，保证顺序稳定（抽样结果可复现）
    pool.sort(key=lambda k: (-cnt[k], k))
    return pool


def build_cta(slug, pool):
    """生成一个页面的 CTA 标签。"""
    picks = random.Random('key-themes/' + slug).sample(pool, LINKS_PER_PAGE)
    links = ['/search/?topic_tags=' + p for p in picks]
    # HTML 属性里必须把引号转成实体，否则 JSON 的双引号会截断属性（原页也是 &#34;）
    data_links = json.dumps(links, ensure_ascii=False).replace('"', '&#34;')
    return (
        f'<a aria-label="{CTA_LABEL}" class="key-themes-cta-link" '
        f'data-links="{data_links}" href="{links[0]}">'
        f'<div class="key-themes-cta-wrapper">'
        f'<div class="key-themes-cta">{CTA_LABEL}</div>'
        f'{ARROW_SVG}'
        f'</div></a>'
    )


def block_end(text, start):
    """从 <div ...> 起点做 div 配平，返回块尾下标。找不到返回 -1。"""
    j = text.index('>', start) + 1
    depth = 1
    k = j
    while depth > 0:
        m = DIV_TAG_RE.search(text, k)
        if not m:
            return -1
        depth += -1 if m.group(1) else 1
        k = m.end()
    return k


def fix_text(text, pool, slug):
    """给文本里每个 key-themes 模块补上 CTA，返回 (新文本, 补了几处)。"""
    n = 0
    pos = 0
    pieces = []
    while True:
        m = MODULE_RE.search(text, pos)
        if not m:
            pieces.append(text[pos:])
            break
        end = block_end(text, m.start())
        if end < 0:
            pieces.append(text[pos:m.end()])
            pos = m.end()
            continue
        module = text[m.start():end]
        if 'key-themes-cta-link' in module:
            # 已经有（幂等 / 源模板本就没缺）：原样跳过
            pieces.append(text[pos:end])
            pos = end
            continue
        w = module.find(WRAPPER_OPEN)
        if w < 0:
            pieces.append(text[pos:end])
            pos = end
            continue
        w_end = block_end(module, w)
        if w_end < 0:
            pieces.append(text[pos:end])
            pos = end
            continue
        cta = build_cta(slug, pool)
        new_module = module[:w_end - len('</div>')] + cta + module[w_end - len('</div>'):]
        pieces.append(text[pos:m.start()])
        pieces.append(new_module)
        pos = end
        n += 1
    return ''.join(pieces), n


def targets():
    out = []
    for root, _dirs, files in os.walk(os.path.join(ROOT, 'site')):
        for fn in files:
            if fn.endswith('.html'):
                out.append(os.path.join(root, fn))
    for rel in SOURCE_FILES:
        p = os.path.join(ROOT, rel)
        if os.path.exists(p):
            out.append(p)
    return out


def main():
    check = '--check' in sys.argv
    pool = topic_pool()
    files = targets()
    print(f'主题池   : {len(pool)} 项（来自本地 doodles_data.js）')
    print(f'目标文件 : {len(files)}  (site/ 全部 HTML + {len(SOURCE_FILES)} 个源模板)')
    print(f'每页挂   : {LINKS_PER_PAGE} 条随机主题\n')

    changed = 0
    untouched = 0
    no_module = 0
    already = 0
    total_cta = 0
    grew = 0
    examples = []

    for p in files:
        text = open(p, 'rb').read().decode('utf-8')
        if 'key-themes' not in text:
            no_module += 1
            continue
        slug = os.path.basename(os.path.dirname(p))
        out, n = fix_text(text, pool, slug)
        if n == 0:
            if 'key-themes-cta-link' in text:
                already += 1
            else:
                untouched += 1
            continue
        changed += 1
        total_cta += n
        grew += len(out) - len(text)
        if len(examples) < 5:
            examples.append((os.path.relpath(p, ROOT), n))
        if not check:
            with open(p, 'wb') as f:
                f.write(out.encode('utf-8'))

    verb = '将补' if check else '已补'
    print(f"{verb} CTA       : {changed} 个文件 / {total_cta} 处")
    print(f"已有 CTA   : {already}")
    print(f"无 key-themes 模块 : {no_module}")
    print(f"命中但未处理       : {untouched}")
    print(f"页面体量   : +{grew:,} B")
    if examples:
        print('\n示例：')
        for rel, n in examples:
            print(f'  {rel}  ({n} 处)')
    if not check:
        print('\n完成。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
