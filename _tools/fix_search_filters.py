#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把检索页的「筛选词表」与本地数据对齐，并修掉会整页崩掉的 chip 链接。

【为什么会有这个脚本】
检索页 /search/ 的筛选面板是**原站服务端渲染好的 HTML**，面板里每个选项的
value 来自原站 API 的词表；而详情页上的主题 chip 与 rss/doodles.json 里的
标记，来自我们本地这套抓取/生成的词表。两套词表从来没有对齐过，于是出现
三类缺陷，且全部是「不报错、界面看不出异常」的那种：

  ① chip 链接整页崩
     「Plants & Flowers」这个标签含 `&`，生成 href 时没有做百分号编码，
     属性值写成了 /search/?topic_tags=Plants &amp; Flowers。浏览器解析后
     查询串裂成两个参数，第二个参数名是 " Flowers" —— 筛选表单里没有这个
     字段，命中原 bundle loadParams() 的裸读 .tagName，抛异常并中断整个
     初始化，结果列表全白。共 13 处链接（13 个详情页各一处）。

  ② chip 链接静默空转
     loadParams 只做 toLowerCase()，然后按 `[name="topic_tags"][value=...]`
     去表单里找对应的复选框。找不到就不勾选 —— 于是「筛选条件没生效」，
     页面显示的是**未筛选的最新 16 条**。实测 9 个标签落在这里：

         Other(2848)  Birthday(1567)  Football/Soccer(273)  New Year(49)
         Plants & Flowers(13)  Easter(12)  Badminton(1)  Diwali(1)
         Television/Film(1)

     其中 Football/Soccer、Television/Film、Plants & Flowers 是**标点差异**：
     面板的取值规则是「非字母数字折叠成空格」，所以面板里叫 football soccer /
     television film / plants flowers。

  ③ 筛选维度整个不可用
     面板里还有一批取值与数据词表是「同义不同名」，点了必然是 0 条：
         form_tags : animated gif → animated / interactive game → game
                     still image → still        （5 个选项里坏 3 个）
         color_tags: gray → grey                 （12 个选项里坏 1 个）

【本脚本做什么】
  A. 把 3 个含标点的话题 chip 链接改写为面板的规范取值（顺带治好 ①）
  B. 给话题面板补 6 个缺失的选项（治 ② 里剩下的 6 个）
  C. 把 form_tags / color_tags 里同义不同名的 4 个取值改成数据词表（治 ③）
  D. 给检索页挂上 /vendor/search-filter-guard.js（未知参数优雅降级，不再整页白）

只动 site/，幂等，可重复执行。改动落在 site/ 之后，_deploy 由
build_deploy.py 重新镜像即可同步。

用法：
    python _tools/fix_search_filters.py --check   # 预演，只报告不落盘
    python _tools/fix_search_filters.py           # 实际执行
    python _tools/fix_search_filters.py --check --root _deploy
                                                  # 把回归检查指向部署包

