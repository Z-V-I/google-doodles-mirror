#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
verify_rss.py —— RSS 模式（订阅源 / 元数据导出 / 图片包 / 页眉入口）的独立验收。

分三层，逐层加证据强度：

  ① 静态产物自洽
       rss.xml 能被 XML 解析器读出来；条数 = doodles.json 条数 = CSV 数据行数；
       每条都有 title/link/guid/enclosure/description；enclosure 的 URL 在磁盘上
       真有那张图，且 type 与扩展名一致、length 与真实字节数一致。
  ② 与源数据交叉核对
       不信任生成器，直接用 rss_common 从 doodles_data.js 重算一遍，
       逐条比对 slug/title/图片/分类，确认「喂进去的」和「吐出来的」是同一份。
  ③ HTTP 实测（需要 serve.py 在跑）
       /rss/ 页面、/rss.xml（含按 Host 改写基址）、元数据下载都通；
       /rss/images.zip 真的下载下来、用 zipfile 回读，图片字节与磁盘原件逐字节一致；
       再验一次「下载中途取消」不会把服务打挂。

用法：
    python _tools/verify_rss.py                 # 默认 127.0.0.1:8765
    python _tools/verify_rss.py --port 8770
    python _tools/verify_rss.py --no-http       # 只跑 ①②
    python _tools/verify_rss.py --page          # 额外跑 ⑤（真浏览器验页面筛选）
    python _tools/verify_rss.py --full          # 额外实际下载全量包（1.7 GB，很慢）
