# 图片与素材：怎么分发、怎么获取

> 开源仓库里**没有图片**（1.73 GB，GitHub 放不下）。这份文档说明素材都有哪些、
> 能从哪拿、以及为什么线上没有「一键下载全部图片」的按钮。

---

## 0. 素材清单

| 素材 | 数量 | 体积 | 在仓库里？ |
|---|---|---|---|
| 涂鸦原图（GIF/PNG/JPG） | 6,392 张 | **1.73 GB** | ✗ 硬链接自项目根目录，已 `.gitignore` |
| 卡片图变体（480px WebP） | 3,913 个 | 189 MB | ✗ 可再生的构建缓存 |
| 字体 / 原站 CSS+JS / 互动涂鸦 / 故事书 / CMS 图 | — | 约 400 MB | ✗ 全是原站原物 |
| 页面 HTML + 订阅源 + 元数据 | 6,384 个 | **139.5 MB** | ✓ **在仓库里** |
| 构建 / 修补 / 验收脚本 | 52 个 | 0.6 MB | ✓ 在仓库里 |

仓库总量：**6,453 个文件 / 143.1 MB**，最大的单文件 5.75 MB（`site/rss.xml`）。

---

## 0.1 已经打好的包

`python _tools/pack_images.py` 会生成 `_dist/doodles-images.zip`：

| | |
|---|---|
| 路径 | `_dist/doodles-images.zip`（不进仓库，`.gitignore` 里的 `/_dist/`） |
| 大小 | **1,858,697,738 字节（1.73 GB）** |
| 张数 | **6,392 张** —— `site/img/` 全量，含 17 张不在订阅源里的高分屏素材 |
| 结构 | `images/` + `manifest.csv` + `manifest.json` + `README.txt` |
| 压缩 | `ZIP_STORED` —— 图片本身已是压缩格式，再压一遍只白白耗时 |
| SHA-256 | `64cd53407dcd75b49b58b47e5179dde22515d4d9d69a15082a305f4e0927d99a` |

上传完**请核对一次 SHA-256**：1.7 GB 的文件在网络往返里出错的概率不低，
校验一次能省掉后面所有「为什么解压失败」的排查。

包内说明（`README.txt`）与本地 `serve.py` 实时打包**用的是同一个模板**，
所以两条渠道拿到的东西一致。

---

## 1. 三个常被问的问题

### Q1：RSS 能直接下载全量图片吗？

**能，但是「逐张」，不是「一键」—— 而且**确实**已经把每张图的直链都给你了。**

`/rss.xml`（全量 6,375 条）和 `/rss/latest.xml`（最新 100 条）里，**每条都带一个
`<enclosure>`**，这就是图片本体的直接地址：

```xml
<item>
  <title>Men's IPL 2026 - Final</title>
  <link>https://google.zvi.onl/doodle/mens-ipl-2026-final/</link>
  <enclosure url="https://google.zvi.onl/img/mens-ipl-2026-final.gif"
             type="image/gif" length="859038"/>
</item>
```

`type` 是 MIME、`length` 是字节数（可以直接用来校验下全了没有）。所以：

- **RSS 阅读器**：多数会把 enclosure 当附件展示，可逐条下载。
- **批量抓取**：任何 RSS 库（`feedparser` 等）读一遍就能拿到 6,375 个直链，然后
  并发下载。示例：

```python
import feedparser, urllib.request, os
os.makedirs("images", exist_ok=True)
for e in feedparser.parse("https://google.zvi.onl/rss.xml").entries:
    for enc in e.get("enclosures", []):
        name = enc["href"].rsplit("/", 1)[-1]
        urllib.request.urlretrieve(enc["href"], os.path.join("images", name))
```

**注意**：这是 6,375 次 HTTP 请求。请控制并发（单线程 + 重试就够，别打满），
毕竟那是别人自费托管的静态站。

### Q2：为什么图片包 zip 下载不了？

**因为它只存在于本地 `serve.py` 里，线上根本没有这个文件。**

`/rss/images.zip` 是 **`serve.py` 的服务端实时流式打包**——全量 1.7 GB，不可能预先
落盘，所以本地跑 `python serve.py` 时它是好的。但线上是 **Cloudflare Pages 纯静态托管**，
没有服务端，这个能力不存在。而且 Pages 还有 **25 MiB 的单文件上限**，就算预先生成
一个 zip 也传不上去。

`_deploy_src/_redirects` 里那行 `302` 一直是**注释状态**，等对象存储配好才启用：

```
#     /rss/images.zip  https://dl.google.zvi.onl/images.zip  302
```

