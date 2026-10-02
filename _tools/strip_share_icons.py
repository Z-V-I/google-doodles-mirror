#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""摘掉正文里的 hero 分享条（`share-container`）。

【它是什么】
详情页配图旁边竖排的一组分享图标（复制链接 / LinkedIn / X / Facebook）加一个展开按钮，
关于页的 hero 区也有一版是横排的（`share-container horizontal`）。
其中三个 <a> 直接指向外部社交站点，是这个镜像里除了页脚之外最后一处外部社交入口。

页脚那一版由 strip_footer_google.py 处理；本脚本处理正文里这一版。
两处都清掉之后，全站不再有指向社交站点的链接和图标。

【为什么按 <div> 深度匹配，而不是非贪婪正则】
块内部嵌套了 <section> / <div> 好几层，`<div class="share-container.*?</div>` 会在
第一个内层 </div> 就收尾，把剩下的闭合标签留在页面里 —— 那样 DOM 结构会当场错乱。
这里从开标签起做 div 配平，取到真正的块尾。

【为什么删整块而不是只删三个 <a>】
那个展开按钮（`share-button`）唯一的用途就是展开/收起这一组图标，留着它就是个
点了没反应的死按钮。整块删掉，布局从「图片 + 分享条」变成纯图片，其余模块不受影响。

用法：
    python _tools/strip_share_icons.py --check    # 预演，不落盘
    python _tools/strip_share_icons.py            # 实际执行
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 开标签：vertical（详情页）/ horizontal（关于页），属性顺序固定，用 class 值兜住
OPEN_RE = re.compile(r'<div class="share-container[^"]*">')
DIV_TAG_RE = re.compile(r'<(/?)div\b[^>]*>', re.I)

# 源模板：只改 site/ 不改这里，重建就会把分享条整批复活
SOURCE_FILES = [
    '_clone_search/doodle-slideshow.html',
    '_clone_search/doodle-pacman-game.html',
    '_clone_search/about-page.html',
]

# 判断有没有摘干净
MARKERS = {
    'share-container': 'share-container',
    'glue-social 图标组': 'glue-social__list',
    'LinkedIn 分享': 'linkedin.com',
    'Facebook 分享': 'facebook.com/sharer',
    'X 分享': 'x.com/share',
    '复制链接按钮': 'copy-to-clipboard',
}


def targets():
    """site/ 下所有 HTML + 固定源模板清单。"""
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


def block_end(text, start):
    """从 <div ...> 起点做 div 配平，返回块尾下标（开区间）。找不到返回 -1。"""
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


def strip_all(text):
    """摘掉全部 share-container 块，返回 (新文本, 摘掉块数, 各特征计数)。"""
    counts = {k: 0 for k in MARKERS}
    n = 0
    pos = 0
    pieces = []
    while True:
        m = OPEN_RE.search(text, pos)
        if not m:
            pieces.append(text[pos:])
            break
        end = block_end(text, m.start())
        if end < 0:
            # 配平失败：不动这一块，原样保留，交给上层报错
            pieces.append(text[pos:m.start()])
            pos = m.start()
            break
        block = text[m.start():end]
        for k, v in MARKERS.items():
            counts[k] += block.count(v)
        n += 1
        pieces.append(text[pos:m.start()])
        pos = end
    return ''.join(pieces), n, counts


def process(path):
    raw = open(path, 'rb').read()
    text = raw.decode('utf-8')
    if 'share-container' not in text:
        return ('none', 0, 0, {k: 0 for k in MARKERS})
    out, n, counts = strip_all(text)
    if n == 0:
        return ('none', 0, 0, counts)
    if out == text:
        return ('unchanged', 0, 0, counts)
    with open(path, 'wb') as f:
        f.write(out.encode('utf-8'))
    return ('changed', n, len(text) - len(out), counts)


def main():
    check = '--check' in sys.argv
    files = targets()
    print(f'目标文件 : {len(files)}  (site/ 全部 HTML + {len(SOURCE_FILES)} 个源模板)\n')

    stat = {'changed': 0, 'unchanged': 0, 'none': 0}
    blocks = 0
    removed = 0
    agg = {k: 0 for k in MARKERS}
    examples = []

    for p in files:
        if check:
            text = open(p, 'rb').read().decode('utf-8')
            if 'share-container' not in text:
                stat['none'] += 1
                continue
            out, n, counts = strip_all(text)
            if n == 0 or out == text:
                stat['unchanged'] += 1
                continue
            stat['changed'] += 1
            blocks += n
            removed += len(text) - len(out)
            for k, v in counts.items():
                agg[k] += v
            if len(examples) < 5:
                examples.append((os.path.relpath(p, ROOT), n))
        else:
            st, n, delta, counts = process(p)
            stat[st] += 1
            if st == 'changed':
                blocks += n
                removed += delta
                for k, v in counts.items():
                    agg[k] += v
                if len(examples) < 5:
                    examples.append((os.path.relpath(p, ROOT), n))

    verb = '将摘除' if check else '已摘除'
    print(f"{verb}分享条 : {stat['changed']} 个文件 / {blocks} 块")
    print(f"无分享条   : {stat['none']}")
    print(f"无需改动   : {stat['unchanged']}")
    print(f"页面体量   : -{removed:,} B")
    print()
    print('将被摘掉的内容特征：')
    for k, v in agg.items():
        print(f'  {k:22s} {v:,}')
    if examples:
        print()
        print('示例：')
        for rel, n in examples:
            print(f'  {rel}  ({n} 块)')

    if not check:
        print('\n完成。')

    # 预演模式下，摘完还有残留就报错；实际执行时同样校验一次
    if agg['share-container'] == 0 and stat['changed'] > 0:
        print('\n【警告】命中文件却没有摘到任何块，正则可能失配。')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
