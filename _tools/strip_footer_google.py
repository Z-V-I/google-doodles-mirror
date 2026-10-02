#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""摘掉页脚里指向谷歌在线服务的入口与社交图标，并换上自定义版权行。

处理对象
    site/                 下所有含 <footer class="glue-footer"> 的 HTML
    _clone_search/        doodle-slideshow.html 等源模板（详情页骨架来源）
    _google_clone/        index_local.html（首页来源）
    —— 只改 site/ 不改源头的话，重跑 rebuild 会把删掉的东西整批复活。

删除内容（全部位于页脚内）
    ① glue-social 社交区       Follow us + LinkedIn / Facebook / Instagram / X / YouTube 图标
    ② recaptcha 免责声明       含 policies.google.com 的 Privacy Policy / Terms of Service 链接
    ③ Google logo              指向 google.com
    ④ About Google / Google Products / Privacy / Terms
    ⑤ Help                     指向 support.google.com
    保留 glue-footer__site-links（Doodles → Library / About）与无障碍标题。

新增
    版权行 "Copyright © 2026 → ∞ ZVI and Google will always be together."

做法
    整块替换 <footer class="glue-footer"> … </footer>。
    之所以不逐块正则摘除：页脚内有嵌套 <section>，用非贪婪正则按块删极易误截；
    而全站页脚只有两个变体（6,377 页字节相同 + 首页 1 页），整块替换既稳又幂等。
    <footer> 之后、</html> 之前的 </body> 与 <script> 不在替换范围内，原样保留。

幂等：替换后仍能被同一正则命中，再跑一次结果不变。
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FOOTER_RE = re.compile(r'[ \t]*<footer class="glue-footer">.*?</footer>', re.S)

COPYRIGHT = 'Copyright © 2026 → ∞ ZVI and Google will always be together.'

# 版权行的内联样式：对齐 .glue-footer__link 的文字观感（Google Sans / 1rem / #5f6368）。
# 用内联而不用新增 CSS 类，是为了不动原站那份 min.css —— 它是抓取原物，
# 且 build 时按哈希文件名重新铺设，改它既不持久也不干净。
COPY_STYLE = (
    'flex:1 0 auto;margin:0;padding:0 0 16px;'
    'font-family:Google Sans,Roboto,Arial,Helvetica,sans-serif;'
    'font-size:1rem;font-weight:500;line-height:1.5;color:#5f6368'
)

# 待删块的识别特征（用于统计，不参与替换逻辑）
MARKERS = {
    '社交图标':    'glue-social__item',
    '谷歌外链入口': '/offline/?to=',
    'Google logo': '<div class="glue-footer__logo">',
    '页脚法务链接': 'glue-footer__global-links-list-item',
    'recaptcha':   'recaptcha-disclaimer',
}

NEW_FOOTER_LF = '''  <footer class="glue-footer">
  <h2 class="glue-visually-hidden">Footer Links</h2>
  <nav class="glue-footer__site-links">
    <div class="glue-footer__site-links-grid glue-footer__site-links-grid--1-col">
      <div class="glue-footer__site-links-column">
        <h3 class="glue-footer__site-links-header">
          <span class="glue-footer__site-links-header-button" id="column-1-toggle">
             <span class="glue-footer__site-links-header-text">
              Doodles
            </span>
            <svg role="presentation" aria-hidden="true"
                 class="glue-icon glue-icon--footer glue-expansion-panel__header-arrow">
              <use href="/static/0f99e02819f0bba398fbcbeff94c8a0eb698e988148884f15cb92c39c17487d7.glue-icons.svg#expand-more"></use>
            </svg>
          </span>
        </h3>
        <div class="glue-footer__site-links-list" id="column-1-content">
          <ul class="glue-no-bullet" role="list">
            <li class="glue-footer__site-links-list-item">
              <a class="glue-footer__link" href="/search/"
                  data-g-event-trigger="click"
                  data-g-event="module_footer"
                  data-g-module_id=""
                  data-g-module_name="footer"
                  data-g-module_postion=""
                  data-g-cta_type="button"
                  data-g-cta_text="Library"
                  data-g-cta_url="/search/"
                  data-g-template_id="footer_item"
                  data-g-template_position="1/2">
                Library
              </a>
            </li>
            <li class="glue-footer__site-links-list-item">
              <a class="glue-footer__link" href="/about/"
                  data-g-event-trigger="click"
                  data-g-event="module_footer"
                  data-g-module_id=""
                  data-g-module_name="footer"
                  data-g-module_postion=""
                  data-g-cta_type="button"
                  data-g-cta_text="About"
                  data-g-cta_url="/about/"
                  data-g-template_id="footer_item"
                  data-g-template_position="2/2">
                About
              </a>
            </li>
          </ul>
        </div>
      </div>
    </div>
  </nav>
  <section class="glue-footer__global">
    <p class="glue-footer__copyright" style="__STYLE__">__TEXT__</p>
  </section>
</footer>'''.replace('__STYLE__', COPY_STYLE).replace('__TEXT__', COPYRIGHT)

