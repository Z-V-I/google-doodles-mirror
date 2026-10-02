# 部署到 Cloudflare Pages（google.zvi.onl）

> 目标：把 `Google涂鸦全集` 这个离线镜像发布到 `https://google.zvi.onl`。
> 结论先放这儿：**零 Function、100% 纯静态**，把 `_deploy/` 目录整个传上去就行。

## 当前状态（2026-10-02 图片优化已上线）

| 项 | 值 |
|---|---|
| 线上地址 | <https://google.zvi.onl> —— 自定义域 `active` |
| Pages 项目 | `doodles-mirror`（生产分支 `main`） |
| 账号 ID | `0144b3c76b4a7336a9cd617564399c9e` |
| Zone | `zvi.onl` = `e6883fc31cdcf6345cf9efa15b2cd8f4` |
| DNS | `CNAME google.zvi.onl → doodles-mirror.pages.dev`（proxied） |
| 部署包 | **18,223 个文件 / 2.17 GiB**（含 3,913 个卡片图变体）；上限 20,000，余量 1,777 |
| 最近部署 | 2026-10-02 · 上传 10,292 个文件（7,929 个已存在）/ 93 秒 · 部署 ID `b514790a` |
| 首次部署 | 14,307 个文件 / 461 秒 |
| 验收 | 本地 `build_deploy.py` 闸门全绿 + `verify_deploy.py` 20/20；线上实测见下 |

> 本次变更（图片加速，见 `IMAGE-PERF.md`）：新增 `/img-c/card/*` 共 3,913 个
> 480px WebP 变体（188.6 MB），并把首页与 6,375 个详情页的卡片 `<img>` 改指向
> 变体、加懒加载。
>
> **线上实测**：首页 14/16 张卡片指向变体（另 2 张原图 ≤150 KB），
> 前 8 张 **4.33 MB → 0.61 MB（14%）**；变体返回
> `Cache-Control: public, max-age=31536000, immutable`，内容与本地逐字节一致。
>
> 部署凭证**只经环境变量传递，从未写入任何文件**。当前账号用的是 API Key 风格
> （`X-Auth-Email` + `X-Auth-Key`，见 2.2 节），建议轮换后改用 Scoped Token 或
> `wrangler login`。
>
> **部署工具的一个坑**：`npx wrangler` 装的包缺 `@esbuild/win32-x64`（optional
> 依赖没落地），启动即报 `The package "@esbuild/win32-x64" could not be found`。
> 绕法是装到独立目录后用本地入口：
>
> ```bash
> W=~/.workbuddy/binaries/node/workspace/wrangler
> cd "$W" && npm install wrangler --no-audit --no-fund
> node "$W/node_modules/wrangler/bin/wrangler.js" pages deploy _deploy \
>   --project-name doodles-mirror --commit-dirty=true
> ```
>
> 注意：该目录的 `npm install` 末尾会因某个子进程被拦而报错退出，但 wrangler
> 本体已装好、可直接用 —— 别被那行 `npm error` 误导。

---

## 0. 为什么是这个架构

先把三个硬约束摆出来，后面所有设计都是绕着它们转的。

| 约束 | 实测数字 | 影响 |
|---|---|---|
| 文件数 | 14,310 个 | Cloudflare Pages 上限 20,000，余量 5,690 |
| 单文件 | 最大 5.73 MB（`rss.xml`） | 上限 25 MiB，**已满足** |
| 总体积 | 1.99 GiB | Pages 无硬上限，但首次上传耗时取决于上行带宽 |
| 带宽 | 图片 1.7 GB 是流量大头 | Pages **不限带宽**，这是它相对 Vercel/Netlify 的决定性优势 |

### 检索页怎么办 —— 这是唯一真正的难题

原站 `/search/` 靠一个服务端接口 `/live/api/v1/doodles` 取数据。纯静态托管下这个接口必然 404，
前端拿到 HTML 去 `JSON.parse` 会抛异常，结果是**筛选控件都在、结果列表空白** —— 不报错，就是安静地坏掉。

