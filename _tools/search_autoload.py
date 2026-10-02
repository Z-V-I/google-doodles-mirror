#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
search_autoload.py —— 把 /search/ 的「Show More」按钮改成滚动到底自动加载。

【为什么要这么绕】
分页逻辑不在页面里，而在打包好的前端运行时里：

    site/static/9d6cae2b….index.min.js

其中 ResultsDoodleForm 把 `.search-doodle__results-button` 当作**唯一分页触发器**：
点击 → typeSubmit=append → 用 currentPage 拉下一页 → appendResults。
结果推进到第几页、还有没有下一页（e.next），状态全都挂在这个 <button> 的
`.active` 类上 —— active 在 = 还有下一页，active 没了 = 到底了。

所以正确做法不是删掉按钮（删了 JS 会 querySelector 拿到 null 直接抛错，
分页链路整条断掉），而是：
  1. 把按钮**视觉上藏掉**（display:none），DOM 与事件原样保留；
  2. 用一个旁挂脚本在「列表底部接近视口」时替用户点它。
     —— 复用按钮既有的点击处理，不复制、不重写任何分页逻辑。

自动加载的刹车全部来自运行时自己的状态，不额外发明：
  · 加载中：按钮的 .active 会被运行时同步摘掉（handleLoading），
    再叠一层 `.search-doodle__results-loading.active` 判断兜底；
  · 到底了：appendResults 里 `toggle(active, Boolean(e.next))` 自动摘掉 active；
  · 点了没反应（网络错/空页）：比对子节点数量有没有增长，没长就停手，
    避免陷进「点了没反应 → 再点」的死循环。

【为什么要排在流水线最后】
它改的是页面 HTML 尾部，而 build / pages / localize 等步骤都会重写 HTML。
排在它们之后 + 幂等标记，才能保证不会被下一次重建冲掉。

用法：
    python _tools/search_autoload.py            # 打补丁（幂等）
    python _tools/search_autoload.py --revert   # 还原
    python _tools/search_autoload.py --check    # 只报告状态
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

# _clone_search/page.html 是搜索页的源模板（build_site.py 由它产出 site/search/index.html），
# 两个都要写：只写产物的话，下次重建就没了。
TARGETS = [
    "_clone_search/page.html",
    "site/search/index.html",
]

BEGIN = "<!-- zvi:search-autoload -->"
END = "<!-- /zvi:search-autoload -->"

BLOCK = BEGIN + """
<style>
  /* Show More 已改为滚动自动加载：按钮只作为分页触发器留在 DOM 里，不再展示 */
  .search-doodle__results-button { display: none !important; }
</style>
<script>
(function () {
  var SEL_BTN = '.search-doodle__results-button';
  var SEL_LIST = '.search-doodle__results-wrapper';
  var SEL_LOADING = '.search-doodle__results-loading';

  var THRESHOLD = 800;      // px：列表底部进入「视口下方这么多」就预取下一页
  var POLL_MS = 300;        // 首屏结果走 fetch 异步落地，头 60s 轮询兜底
  var POLL_MAX = 200;
  var TIMEOUT_MS = 40000;   // 单页请求兜底超时

  var btn = document.querySelector(SEL_BTN);
  var list = document.querySelector(SEL_LIST);
  var loading = document.querySelector(SEL_LOADING);
  if (!btn || !list) return;

  var inflight = false;
  var ticks = 0;

  function isLoading() {
    return !!(loading && loading.classList.contains('active'));
  }
  function hasMore() {
    return btn.classList.contains('active');   // 运行时用它表示「还有下一页」
  }
  function inRange() {
    var r = list.getBoundingClientRect();
    if (!r.height) return false;                    // 首屏结果还没落地
    if (r.top > window.innerHeight) return false;   // 结果区整体还在视口下方
    return r.bottom - window.innerHeight <= THRESHOLD;
  }

  function pump() {
    if (inflight || isLoading() || !hasMore() || !inRange()) return;
    inflight = true;
    var before = list.childElementCount;
    var t0 = Date.now();
    btn.click();                                    // 复用运行时既有的分页逻辑
    (function settle() {
      if (isLoading() && Date.now() - t0 < TIMEOUT_MS) {
        setTimeout(settle, 100);
        return;
      }
      inflight = false;
      // 内容确实增长了才继续往下取；没长说明点了没用，停手等下一次滚动
      if (list.childElementCount > before) setTimeout(pump, 120);
    })();
  }

  var raf = 0;
  function schedule() {
    if (raf) return;
    raf = window.requestAnimationFrame(function () { raf = 0; pump(); });
  }

  window.addEventListener('scroll', schedule, { passive: true });
  window.addEventListener('resize', schedule);
  if (window.MutationObserver) {
    new MutationObserver(schedule).observe(list, { childList: true });
  }

  var timer = setInterval(function () {
    pump();
    if (++ticks > POLL_MAX) clearInterval(timer);
  }, POLL_MS);
  pump();
})();
</script>
""" + END


def find_anchor(text):
    """返回插入点：整页尾部最后一处 </html>（脚本都在它之前），退而求其次 </body>。"""
    i = text.rfind("</html>")
    if i >= 0:
        return i
    i = text.rfind("</body>")
    if i >= 0:
        return i
    return -1


def patch(text):
    if BEGIN in text:
        return text, 0
    at = find_anchor(text)
    if at < 0:
        return text, -1
    # 统一换行风格：源模板是 CRLF，产物是 LF，随各文件自身的风格来
    nl = "\r\n" if "\r\n" in text[:2000] else "\n"
    block = BLOCK if nl == "\n" else BLOCK.replace("\n", "\r\n")
    return text[:at] + block + nl + text[at:], 1


def revert(text):
    i = text.find(BEGIN)
    if i < 0:
        return text, 0
    j = text.find(END, i)
    if j < 0:
        return text, 0
    j += len(END)
    # 连同后面的一个换行一起去掉，还原成插入前的样子
    while j < len(text) and text[j] in "\r\n":
        j += 1
    return text[:i] + text[j:], 1


def main():
    args = set(sys.argv[1:])
    do_revert = "--revert" in args
    check_only = "--check" in args

    for p in TARGETS:
        if not os.path.exists(p):
            print("  [跳过] %s（不存在）" % p)
            continue
        with open(p, encoding="utf-8", newline="") as f:
            text = f.read()

        if check_only:
            print("  [%s] %s" % ("已改" if BEGIN in text else "原样", p))
            continue

        new, n = (revert(text) if do_revert else patch(text))
        if n == -1:
            print("  ! %s 找不到 </html>/</body> 锚点，未改动" % p)
            continue
        if n == 0 or new == text:
            print("  [跳过] %s（%s无需改动）" % (p, "还原" if do_revert else "打补丁"))
            continue
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(new)
        print("  [%s] %s  改动 %d 处" % ("还原" if do_revert else "打补丁", p, n))


if __name__ == "__main__":
    main()
