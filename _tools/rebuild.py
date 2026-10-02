#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rebuild.py —— 一条命令跑完整条离线镜像构建流水线。

【为什么需要它】
各步骤之间有**强顺序依赖**，手工依次执行很容易踩坑。已经踩过的三个：

  · build_site.py 会重写 4 个主路由页 → 必须在 offline_links.py **之前**跑。
    顺序颠倒会把已完成的「谷歌外链接管」覆盖回原始外链。
  · localize_all.py 会重写 site/ 下所有 HTML → 必须跳过 games/ storybooks/
    等硬链接目录，否则会连工程根下的原始素材一起改掉。
  · remove_creating_doodle.py / strip_footer_google.py / strip_share_icons.py /
    fix_key_themes.py / search_autoload.py / slash_google_o.py / gen_rss.py 都是
    「改内容」型步骤，必须在所有会重写 HTML 的步骤**之后**跑，否则删掉的导航入口、
    页脚、分享条会被覆盖回来，补上的 key-themes 胶囊会被冲掉，搜索页尾部的自动加载
    脚本会被抹掉（精灵图那步同理）。
  · gen_rss.py 还多一层约束：它生成 RSS 模式页时要从 site/about 抽页眉骨架，
    排在 strip/footer/logo 之前的话，抽到的会是「还没改好的」骨架，
    于是 RSS 页的页眉与全站其它页面不一致。
  · gen_doodle_pages.py 现在自己就会产出 key-themes 胶囊；fix_key_themes.py 作为
    后置兜底存在（幂等），用于不重新生成页面、只修现有产物的场景。

用法：
    python _tools/rebuild.py                # 复用已下载资源，全流程重建
    python _tools/rebuild.py --with-net     # 先尝试联网补齐资源，再重建
    python _tools/rebuild.py --only verify  # 只跑某一步