三种解法：

| 方案 | 做法 | 问题 |
|---|---|---|
| A. 边缘函数 | 写 Pages Function，在边缘 `JSON.parse` 3.2 MB 数据 | ❌ 免费档单次调用只有 **10 ms CPU**，解析这个量级本身要 20–40 ms，必然踩线 |
| B. 砍掉检索页 | 换成说明页 | ❌ 直接丢功能 |
| **C. 前端复刻（采用）** | 把接口语义搬到浏览器里，数据用构建期已落盘的 `rss/doodles.json` | ✅ 服务端 CPU 开销**为零** |

方案 C 的实现是 `_deploy/vendor/search-api-shim.js`：patch `window.fetch`，
凡是指向 `/live/api/v1/doodles` 的请求就地算出同样的响应。
**原站 bundle 一行都不用改**，而且检索页在**任何**静态托管上都能跑（GitHub Pages、对象存储、本地 `file://`）。

它有个前提是能成立的：原站前端用的正是 `fetch`（不是 XHR），而且请求前的 reCAPTCHA 已经被
`vendor/recaptcha-stub.js` 打桩成 `'offline-token'` 了。这两点都已经确认过。

### 其余三个动态路由的处置

| 路由 | 原站行为 | 纯静态下的处置 |
|---|---|---|
| `/logos/**` | 521 处引用，磁盘上**一个文件都没有** | `_redirects` 无条件重写到 1×1 透明 PNG |
| 越界 slug | — | 实测**不存在**（6375 条 slug 与 6375 个目录完全对齐），无需处理 |
| `/rss/images.zip` | 服务端实时打包 1.7 GB | 见第 6 节（可选 R2） |

`/logos/*` 之所以能无条件重写，是因为 `site/logos/` 目录**根本不存在** —— 不需要「有则返回、无则兜底」的判断，
所以连 Function 都省了。

---

## 1. 部署包长什么样

`_deploy/` 由 `_tools/build_deploy.py` 生成，**不在 Git 里**（1.99 GiB）：

```bash
python _tools/build_deploy.py
```

| 组成部分 | 来源 | 说明 |
|---|---|---|
| `site/` 的镜像 | 硬链接（秒级） | 排除 123 个无引用资源，省 273 MB |
| `img-c/card/*` | `site/img-c/` 随镜像进包 | 3,913 个 480px WebP 卡片变体（188.6 MB），由 `_tools/gen_image_variants.py` 生成 |
| `_redirects` | `_deploy_src/` | `/logos/*` → 1×1 |
| `_headers` | `_deploy_src/` | 缓存策略 + 安全头 |
| `robots.txt` | `_deploy_src/` | 默认不被索引 |
| `vendor/search-api-shim.js` | `_deploy_src/` | 检索接口前端复刻（并把卡片图指向变体） |
| `disclaimer/index.html` | 构建脚本生成 | 免责声明 |
| `404.html` | 构建脚本生成 | **必需**：没有它 Pages 会把根 `index.html` 当兜底、用 200 返回（软 404） |
| `assets/__blank.png` | 构建脚本生成 | 68 字节的 1×1 PNG |

另外这几个在 `site/` 里（**两套都在用**，不是部署专用，所以随镜像一起进包）：

| 文件 | 作用 |
|---|---|
| `vendor/search-filter-guard.js` | 兜住原 bundle 两处裸读 `.tagName` 的空指针 —— 少了它，带 `?page=2` 这类非表单参数进检索页会**整页空白** |
| 检索页的筛选面板改动 | 补齐 6 个缺失选项 + 4 个取值改名，并调平标签匹配的归一化 |

后两项由 `_tools/fix_search_filters.py` 在构建流水线里完成（见 README 第 8 节第 10、11 条）。

**三条铁律**（脚本里都有断言守着）：

