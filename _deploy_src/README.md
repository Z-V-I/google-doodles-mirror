# `_deploy_src/` —— 部署专用文件的**源**

这个目录里的文件会被 `_tools/build_deploy.py` 原样铺到 `_deploy/`（部署包）上，
覆盖 `site/` 镜像里的同名文件。

## 为什么单独放一层，而不是直接写进 `site/`

`site/` 的定位是「原站的可离线镜像」——它必须保持自洽，能继续被 `serve.py` 直接托管。
而这里的 `_redirects`、`_headers`、`robots.txt` 是**托管平台特有**的东西，
放进去会污染镜像的纯粹性（比如本地 `python -m http.server` 根本不认 `_redirects`）。

分开之后：`site/` 照旧，`_deploy/` = `site/` 的瘦身版 + 这一层部署配置。

## 各文件在干什么

| 文件 | 作用 | 关键说明 |
|---|---|---|
| `_redirects` | `/logos/*` → 1×1 透明 PNG | `site/logos/` 目录**根本不存在**，521 处引用全落空，所以可以无条件重写，连 Function 都不用写 |
| `_headers` | 缓存策略 + 安全头 | **不要在 `/*` 通用规则里写 `Cache-Control`**，Cloudflare 是「合并」而非「覆盖」，会污染下面所有具体规则 |
| `robots.txt` | 默认不被索引 | 定位是离线镜像/教学演示，不主动推广；想被收录就改成 `Disallow: /search/` |
| `vendor/search-api-shim.js` | 检索接口的前端复刻 | 见下 |

## `vendor/search-api-shim.js` 是最需要小心的一个

它 patch 了 `window.fetch`，把 `/live/api/v1/doodles` 的请求在**浏览器里**算出来。

**为什么不用 Pages Function**：免费档单次调用只有 10 ms CPU，而 `JSON.parse` 3.2 MB 数据
本身就要 20–40 ms，必然踩线。放到访客浏览器里就没有这个天花板，而且服务端 CPU 开销为零。

**语义只有一份**（这一点最重要）：筛选/排序/分页规则同时存在于三处 ——

1. `serve.py` 的 `query_doodles()`
2. `_tools/rss_common.py` 的 `select()`（RSS 页用）
3. 本文件（线上检索页用）

**任何一处改动都必须同步另外两处**，然后跑 `python _tools/verify_deploy.py`
做三方对账（浏览器 shim ↔ Python 独立复算 ↔ `serve.py` 接口），
确认 10 组筛选条件的条数与排序完全一致。否则线上和线下会悄悄分叉，
表现为「线下搜到 92 条、线上 88 条」这种没人会主动报的 bug。

**已经踩过的坑**：排序不能写「先升序排一遍再整体 `reverse()`」——
`reverse` 会把日期相同的那批条目的相对顺序一并翻过来，而 Python 侧 `sort(reverse=True)`
是稳定排序。两边只要有一处错位，翻到第 2 页就会整页内容差一条。