（verify_deploy.py 就是用最后那种方式复用本脚本的检查的 —— 判据只写一份，
免得「修的时候一套、验的时候另一套」而互相放过。）
"""

import argparse
import collections
import json
import os
import re
import sys
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(ROOT, "site")
SEARCH = os.path.join(SITE, "search", "index.html")
DATA = os.path.join(SITE, "rss", "doodles.json")

GUARD_SRC = "/vendor/search-filter-guard.js"
GUARD_ANCHOR = '<script src="/vendor/recaptcha-stub.js">'

# ── ① chip href 改写：含标点的话题标签 → 面板里的规范取值 ─────────────────────
# 面板取值一律不含 `&`（setParams 是把 name/value 直接拼进查询串的，不做编码，
# 所以面板里绝不能出现 `&`，否则用户一勾选就会生成同一个坏 URL）。
HREF_FIXES = [
    ("/search/?topic_tags=Football/Soccer", "/search/?topic_tags=football%20soccer"),
    ("/search/?topic_tags=Television/Film", "/search/?topic_tags=television%20film"),
    ("/search/?topic_tags=Plants &amp; Flowers", "/search/?topic_tags=plants%20flowers"),
    ("/search/?topic_tags=Plants & Flowers", "/search/?topic_tags=plants%20flowers"),
]

# ── ② 话题面板补 6 个选项：(value, 显示标签, 插在哪个取值后面) ────────────────
TOPIC_ADD = [
    ("other", "Other", "new google logo"),
    ("birthday", "Birthday", "asian pacific american heritage month"),
    ("new year", "New Year", "native american heritage month"),
    ("easter", "Easter", "earth day"),
    ("diwali", "Diwali", "day of the dead"),
    ("badminton", "Badminton", "badmington"),
]

# ── ③ 同义不同名的取值 → 数据词表 ─────────────────────────────────────────────
VALUE_FIXES = [
    ("form_tags", "animated gif", "animated"),
    ("form_tags", "interactive game", "game"),
    ("form_tags", "still image", "still"),
    ("color_tags", "gray", "grey"),
]


def slug(s):
    """面板的取值规则：非字母数字折叠成单个空格，再转小写、去首尾。

    与 serve.py / search-api-shim.js / verify_deploy.py 里标签匹配用的
    normTag 是**同一条规则**（那一侧已同步改为折叠标点）。
    """
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def panel_values(text, name):
    return [m.group(1) for m in
            re.finditer(r'<input[^>]*?name="%s"[^>]*?value="([^"]*)"' % name, text, re.S)]


def rename_value(text, name, old, new):
    """只改 <input ... name="X" ... value="OLD"> 里的 value，显示标签不动。"""
    pat = re.compile(r'(<input\b[^>]*?name="%s"[^>]*?value=")%s(")'
                     % (re.escape(name), re.escape(old)))
    return pat.subn(lambda m: m.group(1) + new + m.group(2), text)


def label_block(text, value, name="topic_tags"):
    """取出某个取值所在的整个 <label ...>…</label> 块。

    返回 (起下标, 止下标, 块文本, 该块自己的缩进)。缩进单独取出来是为了克隆时
    沿用原条目的排版 —— 面板分了 8 个主题分组，各分组的缩进并不一致，写死会错位。
    """
    m = re.search(r'<input[^>]*?name="%s"[^>]*?value="%s"' % (re.escape(name), re.escape(value)),
                  text, re.S)
    if not m:
        return None
    start = text.rfind("<label", 0, m.start())
    if start < 0:
        return None
    end = text.find("</label>", m.end())
    if end < 0:
        return None
    line_start = text.rfind("\n", 0, start) + 1
    indent = text[line_start:start]
    if indent.strip():            # <label 前面还有别的内容，说明缩进取错了
        indent = ""
    return start, end + len("</label>"), text[start:end + len("</label>")], indent


def make_block(template, value, label):
    """用现有条目当模板克隆一条，只替换 value 与显示文本（连缩进一起沿用）。"""
    out = re.sub(r'(value=")[^"]*(")', lambda m: m.group(1) + value + m.group(2),
                 template, count=1)
    # 显示文本连同它两侧的换行缩进一起换掉，避免把模板里的缩进弄丢
    return re.sub(r'(<span class="search-doodle__tag-item-label">)(\s*).*?(\s*)(</span>)',
                  lambda m: m.group(1) + m.group(2) + label + m.group(3) + m.group(4),
                  out, count=1, flags=re.S)


def patch_panel(text):
    """改动检索页的筛选面板，返回 (新文本, 改动说明列表)。"""
    notes = []

    # ③ 同义不同名
    for name, old, new in VALUE_FIXES:
        text, n = rename_value(text, name, old, new)
        if n:
            notes.append("取值改名 %s: %r → %r（%d 处）" % (name, old, new, n))

    # ② 补话题选项（克隆现有条目，结构一定与面板一致）
    tmpl = label_block(text, "animals") or label_block(text, "animation")
    if not tmpl:
        notes.append("!! 找不到可作模板的话题条目，跳过补选项")
        return text, notes
    template, indent = tmpl[2], tmpl[3]
    for value, label, after in TOPIC_ADD:
        if value in panel_values(text, "topic_tags"):
            continue
        anchor = label_block(text, after)
        if not anchor:
            notes.append("!! 锚点取值 %r 不存在，无法插入 %r" % (after, value))
            continue
        text = (text[:anchor[1]] + "\n" + indent +
                make_block(template, value, label) + text[anchor[1]:])
        notes.append("补话题选项 %r（显示 %s），插在 %r 之后" % (value, label, after))

    # D 挂守卫脚本
    if GUARD_SRC not in text:
        i = text.find(GUARD_ANCHOR)
        if i < 0:
            notes.append("!! 找不到 %s，守卫脚本未挂上" % GUARD_ANCHOR)
        else:
            line_start = text.rfind("\n", 0, i) + 1
            indent = text[line_start:i]
            text = (text[:line_start] + indent +
                    '<script src="%s"></script>\n' % GUARD_SRC + text[line_start:])
            notes.append("挂上守卫脚本 %s（在 recaptcha-stub 之前）" % GUARD_SRC)
    return text, notes


def fix_hrefs(dry):
    """全站改写 3 个含标点的话题 chip 链接。"""
    total = collections.Counter()
    files = 0
    for root, _dirs, fs in os.walk(SITE):
        for fn in fs:
            if not fn.endswith((".html", ".htm")):
                continue
            p = os.path.join(root, fn)
            try:
                t = open(p, encoding="utf-8").read()
            except (OSError, UnicodeDecodeError):
                continue
            orig = t
            for old, new in HREF_FIXES:
                if old in t:
                    total[old] += t.count(old)
                    t = t.replace(old, new)
            if t != orig:
                files += 1
                if not dry:
                    open(p, "w", encoding="utf-8", newline="").write(t)
    return total, files


# ── 已知且暂不处理的缺口：(维度, 取值) -> 原因 ────────────────────────────────
# 这些不是本次要修的东西，但必须显式列出来，否则验收里会一直报红、看久了就被忽略。
KNOWN_GAPS = {
    ("color", "cyan"): (
        "面板 12 色的词表里没有 cyan，本地数据却有 413 条。面板的 multicolor 与"
        "本地的 cyan 数量结构上是一一对应（12 对 12，只差这一对），但没有任何"
        "证据能确认二者同义；补一个 Cyan 色块要克隆色圈结构、有视觉风险，故不动。"
        "站内没有任何链接指向 color_tags，影响面仅限筛选面板。"
    ),
    ("color", "brown"): "面板有色块、本地数据没有这类涂鸦，点进去就是 0 条，属正常。",
    ("color", "multicolor"): "同上。见 cyan 那条的说明。",
}


def audit(text):
    """回归检查：面板取值必须覆盖数据词表，chip 链接取值必须落在面板取值里。"""
    problems, warnings = [], []
    j = json.load(open(DATA, encoding="utf-8"))
    rows = j["items"]
    panel = set(panel_values(text, "topic_tags"))

    # 数据里的话题是否都能在面板里找到对应选项
    unreachable = []
    for r in rows:
        for t in (r.get("topics") or []):
            if slug(t) not in panel:
                unreachable.append(t)
    for t, n in collections.Counter(unreachable).most_common():
        problems.append("数据话题 %r（%d 条涂鸦）在筛选面板里没有对应选项 → UI 筛不到" % (t, n))

    # 其它维度：面板取值要能命中数据
    for name, field in (("color_tags", "color"), ("form_tags", "format")):
        vals = set(panel_values(text, name))
        bad = collections.Counter()
        for r in rows:
            v = r.get(field)
            if v and slug(v) not in vals:
                bad[v] += 1
        for v, n in bad.most_common():
            msg = "数据 %s=%r（%d 条）在面板 %s 里没有对应取值" % (field, v, n, name)
            if (field, v) in KNOWN_GAPS:
                warnings.append(msg + "　【已知】" + KNOWN_GAPS[(field, v)])
            else:
                problems.append(msg)
    return problems, warnings


def audit_links():
    """全站 chip 链接的取值必须落在面板取值集合内（否则点了就是空转或崩溃）。

    【取值在哪里断句】
    正则用 [^"'<>&#] 作为边界。为什么把 `&` 也当边界：面板取值按设计**不含** `&`
    （setParams 不编码，含 `&` 的取值会直接生成坏 URL），所以原文里出现 `&`
    就说明取值是坏的。而 data-links="[&#34;/search/?topic_tags=X&#34;]" 这种
    JSON 属性里，条目的结束符正是 `&#34;`，从 `&` 断开恰好也能切对。
    """
    text = open(SEARCH, encoding="utf-8").read()
    panel = set(panel_values(text, "topic_tags"))
    seen = collections.Counter()
    for root, _dirs, fs in os.walk(SITE):
        for fn in fs:
            if not fn.endswith((".html", ".htm")):
                continue
            try:
                t = open(os.path.join(root, fn), encoding="utf-8").read()
            except (OSError, UnicodeDecodeError):
                continue
            for m in re.finditer(r"/search/\?topic_tags=([^\"'<>&#]*)", t):
                seen[m.group(1)] += 1
    bad = [(v, n) for v, n in seen.items() if slug(urllib.parse.unquote(v)) not in panel]
    return bad, len(seen)


def main():
    ap = argparse.ArgumentParser(description="对齐检索页筛选词表并修复 chip 链接")
    ap.add_argument("--check", action="store_true", help="预演，只报告不落盘")
    ap.add_argument("--root", default="site",
                    help="要处理的站点根目录（默认 site；传 _deploy 可只做回归检查）")
    args = ap.parse_args()
    dry = args.check

    # --root 让同一个检查既能看 site/（构建期）又能看 _deploy/（发布前）
    global SITE, SEARCH, DATA
    SITE = os.path.join(ROOT, args.root)
    SEARCH = os.path.join(SITE, "search", "index.html")
    DATA = os.path.join(SITE, "rss", "doodles.json")

    if not os.path.isfile(SEARCH):
        print("找不到 %s" % SEARCH)
        return 2

    print("=" * 78)
    print("检索页筛选词表对齐　根目录 %s%s"
          % (args.root, "（预演，不落盘）" if dry else ""))
    print("=" * 78)

    text = open(SEARCH, encoding="utf-8").read()
    old_topic_n = len(set(panel_values(text, "topic_tags")))
    new_text, notes = patch_panel(text)
    new_topic_n = len(set(panel_values(new_text, "topic_tags")))

    for n in notes:
        print("  · " + n)
    print("  话题选项数：%d → %d" % (old_topic_n, new_topic_n))

    if not dry:
        open(SEARCH, "w", encoding="utf-8", newline="").write(new_text)

    totals, files = fix_hrefs(dry)
    print()
    if totals:
        for old, n in totals.most_common():
            new = dict(HREF_FIXES)[old]
            print("  · 链接改写 %-46s → %-40s %d 处" % (old, new, n))
        print("  涉及 %d 个 HTML 文件" % files)
    else:
        print("  · 链接改写：无需改动（已修过）")

    # 守卫脚本文件本身
    gp = os.path.join(SITE, "vendor", "search-filter-guard.js")
    if not os.path.isfile(gp):
        print("  !! 缺少 %s" % gp)
    else:
        print("  · 守卫脚本在位：%s（%d 字节）" % (os.path.relpath(gp, ROOT), os.path.getsize(gp)))

    print()
    print("─" * 78)
    print("回归检查")
    print("─" * 78)
    ref = new_text
    problems, warnings = audit(ref)
    if problems:
        for p in problems:
            print("  ✗ " + p)
    else:
        print("  ✓ 面板取值已覆盖数据词表（话题 / 颜色 / 格式三个维度）")
    for w in warnings:
        print("  ⚠ " + w)

    bad, n_vals = audit_links()
    if bad:
        for v, n in bad:
            print("  ✗ chip 链接取值 %r（%d 处）不在面板取值里 → 点了不会生效" % (v, n))
    else:
        print("  ✓ 全站 chip 链接取值（%d 种）全部落在面板取值内" % n_vals)

    if dry:
        print("\n（预演结束，未写入任何文件）")
    # 有 ✗（problems）就以非零退出：verify_deploy.py 把它当发布前的门禁。
    # ⚠（warnings）只是已知缺口，不阻断。
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