1. **`site/` 一个字节都不动**。注入 shim 时必须先 `os.remove` 断开硬链接 —— 直接改写会连带改掉 `site/`。
   首页与详情页走的是另一条路：镜像时「读—改写—写」，一次 IO 落成独立 inode，
   天然不碰 `site/`，也不产生删除动作（`verify()` 会抽查原图 inode 是否仍与 `site/` 相同）。
2. **硬链接 vs 复制**：默认硬链接（14,303 个文件秒级完成）。若 `site/` 被整体重建，需重跑本脚本。
   跨盘部署用 `--copy`。
3. **单文件不得超 25 MiB**，超了整次部署会被拒。

### 缓存策略里唯一需要留意的地方（zone 会把你的设定改掉）

`/vendor/*` 在 `_headers` 里写的是 `public, max-age=0, must-revalidate`，
但**线上实测拿到的是 `public, max-age=14400, must-revalidate`**。

不是配置没生效，是 **zone 的 Browser Cache TTL**（`zvi.onl` 上是 14400 秒）把
**低于它的 `max-age` 抬了上去**。它的作用范围很精确，实测下来是这样：

| 路径 | 源站写 | 线上实际 | 为什么 |
|---|---|---|---|
| `/vendor/*`（.js） | `max-age=0` | `max-age=14400` | 小于 zone 值 → 被抬高 |
| `/img/*`（.gif） | `max-age=31536000` | 原样保留 | 大于 zone 值 → 不动它 |
| `/`、`/search/`（HTML） | `max-age=0` | 原样保留 | HTML 不被边缘缓存（`cf-cache-status: DYNAMIC`），改不到 |
| `/rss.xml`、`/rss/doodles.json` | `1800` / `86400` | 原样保留 | 同上，这两种扩展名不在默认缓存列表里 |

为什么要盯它：`/vendor/` 下住着我们自己的离线化层（检索 shim、筛选守卫、各种桩件），
它们与 `rss/doodles.json` 的**数据格式和检索语义强耦合**，而文件名不带内容哈希。
给长了就会出现「老访客拿旧 shim 配新数据」—— 表现是**筛选条数莫名变成 0**，不报错。
（这个坑在开发阶段真踩过：当时 `_headers` 给的是 7 天，本地验收的前三个用例全部返回 0，
排查很久才意识到浏览器在复用旧文件。）

**想让它「改完即刻生效」**：去 zone 设置里把 **Browser Cache TTL** 改成
**"Respect Existing Headers"**（Cloudflare Dashboard → `zvi.onl` → Caching → Configuration）。
这样源站写的 `max-age=0` 就会如实生效。

> 注意这是 **zone 级设置，会影响该域名下其它站点**（账号里另有 5 个 Pages 项目挂在
> `zvi.onl` 子域上），所以没有擅自改。14400 秒（4 小时）是当前可接受的上限，
> 门禁按这个上限守着：`verify_deploy.py` 里 `/vendor/*` 的 `max-age` 超过 14400 即判失败。

> 另一个通用做法是把文件名做成内容哈希（`search-api-shim.<hash>.js`），
> 那样给一年缓存都无害 —— 但 Cloudflare 的缓存键**默认忽略查询串**，
> 所以 `?v=<hash>` 这种写法没用，必须换**路径**。目前规模不值得，留作后续优化。

---

## 2. 一次性准备

### 2.1 域名接入 Cloudflare

`zvi.onl` 必须作为 **zone** 托管在 Cloudflare 下，否则绑不了自定义域。

- 域名在别处注册 → 去注册商处把 NS 改成 Cloudflare 分配的两个 nameserver；
- 已经在 Cloudflare → 跳过。

> 这一步**不需要备案**（Cloudflare 的节点在境外）。代价是你自己在国内访问会有些波动。

### 2.2 创建 API Token（**不要用 Global API Key**）

Global API Key 是**账户级全权限**凭证：能改你名下所有域名的 DNS、删 Workers/R2、看账单，
而且**不能限定作用域、不能设过期**。一旦泄漏等于整个 Cloudflare 账户失守。

正确做法 —— Scoped API Token：