# 源模板：改了 site/ 不改这里，rebuild 就会把删掉的内容整批复活
SOURCE_FILES = [
    '_google_clone/index_local.html',
    '_clone_search/page.html',
    '_clone_search/about-page.html',
    '_clone_search/doodle-slideshow.html',
    '_clone_search/doodle-pacman-game.html',
    '_clone_search/footer.html',
    '_clone_search/creating-page.html',
]


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


def process(path):
    """返回 (状态, 旧块字节数, 新块字节数, 特征计数)。"""
    raw = open(path, 'rb').read()
    text = raw.decode('utf-8')
    m = FOOTER_RE.search(text)
    if not m:
        return ('no-footer', 0, 0, {})

    old = m.group(0)
    # 跟随文件原有换行风格：site/ 是 LF，_clone_search/ 与 _google_clone/ 是 CRLF
    nl = '\r\n' if '\r\n' in old else '\n'
    new = NEW_FOOTER_LF.replace('\n', nl)

    if old == new:
        return ('unchanged', len(old), len(new), {})

    counts = {k: old.count(v) for k, v in MARKERS.items()}
    out = text[:m.start()] + new + text[m.end():]
    with open(path, 'wb') as f:
        f.write(out.encode('utf-8'))
    return ('changed', len(old), len(new), counts)


def main():
    check = '--check' in sys.argv
    files = targets()
    print(f'目标文件 : {len(files)}  (site/ 全部 HTML + {len(SOURCE_FILES)} 个源模板)\n')

    stat = {'changed': 0, 'unchanged': 0, 'no-footer': 0}
    tot_old = tot_new = 0
    agg = {k: 0 for k in MARKERS}
    changed_examples = []

    for p in files:
        if check:
            raw = open(p, 'rb').read().decode('utf-8')
            m = FOOTER_RE.search(raw)
            if not m:
                stat['no-footer'] += 1
                continue
            old = m.group(0)
            nl = '\r\n' if '\r\n' in old else '\n'
            new = NEW_FOOTER_LF.replace('\n', nl)
            if old == new:
                stat['unchanged'] += 1
                continue
            stat['changed'] += 1
            for k, v in MARKERS.items():
                agg[k] += old.count(v)
            tot_old += len(old)
            tot_new += len(new)
        else:
            st, a, b, counts = process(p)
            stat[st] += 1
            if st == 'changed':
                tot_old += a
                tot_new += b
                for k, v in counts.items():
                    agg[k] += v
                if len(changed_examples) < 5:
                    changed_examples.append(os.path.relpath(p, ROOT))

    verb = '将替换' if check else '已替换'
    print(f"{verb}页脚 : {stat['changed']}")
    print(f"无需改动   : {stat['unchanged']}")
    print(f"无页脚     : {stat['no-footer']}")
    print(f"页脚体量   : {tot_old:,} B -> {tot_new:,} B  ({tot_new - tot_old:+,} B)")
    print()
    print('摘除明细（按特征串计数）：')
    for k, v in agg.items():
        print(f'  {k:12s} {v:,}')
    if changed_examples:
        print()
        print('样本:', ', '.join(changed_examples))
    if check:
        print('\n（预演模式，未写盘）')


if __name__ == '__main__':
    main()
