#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
doodle_data.py —— 涂鸦数据的统一规范化层。

doodles_data.js 是抓取所得的原始数据，存在两类瑕疵：
  1. date 字段是「历史事件日期」而非「涂鸦上线日期」，
     例如 fourth-of-july-2022 记成了 July 4, 1776；
  2. 6375 条里只有 752 条带日期。

本模块统一推导出可靠的 run_date（上线日期）：
  * 年份：优先取 slug 中出现的 1998–2030 年份（涂鸦 slug 普遍带年份）
  * 月日：取原始 date / dateISO 的月日
  * 兜底：整条无日期时 run_date 为 None，展示层自行降级

serve.py（检索接口）与 gen_doodle_pages.py（详情页）共用这一份逻辑，
保证两处日期完全一致。
"""
import os
import re
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]
MONTHS_SHORT = [m[:3] for m in MONTHS]

YEAR_IN_SLUG = re.compile(r"(?<!\d)(19[89]\d|20[0-3]\d)(?!\d)")
PLAUSIBLE_YEAR = re.compile(r"^(199[89]|20[0-3]\d)$")   # Doodles 1998 年上线
ISO_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
LONG_RE = re.compile(r"^([A-Z][a-z]+)\s+(\d{1,2}),\s*(\d{4})$")


def parse_raw_date(rec):
    """从 dateISO / date 里解析出 (year, month, day)，解析不出返回 None。"""
    iso = (rec.get("dateISO") or "").strip()
    m = ISO_RE.match(iso)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return y, mo, d
    raw = (rec.get("date") or "").strip()
    m = LONG_RE.match(raw)
    if m:
        name = m.group(1)
        if name in MONTHS:
            mo = MONTHS.index(name) + 1
        elif name in MONTHS_SHORT:
            mo = MONTHS_SHORT.index(name) + 1
        else:
            return None
        return int(m.group(3)), mo, int(m.group(2))
    return None


def normalize(rec):
    """给单条记录补上 run_date（三元组）与 run_date_label（'Oct 19, 2024'）。

    年份判定优先级：
      1. 原始日期年份若落在 1998–2030（Doodles 上线至今），直接采信；
      2. 否则说明它是历史事件年份（如 7 月 4 日记成 1776），
         改取 slug 里的年份——涂鸦 slug 普遍带真实上线年份；
      3. 两条路都拿不到合理年份时，宁可不显示日期，也不显示一个错的。
    """
    slug = rec.get("slug") or ""
    parsed = parse_raw_date(rec)

    slug_years = [int(y) for y in YEAR_IN_SLUG.findall(slug)]

    year = None
    mo = d = None
    if parsed:
        raw_year, mo, d = parsed
        if PLAUSIBLE_YEAR.match(str(raw_year)):
            year = raw_year
        elif slug_years:
            year = slug_years[0]
        # else: 历史年份且 slug 无线索 -> 保持 year=None

    run = (year, mo, d) if (year and mo and d) else None

    out = dict(rec)
    out["run_date"] = run
    out["run_date_label"] = (f"{MONTHS_SHORT[run[1]-1]} {run[2]}, {run[0]}" if run else "")
    out["run_date_year"] = run[0] if run else None
    out["run_date_month"] = run[1] if run else None
    out["run_date_day"] = run[2] if run else None
    out["image_path"] = "/img/" + os.path.basename(rec.get("image_file") or "")
    return out


def load(root=None):
    """载入并规范化全部涂鸦数据。"""
    root = root or ROOT
    p = os.path.join(root, "doodles_data.js")
    t = open(p, encoding="utf-8").read()
    raw = json.loads(t[t.index("["):].rstrip().rstrip(";"))
    return [normalize(r) for r in raw]


if __name__ == "__main__":
    import collections
    data = load()
    print(f"总数 {len(data)}")
    print("有 run_date:", sum(1 for r in data if r["run_date"]))
    print("无 run_date:", sum(1 for r in data if not r["run_date"]))
    yr = collections.Counter(r["run_date_year"] for r in data if r["run_date"])
    print("年份范围:", min(yr), "-", max(yr))
    print("最近 5 年分布:", sorted((y, c) for y, c in yr.items() if y >= 2021))
    print()
    for s in ["fourth-of-july-2022", "denmark-constitution-day-and-national-elections-2019",
              "celebrating-the-staurikosaurus", "30th-anniversary-of-pac-man"]:
        r = next((x for x in data if x["slug"] == s), None)
        if r:
            print(f"  {s:52s} -> {r['run_date_label']}")