1. 打开 <https://dash.cloudflare.com/profile/api-tokens>
2. **Create Token** → **Custom token**
3. 权限只勾这三条：

   | 类型 | 资源 | 权限 |
   |---|---|---|
   | Account | Cloudflare Pages | **Edit** |
   | Zone | DNS | **Edit** |
   | Zone | Zone | Read |

4. **Account Resources**：选你的账号
5. **Zone Resources**：Include → Specific zone → `zvi.onl`
6. **TTL**：设 1 天（部署完就作废，最省心）
7. 生成后是一串 **40 字符**的字符串，形如 `abcd1234...`，**没有 `cfk_` 之类的前缀**

> 更省事的替代方案：直接跑 `npx wrangler login`，在浏览器里点一下授权，**一个密钥都不用给**。
> 长期维护推荐这个。

#### 如果只能用 API Key（实测可行，但不推荐长期留用）

账户里如果已经发过一个 API Key（形如 `cfk_` 开头），它走的是
**`X-Auth-Email` + `X-Auth-Key`** 这套头，不是 `Authorization: Bearer`。
两种风格的报错**完全不一样**，认错了很容易误判成「密钥无效」：

| 认证方式 | 请求头 | 拿错风格的典型报错 |
|---|---|---|
| Scoped API Token | `Authorization: Bearer <40 字符>` | `{"code":1000,"message":"Invalid API Token"}` |
| API Key | `X-Auth-Email` + `X-Auth-Key` | `{"code":9109,"message":"Invalid access token"}` |

wrangler 侧对应两套环境变量：

```bash
# 风格一：Scoped Token
export CLOUDFLARE_API_TOKEN='<40 字符 token>'

# 风格二：API Key（配合账号邮箱）
export CLOUDFLARE_EMAIL='<你的 Cloudflare 登录邮箱>'
export CLOUDFLARE_API_KEY='<cfk_ 开头的 key>'
```

**怎么快速判断手里的东西是哪一种**，别靠猜：

```bash
# 风格一的验证端点
curl -s -H "Authorization: Bearer $K" https://api.cloudflare.com/client/v4/user/tokens/verify

# 风格二的验证端点（换自己的邮箱）
curl -s -H "X-Auth-Email: $EMAIL" -H "X-Auth-Key: $K" \
     https://api.cloudflare.com/client/v4/user
```

哪个返回 `"success":true`（或 200 + 用户信息）就是哪种。装好之后顺手确认一下
`/user/tokens/verify` 对新式 token 才有意义 —— 对 API Key 它会报
`Missing "Authorization" header`，那**不是**密钥坏了。

> 安全提醒：API Key 是账户级全权限、不能限域、不能设过期。部署完建议回
> <https://dash.cloudflare.com/profile/api-tokens> 把它**轮换掉**，
> 之后日常改用 Scoped Token 或 `wrangler login`。

### 2.3 装 wrangler

```bash
npm i -g wrangler     # 或每次用 npx wrangler@latest
wrangler --version    # 期望 4.x
```

---

## 3. 部署

### 如果要我（AI）代跑

只需提供 **Scoped API Token**（第 2.2 节），我会执行：

```bash
export CLOUDFLARE_API_TOKEN='<你的 token>'
wrangler pages project create doodles-mirror --production-branch main
wrangler pages deploy _deploy --project-name doodles-mirror --commit-dirty=true
```

token 只经环境变量传递，**不会写进任何文件**。

### 如果自己跑

```bash
cd "E:/Users/Desktop/zvi/Google涂鸦全集"
export CLOUDFLARE_API_TOKEN='<你的 token>'

# 首次：建项目
wrangler pages project create doodles-mirror --production-branch main

# 每次部署
wrangler pages deploy _deploy --project-name doodles-mirror --commit-dirty=true
```

**预期输出**（首次）：

```
✨ Uploading... (14308/14308)
✨ Success! Uploaded 14308 files (12 already uploaded)
✨ Deployment complete! Take a peek over at https://<hash>.doodles-mirror.pages.dev
```