"""
import os
import sys
import time
import argparse
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

PY = sys.executable

# (键, 脚本, 说明, 是否需要联网)
STEPS = [
    ("build",   "_tools/build_site.py",       "重写 4 个主路由页 + 铺设资源/桩件/共享素材", True),
    ("vendor",  "_tools/patch_vendor.py",     "本地化 vendor 真实脚本内置的外链请求", False),
    ("pages",   "_tools/gen_doodle_pages.py", "生成 6375 个涂鸦详情页", False),
    ("localize", "_tools/localize_all.py",    "对 site/ 下 HTML 做离线化重写", False),
    # charts 必须排在 localize 之后：它要往 site/index.html 里插入真 loader 标签
    # 并摘掉 charts/visualization 空桩，而 localize 会重写这些 HTML。
    # 反过来则本次改动会被 localize 覆盖掉。
    ("charts",  "_tools/patch_charts.py",     "把 Google Charts 整套搬进本地（首页世界地图）", False),
    ("runtime", "_tools/patch_runtime.py",    "从原始 bundle 重建并改造前端运行时", False),
    ("links",   "_tools/offline_links.py",    "接管指向谷歌站点的普通链接", False),
    # strip 必须排在所有「会重写 HTML」的步骤之后：build / pages / localize / charts /
    # runtime / links 都会重新写入页面，若在它们之前摘除，导航入口会被覆盖回来。
    ("strip",   "_tools/remove_creating_doodle.py", "移除 /creating-a-doodle/ 路由与导航入口", False),
    # 同 strip：必须在所有会重写 HTML 的步骤之后，否则页脚里的谷歌入口会被写回来。
    ("footer",  "_tools/strip_footer_google.py", "摘掉页脚里的谷歌入口/社交图标，换自定义版权行", False),
    # 同 strip：要改的是 localize 之后才成形的「本地路径 + i + dl」拼接
    ("fixgtm",  "_tools/fix_gtm_src.py",      "修掉 GTM 脚本地址的拼接 bug（全站 6377 处 404）", False),
    # 同 strip：正文里的 hero 分享条要在所有重写 HTML 的步骤之后摘，否则会被写回来
    ("share",   "_tools/strip_share_icons.py", "摘掉正文里的 hero 分享条（外部社交图标）", False),
    # 同 strip：补回 gen_doodle_pages 生成时漏掉的 key-themes CTA，修全站 JS 异常
    ("themes",  "_tools/fix_key_themes.py",   "补回 key-themes 的随机主题胶囊，修 KeyThemes 报错", False),
    # 同 strip：这两步也改页面内容 —— 搜索页尾部注入自动加载脚本、
    # 精灵图里给 Google 字标的两个 o 加斜杠。放在会重写 HTML 的步骤之后就稳。
    ("searchmore", "_tools/search_autoload.py", "搜索页 Show More 改成滚动到底自动加载", False),
    ("logo",    "_tools/slash_google_o.py",   "页眉 Google 字标改成 Gøøgle（改精灵图，全站生效）", False),
    # gen_rss 也改页面内容（全站页眉加 RSS 入口），且它生成 RSS 模式页时要从
    # site/about 抽页眉骨架 —— 必须排在 strip/footer/logo 之后，否则抽到的骨架
    # 会缺掉前面几步刚改好的东西，新页面就会长得和别人不一样。
    ("rss",     "_tools/gen_rss.py",          "生成 RSS 订阅源/元数据/RSS 模式页，并给页眉加 RSS 入口", False),
    # 同 strip：这一步改的是「全站详情页里的主题 chip 链接 + 检索页的筛选面板」，
    # 必须排在所有会重写 HTML 的步骤之后（rss 是最后一个），否则改好的 href
    # 会被后面某一步重写回去。
    #
    # 它修的三件事都是「界面看着正常、其实坏了」的那种：
    #   ① 含 & 的标签（Plants & Flowers）href 没编码 → 检索页整页空白；
    #   ② chip 链接取值与筛选面板取值对不上 → 点了不筛选，静默显示未筛选结果；
    #   ③ 含标点的标签（Football/Soccer）在面板里叫 football soccer → 同上。
    ("searchfix", "_tools/fix_search_filters.py", "对齐检索页筛选词表、修会崩/空转的 chip 链接", False),
    # 只跑静态层（产物自洽 + 与 doodles_data.js 逐条交叉核对）。HTTP 层的验收
    # 需要 serve.py 在跑，不适合放进构建流水线；要跑完整版：
    #     python serve.py  然后在另一个终端 python _tools/verify_rss.py
    ("rssver",  "_tools/verify_rss.py --no-http", "验收 RSS 订阅源与元数据自洽（HTTP 层另跑）", False),
    ("verify",  "_tools/verify_offline.py",   "校验是否已彻底脱离谷歌生态", False),
    ("embedded", "_tools/audit_embedded_urls.py", "审计脚本内置的外链调用（HTML 属性扫不到）", False),
    ("deadlinks", "_tools/scan_deadlinks.py", "扫描站内静态引用是否全部可达", False),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-net", action="store_true",
                    help="允许联网补齐缺失资源（默认离线，复用已有 manifest）")
    ap.add_argument("--only", default="", help="只跑指定步骤，逗号分隔")
    args = ap.parse_args()

    only = {s.strip() for s in args.only.split(",") if s.strip()}
    steps = [s for s in STEPS if not only or s[0] in only]
    if not steps:
        sys.exit(f"没有匹配的步骤。可选：{', '.join(s[0] for s in STEPS)}")

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    if not args.with_net:
        env["SKIP_DOWNLOAD"] = "1"

    print("=" * 68)
    print(f"  Google Doodles 离线镜像 · 重建（{'联网' if args.with_net else '离线'}模式）")
    print("=" * 68)

    t_all = time.time()
    for key, script, desc, needs_net in steps:
        if needs_net and args.with_net:
            env.pop("SKIP_DOWNLOAD", None)
        elif needs_net:
            env["SKIP_DOWNLOAD"] = "1"

        print(f"\n▸ [{key}] {desc}")
        print(f"  $ python {script}")
        t0 = time.time()
        # script 允许带参数（例如 verify_rss.py --no-http），用 split 拆成 argv
        r = subprocess.run([PY] + script.split(), env=env, cwd=ROOT)
        dt = time.time() - t0
        if r.returncode != 0:
            sys.exit(f"\n✗ 步骤 [{key}] 失败（退出码 {r.returncode}），已中止。")
        print(f"  ✓ 用时 {dt:.1f}s")

    print("\n" + "=" * 68)
    print(f"  全部完成，共 {time.time() - t_all:.1f}s")
    print("  启动预览：python serve.py")
    print("=" * 68)


if __name__ == "__main__":
    main()
