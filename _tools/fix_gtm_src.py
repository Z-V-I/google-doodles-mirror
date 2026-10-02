#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修掉 GTM 脚本地址的拼接 bug —— 全站 6,377 个页面各有一个 404。

【现象】
    每个页面都请求 http://…/vendor/gtmc67cef9231.jsGTM-KQ5TXT7 → 404。
    console 里只留一句「Failed to load resource: the server responded with a
    status of 404 (File not found)」，不带 URL，光看控制台无法定位。

【根因】
    原站的标准 GTM 片段是：
        j.src='https://www.googletagmanager.com/gtm.js?id='+i+dl;
    其中 i = 'GTM-KQ5TXT7'、dl = ''。离线化时把 URL 部分
    （含尾部的 '?id='）整段换成了本地路径，但**后面 +i+dl 的拼接留了下来**，
    运行时就把容器 ID 直接粘到了文件名末尾：
        /vendor/gtmc67cef9231.js  +  GTM-KQ5TXT7  →  /vendor/gtmc67cef9231.jsGTM-KQ5TXT7

【修法】
    把整个 src 赋值连同后面的拼接一起收掉，直接指向已有的本地桩件：
        j.src='/vendor/gtm-stub.js';
    桩件本来就定义了 window.dataLayer / google_tag_manager，语义比那个 27 字节的
    `/* offline placeholder */` 占位文件更准确，也顺带让这个 404 消失。

    同时处理两种形态：
      · 源模板里是原始 URL   '…gtm.js?id='+i+dl
      · site/ 产物里是本地路径 '/vendor/gtm<hash>.js'+i+dl
    只改产物不改源模板的话，重跑 rebuild 会再次生成带 bug 的版本。

幂等：替换后不再匹配，重跑无变化。
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 两种前缀都吃掉：原始远程 URL（源模板）与已本地化的哈希名（site 产物）。
# j.src= 与引号之间可能是换行 —— 原站 GTM 片段是把 URL 另起一行写的，
# 源模板里保留了这个形态，所以中间要允许 \s*。
SRC_RE = re.compile(
    r"""j\.src=\s*(['"])"""
    r"""(?:https?://www\.googletagmanager\.com/gtm\.js\?id=|/vendor/gtm[\w.-]*\.js)"""
    r"""\1\s*\+\s*i\s*\+\s*dl\s*;"""
)

REPLACEMENT = "j.src='/vendor/gtm-stub.js';"

SOURCE_FILES = [
    '_google_clone/index_local.html',
    '_clone_search/page.html',
    '_clone_search/about-page.html',
    '_clone_search/doodle-slideshow.html',
    '_clone_search/doodle-pacman-game.html',
    '_clone_search/creating-page.html',
]


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
    files = targets()
    print(f'目标文件 : {len(files)}\n')

    changed = 0
    hits = 0
    samples = []
    for p in files:
        raw = open(p, 'rb').read()
        text = raw.decode('utf-8')
        n = len(SRC_RE.findall(text))
        if not n:
            continue
        changed += 1
        hits += n
        if check:
            if len(samples) < 3:
                samples.append(os.path.relpath(p, ROOT))
            continue
        out = SRC_RE.sub(REPLACEMENT, text)
        with open(p, 'wb') as f:
            f.write(out.encode('utf-8'))
        if len(samples) < 3:
            samples.append(os.path.relpath(p, ROOT))

    verb = '将修复' if check else '已修复'
    print(f'{verb}文件 : {changed}')
    print(f'修复处数   : {hits}')
    print(f'替换为     : {REPLACEMENT}')
    if samples:
        print(f'样本       : {", ".join(samples)}')
    if check:
        print('\n（预演模式，未写盘）')


if __name__ == '__main__':
    main()