**耗时**：取决于上行带宽。1.99 GiB 在 100 Mbps 上行下约 3 分钟；10 Mbps 下约 30 分钟。
先跑一次 `curl -s https://speed.cloudflare.com/__down?bytes=100000000 -o /dev/null -w '%{speed_download}'`
可以估个底。

> 首次部署后，后续更新只会传**变化的文件** —— Pages 会做内容哈希比对，
> 只改了 HTML 的话通常几百 KB、几秒钟。

---

## 4. 绑定 google.zvi.onl

```bash
# 1) 把域名挂到项目上
curl -X POST "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT_ID/pages/projects/doodles-mirror/domains" \
  -H "X-Auth-Email: $CLOUDFLARE_EMAIL" -H "X-Auth-Key: $CLOUDFLARE_API_KEY" \
  -H "Content-Type: application/json" \
  --data '{"name":"google.zvi.onl"}'

# 2) 手工补 CNAME（实测**不会**自动创建，见下）
curl -X POST "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records" \
  -H "X-Auth-Email: $CLOUDFLARE_EMAIL" -H "X-Auth-Key: $CLOUDFLARE_API_KEY" \
  -H "Content-Type: application/json" \
  --data '{"type":"CNAME","name":"google.zvi.onl","content":"doodles-mirror.pages.dev","proxied":true,"ttl":1}'
```

**踩到的两点**（2026-10-01 实际执行）：

- **通过 API 添加自定义域时，DNS 记录不会自动创建** —— 域会一直卡在 `pending`，
  zone 里也查不到任何记录（`GET /zones/$ZONE_ID/dns_records?name=google.zvi.onl` 返回空）。
  补上 CNAME 之后约 2 分钟转 `active`。走 Dashboard 添加时通常会自动建，API 这条路不会。
- 判定激活看 `GET .../pages/projects/doodles-mirror/domains/google.zvi.onl` 的
  `status`，从 `pending` → `active` 才算好；光看 DNS 记录存在还不够。

**别忘了这一步的 zone 级前提**：`zvi.onl` 已是本账号下的 zone（账号里已有 5 个
Pages 项目挂在它的子域上，约定就是 `CNAME → <project>.pages.dev` + proxied）。

---

## 5. 验收

```bash
# A. 本地：在 Pages 的模拟运行时里跑一遍（需要先 wrangler pages dev _deploy --port 8788）
wrangler pages dev _deploy --port 8788 --ip 127.0.0.1
python _tools/verify_deploy.py --port 8788

# B. 线上：直接对着域名跑（HTTPS + 边缘能力，本地那条验不到）
python _tools/verify_online.py --base https://google.zvi.onl

# C. 线上检索页的交互链路（真浏览器）
node _tools/verify_deploy_page.js --base https://google.zvi.onl --proxy "$https_proxy"
```

三层分工不同，都要跑：

| 脚本 | 打什么靶 | 只有它能发现的 |
|---|---|---|
| `verify_deploy.py` | 磁盘上的 `_deploy/` + 本机静态服务器 | 产物自洽、筛选词表对齐、三方语义对账 |
| `verify_online.py` | 线上域名 | 自定义域是否生效、`_headers`/`_redirects` 的**边缘**效果、真 404、订阅源可解析 |
| `verify_deploy_page.js --base` | 线上域名 + 真浏览器 | URL → 填表 → 取数 → 渲染 这条链路 |

> 线上跑 C 时记得给 `--proxy`：Chrome 走的是系统代理设置而不是 `https_proxy` 环境变量，
> 在需要代理出网的环境里不传就加载不出页面。另外线上每页都要重拉 3.2 MB 数据，
> 默认等 60 秒（`--nav-wait` 可调）；等不够会显示成「渲染 0 张」，那是超时不是故障。

`verify_deploy.py` 会检查六层共 **19 项**，其中最关键的是两条链路：

