#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
patch_runtime.py —— 对原站前端运行时做最小必要改造（从原始 bundle 重建）。

【为什么是「重建」而不是「就地打补丁」】
初期版本采用「读 site/static 下已产出的 JS → 字符串替换 → 写回」的做法，
但 DATE_PATCHES 的替换结果字符串**包含**被替换的原串（新增的只是外层守护表达式），
于是 `if old in t` 每次都成立，每跑一次就多嵌一层守护，最终把文件撑大、
也让「是否已修补」无法判断。

现在改为：永远以抓取到的**原始 bundle** 为输入，在内存里打补丁，再写出到
site/static/。补丁次数由断言保证（每个目标表达式在原始 bundle 中恰好出现 1 次），
因此本脚本天然幂等 —— 重复执行结果完全一致。

【改造内容】
1) 接口宿主判断
     原站：new URL("/live/api/v1/doodles",
                  "localhost" == location.hostname ? <谷歌云 appspot 地址> : location.origin)
     问题：用 localhost 打开时会去连谷歌云真接口，离线必然失败。
     改法：整段三元统一为 window.location.origin，接口始终落在本地服务器上。
2) 常量表里残留的 appspot 接口根地址（已无引用）改指本地 origin，清除谷歌域名痕迹。
3) 两处卡片渲染无条件 new Date(...)：记录没有确切日期时会渲染出 "Invalid Date"；
   检索页那处更糟 —— 读的是数组下标，缺字段直接抛 TypeError，导致整个结果列表
   渲染中断（表现为一直转圈）。改为「无日期则不显示日期」。
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "_tools"))

BUNDLE = "9d6cae2be5a675ee8d47d312fbe35031777c14f56b01a3e819ca85fc8e4a8548.index.min.js"
JS = os.path.join("site", "static", BUNDLE)

# 原始 bundle 的白名单副本（未做任何改造）
PRISTINE_CANDIDATES = [
    os.path.join("_clone_search", BUNDLE),
    os.path.join("_google_clone", BUNDLE),
]

HOSTCHECK = re.compile(
    r'"localhost"\s*==\s*window\.location\.hostname\s*\?\s*[\w.$.]+\s*:\s*window\.location\.origin'
)
HOSTCHECK_NEW = "window.location.origin"

# 两处卡片渲染的日期容错
DATE_PATCHES = [
    (
        'new Date(e.run_date_array[0],e.run_date_array[1]-1,e.run_date_array[2])'
        '.toLocaleDateString("en-US",{year:"numeric",month:"long",day:"numeric"})',
        '(e.run_date_array&&e.run_date_array[0]'
        '?new Date(e.run_date_array[0],e.run_date_array[1]-1,e.run_date_array[2])'
        '.toLocaleDateString("en-US",{year:"numeric",month:"long",day:"numeric"})'
        ':"")',
    ),
    (
        'new Date(e.run_date_year,e.run_date_month-1,e.run_date_day)'
        '.toLocaleDateString("en-US",{year:"numeric",month:"long",day:"numeric",timeZone:"UTC"})',
        '(e.run_date_year'
        '?new Date(e.run_date_year,e.run_date_month-1,e.run_date_day)'
        '.toLocaleDateString("en-US",{year:"numeric",month:"long",day:"numeric",timeZone:"UTC"})'
        ':"")',
    ),
]

URLBASE_PATCHES = [
    (
        'e.URL_BASE="https://live-api-dot-doodles-website-prod.uc.r.appspot.com"',
        'e.URL_BASE=window.location.origin',
    ),
]

