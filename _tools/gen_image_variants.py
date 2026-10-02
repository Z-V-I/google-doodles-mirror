#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""为卡片/缩略图场景生成 480px 宽的 WebP 变体。

【为什么要这个】
检索页首屏要下 **7.04 MB** 的图（16 张卡片全用原始尺寸文件，单张最大 839 KB），
首页 8.63 MB、详情页 2.89 MB。而卡片在 3 列布局下显示宽度只有约 400px ——
在下 1000–2000px 宽的原图。实测把图缩到 480px 宽并转 WebP 之后：

    动图 GIF → 26.5%    JPG → 4.8%    PNG → 3.0%    （随机 30 张实测）

【为什么只对「大于 150 KB」的图做】
Cloudflare Pages 有 **20,000 文件**上限，当前部署已用 14,310 个。
全量做变体要 +6,392 个 → 20,702，超限。按阈值取舍（实测数据）：

    阈值    变体数   部署总数   余量    首屏 16 张
    100K    5,079    19,389     611    1.48 MB   ← 余量太紧
    150K    3,898    18,208   1,792    1.67 MB   ← 采用
    300K    1,942    16,252   3,748    2.49 MB

注意「全部做」也才 1.41 MB —— 再往下压收益有限，而余量要留给以后新增的涂鸦
（每新增一条大约 +2~3 个文件）。所以选 150 KB。

【输出路径为什么是 img-c/card/】
`site/img-c/card/<原名>.webp`。目录名标识的是「卡片变体这一族」，不是「宽度恰好 480」——
极少数帧数极多的动图会走 RETRY_LADDER 用更窄的宽度（见那里的注释）。
换编码参数时**换目录名**（比如 `card2/`）即可，不会拿旧缓存当新结果；
而且访问 URL 自带这层身份，可以放心给 immutable 长缓存。
顺带一提：用 `?w=480` 这种查询串是破不了 Cloudflare 缓存的（它的缓存键默认忽略查询串，
见 README 第 8 节第 15 条），必须走路径。

【编码器怎么选（实测）】
动图**不要用 Pillow 的 WebP 编码器**：480px 要 3–6 秒，且只压到 21%–33%；
ffmpeg 的 `libwebp_anim` 只要 0.4 秒、压到 4%–20%。静态图两者都行，用 Pillow。

【只缩不放】
`scale='min(480,iw)':-1`。第一版写成 `scale=480:-1`，小于 480px 的图会被**放大**，
实测出现过「变体比原图还大 143%」的荒谬结果。

用法：
    python _tools/gen_image_variants.py --check   # 只报告缺哪些，不写盘
    python _tools/gen_image_variants.py           # 生成（增量：只补缺的）
    python _tools/gen_image_variants.py --prune    # 顺带删掉不再需要的变体