- **三方对账**：浏览器里的 shim、Python 独立复算、`serve.py` 服务端接口，在同一批
  筛选条件下**条数与排序必须完全一致**。任何一处语义漂移都会在这里被抓出来。
- **带参数直接进页面**（8 个用例）：真导航 `?topic_tags=...`、`?page=2` 这类 URL，
  把渲染出的卡片和同一条件问 API 的结果**逐条对齐**，并断言 0 条 JS 异常。
  只问 API 是不够的 —— URL 参数要先被 bundle 填回筛选表单，这条链路断过两次
  （未编码 `&` 整页崩、词表错配静默显示未筛选结果），而且两次都是不报错的。

上线后手工抽查这几项：

| 检查 | 期望 |
|---|---|
| `https://google.zvi.onl/` | 首页正常，地图渲染出来 |
| `https://google.zvi.onl/search/` | **筛选、搜索、翻页都能用**（这是最容易坏的） |
| `https://google.zvi.onl/search/?topic_tags=other` | 出结果且顶部筛选标签显示 `Other`（不是未筛选的最新 16 条） |
| `https://google.zvi.onl/search/?page=2` | 不白屏（表单里没有 `page` 这个字段，属于「不认识的参数」还应正常降级） |
| `https://google.zvi.onl/rss/` | RSS 模式页正常 |
| `https://google.zvi.onl/rss.xml` | 能被阅读器直接订阅 |
| `https://google.zvi.onl/logos/2012/basketball12-hp-anim.png` | 返回 1×1 PNG（不是 404） |
| `https://google.zvi.onl/img/<任意图>` | `Cache-Control: ... immutable` |
| `https://google.zvi.onl/vendor/search-api-shim.js` | `Cache-Control: ... max-age=0, must-revalidate` |
| `https://google.zvi.onl/disclaimer/` | 免责声明页 |

---

## 6. 图片包（可选，需要 R2）

线上**不做** `/rss/images.zip` 实时打包 —— 那是 `serve.py` 的能力，纯静态托管给不了；
而 1.7 GB 也塞不进部署包（单文件 25 MiB 上限）。

要用的话：构建期生成一次，传到 R2（**出口流量免费**，1.7 GB 在 10 GB 免费额度内 = ¥0/月）。

```bash
# 1) 本地生成（约 1-2 分钟）
python -c "
import sys; sys.path.insert(0,'_tools')
import rss_common, zipfile, os
items = rss_common.load_items()
os.makedirs('_dist', exist_ok=True)
with zipfile.ZipFile('_dist/images.zip','w',zipfile.ZIP_STORED) as z:
    z.writestr('manifest.csv', rss_common.manifest_csv(items))
    z.writestr('manifest.json', rss_common.manifest_json(items))
    for it in items:
        z.write(os.path.join('site', it['image_url'].lstrip('/')), 'images/'+it['image'])
print('done')
"

# 2) 建桶并上传
wrangler r2 bucket create doodles-images
wrangler r2 object put doodles-images/images.zip --file _dist/images.zip

# 3) 公开访问（R2 需要绑定自定义域，不能用 r2.dev 做生产）
#    Dashboard → R2 → doodles-images → Settings → Public access → Connect Domain
#    绑一个 dl.google.zvi.onl

# 4) 在 _deploy_src/_redirects 里启用这一行，然后重新 build + 部署：
#    /rss/images.zip  https://dl.google.zvi.onl/images.zip  302
```

配好之前，RSS 页上的「图片包 ZIP」卡片会指向一个 404。这是有意的取舍：
宁可诚实 404，也不要放一个假的下载链接。本地 `serve.py` 的实时打包不受影响，一直可用。

---

## 7. 更新流程

