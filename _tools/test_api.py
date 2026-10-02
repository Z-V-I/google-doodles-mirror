#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_api.py —— 不启服务器，直接验证本地检索接口的查询语义。"""
import os
import sys
from urllib.parse import parse_qs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

import serve  # noqa: E402

serve.load_data()
print(f"载入 {len(serve.DATA)} 条\n")

CASES = [
    ("默认首页 16 条",            "order_by=date&sort_direction=desc&page=1&limit=16"),
    ("关键字 pac",                "title_like=pac&limit=5"),
    ("关键字 moon",               "q=moon&limit=5"),
    ("话题 Birthday",             "topic_tags=Birthday&limit=5"),
    ("话题 Birthday + 颜色 pink", "topic_tags=Birthday&color_tags=pink&limit=5"),
    ("格式 game",                 "form_tags=game&limit=5"),
    ("颜色 blue 第 2 页",         "color_tags=blue&page=2&limit=10"),
    ("按日期升序",                "order_by=date&sort_direction=asc&limit=5"),
    ("年份 2010",                 "date_like_year=2010&limit=5"),
    ("组合：Food and Drink+still", "topic_tags=Food and Drink&form_tags=still&limit=5"),
    ("空结果",                    "title_like=zzzzzzzznotexist"),
]

for name, qs in CASES:
    payload = serve.query_doodles(parse_qs(qs, keep_blank_values=True))
    res = payload["result"]
    print(f"■ {name}")
    print(f"   命中 {payload.get('total')} 条, 本页 {len(res)} 条, next={payload['next']}")
    for r in res[:3]:
        d = f"{r['run_date_year']}-{r['run_date_month']:02d}-{r['run_date_day']:02d}" \
            if r["run_date_year"] else "无日期"
        print(f"     · {r['name'][:44]:44s} {d:12s} {r['title'][:34]}")
    print()