# Glue 的 YouTube 视频组件。这两处是**真正的运行时外部加载**，静态扫描
# <script src> 抓不到：
#   1) 组件初始化时动态插入 <script src="{IFRAME_SCRIPT_URL}">；
#   2) 封面 src 为空时回填 i.ytimg.com 的在线缩略图。
# 前者会导致「涂鸦创作流程」页在离线环境里挂起等待 YouTube，必须改到本地桩件。
YOUTUBE_PATCHES = [
    (
        'IFRAME_SCRIPT_URL="https://www.youtube.com/iframe_api"',
        'IFRAME_SCRIPT_URL="/vendor/youtube-api.js"',
    ),
    (
        '`https://i.ytimg.com/vi_webp/${this.options.videoId}/maxresdefault.webp`',
        '`/assets/yt/${this.options.videoId}.jpg`',
    ),
]


def find_pristine():
    """挑一个确实未被改造过的副本。"""
    for p in PRISTINE_CANDIDATES:
        if not os.path.isfile(p):
            continue
        t = open(p, encoding="utf-8", errors="replace").read()
        if HOSTCHECK.search(t) and "e.run_date_array&&e.run_date_array[0]" not in t:
            return p, t
        print(f"  · 跳过 {p}（已被改造过，不适合作为基线）")
    return None, None


def main():
    # 1) 重写桩件（build_site 里维护的那份是唯一真源）
    import build_site as B
    B.write_stubs()
    print("已刷新本地桩件")

    src, t = find_pristine()
    if src is None:
        sys.exit("✗ 找不到未改造过的原始 bundle，无法重建。")
    print(f"基线: {src}  ({len(t)} B)")

    # 2) 接回宿主判断
    n = len(HOSTCHECK.findall(t))
    if n != 1:
        sys.exit(f"✗ 宿主判断表达式预期 1 处，实际 {n} 处，已中止。")
    t = HOSTCHECK.sub(HOSTCHECK_NEW, t)
    print(f"宿主判断: 1 处 -> {HOSTCHECK_NEW}")

    # 3) 日期容错
    for i, (old, new) in enumerate(DATE_PATCHES, 1):
        c = t.count(old)
        if c != 1:
            sys.exit(f"✗ 日期表达式 #{i} 预期 1 处，实际 {c} 处，已中止。")
        t = t.replace(old, new)
        print(f"日期容错: 表达式 #{i} 已加守护")

    # 4) 清理 appspot 常量
    for old, new in URLBASE_PATCHES:
        c = t.count(old)
        if c != 1:
            sys.exit(f"✗ 接口常量预期 1 处，实际 {c} 处，已中止。")
        t = t.replace(old, new)
        print("接口常量: 已改指本地 origin")

    # 4b) YouTube 组件的动态加载改到本地
    for i, (old, new) in enumerate(YOUTUBE_PATCHES, 1):
        c = t.count(old)
        if c != 1:
            sys.exit(f"✗ YouTube 改造 #{i} 预期 1 处，实际 {c} 处，已中止。")
        t = t.replace(old, new)
        print(f"YouTube 组件: 改造 #{i} 已指向本地")

    # 5) 写出
    os.makedirs(os.path.dirname(JS), exist_ok=True)
    with open(JS, "w", encoding="utf-8", newline="") as f:
        f.write(t)
    print(f"已写出 {JS}  ({len(t)} B)")

    # 6) 复核
    t2 = open(JS, encoding="utf-8", errors="replace").read()
    checks = [
        ("残留宿主三元判断", HOSTCHECK.findall(t2)),
        ("残留谷歌/YouTube 地址",
         re.findall(r'https?://[\w.-]*(?:appspot|google|gstatic|googleapis|youtube|ytimg)[\w.-]*', t2)),
        ("残留无保护的 new Date 渲染", re.findall(r'innerHTML=new Date\(', t2)),
        ("守护表达式层数（应为 1/1）",
         [len(re.findall(r'e\.run_date_array&&e\.run_date_array\[0\]', t2)),
          len(re.findall(r'\(e\.run_date_year\?new Date', t2))]),
    ]
    for name, left in checks:
        ok = (left == [1, 1]) if name.startswith("守护") else (not left)
        print(f"  {'✅' if ok else '⚠'} {name}: {left if left else '无'}")


if __name__ == "__main__":
    main()