"""
import os
import io
import re
import sys
import csv
import json
import zlib
import shutil
import argparse
import subprocess
import xml.etree.ElementTree as ET
import urllib.request
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "_tools"))

import rss_common as R  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DOC = "site"
FEED = os.path.join(DOC, "rss.xml")
LATEST = os.path.join(DOC, "rss", "latest.xml")
PAGE = os.path.join(DOC, "rss", "index.html")
DJSON = os.path.join(DOC, "rss", "doodles.json")
DCSV = os.path.join(DOC, "rss", "doodles.csv")

NS = {"a": "http://www.w3.org/2005/Atom"}
MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml"}

RESULTS = []


def check(ok, label, detail=""):
    RESULTS.append((bool(ok), label, detail))
    print("  [%s] %s%s" % ("✓" if ok else "✗", label,
                           ("  —— " + detail) if detail else ""))
    return ok


# ---------------------------------------------------------------- ① 静态产物
def step_static():
    print("\n① 静态产物自洽")
    for p in (FEED, LATEST, PAGE, DJSON, DCSV):
        check(os.path.isfile(p), "存在 %s" % p,
              R.human_size(os.path.getsize(p)) if os.path.isfile(p) else "缺失")
    if not os.path.isfile(FEED):
        return None

    tree = ET.parse(FEED)
    chan = tree.getroot().find("channel")
    items = chan.findall("item")
    src = R.load_items()

    check(len(items) == len(src), "订阅源条数 = 源数据条数",
          "%d vs %d" % (len(items), len(src)))
    check(chan.findtext("title") is not None and chan.find("a:link", NS) is not None,
          "channel 有 title 与 atom:self")

    miss = {"title": 0, "link": 0, "guid": 0, "enclosure": 0, "description": 0}
    bad_type = bad_len = bad_file = no_cat = 0
    for it in items:
        for k in miss:
            if it.find(k) is None:
                miss[k] += 1
        enc = it.find("enclosure")
        if enc is not None:
            url = enc.get("url") or ""
            name = os.path.basename(url)
            path = os.path.join(DOC, "img", name)
            if not os.path.isfile(path):
                bad_file += 1
            else:
                if int(enc.get("length")) != os.path.getsize(path):
                    bad_len += 1
                if enc.get("type") != MIME.get(os.path.splitext(name)[1].lower()):
                    bad_type += 1
        if not it.findall("category"):
            no_cat += 1

    for k, v in miss.items():
        check(v == 0, "每条都有 <%s>" % k, "缺失 %d 条" % v if v else "")
    check(bad_file == 0, "enclosure 指向的图片在磁盘上都存在", "缺 %d 张" % bad_file)
    check(bad_len == 0, "enclosure length 等于真实字节数", "不符 %d 张" % bad_len)
    check(bad_type == 0, "enclosure type 与扩展名一致", "不符 %d 张" % bad_type)
    check(no_cat == 0, "每条至少有一个分类", "无分类 %d 条" % no_cat)

    dates = sum(1 for it in items if it.find("pubDate") is not None)
    check(dates == sum(1 for it in src if it["date"]),
          "带 pubDate 的条数 = 有确切日期的条数（不用推定年份伪造日期）",
          "%d 条" % dates)

    # latest.xml 应当就是全量源的前 100 条
    lt = ET.parse(LATEST).getroot().find("channel").findall("item")
    check(len(lt) == 100, "latest.xml 为 100 条", "%d 条" % len(lt))
    same = all(a.findtext("guid") == b.findtext("guid")
               for a, b in zip(lt, items[:100]))
    check(same, "latest.xml 与全量源的前 100 条一致")

    # 元数据三份对齐
    dj = json.load(open(DJSON, encoding="utf-8"))
    with open(DCSV, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    check(dj["count"] == len(items), "doodles.json 条数与订阅源一致",
          "%d vs %d" % (dj["count"], len(items)))
    check(len(rows) - 1 == len(items), "doodles.csv 数据行数与订阅源一致",
          "%d vs %d" % (len(rows) - 1, len(items)))
    need = {"标题", "上线日期", "年份", "主色", "话题", "格式", "地区",
            "图片文件", "详情页", "介绍"}
    check(need <= set(rows[0]), "CSV 含全部必需列",
          "缺：" + str(sorted(need - set(rows[0]))) if not need <= set(rows[0]) else "")

    # ② 与源数据逐条交叉核对
    print("\n② 与 doodles_data.js 逐条交叉核对")
    src_by_slug = {s["slug"]: s for s in src}
    diff = []
    for it in items:
        slug = (it.findtext("guid") or "").rsplit("/", 1)[-1]
        s = src_by_slug.get(slug)
        if not s:
            diff.append("%s 不在源数据里" % slug)
            continue
        if it.findtext("title") != s["title"]:
            # 注意：XML 解析出来的文本已经解码过实体，不能再和 _esc() 的结果比。
            diff.append("%s 标题不符" % slug)
        if os.path.basename(it.find("enclosure").get("url")) != s["image"]:
            diff.append("%s 图片不符" % slug)
        cats = {c.text for c in it.findall("category") if c.get("domain") is None}
        if cats != set(s["topics"]):
            diff.append("%s 话题不符" % slug)
        if len(diff) > 5:
            break
    check(not diff, "订阅源与源数据逐条一致（标题/图片/话题）",
          "; ".join(diff[:3]) if diff else "")

    # 页面 vs 源数据：筛选条数必须同源同解
    for kw in ({}, {"topic": "Football/Soccer"}, {"color": "red", "form": "animated"},
               {"year": "2019"}, {"q": "halloween"}):
        got = len(R.select(src, **kw))
        check(got > 0, "页面/打包共用的筛选语义可用 %s" % (kw or "(全量)"),
              "%d 条" % got)

    return src


# ---------------------------------------------------------------- ③ HTTP
def get(url, timeout=120):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, dict(r.headers), r.read()


def get_prefix(url, nbytes, timeout=60):
    """只读前 nbytes 就断开 —— 模拟用户中途取消下载。"""
    req = urllib.request.Request(url)
    r = urllib.request.urlopen(req, timeout=timeout)
    try:
        return r.status, dict(r.headers), r.read(nbytes)
    finally:
        r.close()


def step_http(port, src, full=False):
    print("\n③ HTTP 实测（127.0.0.1:%d）" % port)
    base = "http://127.0.0.1:%d" % port

    try:
        st, hd, body = get(base + "/rss/")
    except Exception as e:
        check(False, "GET /rss/", "%s（serve.py 没在跑？）" % e)
        return
    html = body.decode("utf-8", "replace")
    check(st == 200, "GET /rss/ 返回 200")
    check("zvi-rss" in html and "images.zip" in html,
          "页面含订阅视图与下载区")
    check(html.count("<header") == 1 and html.count("<footer") == 1,
          "复用了站点页眉/页脚（各 1 处）")
    check('href="/rss/"' in html, "RSS 模式页的页眉里也带 RSS 入口")

    # 订阅源 + Host 改写
    st, hd, body = get(base + "/rss.xml")
    check(st == 200 and "rss" in hd.get("Content-Type", ""),
          "GET /rss.xml 返回 200 且为 rss 类型", hd.get("Content-Type", ""))
    text = body.decode("utf-8")
    check("%s/img/" % base in text, "订阅源里的绝对地址跟随请求 Host", base)
    check(len(ET.fromstring(text).find("channel").findall("item")) == len(src),
          "HTTP 拿到的订阅源条数与源数据一致")

    st, _, body = get(base + "/rss/latest.xml")
    check(st == 200, "GET /rss/latest.xml 返回 200")
    st, _, body = get(base + "/rss/doodles.csv")
    check(st == 200 and body[:3] == b"\xef\xbb\xbf",
          "GET /rss/doodles.csv 返回 200 且带 UTF-8 BOM")
    st, _, body = get(base + "/rss/doodles.json")
    check(st == 200 and json.loads(body)["count"] == len(src),
          "GET /rss/doodles.json 条数正确")

    # 图片包（小筛选，逐字节回读）
    import zipfile
    q = "topic=Space"
    expect = R.select(src, topic="Space")
    st, hd, body = get(base + "/rss/images.zip?" + q)
    check(st == 200 and hd.get("Content-Type") == "application/zip",
          "GET /rss/images.zip?%s 返回 zip" % q, hd.get("Content-Disposition", ""))
    z = zipfile.ZipFile(io.BytesIO(body))
    names = z.namelist()
    imgs = [n for n in names if n.startswith("images/")]
    check(z.testzip() is None, "下载回来的 zip CRC 全部正确")
    check(set(names) >= {"README.txt", "manifest.csv", "manifest.json"},
          "包内含 README / manifest.csv / manifest.json")

    rows = list(csv.reader(io.StringIO(z.read("manifest.csv").decode("utf-8-sig"))))
    check(len(rows) - 1 == len(imgs),
          "manifest.csv 行数 = 包内图片数", "%d vs %d" % (len(rows) - 1, len(imgs)))
    check(len(imgs) == len(expect), "包内图片数 = 该筛选在源数据里的条数",
          "%d vs %d" % (len(imgs), len(expect)))
    check(len(names) == len(imgs) + 3, "包内没有多余条目",
          "%d 条目" % len(names))

    # 逐字节比对包内图片与磁盘原件（抽 3 张，避免读整个包）
    same = 0
    for n in imgs[:3]:
        disk = os.path.join(DOC, "img", os.path.basename(n))
        if z.read(n) == open(disk, "rb").read():
            same += 1
    check(same == min(3, len(imgs)), "包内图片与磁盘原件逐字节一致", "%d/%d" % (same, min(3, len(imgs))))

    # 清单里确实带着「介绍 + 分类」
    head = rows[0]
    i_desc, i_topic, i_color = head.index("介绍"), head.index("话题"), head.index("主色")
    ok = all(rows[i][i_desc] and (rows[i][i_topic] or rows[i][i_color])
             for i in range(1, len(rows)))
    check(ok, "清单每行都带介绍与分类")

    # 中途取消下载不能把服务打挂
    try:
        st, hd, chunk = get_prefix(base + "/rss/images.zip", 200000)
        check(st == 200 and hd.get("Transfer-Encoding", "").lower() == "chunked",
              "全量包走 chunked（无需先落盘算长度）", hd.get("Transfer-Encoding", ""))
    except Exception as e:
        check(False, "全量包请求头", str(e))
    try:
        st, _, _ = get(base + "/rss/")
        check(st == 200, "中断全量包下载后服务仍然正常")
    except Exception as e:
        check(False, "中断后服务是否存活", str(e))

    if full:
        print("  … 实际下载全量包（1.7 GB）")
        st, _, body = get(base + "/rss/images.zip", timeout=900)
        z = zipfile.ZipFile(io.BytesIO(body))
        check(len([n for n in z.namelist() if n.startswith("images/")]) == len(src),
              "全量包图片数 = 源数据条数")
        check(z.testzip() is None, "全量包 CRC 全部正确")

    # 页眉入口（三个代表性页面）
    print("\n④ 页眉入口")
    for path, label in (("/", "首页"), ("/search/", "检索页"),
                        ("/about/", "关于页"),
                        ("/doodle/100th-anniversary-of-bauhaus/", "详情页")):
        try:
            st, _, body = get(base + path)
        except Exception as e:
            check(False, "%s 可访问" % label, str(e))
            continue
        h = body.decode("utf-8", "replace")
        # 页面里有两个 glue-header__list（导航 + 搜索图标那栏），
        # 要挑出含 /about/ 的那个才是导航。
        seg = ""
        for m in re.finditer(r'<ul class="glue-header__list">.*?</ul>', h, re.S):
            if 'href="/about/"' in m.group(0):
                seg = m.group(0)
                break
        check('href="/rss/"' in seg, "%s 页眉里有 RSS 入口" % label)
        check('data-g-cta_text="RSS"' in seg, "%s 入口埋点完整" % label)
        pos = re.findall(r'data-g-template_position="(\d)/3"', seg)
        check(pos == ["1", "2", "3"], "%s 导航序号收敛为 1/3 2/3 3/3" % label,
              str(pos))


def step_page(src, port):
    """⑤ 页面侧筛选 vs 源数据 —— 页面 JS 与 serve.py 的 Python 是两套独立实现。"""
    print("\n⑤ 页面筛选 vs 源数据（真浏览器）")
    node = shutil.which("node") or r"C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2-3\node.exe"
    if not os.path.isfile(node) and not shutil.which("node"):
        check(False, "找到 node", "跳过页面侧检查")
        return
    script = os.path.join(ROOT, "_tools", "verify_rss_page.js")
    try:
        r = subprocess.run([node, script, "--port", str(port)],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=900, cwd=ROOT)
    except Exception as e:
        check(False, "运行页面侧脚本", str(e))
        return
    out = (r.stdout or "") + (r.stderr or "")
    rows = []
    for line in out.splitlines():
        if line.startswith("RESULT "):
            try:
                rows.append(json.loads(line[len("RESULT "):]))
            except Exception:
                pass
    if not rows:
        check(False, "页面侧脚本产出结果", out.strip().splitlines()[-1][:200] if out.strip() else "无输出")
        return

    keymap = {"ftopic": "topic", "fcolor": "color", "fform": "form",
              "fyear": "year", "fq": "q"}
    bad, badzip = [], []
    for row in rows:
        kw = {keymap[k]: v for k, v in row["filter"].items()}
        want = len(R.select(src, **kw))
        if row["count"] != want:
            bad.append("%s: 页面 %d vs 源数据 %d" % (kw, row["count"], want))
        # 打包链接必须带着同一组筛选参数，否则「页面看的是 A、下的是 B」
        qs = row["zip"].split("?", 1)[1] if "?" in row["zip"] else ""
        from urllib.parse import parse_qs
        got = {k: v[0] for k, v in parse_qs(qs).items()}
        for k, v in kw.items():
            if v and got.get(k) != v:
                badzip.append("%s 的 zip 链接缺 %s" % (kw, k))
    check(not bad, "页面算出的条数与源数据逐条一致（%d 组筛选）" % len(rows),
          "; ".join(bad[:3]))
    check(not badzip, "打包链接带着当前筛选参数", "; ".join(badzip[:3]))
    check(r.returncode == 0, "页面侧无 JS 异常 / 无 4xx",
          "退出码 %d" % r.returncode)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-http", action="store_true")
    ap.add_argument("--page", action="store_true",
                    help="额外用真浏览器验页面筛选（需要 node 与 Chrome/Edge）")
    ap.add_argument("--full", action="store_true", help="实际下载全量包（1.7 GB）")
    args = ap.parse_args()

    src = step_static()
    if src is None:
        print("\n静态产物缺失，跳过 HTTP 检查。")
    elif not args.no_http:
        step_http(args.port, src, args.full)
        if args.page:
            step_page(src, args.port)

    bad = [r for r in RESULTS if not r[0]]
    print("\n" + "=" * 60)
    print("共 %d 项检查，通过 %d，失败 %d" % (len(RESULTS), len(RESULTS) - len(bad), len(bad)))
    for _, label, detail in bad:
        print("  ✗ %s  %s" % (label, detail))
    print("=" * 60)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