实测线上 `/rss/images.zip` → **404**（RSS 页上的下载卡会自动检测并提示「线上暂不提供」）。

### Q3：GitHub 能放那么大的文件吗？

**不能。** GitHub 的现行限制（2026 年）：

| 限制项 | 数值 | 触发后 |
|---|---|---|
| 单文件硬上限 | **100 MiB** | 直接拒绝 push（`file is too large`） |
| 单文件警告线 | 50 MiB | 能推上去，但会警告 |
| 网页界面上传 | 25 MiB | 更严，别用网页传二进制 |
| 仓库推荐体积 | < 1 GB（强烈建议 < 5 GB） | 超了会变慢、可能被要求瘦身 |
| 单次 push | 2 GB | 超了直接失败 |
| Git LFS | 单文件 2 GB；免费配额 10 GiB 存储 + 10 GiB/月流量 | 超配额后 push 失败 |

所以 1.73 GB 的图片**进不了仓库**，也不该进——Git 每次提交都存全量快照，
大二进制会把仓库越滚越大。这就是 `.gitignore` 把它们排除的原因。

---

## 2. 分发方案（可组合）

### 方案 A：只留 RSS 直链 —— 零成本，现在的状态

什么都不用做。任何人拿到 `/rss.xml` 就有全部 6,375 个图片直链。

- **优点**：零维护，无额外账号，数据一直在
- **缺点**：没有「下载全部」的按钮；6,375 次请求对主机有压力
- **适合**：愿意写两行脚本的人

### 方案 B：GitHub Release 挂一个 zip —— 最省事

1. 本地生成 zip（图片本身已压缩，用 `ZIP_STORED` 不重复压，快得多）：

```bash
python - <<'PY'
import zipfile, os
with zipfile.ZipFile('_dist/doodles-images.zip','w',zipfile.ZIP_STORED) as z:
    for f in sorted(os.listdir('site/img')):
        z.write(os.path.join('site/img',f), 'img/'+f)
PY
```

2. 在仓库的 Releases 页面新建一个 Release，把 zip 作为附件上传。

- **优点**：和代码同处一地，读者容易找到；免费、长期
- **限制**：单附件上限 2 GB（Free 计划），1.73 GB 卡在范围内但**没有余量**
- **缺点**：国内下载 GitHub Release 通常较慢

### 方案 C：Cloudflare R2 + 302 —— 原计划，出口流量免费

R2 免费额度：**10 GB 存储 + 出口流量全免费**（这是它最大的优势）。

```bash
# 1) 建桶并上传（构建期一份即可，不用线上实时打包）
wrangler r2 bucket create doodles-images
wrangler r2 object put doodles-images/images.zip --file _dist/doodles-images.zip

# 2) 在 R2 里绑一个自定义域（不能用 r2.dev 做生产）
#    Dashboard → R2 → doodles-images → Settings → Public access → Connect Domain
#    例如绑 dl.google.zvi.onl

# 3) 启 _deploy_src/_redirects 里那行，重新构建 + 部署
#    /rss/images.zip  https://dl.google.zvi.onl/images.zip  302
```

- **优点**：出口免费、速度快、能扛量
- **缺点**：多一个服务；R2 要用自定义域才适合生产

### 方案 D：网盘（百度网盘 / 阿里云盘 / 夸克 …）

把 zip 传上去，在 README 里放分享链接。

- **优点**：国内下载体验最好
- **缺点**：链接会失效、需要人工维护、无法用脚本自动化
- **建议**：作为方案 B/C 的补充，而不是唯一渠道

### 附：如果只是给人「看图」，不用给 1.7 GB

`site/img-c/card/` 里的 480px 变体只有 **189 MB**，体积是原图的 1/9，
而卡片/缩略图场景观感几乎一样。如果目标读者只是浏览而不是要原始素材，
打一个变体包会好传得多（注意它现在只覆盖 >150 KB 的 3,913 张，
要全量可以调 `gen_image_variants.py` 的阈值重跑）。

---

## 3. 建议

| 你的目标 | 建议组合 |
|---|---|
| 只是让仓库可开源、别人能看懂 | **方案 A**（现状即可） |
| 让人方便拿到全量素材 | **方案 B**（Release）+ **方案 A**（RSS 兜底） |
| 想要稳定、快、且免费 | **方案 C**（R2）+ README 里写清直链 |
| 主要服务国内访客 | **方案 D**（网盘）+ 方案 A |

三个渠道不冲突，可以同时挂。无论选哪个，**RSS 的 enclosure 都是最底层的兜底**——
它不依赖任何额外服务，站只要活着就一直有效。
