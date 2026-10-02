#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把全量图片打成一个 zip —— 供上传到网盘/对象存储做第三方分发。

【为什么需要这个脚本】
线上是 Cloudflare Pages **纯静态托管**，给不了「服务端实时打包」这一个能力。
`/rss/images.zip` 只在本地 `serve.py` 下存在（见 IMAGES.md 第 1 节 Q2）。
所以要把 1.7 GB 的全量图片交给别人，只能在本地打一个包传出去，
再把云盘/Release 的链接挂到 README 和 RSS 页上。

包内结构与 `serve.py` 的实时打包**逐项一致**（两条渠道拿到的东西一样）：

    images/        6376 张原图，文件名与订阅源里 <enclosure url> 的最后一段相同
    manifest.csv   每张图的标题/日期/年份/主色/话题/格式/地区/文件名/字节数/详情页
    manifest.json  同一份数据的结构化版本
    README.txt     包内说明（直接复用 serve.py 的 ZIP_README 模板，避免两份漂移）

用 ZIP_STORED 而非压缩：JPEG/PNG/GIF 本身已是压缩格式，再压一遍只白白耗时，
对 1.7 GB 的量级尤其明显（DEFLATE 要跑几分钟，收益往往不到 1%）。

用法：
    python _tools/pack_images.py                    # → _dist/doodles-images.zip
    python _tools/pack_images.py --out E:/x.zip      # 指定输出路径
"""

import argparse
import hashlib
import os
import shutil
import sys
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "_tools"))

import rss_common                                  # noqa: E402
import serve                                       # noqa: E402
# import serve 只为复用 ZIP_README。serve.py 的模块级代码只有常量定义与
# mimetypes.add_type()，不会起服务（服务在 main() 里），所以导入是安全的。

IMAGES = os.path.join(ROOT, "site", "img")
DEFAULT_OUT = os.path.join(ROOT, "_dist", "doodles-images.zip")


def sha256_of(path, progress=None):
    h = hashlib.sha256()
    done = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done)
    return h.hexdigest()


def pack(items, out_path):
    """写出 zip。返回 (打包张数, 额外张数, 原始字节数)。

    以 `site/img` 下的**全部文件**为准，而不是只打订阅源引用的那些：站点里还有
    一批不在订阅源中的素材（实测 17 个，多为首页轮播用的高分屏版本，文件名带
    `-2x` / `-2xa` 后缀）。这个包是给第三方分发原始素材用的，应当一个不漏 ——
    它们只是没有对应的 manifest 行，README.txt 里会说明。
    """
    on_disk = sorted(f for f in os.listdir(IMAGES)
                     if os.path.isfile(os.path.join(IMAGES, f)))
    referenced = set(it["image"] for it in items if it.get("image"))
    extra = [f for f in on_disk if f not in referenced]

    readme = serve.ZIP_README.format(
        n=len(items),
        dated=sum(1 for it in items if it["year_exact"]),
        approx=sum(1 for it in items if it["year"] and not it["year_exact"]),
        when=time.strftime("%Y-%m-%d %H:%M:%S"))
    if extra:
        readme += (
            "\n补充\n"
            " 除上述条目外，images/ 里另有 %d 个文件属于站点页面（首页轮播等）使用、\n"
            " 但未收录进订阅源的素材，多为高分屏版本（文件名带 -2x / -2xa 后缀）。\n"
            " 它们没有对应的 manifest 行。\n" % len(extra))

    packed = 0
    raw_bytes = 0
    t0 = time.time()
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_STORED,
                         allowZip64=True) as zf:
        # 清单先写：万一传输中断，解出来的包里也至少有目录可查
        zf.writestr("README.txt", readme)
        zf.writestr("manifest.csv", rss_common.manifest_csv(items))
        zf.writestr("manifest.json", rss_common.manifest_json(items))

        for i, name in enumerate(on_disk, 1):
            src = os.path.join(IMAGES, name)
            zi = zipfile.ZipInfo("images/" + name, (2026, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_STORED
            zi.external_attr = 0o644 << 16
            with zf.open(zi, "w") as dst, open(src, "rb") as fsrc:
                shutil.copyfileobj(fsrc, dst, 1024 * 256)
            packed += 1
            raw_bytes += os.path.getsize(src)
            if i % 1000 == 0:
                print("    打包中 %d/%d　%.0fs" % (i, len(on_disk), time.time() - t0))
    return packed, len(extra), raw_bytes


def main():
    ap = argparse.ArgumentParser(
        description="把全量图片打成 zip（结构与 serve.py 的实时打包一致）")
    ap.add_argument("--out", default=DEFAULT_OUT,
                    help="输出路径，默认 _dist/doodles-images.zip")
    args = ap.parse_args()

    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    if os.path.exists(out):
        os.remove(out)

    t0 = time.time()
    print("=" * 70)
    print("打包全量图片")
    print("=" * 70)
    items = rss_common.load_items(ROOT)
    print("  条目 %d 条，图片目录 %s" % (len(items), IMAGES))

    packed, extra, raw = pack(items, out)
    size = os.path.getsize(out)
    print("  打包完成：%d 张（其中 %d 张不在订阅源里），用时 %.0fs"
          % (packed, extra, time.time() - t0))

    print("  计算 SHA-256 ...")
    t1 = time.time()
    digest = sha256_of(out)
    print("    %s（%.0fs）" % (digest, time.time() - t1))

    print()
    print("=" * 70)
    print("结果")
    print("=" * 70)
    print("  文件      %s" % out.replace(os.sep, "/"))
    print("  大小      %s（%d 字节）" % (rss_common.human_size(size), size))
    print("  原始图片  %s（zip 不压缩，两者应当接近）" % rss_common.human_size(raw))
    print("  张数      %d" % packed)
    print("  SHA-256   %s" % digest)
    print()
    print("  包内结构  images/ + manifest.csv + manifest.json + README.txt")
    print()
    print("  上传到云盘后，把分享链接填进这两处（见 IMAGES.md 第 2 节）：")
    print("    · README.md 的「素材怎么拿」")
    print("    · _deploy_src/_redirects 里 /rss/images.zip 那一行（302 到云盘分享页）")

    # 反向校验：订阅源引用的图必须都在磁盘上，否则 manifest 会指向不存在的文件
    referenced = set(it["image"] for it in items if it.get("image"))
    absent = sorted(referenced - set(os.listdir(IMAGES)))
    if absent:
        print()
        print("  ！订阅源引用了 %d 个 site/img 下不存在的文件：%s"
              % (len(absent), ", ".join(absent[:5])))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