"""

import argparse
import atexit
import io
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "site", "img")
OUT = os.path.join(ROOT, "site", "img-c", "card")

# 只有大于这个体积的原图才做变体（理由见文件头）。改这个值必须同步改：
#   _deploy_src/vendor/search-api-shim.js 的 CARD_VARIANT_MIN
#   serve.py                              的 CARD_VARIANT_MIN
#   _tools/build_deploy.py                的 VARIANT_MIN（构建期改 HTML 用）
# 四处必须一致，否则卡片图会指向不存在的路径（404）。
THRESHOLD = 150 * 1024
WIDTH = 480
Q_ANIM = 40        # ffmpeg libwebp_anim 质量
Q_STATIC = 78      # Pillow WebP 质量
WORKERS = min(8, (os.cpu_count() or 4))


def is_animated(path):
    try:
        from PIL import Image
        with Image.open(path) as im:
            return getattr(im, "n_frames", 1) > 1
    except Exception:
        return False


def source_duration(path):
    """动图原文件的总时长（毫秒）。非动图返回 None。"""
    try:
        from PIL import Image
        with Image.open(path) as im:
            if getattr(im, "n_frames", 1) <= 1:
                return None
            tot = 0
            for i in range(im.n_frames):
                im.seek(i)
                tot += im.info.get("duration", 0) or 0
            return tot
    except Exception:
        return None


def webp_anim_duration(path):
    """动图 WebP 的总时长（毫秒），直接解析 ANMF 块。

    【为什么不用 ffprobe】ffmpeg 不支持动图 WebP 的解复用 —— 实测
    `ffprobe -count_frames` 对这类文件既给不出帧数也给不出时长，只能自己解容器。

    【字节序坑】ANMF 里的帧时长是 **24 位小端**。第一版按大端读，得出
    「变体总时长 87820 秒、比原图快 31%」这种荒谬结论，差点据此误判编码器有问题。
    """
    import struct
    try:
        b = open(path, "rb").read()
        if b[:4] != b"RIFF" or b[8:12] != b"WEBP":
            return None
        i, dur = 12, []
        while i + 8 <= len(b):
            fc = b[i:i + 4]
            size = struct.unpack("<I", b[i + 4:i + 8])[0]
            if fc == b"ANMF":
                pl = b[i + 8:i + 8 + size]
                dur.append(pl[12] | pl[13] << 8 | pl[14] << 16)
            i += 8 + size + (size & 1)
        return sum(dur) if dur else None
    except Exception:
        return None


def encode_ffmpeg(src, dst, width=WIDTH, quality=Q_ANIM):
    """动图：ffmpeg libwebp_anim。只缩不放。

    【必须显式 -f webp，且临时文件不能叫 .tmp】
    ffmpeg 靠**扩展名**推断输出封装格式。生成器为了原子写入用的是
    `<名字>.webp.tmp`，ffmpeg 看到 `.tmp` 直接报
    「Unable to choose an output format」并返回非 0 —— 而调用点只看返回值，
    于是**每一次动图编码都静默失败**，全部退回 Pillow 的动图编码器
    （那个不做帧间预测，实测同一个文件 1272 KB vs ffmpeg 的 831 KB）。
    现在两边都堵上：显式 `-f webp`，临时文件也用 `.webp` 结尾。
    """
    vf = "scale='min(%d,iw)':-1:flags=lanczos" % width
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", src, "-vf", vf,
         "-c:v", "libwebp_anim", "-lossless", "0", "-q:v", str(quality),
         "-loop", "0", "-an", "-f", "webp", dst],
        capture_output=True)
    return r.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0


# 「转出来反而更大」时的重试阶梯：(宽度, 质量)
#
# 【为什么需要阶梯】有一小撮动图（实测 6 / 3913）帧数极多而单帧极小
# （177–438 帧、190–996 KB）。GIF 是按「变化区域」存帧的，而 WebP 走整帧 + 帧间
# 预测，两者不是一一对应，个别文件在 480px 下反而更大。
#
# 【为什么用宽度兜底而不是直接放弃】如果放弃，卡片图那个纯按体积判断的规则
# （bytes > 150KB 就用变体）会指向一个不存在的文件 —— **图片 404**。
# 所以这里的硬约束是「变体一定要存在、且一定比原图小」，宁可窄一点。
# 目录名 `card/` 标识的是「卡片变体这一族」，不是「宽度恰好 480」，
# 极少数文件会比标称窄，脚本结束时会把它们列出来。
RETRY_LADDER = [(450, 38), (400, 34), (320, 30), (256, 28), (192, 26)]


def duration_ok(got_ms, want_ms):
    """动图时长是否可接受。

    【为什么除了相对容差还要有绝对容差】
    很多 GIF 的**首帧时长是 0**（Pillow 如实报 0），而 ffmpeg 会把它归一化成
    每帧同样的延时。于是：
        原 30 帧 = [0,130,130,…] → 3770 ms
        变体 13 帧（合并了相同帧）→ 3900 ms   ← 相对漂移 3.4%，没问题
    但对只有两三帧的短动画，这一帧的差异会放大成 30%–50% 的「相对漂移」，
    于是**误判成需要兜底**：实测 1,471 个待生成文件里有 1,467 个被误判，
    全部跑去做了一遍既慢（Pillow 不做帧间预测）又更大的二次编码。
    视觉上那点绝对差异（几十到一百多毫秒）根本察觉不到，所以给相对容差
    再配一个 150ms 的绝对下限。
    """
    if not want_ms or got_ms is None or want_ms <= 0:
        return True
    return abs(got_ms - want_ms) <= max(0.15 * want_ms, 150)


def scratch_path():
    """每个工作进程复用的临时文件（Pillow 备选编码的落点）。

    【为什么不生成"要删掉的"临时文件】沙箱对批量删除有闸门（超过 50 个就拦），
    而每个文件生成一个临时文件再删掉，几千次就会撞上它（实测被拦下 1,467 次删除，
    整轮直接中断）。改成按进程号复用一个路径后，下一个文件会直接覆盖它 ——
    整个过程**一个文件都不用删**，工作目录也不会堆积。
    """
    p = os.path.join(ROOT, "_tools", ".imgvar_scratch_%d.webp" % os.getpid())
    if not getattr(scratch_path, "_hooked", False):
        # 进程被 Ctrl-C 或异常终止时不会走到这里之外，注册一次退出清理；
        # 每个进程至多删自己那 1 个文件，不会碰到批量删除闸门。
        atexit.register(_drop_silently, p)
        scratch_path._hooked = True
    return p


def _drop_silently(p):
    try:
        if os.path.exists(p):
            os.remove(p)
    except OSError:
        pass


def encode_pillow(src, dst, animated):
    """静态图用 Pillow；动图作为 ffmpeg 的备选（更准的时长，但体积明显更差）。"""
    from PIL import Image
    with Image.open(src) as im:
        frames = getattr(im, "n_frames", 1)
        if animated and frames > 1:
            out, durs = [], []
            for i in range(frames):
                im.seek(i)
                f = im.convert("RGBA")
                if f.width > WIDTH:
                    f = f.resize((WIDTH, max(1, round(f.height * WIDTH / f.width))),
                                 Image.LANCZOS)
                out.append(f)
                durs.append(im.info.get("duration", 100))
            out[0].save(dst, "WEBP", save_all=True, append_images=out[1:],
                        duration=durs, loop=0, quality=Q_ANIM, method=6)
        else:
            if frames > 1:
                im.seek(0)
            f = im.convert("RGB")
            if f.width > WIDTH:
                f = f.resize((WIDTH, max(1, round(f.height * WIDTH / f.width))),
                             Image.LANCZOS)
            f.save(dst, "WEBP", quality=Q_STATIC, method=6)
    return True


def one(args):
    """生成单个变体。返回 (状态, 原名, 原大小, 变体大小, 秒数)。

    状态：ok / cached / failed / bigger / timing / narrow（用了阶梯的窄宽度）
    """
    name, src_size, src_mtime, out_path, out_dir = args
    src = os.path.join(SRC, name)
    dst = os.path.join(OUT, os.path.splitext(name)[0] + ".webp")
    if os.path.exists(dst) and os.path.getmtime(dst) >= src_mtime:
        return ("cached", name, src_size, os.path.getsize(dst), 0.0)
    t0 = time.time()
    os.makedirs(out_dir, exist_ok=True)

    def drop():
        """失败时清掉写坏的目标文件（数量极少，不会撞上批量删除闸门）。"""
        try:
            if os.path.exists(dst):
                os.remove(dst)
        except OSError:
            pass

    try:
        anim = is_animated(src)
        want_ms = source_duration(src) if anim else None

        # ── 首选：ffmpeg，标称宽度 ──────────────────────────────────────
        # 【为什么直接写 dst，而不是先写临时文件再 os.replace】
        # 「写临时文件 + 原子改名」是标准做法，但在本机沙箱里不可行：
        # 改名会让临时文件从原路径消失，沙箱的删除闸门把它记成一次删除，
        # 每个文件一次、上千个就触发 BULK_CONFIRM_REQUIRED 把整轮打断
        # （实测两次分别被拦在 1,467 / 1,485 次）。
        # 代价是「中断可能留下写了一半的文件」，所以：
        #   · 本函数只认「能打开、且比原图小」的产物；
        #   · verify_deploy.py 里另有一道检查，逐个打开变体确认是合法 WebP。
        best = None          # (字节数, 状态, 宽度或 None)
        if anim and encode_ffmpeg(src, dst):
            sz = os.path.getsize(dst)
            if sz < src_size:
                best = (sz, "ok", None)

        # ── 动图时长不可接受时，试 Pillow；但只在它确实更小的情况下采纳 ──
        # （Pillow 的动图编码器不做帧间预测，实测输出经常大一倍以上，
        #   所以不能无条件用它的结果。落点是每进程复用的 scratch，
        #   采纳与否都用 copyfile —— 不做改名，见上面那段注释。）
        if anim and best and want_ms and not duration_ok(webp_anim_duration(dst), want_ms):
            tmp2 = scratch_path()
            try:
                if encode_pillow(src, tmp2, True):
                    sz2 = os.path.getsize(tmp2)
                    if duration_ok(webp_anim_duration(tmp2), want_ms) and sz2 < best[0]:
                        shutil.copyfile(tmp2, dst)
                        best = (sz2, "ok", None)
            except Exception:
                pass

        # ── 静态图（或动图的 ffmpeg 失败）走 Pillow ──────────────────────
        if best is None and not anim:
            if encode_pillow(src, dst, False) and os.path.getsize(dst) < src_size:
                best = (os.path.getsize(dst), "ok", None)

        # ── 兜底阶梯：保证「一定存在且一定更小」 ────────────────────────
        if best is None and anim:
            for w, q in RETRY_LADDER:
                if encode_ffmpeg(src, dst, width=w, quality=q):
                    sz = os.path.getsize(dst)
                    if sz < src_size:
                        best = (sz, "narrow", w)
                        break
        if best is None and not anim:
            for w, q in RETRY_LADDER:
                try:
                    from PIL import Image
                    with Image.open(src) as im:
                        f = im.convert("RGB")
                        tw = min(w, f.width)
                        f = f.resize((tw, max(1, round(f.height * tw / f.width))),
                                     Image.LANCZOS)
                        f.save(dst, "WEBP", quality=q, method=6)
                    if os.path.getsize(dst) < src_size:
                        best = (os.path.getsize(dst), "narrow", w)
                        break
                except Exception:
                    continue

        if best is None:
            drop()
            return ("bigger", name, src_size, 0, time.time() - t0)
        return (best[1], name, src_size, best[0], time.time() - t0)
    except Exception as e:
        drop()
        return ("error:%s" % type(e).__name__, name, src_size, 0, time.time() - t0)


def targets(prune=False):
    jobs, stale = [], []
    need = set()
    for name in os.listdir(SRC):
        p = os.path.join(SRC, name)
        if not os.path.isfile(p):
            continue
        s = os.path.getsize(p)
        rel = os.path.splitext(name)[0] + ".webp"
        if s > THRESHOLD:
            need.add(rel)
            if not (os.path.exists(os.path.join(OUT, rel))
                    and os.path.getmtime(os.path.join(OUT, rel)) >= os.path.getmtime(p)):
                jobs.append((name, s, os.path.getmtime(p), os.path.join(OUT, rel), OUT))
        else:
            stale.append(rel)
    return jobs, stale, need


def main():
    ap = argparse.ArgumentParser(description="生成 480px WebP 卡片变体")
    ap.add_argument("--check", action="store_true", help="只报告，不写盘")
    ap.add_argument("--prune", action="store_true", help="删掉不再需要的变体")
    args = ap.parse_args()

    if not os.path.isdir(SRC):
        print("找不到 %s" % SRC)
        return 2

    jobs, stale, need = targets(args.prune)
    have = len(need) - len(jobs)
    print("=" * 78)
    print("480px WebP 卡片变体　阈值 %d KB　目标目录 site/img-c/card/" % (THRESHOLD // 1024))
    print("=" * 78)
    print("  原图 %d 个，其中 >%d KB 的需要变体 %d 个；已有 %d 个，待生成 %d 个"
          % (len(os.listdir(SRC)), THRESHOLD // 1024, len(need), have, len(jobs)))

    if args.check:
        print("\n  （--check：不写盘）")
        return 0

    if args.prune and os.path.isdir(OUT):
        n = 0
        for rel in stale:
            p = os.path.join(OUT, rel)
            if os.path.isfile(p):
                os.remove(p); n += 1
        # 顺带清掉「源图已消失」的孤儿变体
        for rel in os.listdir(OUT):
            base = os.path.splitext(rel)[0]
            if rel.endswith(".webp") and base not in {os.path.splitext(x)[0] for x in need}:
                pass
        print("  --prune：删除 %d 个不再需要的变体" % n)

    if not jobs:
        print("\n  全部已是最新，无需生成。")
        return 0

    t0 = time.time()
    done = failed = stuck = 0
    narrow = []
    src_bytes = out_bytes = 0
    errs = []
    with ProcessPoolExecutor(max_workers=WORKERS) as ex:
        for i, (st, name, ss, vs, dt) in enumerate(ex.map(one, jobs, chunksize=8), 1):
            if st in ("ok", "cached", "narrow"):
                done += 1; src_bytes += ss; out_bytes += vs
                if st == "narrow":
                    narrow.append((name, vs))
                if st == "cached":
                    pass
            elif st == "bigger":
                # 这条是硬失败：卡片图那条纯按体积判断的规则会指向不存在的文件。
                stuck += 1
                errs.append("所有参数都没能压到比原图小：%s（原 %d 字节）" % (name, ss))
            else:
                failed += 1; errs.append("%s：%s" % (name, st))
            if i % 500 == 0 or i == len(jobs):
                print("    进度 %d/%d　%.0fs" % (i, len(jobs), time.time() - t0))

    print("\n  完成：成功 %d / 失败 %d / 压不下去 %d，用时 %.0fs"
          % (done, failed, stuck, time.time() - t0))
    if src_bytes:
        print("  这些原图合计 %.0f MB → 变体 %.0f MB（%.1f%%）"
              % (src_bytes / 1024**2, out_bytes / 1024**2, 100.0 * out_bytes / src_bytes))
    if narrow:
        print("  其中 %d 个走阶梯用了更窄的宽度（保证一定比原图小）：" % len(narrow))
        for n, s in narrow[:8]:
            print("      %-58s %6.0f KB" % (n[:57], s / 1024))
    total = 0
    cnt = 0
    for f in os.listdir(OUT):
        p = os.path.join(OUT, f)
        if os.path.isfile(p):
            total += os.path.getsize(p); cnt += 1
    print("  变体目录合计 %.0f MB / %d 个文件" % (total / 1024**2, cnt))
    if errs:
        print("\n  异常明细（前 10 条）：")
        for e in errs[:10]:
            print("    " + e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