```bash
# 1) 改内容（如果改了 site/，先跑一次完整重建）
python _tools/rebuild.py

# 2) 补齐新增涂鸦的卡片变体（增量：只处理新图或改动过的图，通常几秒）
#    没跑也不会报错，只是新图会继续用原始尺寸、卡片图偏大。
#    阈值/编码参数改动时必须换目录名（见 IMAGE-PERF.md 第 9 节）。
python _tools/gen_image_variants.py --check   # 先看要补哪些
python _tools/gen_image_variants.py           # 再生成

# 3) 重新打包（会把 6,376 个页面的卡片图改指向变体 + 加懒加载）
python _tools/build_deploy.py

# 4) 验收
wrangler pages dev _deploy --port 8788 --ip 127.0.0.1   # 另开一个终端
python _tools/verify_deploy.py --port 8788

# 5) 部署
wrangler pages deploy _deploy --project-name doodles-mirror --commit-dirty=true
```

> `build_deploy.py` 的校验段会打印图片优化摘要（变体数、首页/详情页命中数、
> 检索页首屏字节）。**首屏超过原值 50% 就会以非零码退出** —— 那意味着变体没接上。

---

## 8. 故障排查

| 现象 | 原因 | 处置 |
|---|---|---|
| 部署被拒：`file exceeds 25 MiB` | 有文件超限 | 跑 `build_deploy.py`，它的断言会在本地就拦下 |
| 部署被拒：`too many files` | 超 20,000 个文件 | 同上，报告里会显示文件数 |
| 检索页空白 | shim 没生效 | `curl -I https://google.zvi.onl/vendor/search-api-shim.js` 应是 200；再看 `search/index.html` 里有没有这个 script 标签 |
| 检索页空白，但 shim 是 200 | `search-filter-guard.js` 没挂上 | 带一个非表单参数进页面（如 `?page=2`）就会触发这个空白。看 `search/index.html` 里守卫脚本在不在 bundle 之前 |
| 检索页**筛选条数变 0** / 和本地不一致 | ① 浏览器缓存了旧的 shim；② 语义漂移 | 先强刷（Shift+F5）排除缓存；仍在就 `curl -I .../vendor/search-api-shim.js` 看 `Cache-Control` 是不是 `max-age=0, must-revalidate`；最后跑 `verify_deploy.py`，三方对账会指出是哪一组条件 |
| 点标签进检索页，结果像是「没筛选」 | 面板取值与数据词表不一致（该标签在面板里没有对应选项） | 跑 `python _tools/fix_search_filters.py --check --root _deploy`，它会列出对不上的取值 |
| `/logos/**` 刷 404 | `_redirects` 没生效 | 检查 `_deploy/_redirects` 是否存在、首行是否被注释 |
| 图片更新后访客仍看到旧的 | `_headers` 给了 immutable | `/img/` 下的文件名按 slug 命名、内容不回头改，正常；若确实要改，换文件名 |
| 绑域名卡在 "Pending" | NS 还没生效或代理状态冲突 | `dig NS zvi.onl` 确认 NS 是 Cloudflare 的 |
| 国内访问慢 | Cloudflare 无大陆节点 | 预期之内。要提速需另挂国内 CDN（需备案） |

---

## 9. 成本

| 项 | 费用 |
|---|---|
| 域名 | ¥60–80 / 年（已有） |
| Pages 静态托管 | **¥0**（不限带宽，Direct Upload 不占构建次数） |
| Pages Functions | **不适用**（本方案零 Function） |
| R2（可选） | **¥0**（1.7 GB 在 10 GB 免费额度内，出口流量免费） |
| **合计** | **¥0 / 月** |

---

## 10. 合规

上线前请确认你已经读过 `robots.txt` 和 `/disclaimer/`：

- 站内涂鸦图片、文案、商标的权利**仍在原权利人手里**，本副本不主张任何权利、也无授权；
- 已声明**不被搜索引擎索引**（`robots.txt` 里 `Disallow: /`），避免与官方站点混淆；
- 已声明**不收集任何用户数据**（无账号、无埋点、无第三方统计）；
- 定位是**离线镜像 / 教学演示**，建议**不主动推广**。

如果需要让搜索引擎收录，把 `_deploy_src/robots.txt` 里的 `Disallow: /` 改成 `Disallow: /search/` 即可。
