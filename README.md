# Google Doodles 离线镜像（doodles.google 全站克隆）

把 <https://doodles.google> 的各类路由页完整克隆到本地，并把所有谷歌依赖改造掉，
让**原网页本身**能脱离谷歌生态直接运行、外观与原站一致。

> **注意：仓库里没有图片素材。**
> 这里开源的是**代码与页面**（6,503 个文件 / 156 MB），不含 1.73 GB 的涂鸦原图 ——
> GitHub 单文件硬上限 100 MiB、仓库建议控制在 1 GB 以内，放不下也不该放
> （Git 每次提交都存全量快照，大二进制只会把仓库越滚越大）。
> **所以 clone 下来直接跑，图片位置会是空的。**
> 素材清单、分发方案与获取方式见 **[IMAGES.md](IMAGES.md)**。

启动（先把素材放到位，见上）：

```bash
python serve.py
```

然后打开 <http://127.0.0.1:8765/>。全过程不发起任何外部网络请求。

---

## 0. 开源仓库包含什么

| | 内容 | 规模 |
|---|---|---|
| **在仓库里** | 页面 HTML + 订阅源与元数据（`site/`，**不含素材**） | 6,384 个 / 139.5 MB |
| | 构建 / 修补 / 验收脚本（`_tools/`） | 52 个 / 0.6 MB |
| | 抓取基线模板（`_google_clone/`、`_clone_search/`）—— 重建的输入 | 49 个 / 12.8 MB |
| | 部署配置、数据源、`serve.py`、文档 | 约 3.0 MB |
| | **合计** | **6,503 个 / 155.9 MB** |
| **不在仓库里** | 涂鸦原图 6,392 张 | 1.73 GB |
| | 卡片图变体（480px WebP，可再生的构建缓存） | 189 MB |
| | 字体 / 原站 CSS+JS / 互动涂鸦 / 故事书 / CMS 图 | 约 400 MB |

**能做什么**：读代码、看页面结构与订阅源、跑构建与验收脚本、研究「依赖第三方生态的
网站是怎么被改造成离线自足的」这套方法。

**不能做什么**：clone 完打开就是完整站点 —— 缺素材，图片与互动涂鸦会是空的。
把素材补齐到对应位置后，`python _tools/rebuild.py` 可以完整重建。

### 素材怎么拿

**图片包 —— 1.73 GB / 6,392 张，含清单**

| | |
|---|---|
| 下载 | **123 云盘（免登录）**：<https://1812063966.share.123pan.cn/123pan/kD4rVv-k4NcA> |
| 校验 | SHA-256 `64cd53407dcd75b49b58b47e5179dde22515d4d9d69a15082a305f4e0927d99a` |
| 包内 | `images/`（6392 张原图）+ `manifest.csv` + `manifest.json` + `README.txt` |

解压后把 `images/` 里的文件放进 `site/img/`，`python serve.py` 就能完整跑起来。
（下载完**建议核对一次 SHA-256** —— 1.7 GB 的文件在网络往返里出错的概率不低。）

**只想逐张取图**：订阅源每条都带 `<enclosure>`，那就是图片本体的直接地址。
见 [IMAGES.md](IMAGES.md) 第 1 节 Q1，附一段批量抓取的示例代码。

**其余素材**（字体、原站 CSS/JS、互动涂鸦、故事书、CMS 图，约 400 MB）：
可以按站内路径从 <https://google.zvi.onl> 直接取（例如 `/fonts/…`、`/games/…`），
或用镜像站自己重新抓取。分发方案的取舍见 [IMAGES.md](IMAGES.md) 第 2 节。

仓库里最大的单文件是 5.75 MB（`site/rss.xml`），远低于 GitHub 的 50 MiB 警告线，
所以 `git clone` 和日常提交都很轻。

---

## 1. 交付物在哪

真正的交付物是 **`site/` 目录**（6,543 个 HTML、约 2.4 GB）。它是一个标准的静态站点，
站点根就是 `site/`，任何静态服务器都能直接托管 —— `serve.py` 只是额外补了订阅源
按 Host 改写、图片包实时打包和一个降级处理，见第 4 节。

需要本机已装 Python 3（无需第三方包，只用到标准库；构建阶段用到的 Pillow 也只在重建时才需要）。

```
site/
├── index.html                  首页
├── search/index.html           检索页（筛选/搜索/分页，已改为滚到底自动加载）
├── about/index.html            关于 Doodles（时间轴）
├── doodle/<slug>/index.html    涂鸦详情页 ×6375
├── games/     storybooks/      163 个本地互动涂鸦 + 22 个故事书
├── img/       fonts/  static/  assets/  vendor/
├── rss/index.html              RSS 模式页（订阅视图 + 筛选 + 下载区）
├── rss.xml  rss/latest.xml     RSS 订阅源（全量 6375 条 / 最新 100 条）
├── rss/doodles.csv|json        元数据导出（含每张图的介绍与分类）
└── offline/index.html          外链提示页
```

## 2. 克隆覆盖的路由

| 路由 | 说明 | 状态 |
|---|---|---|
| `/` | 首页（含当日/精选涂鸦轮播） | ✅ 原页 |
| `/search/` | 检索页，关键字 / 主题 / 颜色 / 形态 / 年份 筛选 + 分页（已改为**滚到底自动加载**） | ✅ 原页 + 本地接口 |
| `/about/` | 关于页（涂鸦历史时间轴，含图表） | ✅ 原页 |
| `/doodle/<slug>/` | 涂鸦详情页，共 **6,375** 个 | ✅ 原页骨架批量实例化 |
| `/games/…` `/storybooks/…` | 互动涂鸦，**163** 个详情页挂了本地互动模块 | ⚠️ 部分素材见第 6 节 |
| `/rss/` | **RSS 模式**：订阅视图（筛选 + 滚到底自动续载）+ 下载区 | 🆕 自建页，复用站点页眉/页脚 |
| `/rss.xml` | 全量订阅源（**6,375** 条，标准 RSS 2.0，含图片 enclosure / 介绍 / 四类分类） | 🆕 构建期生成 |
| `/rss/latest.xml` | 最新 100 条（给阅读器日常订阅） | 🆕 构建期生成 |
| `/rss/doodles.csv` `/rss/doodles.json` | 元数据导出（标题/日期/年份/主色/话题/格式/地区/图片/详情页/介绍） | 🆕 构建期生成 |
| `/rss/images.zip` | 图片包，服务端实时流式打包（含 `manifest.csv`），支持按话题/主色/格式/年份/关键字筛选 | 🆕 serve.py 现算 |
| `/live/api/v1/doodles` | 原站数据接口 | ✅ 本地复刻 |

三个主路由页（`/`、`/search/`、`/about/`）是**原站抓取页本身**
改写而成，不是重写的仿制品：DOM 结构、class 名、样式表、运行时 JS 都是原物。

> `/creating-a-doodle/` 已按需求**整站移除**：路由目录、全站导航栏与页脚入口、
> 页面内引用、源头模板中的对应项全部清除，共改动 6,386 个文件、摘除 12,770 个入口块。
> 抓取源料 `_clone_search/creating-page.html` 保留未删，但已从 `build_site.py` 的
> `PAGES` / `FLAT_ROUTE` 中摘除，不再参与任何构建。见 `_tools/remove_creating_doodle.py`。

> **页脚已按需求重做**（`_tools/strip_footer_google.py`）：原本指向谷歌在线服务的
> 入口 —— Google logo、About Google、Google Products、Privacy、Terms、Help、以及
> reCAPTCHA 免责声明 —— 连同「Follow us」社交图标组（LinkedIn / Facebook /
> Instagram / X / YouTube）**整块删除**；版权行换成
> `Copyright © 2026 → ∞ ZVI and Google will always be together.`
>
> 改动 6,385 个页脚（6,378 个 site 页面 + 7 个源头模板），页脚体量 85.5 MB → 16.6 MB。
> 保留下来的只有 `Doodles → Library / About` 两项站内链接与无障碍标题。
> 结果：全站 `/offline/?to=` 转向链接计数归 **0**，页脚内已无任何谷歌或社交域名。

> **正文里的 hero 分享条也已删除**（`_tools/strip_share_icons.py`）：详情页配图旁边
> 那组竖排的「复制链接 / LinkedIn / X / Facebook」图标加展开按钮
> （`share-container vertical`），以及关于页 hero 区的横排版（`share-container horizontal`）
> 一并整块摘除 —— 改动 6,379 个文件 / 6,381 块，页面体量 −39.7 MB。
> 至此全站再无指向社交站点的链接与图标（`linkedin.com` / `facebook.com/sharer` /
> `x.com/share` / `share-container` / `glue-social__list` 计数全部为 **0**）。

> **检索页的分页改成「滚到底自动加载」**（`_tools/search_autoload.py`）：
> 原来的 `Show More` 按钮已不再展示（`display:none`），改为列表滚到底附近时自动续页。
>
> 这里**没有删掉按钮**，是有意的：分页状态（当前第几页、还有没有下一页 `e.next`）
> 全部挂在 `.search-doodle__results-button` 这个节点上 —— `ResultsDoodleForm` 用它
> 当唯一的分页触发器，`querySelector` 拿到 `null` 会直接抛错、整条分页链路断掉。
> 所以做法是「按钮留在 DOM 里但视觉上藏掉，另挂一个脚本替用户点它」，
> 复用运行时既有的分页逻辑，没有复制也没有重写任何一行业务代码。
>
> 刹车全部来自运行时自己的状态，不额外发明：加载中时按钮的 `.active` 会被运行时
> 同步摘掉（`handleLoading`），到达末页时 `appendResults` 用 `Boolean(e.next)` 摘掉
> `.active`，脚本再叠一层「卡片数有没有真的增长」的判断防止死循环。
> 实测：`/search/` 滚动 5 次 16 → 96 张、无任何 JS 异常；
> `/search/?title_like=halloween` 16 → 28 张后**自动停住**（28 条就是全部结果）；
> 空结果页不触发加载、也不报错。见 `_tools/verify_search_autoload.js`。

> **页眉 Google 字标改成 Gøøgle**（`_tools/slash_google_o.py`）：两个 `o` 各叠一块
> 同色斜条，方向按 Ø 的字形规范取「左下 → 右上」，端头竖直切边（对齐系统字体的 Ø）。
>
> 妙处在于**只改一处、全站生效**：页眉字标是「精灵图 + `<use>`」结构，
> `google-color-logo` 的定义只有一份（`site/static/…glue-icons.svg`），
> 6,378 个页面共用。所以不需要动 6,500 多个 HTML，桌面条与移动条同时生效。
> 斜条几何是在真实页眉尺寸到 520px 之间做了六个变体的并排比对后定的中档
> （`_shots_after/_logo_variants.png`），探出太多会像「禁止符」，太少又不像 Ø。

6,375 个详情页以真实抓取的 `doodles.google/doodle/…` 页面为骨架（页头 8.6 KB、
页脚 2.6 KB 都取自原页），再按数据逐页填充标题/日期/配图/主题标签/相关推荐，
因此版式与原站一致。

### RSS 模式（`/rss/`）

把整个镜像当成一个订阅源用：进阅读器订阅，或者按分类整包下载图片。

**入口**：全站页眉导航栏在 Library / About 之后加了第三项 **RSS**。页眉是全站共用
的一份 DOM，所以这一项 6,379 个页面都有（另 164 个游戏页本来就没有页眉）。
改动落在 8 个源模板 + 6,379 个已生成页面上，见 `_tools/gen_rss.py`。

**订阅源**：标准 RSS 2.0，每条 `<item>` = 一个涂鸦，字段这样对应：

| RSS 字段 | 内容 |
|---|---|
| `<title>` / `<link>` / `<guid>` | 标题 / 详情页 / 稳定标识 |
| `<pubDate>` | 上线日期（**只给有确切日期的 634 条**，见下） |
| `<enclosure>` | 图片本体（带 `type` 与真实字节数），阅读器可直接抓取 |
| `<description>` | 官方介绍 + 「上线日期 / 年份 / 主色 / 话题 / 格式 / 地区」明细 |
| `<category>` | 话题（裸值）；主色 / 格式 / 地区带 `domain="zvi:color"` 之类标注，便于下游按维度分辨 |

**排序为什么不跟站点其它页面一致**：站点数据的 `run_date` 只有 **634 / 6,375** 条
成立，其余整块沉底按标题排 —— 对列表浏览没问题，但一个订阅源九成条目按字母序排
就完全没有时间感。所以订阅源额外借用了 slug 里的年份（`doodle_data.YEAR_IN_SLUG`，
**与详情页/检索页的日期推定共用同一份正则**），把有年份线索的 4,129 条按年倒序排在前。
**推定年份绝不写进 `<pubDate>`** —— 那等于伪造一个具体日期；它只用于排序、筛选，
以及在清单里以「年份来源：名称推定」显式标注。

**图片包 `/rss/images.zip` 是现算的**，不是预先生成的文件：

- 全量 1.7 GB，不可能先落盘再算 `Content-Length`，所以走 **HTTP/1.1 chunked** 边打边发。
- `zipfile` 写给一个**只能顺序写、故意不实现 `seek`/`tell`** 的流对象 ——
  这时 zipfile 会自己套 `_Tellable` 记录偏移，并把每个条目的大小写进 data
  descriptor（而不是回填 local header），这正是顺序流需要的形态。
  反过来，如果假装可寻址，zipfile 会去 `seek` 回填，socket 上必然崩。
- 包内是 `images/` + `manifest.csv`（每张图的标题/日期/年份/主色/话题/格式/地区/
  详情页/介绍）+ `manifest.json` + `README.txt`。清单**先写**，所以即使中途取消，
  解出来的包里也有目录。
- 支持 `topic` / `color` / `form` / `year` / `q` 筛选，实测吞吐约 **50 MB/s**
  （92 张 / 32.5 MB 用时 0.67 s；全量 1.7 GB 约 35 s）。

**筛选语义只有一份**：页面上算条数的 JS 与决定 zip 里装哪些图的 Python 是两套
独立实现，只要有一处漂移就会出现「页面说 128 张、下载下来 135 张」。所以
`serve.py` 与页面前端共用 `_tools/rss_common.py` 的语义，并由
`_tools/verify_rss_page.js` 用真浏览器把 8 组筛选条件的页面条数、打包链接、体积
估计逐条读回来对账（详见第 5 节第 6 层）。

## 3. 谷歌依赖是怎么被改造的

原则是**能保留原物就保留原物，只在必须断掉的地方动手**。

| 依赖 | 改造方式 |
|---|---|
| **数据接口**（谷歌云 appspot） | 前端原本在 `localhost` 下会去连云端真接口。改 `patch_runtime.py` 把宿主判断统一成 `location.origin`；`serve.py` 按原参数契约复刻接口（`title_like` / `color_tags` / `topic_tags` / `form_tags` / `date_like_year…` / `page` / `limit` / `sort_direction`），并同时返回 `run_date_array` 与 `run_date_year/month/day` 两种日期字段，因为两个渲染位置读的字段不一样。 |
| **reCAPTCHA** | 原站每次请求前要 `grecaptcha.enterprise.execute()`。用本地桩件实现同名命名空间，直接返回固定 token。 |
| **Google Fonts** | 201 个 woff2 全部落盘到 `fonts/`，`fonts.css` 保持原样（Google Sans / Google Sans Text / Google Sans Display / Product Sans）。 |
| **Google Tag Manager / Ads** | 本地空实现桩件，不做任何上报。<br>**顺带修掉一个改造遗留 bug**：原站的 GTM 片段写作 `j.src='https://www.googletagmanager.com/gtm.js?id='+i+dl`，离线化时把 URL（含尾部 `?id=`）整段换成了本地路径，但**后面 `+i+dl` 的拼接留了下来** —— 运行时容器 ID 被直接粘到文件名尾部，请求成了 `/vendor/gtmc67cef9231.jsGTM-KQ5TXT7`，**全站 6,377 个页面各有一个 404**。控制台只留一句不带 URL 的 `Failed to load resource: 404`，静态扫描也扫不到（它是 JS 拼出来的）。现改为整段收掉、直接指向 `/vendor/gtm-stub.js`。见 `_tools/fix_gtm_src.py`。 |
| **Glue 设计系统** | `glue` 的 CSS / JS / 图标精灵图（含 30 + 43 个图标）全部本地化，`<use href="…#id">` 指向本地 sprite。 |
| **YouTube** | `<script src>` 换成桩件；更关键的是 runtime 里**动态注入**的两处（`IFRAME_SCRIPT_URL` 与封面图模板串）也在 `patch_runtime.py` 里改到本地 —— 这类运行时加载静态扫描是抓不到的。 |
| **日期渲染** | 原站代码无条件 `new Date(...)`。本地数据有 9/10 的条目拿不到确切上线日期，直接跑会渲染出 `Invalid Date`；检索页那处读的是数组下标，缺字段会抛 `TypeError` 打断整个结果列表（表现为一直转圈）。改为「无日期则不显示日期」。 |
| **Google Charts**（首页世界地图） | 首页「Latest Doodles from around the world」是一张 Google **GeoChart** 世界地图。早期为了「能让页面离线打开」，把 `google.charts.load` 换成了一句空函数桩件 —— 页面确实不报错，但那一块 **1424×676** 的区域永远空白（整页截图看不出问题，只有量 DOM 才发现 `.mapChart` 的 `innerHTML` 长度是 0）。<br>正解不是砍掉而是搬进来：用 CDP 实测出 geochart 需要的**全部 11 个文件**（版本化 loader、2 个 CSS、5 个 `jsapi_compiled_*` 模块、地图清单与 `world_COUNTRIES` 几何数据，共 1.24 MB）取回本地，把包内 23 处 `www.gstatic.com/.cn/charts` 绝对地址改成 `/vendor/charts`，首页再接回**真 loader**。<br>顺带修掉一处**站内不一致**：`/about/`、`/search/` 两页的输入 HTML 早先被写成了桩件路径，于是出现过「同一个东西两种实现」（桩件页 `google.charts.load.toString().length` = 14，真 loader 页 = 202）。现已统一 —— **6,379 个页面全部指向真 loader，桩件引用 0**。见 `_tools/patch_charts.py`。 |
| **外链** | 页脚里指向谷歌站点的入口（Privacy / Terms / Help / 社交图标 / Google logo）**已整块删除**，正文里的 hero 分享条（`share-container`）也已删除 —— 全站 `/offline/?to=` 计数归 **0**，社交域名计数归 **0**。<br>`/offline/` 提示页保留未删。页面其余位置的点击型外链（如互动游戏里的 `twitter.com/intent/tweet`）仍指向原站，离线时点了本来也打不开。 |
| **`key-themes` 的随机主题胶囊** | 原页的「This Doodle's Key Themes」模块底部有一个 `Explore a Random Theme` 胶囊（`<a class="key-themes-cta-link" data-links="[…]">`）。早期 `gen_doodle_pages.py` 拼这个模块时**把它漏了** —— 后果不只是少一个按钮：原站 bundle 里 `KeyThemes` 类的第一句就是 `el.querySelector(".key-themes-cta-link")` 然后读它的 `dataset.links`，元素不存在就直接抛 `TypeError: Cannot read properties of null (reading 'dataset')`，而**这个异常位于主初始化函数的 `forEach` 回调里，会把排在它后面的 `AddLinkClass` / `ContentBlock` / `SocialShare` / `ToutCarousel` 四个模块一起掐断**（全站 6,375 个详情页都命中）。<br>正解是把原页本来就有的那个胶囊补回去，而不是在 bundle 里加 null 判断：`gen_doodle_pages.py` 现在自己就会生成它，`_tools/fix_key_themes.py` 作为幂等后置兜底。随机主题改用**本地主题分类**做池子（原站那批细分主题如 `skateboarding` / `hinamatsuri` 本地数据里没有，点了会落到空结果），按 slug 确定性抽 10 条，机制一致但每次点都落在有内容的检索结果上。 |
| **Cookie 通知条**（真实脚本） | 这是**唯一一处静态扫描抓不到的加载期外链**。`www.gstatic.com/glue/...` 被成功下载到本地，于是重写规则 1（本地映射表）先命中、返回真实脚本，导致「换成本地桩件」那条规则成了死代码 —— 6,378 个页面加载的是**真实**的 Google cookie 通知条，而它在构造函数里无条件执行 `fetch("https://www.gstatic.com" + d + c)`（`d` 是 `/glue/cookienotificationbar/config/{2a,2b}.json`）。<br>按「保留原物」的原则没有换成桩件，而是保留真实脚本、把配置源本地化：`fetch("/vendor"+d+c)`，并把真实的 `2a.json` / `2b.json` 落盘（实测两份都是 `{ "required": false }`，即真站在此配置下同样不展示通知条，所以视觉结果一致）。见 `_tools/patch_vendor.py`。 |

## 4. 为什么还需要 `serve.py`

`site/` 本身可以纯静态托管。`serve.py` 额外做四件静态托管做不到的事：

1. **补齐 `/live/api/v1/doodles`** —— 静态托管没有接口，检索页会空白。
2. **`/logos/**` 降级** —— 互动涂鸦里有一批当年没抓到、且本网络已无法补齐的精灵图
   （详见第 6 节）。对这些请求返回 1×1 透明 PNG，让游戏静默降级，而不是刷一屏 404。
3. **`/doodle/<slug>/` 兜底** —— 首页快照里有 6 张卡片指向比数据集更新的涂鸦
   （见第 6 节第 4 条），这些 slug 不存在。返回一个与站点风格一致的说明页，而不是裸 404。
4. **`/rss/images.zip` 实时打包 + 订阅源按 Host 改写基址** —— 全量图片包 1.7 GB，
   不可能预先落盘，只能边打边发（见第 2 节「RSS 模式」）。订阅源是静态文件，
   但里面的绝对链接要按请求头里的 `Host` 改一次 —— 换端口、换主机名访问时，
   阅读器拿到的地址仍然是对的。

其他它什么都不做：不代理、不联网、无第三方依赖。

## 5. 离线保证（可复现校验）

离线与否**不能靠看代码断言**，得能测。这里叠了六层：前五层从「静态引用」一路查到
「浏览器实际发出的请求」，证明的是「克隆得像不像、离不离线」；第六层是 RSS
这项新增能力自己的判据（两套筛选实现是否语义一致）。每层都有脚本、都可复现。

### 第 1 层 · 静态引用扫描

```bash
python _tools/verify_offline.py     # HTML 属性 / CSS url() 里的谷歌域名
python _tools/scan_deadlinks.py     # 站内引用是否全部可达
```

```
扫描文件        : 6546
阻断型谷歌依赖  : 0   ← 渲染不会失败，也不会发起任何外部请求
非阻断外链      : 1   （fonts.css 里的一条字体许可注释 URL，保留用于署名）
```

「阻断型」指 `<script src>` / `<link href>` / `<img src>` / `<iframe src>` / CSS `url()`
里出现谷歌域名。目前为 **0**。改造后 `site/static/…index.min.js`（原站 webpack 产物）
内已**不含任何** google / gstatic / youtube / ytimg / appspot 域名。

死链扫描结果分四档，只有「真实死链」才算失败：

```
扫描 HTML        : 6543
站内可达引用      : 261036
良性降级（有兜底）: 10 处 / 9 个目标
脚本上下文（非静态）: 0 处 / 0 个目标
真实死链          : 0 处 / 0 个目标 / 0 个文件
```

10 处「良性降级」全部由 `serve.py` 兜底且都有记录：6 处是第 6 节第 4 条的首页快照卡片、
2 处是 `/rss/` 页面上的图片包链接（服务端实时打包，磁盘上本就没有 `/rss/images.zip`）、
1 处是检索页 JS 克隆用的空卡片模板 `href="/doodle/"`、
1 处是游戏 JS 里字符串拼接的前半段 `/logos/2011/gumby11-`。

### 第 2 层 · 脚本内置外链审计（HTML 属性扫不到的那一类）

```bash
python _tools/audit_embedded_urls.py
```

它只审计**页面真正引用到的**本地 JS，并在**可执行位置**上找绝对 URL
（`fetch(` / `XMLHttpRequest` / `importScripts(` / `.src=` / `new Image(` /
`createElement('script')`），同时把 `window.open(` 这类**用户点击才触发**的
单独归类，不计入失败。

```
扫描 HTML            : 6,543
被引用的本地 JS 文件  : 59
✅ 没有任何被引用的脚本在加载期发起外链请求。
· 仅「用户点击时」才打开外链的脚本（26 个 / 26 处）—— 不影响离线渲染：
      twitter.com                        26 处
    （这类是分享按钮等，等同外链 <a>；离线时点它本来也打不开）
```

**这一层是必须的** —— 项目里的 cookie 通知条就是栽在这里：它的
`fetch("https://www.gstatic.com"+d+c)` 是代码里拼出来的，前一层完全看不见。

### 第 3 层 · 断网实测 + CDP 页面目标取证（权威判据）

```bash
python _tools/offline_probe.py      # 拦截代理：非 localhost 出口全部返 502
node   _tools/cdp_probe.js --sample 50   # CDP：只统计「页面目标」的请求
```

- **拦截代理**（`offline_probe.py`）给 Chrome 设 `--proxy-server` 指向一个只记录、
  只返 502 的本地代理，再叠加 `--host-resolver-rules="MAP * ~NOTFOUND"`。页面自己
  的资源走 localhost 直连不受影响，任何外网请求一定被记下并失败。

  它同时暴露了一个**方法论陷阱**：代理会把**浏览器进程自身**的后台请求
  （`clients2.google.com` 校时、`update.googleapis.com`、`accounts.google.com`
  账号一致性检查）和页面请求混在一起。按主机名分不开，按「相对 about:blank 的增量」
  去减又会因为页面存活时长不同而误判。所以它只作为旁证，最终判据看下一项。

- **CDP 取证**（`cdp_probe.js`，Node 22 内置 `WebSocket`，零依赖）用
  `Network.requestWillBeSent`，**只在页面目标上触发** —— 浏览器进程自己的请求
  压根不会出现在这里。这是与时间无关、精确到「谁发起」的清单：

  ```
  互动涂鸦：可解析入口页 144 个，本次测试 144 个；24 个目录当年未抓到 HTML（跳过）
  模式：代表页 10 个 + 详情页抽样 60 个 + 全部互动涂鸦（种子 20260925）
  …
  页面数            : 215
  外部请求总数      : 0
  ✅ 所有页面在渲染期都没有发起任何外部请求（CDP 页面目标级证据）。
  ```

  覆盖构成：3 个主路由 + 检索页（含两个筛选组合）+ 60 个随机详情页 +
  **全部 144 个能解析出入口页的互动涂鸦** + `/offline/`。
  互动涂鸦的入口页命名并不统一（只有 13 个用 `*-hp.html`），
  脚本改为读各目录里的 `_entry.txt` 所记录的原始地址来定位，
  否则覆盖率会掉到 13/168。

  `--sample N` 会额外抽取 N 个详情页 + N 个互动游戏（确定性随机、种子固定可复现），
  把结论从「抽样 10 页」提升到「覆盖各页面类型的大样本」。

### 第 4 层 · 资源可达性

关键路由与降级端点逐一请求，全部 200 —— 含 `/`、`/search/`、`/about/`、
`/offline/`、详情页、`/fonts/fonts.css`（154 KB）、
运行时 bundle（231 KB）、`/vendor/youtube-api.js`、33 张 `/assets/yt/` 占位封面、
`/logos/**` 的 1×1 PNG 降级响应、cookie 条的 `config/2b.json`，
以及 `/vendor/charts/` 下 11 个 Charts 文件（`loader.js`、`51/js/*`、`geochart/10/*`）。

### 第 5 层 · 模块级渲染验收（DOM + 像素）

前四层能证明「不连外网、资源都在」，但证明不了「某个模块真的初始化成功了」——
首页地图就是反例：空白区域在整页截图里看不出异常，也没有任何 JS 报错。

```bash
node _tools/cdp_inspect.js --port 8765 --url / \
     --clip ".full-bleed-map" --clip-out _shots/_home_maparea_after.png
```

`cdp_inspect.js` 一次给出三样证据：**JS 异常 / console error**、**自定义 DOM 表达式
的结果**、**按 CSS 选择器裁剪的截图**。首页地图的判定数据：

| | 桩件版（改动前） | 真 Charts（改动后） |
|---|---|---|
| `google.charts.load` 函数体长度 | 14（空函数） | 202 |
| `.mapChart` `innerHTML` 长度 | **0** | **384,763** |
| `.mapChart` 内 `<path>` 数 | 0 | **780** |
| 该区域 `#dadce0`（陆地色）像素占比 | **0%** | **9%** |
| JS 异常 / console error | 0 / 0 | 0 / 0 |

只裁 `.mapChart` 本体（1090×676 = 736,840 px）再数一次，就是一张 GeoChart
markers 模式世界地图该有的构成：

```
未着色陆地 #dadce0      224,175 px   30.42%
标记蓝     #4285f4        1,577 px    0.21%   ← 3 个数据点
背景白     #ffffff      468,574 px   63.59%
不同颜色 765 种
```

像素占比用 PIL 直接统计同一块裁剪图，是"地图真的画出来了"的客观证据，
不依赖人眼看截图。

### 第 6 层 · RSS 订阅与图片包验收（`verify_rss.py`）

前五层管的是「原站克隆得像不像、离不离线」；RSS 是**新增能力**，需要自己一套判据。
而且它有一个前五层遇不到的风险：**页面 JS 与 `serve.py` 是两套独立实现的筛选逻辑，
一旦语义漂移，人眼看页面完全正常，只是下载下来的图片包和页面上显示的条数不一样。**

```bash
python _tools/verify_rss.py --no-http        # 只跑静态层（构建流水线里跑这个）
python _tools/verify_rss.py --page           # 追加真浏览器页面侧验收
```

一次 63 项检查，分五组：

| 组 | 证什么 | 代表性的判据 |
|---|---|---|
| ① 产物自洽 | `rss.xml` 是合法 XML、条数够、字段齐 | 条数三方一致；`<enclosure length>` = 磁盘真实字节数；带 `<pubDate>` 的条数**恰好等于**确知日期的 634 条（多一条即意味着伪造了日期） |
| ② 与源数据交叉核对 | 逐条对得上 `doodles_data.js` | 标题 / 图片文件 / 话题，全 6,375 条抽样比对 |
| ③ HTTP 实测 | 路由真的可用 | `/rss.xml` 按请求头 `Host` 改写绝对地址；zip 回读后 CRC 全对、**包内图片与磁盘原件逐字节一致**；全量包走 chunked（说明没先落盘算长度）；下载中断后服务仍存活 |
| ④ 页眉入口 | 入口真的进了页眉 | 首页 / 检索页 / 关于页 / 详情页四类页面：入口存在、埋点属性完整、序号收敛为 `1/3 2/3 3/3` |
| ⑤ 页面筛选对账 | **两套筛选实现语义一致** | 用真浏览器（`verify_rss_page.js`）逐组套用 8 种筛选，把页面算出的条数读回来，与 Python 侧独立复算的结果比对 |

第 ⑤ 组是这套验收的核心价值。它取数的方式是页面在列表元素上留的一个可读锚点
（`list.dataset.count`），以及打包链接的 `href` —— 两者都是**页面真的算出来的结果**，
不是页面自己声称的字符串。

写这组用例时踩过一个时序坑：关键字筛选有 180 ms 防抖，而验收脚本开局就取数，
读到的是「选项已清空、关键字还没套上」的中间态，于是页面报 6,375 条、
源数据报 28 条，看起来像严重的逻辑错误，其实是测试自己读快了。
现在的做法是**先跳过防抖窗口，再等连续两次读数相同**才判定。

## 6. 已知缺口（诚实清单）

1. **互动涂鸦的部分精灵图缺失** —— `games/` 是从谷歌 `/logos/` 目录抓下来的，
   其中约 **9,553** 处资源引用当年未被落盘，且当前网络**无法直连 `www.google.com`**
   （`web.archive.org` 同样不可达）补齐。
   - 已把能在本地 `assets/` 找到同名文件的部分（168 处）改回本地相对路径；
   - 完全齐备的目录有 **50** 个；
   - 缺口集中在约 10 个较新的游戏（`celebrating-popcorn`、`celebrating-lake-xochimilco`、
     `halloween-2024`、`rise-of-the-half-moon`、`celebrating-marie-tharp`、
     `celebrating-bubble-tea` 等），这些游戏能打开、能操作，但精灵图为空白。
   - 详情页上的**静态主图是完全正常的**（来自 `img/`），只有点开互动弹窗才会看到缺图。
2. **YouTube 视频不可播放** —— 原页的 33 个视频位显示本地占位封面（缩略图源
   `img.youtube.com` 同样被封）。
3. **日期** —— 仅 **634 / 6375** 条有确切上线日期（原始数据里 9 成条目只有历史事件日期，
   按原值显示会错得很离谱，例如美国独立日被记成 1776 年）。拿不到确切日期的条目
   选择**不显示日期行**，而不是显示一个错日期。

   RSS 侧沿用同一条底线：**`<pubDate>` 只给这 634 条**。另有 4,129 条能从 slug
   里读出年份（`doodle_data.YEAR_IN_SLUG`，与详情页/检索页共用同一份正则），
   这部分年份**只用于排序、筛选和清单里的一列标注**（CSV 的「年份来源」会写明
   `确切` / `推定`），不会写进 `<pubDate>` —— 推定值当日期用等于伪造时间戳，
   会让阅读器里的时间线整片失真。
4. **首页有 6 张卡片指向数据集之外的涂鸦** —— 涂鸦清单来自一次全局导出（最新一条
   2026-05-31），而首页是稍晚时点抓取的，因此「Hari Merdeka 2026」「Notting Hill
   Carnival 60 周年」「Bundesliga 2026-27」「National Parks Week」系列等 6 张卡片的
   目标 slug 没有详情页。点开后显示一个站点风格的说明页（配图本身是有的，在 `img/` 下）。
   若日后更新 `涂鸦目录.csv` / `doodles_data.js`，重新生成即可自动补上。
5. 少数资源以占位图代替（无法直连的主机）：`assets/yt/` 的 33 张视频封面、
   `assets/placeholder.png` 等。
6. **`/rss/images.zip` 只在 `serve.py` 下存在** —— 它是实时流式打包（全量 1.7 GB），
   磁盘上本就没有这个文件（已登记进 `scan_deadlinks.py` 的良性白名单，避免被死链
   扫描误报）。若改用别的静态服务器托管 `site/`，其余路由照常，只有这一个路由会 404；
   `/rss/` 页面里的下载链接也会随之失效（订阅源 `/rss.xml` 不受影响，它是真文件）。
7. **`assets/cms/` 里有 123 个「有文件、没引用」的资源（273.4 MB）** —— 这不是漏抓，
   而是抓取与交付两个阶段错位留下的死重，成因值得记一笔：

   - 抓取期扫到了原站的 `https://doodles.google/creating-a-doodle/`（涂鸦创作流程页），
     把它存成 `_clone_search/creating-page.html`，并**把页面上引用的 124 个
     `www.gstatic.com/marketing-cms/...` 资源一并下载**，映射规则是
     `www.gstatic.com/marketing-cms/X` → `site/assets/cms/X`；
   - 闭环核对过：那 124 个引用**全部在磁盘上、漏抓 0 个**。磁盘共 141 个 =
     124（创作页）+ 17（`about` 页时间轴小图），其中 `image-15.png` 两边共用，
     于是 **141 − 18 = 123 个真孤儿**；
   - 交付期构建 `site/` 时只挑了要交付的页面，**创作页不在其中**（它是营销/教学页，
     带 60 处 `img.youtube.com`、34 处 `youtube.com` 以及 linkedin / x / facebook 外链），
     只停在暂存区。资源却已经落盘，而**清理环节只按「页面还引不引用」回收，
     不检查「引用方是否还在站内」** —— 引用方一消失，资源即成死重。
     本地化脚本遍历的是 `site/`，看不到待在 `_clone_search/` 的创作页，
     于是既不重写也不清理。

   这批文件的构成：`png` 65 个 / 174.4 MB，`jpg` 46 个 / 94.4 MB，其余 20 个 4.5 MB。
   内容上是创作页的配图 —— 插画师肖像与自画像（`derekabelladoodleportrait.jpg`、
   `11-09-2024-maureenwatson-rubiired.jpg`）、编号的创作过程大横幅（`26d.png`
   5000×2000、`8.png` 3840×3840）、以及几张案例涂鸦的 2x 高清版。
   其中最大的一张 **`0923-oskarpicht-utediez.jpg`（33.67 MB，5504×8256）**
   是 2021-09-23 Oskar Picht 涂鸦的客座插画师 Ute Diez 的采访头像 ——
   原站 alt 文本写着 *"Photograph of a middle aged woman looking to the side..."*。
   它不是涂鸦作品，一张采访配图原图直出到 5504×8256，这就是它 33.67 MB 的原因。

   **处置**：`_tools/build_deploy.py` 按「引用方是否还在站内」判定并排除这 123 个
   （`site/` 不动，只在部署包里排除）。排除后单文件最大从 33.67 MB 降到 5.73 MB，
   Cloudflare Pages 的 25 MiB 硬限制**自动满足**。
8. **筛选面板里的 `multicolor` 对应不上任何本地数据**
   （面板 12 色 vs 本地数据 12 色中的 `cyan`：面板有 `brown` / `gray` / `multicolor`，
   本地有 `grey` / `cyan`。`gray → grey` 已按同义改名修掉，见第 8 节第 11 条。）

   剩下这一对 `multicolor ↔ cyan` **没有证据能确认同义**：
   数量结构上确实是一一对应（12 对 12，只差这一对），本地 `cyan` 有 413 条，
   但抽查一条 `color=cyan` 的涂鸦页（`curling-2026`），其模块主题类写的是
   `theme-teal` —— 说明本地的颜色名既不是直接抄主题类、也不来自面板词表，
   中间还有一层来源不明的映射。

   站点里**没有任何链接指向 `color_tags`**（筛选面板是唯一入口），
   所以要判断影响面：只是筛选面板里少一个能用的色块，且 `multicolor` / `brown`
   点进去是 0 条。补一个 Cyan 色块需要克隆色圈结构（有视觉风险），
   在确认映射之前不动。这一条已在 `fix_search_filters.py` 的
   `KNOWN_GAPS` 里显式登记为「已知缺口」，不会让验收一直报红。

## 7. 重新构建

```bash
python _tools/rebuild.py            # 离线重建（复用已下载资源，约 2~3 分钟）
python _tools/rebuild.py --with-net # 允许联网尝试补齐缺失资源
python _tools/rebuild.py --only verify
```

流水线有**强顺序依赖**，`rebuild.py` 就是为此存在的：

```
build_site.py        重写 3 个主路由页 + 铺设资源 / 桩件 / 共享素材（硬链接）
patch_vendor.py      本地化 vendor 真实脚本内置的外链请求（cookie 通知条）
gen_doodle_pages.py  生成 6375 个详情页
localize_all.py      对 site/ 下 HTML 做离线化重写
patch_charts.py      把 Google Charts 搬进本地，首页接回真 loader（详见下）
patch_runtime.py     从原始 bundle 重建并改造前端运行时
offline_links.py     接管指向谷歌站点的 <a> 链接
remove_creating_doodle.py 移除 /creating-a-doodle/ 路由与导航入口（须排在所有会重写 HTML 的步骤之后）
strip_footer_google.py 摘掉页脚里的谷歌入口与社交图标，换成自定义版权行（同上）
fix_gtm_src.py       修掉 GTM 脚本地址的拼接 bug（全站 6377 处 404，同上）
strip_share_icons.py 摘掉正文里的 hero 分享条（外部社交图标，同上）
fix_key_themes.py    补回 key-themes 的随机主题胶囊，修 KeyThemes 报错（同上）
search_autoload.py   检索页：隐藏 Show More 按钮，改成滚到底自动续页（同上）
slash_google_o.py    页眉 Google 字标改成 Gøøgle（改精灵图，一处改动全站生效，同上）
gen_rss.py           生成 RSS 订阅源/元数据/RSS 模式页，并给全站页眉加 RSS 入口（同上）
verify_rss.py --no-http 验收 RSS 订阅源与元数据（只跑静态层，HTTP 层需 serve.py 在跑）
verify_offline.py    闸门① 校验「阻断型谷歌依赖 = 0」
audit_embedded_urls.py 闸门② 校验「脚本加载期无内嵌外链」
scan_deadlinks.py    闸门③ 校验「站内真实死链 = 0」
```

末三步是**验收闸门**，任一非 0 即中止。四处顺序不能颠倒：

- `patch_vendor.py` 必须在 `build_site.py` **之后** —— 它处理的正是构建期从
  `www.gstatic.com` 下载下来的真实第三方脚本（cookie 通知条）。
- `patch_charts.py` 必须在 `localize_all.py` **之后** —— 它要往 `site/index.html`
  里插入真 loader 标签并摘掉 `charts/visualization` 空桩，而 `localize_all.py`
  会重写这些 HTML；顺序反过来，改动会被覆盖掉。
- `remove_creating_doodle.py`、`strip_footer_google.py`、`strip_share_icons.py`、
  `fix_key_themes.py`、`search_autoload.py`、`slash_google_o.py`、`gen_rss.py`
  必须在**所有会重写 HTML 的步骤之后** —— 它们是「改内容」型步骤，提前跑的话，
  删掉的导航入口/页脚/分享条会被写回来、补上的胶囊会被冲掉、检索页尾部的自动加载
  脚本会被抹掉（`slash_google_o.py` 改的是精灵图，同理会被重新下载的原始资源覆盖回去）。
  其中 `fix_key_themes.py` 与后两个都是幂等兜底：全量重建时它们会报「无需改动」
  而一个字都不写 —— 这正是「源头已经改对了」的证据。
- `gen_rss.py` 在上一组里还多一层约束：它生成 RSS 模式页时要从 `site/about/index.html`
  抽页眉骨架。若排在 `strip` / `footer` / `logo` 之前，抽到的就是**还没改好的**骨架，
  于是 RSS 页的页眉会和全站其它页面长得不一样。（`gen_rss.py` 内部也因此先
  `apply_nav()` 再渲染页面 —— 否则它自己生成的页面里反而不带 RSS 入口。）

各脚本也可单独运行（都支持从工程根目录直接调用）。

另有几个一次性 / 修复 / 独立验证类工具：

| 脚本 | 用途 |
|---|---|
| `_tools/repair_prefix.py` | 回滚早期版本写坏到 `games/`、`storybooks/` 的 `/site/<目录>/` 路径前缀（796 处，完全可逆） |
| `_tools/repair_logos.py` | 把游戏里写死的 `/logos/…` 绝对路径按「最长后缀匹配」改回本地 `assets/`（对齐 168 处，其余本地确实没有） |
| `_tools/shoot.py` | 无头浏览器批量截图，用于人工比对还原度 |
| `_tools/offline_probe.py` | **断网实测**：拦截代理 + DNS 黑洞，验证页面渲染期不发外网请求 |
| `_tools/cdp_probe.js` | **CDP 取证**：只统计页面目标自身发起的请求（含 `--games-all` 全量互动涂鸦） |
| `_tools/attribute_gstatic.py` | 归因实验：用对照页面判定某次外连是页面发的还是浏览器自身底噪 |
| `_tools/audit_embedded_urls.py` | 审计被引用脚本里「代码拼出来的」外链调用（HTML 属性扫不到） |
| `_tools/patch_charts.py` | 把 Google Charts 依赖集取回本地并改写内嵌地址，首页接回真 loader |
| `_tools/cdp_inspect.js` | 单页深度检查：JS 异常（带完整堆栈）+ 4xx/5xx 响应清单 + DOM 表达式 + 按选择器裁剪截图（先把视口撑到 16384，页脚这类超出首屏的元素也能截图） |
| `_tools/remove_creating_doodle.py` | 摘除 `/creating-a-doodle/` 路由与全站导航/页脚入口，收敛 `data-g-template_position` 序号 |
| `_tools/strip_footer_google.py` | 整块重写页脚：删掉指向谷歌的入口与「Follow us」社交图标，写入自定义版权行 |
| `_tools/fix_gtm_src.py` | 修掉 GTM 脚本地址的 `+i+dl` 拼接 bug —— 它让全站 6,377 个页面各请求一个不存在的文件名 |
| `_tools/strip_share_icons.py` | 摘掉正文里的 hero 分享条（`share-container`，含 LinkedIn / X / Facebook 三个外链）；按 `<div>` 深度匹配整块剔，避免非贪婪正则把 DOM 撕坏 |
| `_tools/fix_key_themes.py` | 补回 `key-themes` 模块底部缺失的 `Explore a Random Theme` 胶囊，修掉全站 6,375 个详情页的 `KeyThemes` 异常（该异常还会连带掐断其后四个模块的初始化） |
| `_tools/search_autoload.py` | 把检索页的 `Show More` 按钮改成滚到底自动加载（按钮保留在 DOM 但 `display:none`，另挂脚本替用户点它）；同时写 `_clone_search/page.html` 与产物，避免重建回退 |
| `_tools/slash_google_o.py` | 页眉 Google 字标 → Gøøgle：在精灵图的两个 `o` 上叠同色斜条（端头竖直切边）。支持 `--check` / `--revert`，gzip 压缩的抓取副本也能处理 |
| `_tools/verify_search_autoload.js` | 检索页自动加载的**功能验证**：全程不调用任何 `click()`，只滚动，断言「卡片数自己涨」「按钮 display=none」「JS 异常与 4xx 均为 0」；`--path` 可验证带筛选条件与空结果两条边界路径 |
| `_tools/rss_common.py` | **共享数据层**：条目归一化（分类拆分、年份推定、实体还原）、RSS/CSV/JSON 三种序列化、筛选语义 `select()`。被 `gen_rss.py`（构建期落盘）、`serve.py`（打包与筛选）、`verify_rss.py`（独立复算）三方共用 —— 语义只有一份，才可能被验出漂移 |
| `_tools/gen_rss.py` | 生成 `/rss.xml`、`/rss/latest.xml`、`doodles.csv/json`、`/rss/` 页面，并给全站页眉加 RSS 入口。页眉改动**就地取样缩进与分隔符**，支持逐字节 `--revert`；只在内容有变化时写盘（便于识别「本次真的生成了新东西」） |
| `_tools/verify_rss.py` | RSS 的五层验收（产物自洽 / 与源数据逐条交叉核对 / HTTP 实测 / 页眉入口 / 页面筛选对账），共 63 项。`--no-http` 只跑静态层（构建流水线里跑），`--page` 追加真浏览器页面侧验收 |
| `_tools/verify_rss_page.js` | 页面侧验收的执行体：真浏览器里逐组套用筛选，读回页面自己算出的条数与打包链接，以 `RESULT ` 前缀的 JSON 行交回 Python 侧比对 |

### Charts 依赖集是怎么定下来的

不靠猜：先放一个只加载真 loader 的探针页，只调用
`google.charts.load('current', {packages:['geochart']})`，用
`cdp_inspect.js --dump-net all` 把「页面目标」实际发出的请求全部记下来，
得到的就是上面那 11 个文件（版本号 `51` 与地图数据版本 `10` 都是确定值，
不存在运行时协商）。

```bash
python _tools/patch_charts.py --check        # 预演，只报告
python _tools/patch_charts.py                # 应用（缺文件才下载）
python _tools/patch_charts.py --refresh      # 强制重新核对上游
python _tools/patch_charts.py --skip-download # 完全离线（只用已落盘文件）
```

`_tools/charts_manifest.json` 同时记录**下载时的哈希**与**改写后的哈希**，
所以重建时：磁盘上是哪一种都直接复用，既不重复下载也不重复改写
（实测第 2 轮 "新下载 0 个，改动文件 0 个"，退出码 0）。

包内 23 处 `www.gstatic.com/.cn/charts` 全部改到 `/vendor/charts`；另外 4 处
属于 geochart 用不到的包（控件要的 jQuery CDN、geomap 要的 Google Maps API、
输入法工具），也一并改成 `/vendor/charts/_disabled/` 下的空桩 ——
语义上把外发彻底摘掉，万一有代码路径调用，得到的是安静降级而不是一次外网请求。

`jsapi_compiled_ui_module.js` 里仍留有 3 处 `www.google.com/jsapi`、`/ig/adde`、
`/ig/ifr`：它们是图表工具栏「导出 HTML / 发布到网页 / 添加到 iGoogle」
**生成给用户复制的示例代码**里的字符串，既不在加载路径上也不属于本项目依赖，
脚本会把它们单列为「良性残留」，只有加载路径上的残留才计失败。

第 ⑦ 步统一 loader 引用：把仍指向桩件 `/vendor/google-charts-loader.js` 的页面
改到真 loader。这一条也做成了闸门 —— 残留非 0 即中止，避免以后又出现
「同一个东西两种实现」。

## 8. 工程说明

### 硬链接，不复制

`site/img`、`site/fonts`、`site/vendor`、`site/games`、`site/storybooks` 与工程根下的
同名目录是**硬链接**（同一 inode），另有约 6,392 张涂鸦原图平铺在根目录、由
`site/img/` 收拢。所以站点目录不会让 2 GB 素材翻倍。

代价是：写入这些文件会**同时改到根目录的原始素材**。`localize_all.py` 因此显式跳过
这些目录（`SKIP_TOP`）—— 它们本来就是站内自洽的相对路径，不需要重写；而这些原始素材
没有第二份干净副本，一旦被写坏就回不去了。

### 十六个已经踩实并修掉的坑

1. **`<meta content>` 被当 URL 改写**
   早期版本的 URL 重写把 `content=` 也纳入属性列表，于是
   `<meta name="description" content="Learn more about…">` 变成
   `content="/static/Learn more about…"`，全站 8.9 万处 meta 描述被污染。
   现在 `content` 只对 `og:image` / `og:url` / `twitter:*` 这类**值确实是 URL** 的 meta
   做映射（见 `build_site.rewrite_text` 的 4.1 / 4.1b / 4.1c）。

2. **补丁脚本不幂等，越跑越臃肿**
   `patch_runtime.py` 早期的替换结果字符串**包含**被替换的原串，`if old in t` 每次
   都成立，每跑一次就给日期表达式多嵌一层守卫。现在改为**永远以抓取到的原始 bundle
   为输入**在内存里打补丁、带出现次数断言，因此重复执行结果完全一致（可对比 md5 验证）。

3. **死链扫描的属性正则把 `data-*` 的尾巴当成了真链接**
   `\bhref` 会在 `data-base-href="/doodle/"`、`data-g-cta_url="…"` 上也匹配 ——
   `-` 是非单词字符，所以 `\b` 成立。早期扫描因此把这些埋点/模板属性报成死链。
   现在改用 `(?<![-\w])(?:src|href|data-src|poster)`，并把确实属于 JS 模板/拼接的
   结果单独归档为「脚本上下文」，不再计入失败。

4. **截图流水线「越跑越卡」直到彻底卡死**
   早期 `shoot.py` 用 `subprocess.run(..., capture_output=True, timeout=90)`。
   `run()` 在超时后**仍要读完管道才返回**，而 Chrome 会派生多个 renderer 孙进程，
   它们继承了管道写端却不随直接子进程回收 —— 读取线程永久阻塞，`timeout` 形同虚设。
   实测一轮 10 页的批量任务卡了 20 分钟仍未结束，并留下 12 个 Chrome 进程占着 profile 目录。
   另外两个连锁问题：`terminate()` 只杀顶层进程，孙进程继续占着 `--user-data-dir` 的锁，
   下一页启动时会「把 URL 转交给残留实例后立即退出」（表现为 5s 内无产物、也不报错）；
   而共享同一 profile 会让这个现象在多页之间串起来。

   现在的做法：
   - `Popen` + `stdout/stderr=DEVNULL`，从根上避开管道阻塞；
   - **每页一个独立 profile 目录**（`_tools/.chrome-profiles/<页名>`），彻底消除锁竞争；
   - 超时后 `taskkill /PID <pid> /T /F` **杀整棵进程树**；
   - 成功判据是「产物文件出现且大小稳定」，而不是子进程退出码
     （动画页面常常「图已落盘、进程不退出」）；
   - 失败自动重试一次。

   修完单页约 12 秒，整轮 10 页 2 分钟跑完。**不要再用 `shutil.rmtree` 去清 profile
   目录** —— 本机沙箱会用「移到回收站」接管删除，而它对被占用的 Chrome 目录会
   trash 失败并只删掉一部分，留下残缺 profile，反而让下一页启动即失败。

5. **重写规则的「本地映射表」把「换桩件」规则挤成了死代码**
   这是最隐蔽的一个，也是**唯一一处静态扫描抓不到的加载期外链**。

   `build_site.py` 的 URL 重写按顺序匹配：规则 1 查本地映射表（`LOCAL_MAP`），
   命中就直接返回下载后的本地路径；规则 4 才轮到「把 `cookienotificationbar.min.js`
   换成本地桩件」。问题是 `www.gstatic.com/glue/...` 被**成功下载**到了
   `site/vendor/glue/...`，于是 cookie 通知条命中的是规则 1 —— 返回**真实**脚本，
   规则 4 那条桩件分支永远不会被走到（`charts/loader.js` 同理）。

   而真实脚本在**构造函数里无条件**执行：
   `fetch("https://www.gstatic.com" + d + c)`，其中 `d` 是
   `/glue/cookienotificationbar/config/{2a,2b}.json`。这句话是**代码拼出来的**，
   属性扫描（`verify_offline.py`）完全看不见 —— 于是 6,378 个页面在加载时都在
   偷偷请求 gstatic，而所有静态闸门都是绿的。

   **怎么发现的**：不是靠读代码，是靠「断网实测 + 按主机计数对比空白页基线」，
   发现 10 个页面里有 8 个比基线多出一次 `www.gstatic.com` 连接；
   再做对照实验（一个只放文字的页面 vs 一个只加载该脚本的页面，各跑 3 次），
   命中数 3 : 6，确认是页面发的。

   **修法**：没有换成桩件（会丢掉原组件行为），而是保留真实脚本、只把配置源本地化：
   `fetch("/vendor"+d+c)`，并把真实的 `2a.json` / `2b.json` 落盘（实测两份都是
   `{ "required": false }`，即真站在此配置下同样不展示通知条，视觉结果一致）。
   见 `_tools/patch_vendor.py`。

   **留给后续的教训**：`LOCAL_MAP` 命中优先于「要不要换成桩件」的决策，
   意味着**一旦某个资源被成功下载，针对它的桩件规则就自动失效**。改重写规则时
   必须检查这个遮蔽关系。

6. **代理观测会把「浏览器的请求」误算成「页面的请求」**
   拦截代理是最直观的断网验证手段，但它记录的是**本机所有出口**。Chrome 自己会
   周期性地访问 `clients2.google.com`（校时）、`update.googleapis.com`（组件）、
   `accounts.google.com`（账号一致性检查）。按主机名白名单过滤 → 会放走真请求；
   按「相对 about:blank 的增量」去减 → 又会因为页面存活时长不同而误判
   （页面跑得久，浏览器后台就多打了几次卡）。

   **正解是用 CDP**：`Network.requestWillBeSent` 只在**页面目标**上触发，浏览器
   进程自己的请求根本不会出现在这里。这是与时间无关、精确到「谁发起」的判据。
   Node 22 内置全局 `WebSocket`，零依赖即可实现（见 `_tools/cdp_probe.js`）。
   交叉验证：`accounts.google.com` 在 `site/` 的 7,001 个文本文件里出现 **0 次**，
   而 CDP 探测里也一次都没出现 —— 两条独立证据吻合。

7. **截图流水线撑高视口，会顺手触发页面自己的滚动逻辑**
   `cdp_inspect.js --clip` 的取巧办法是先把视口撑到 Chrome 上限 16384，让视口坐标
   与文档坐标重合，页脚这类超出首屏的元素才截得准（原因见该文件里的注释）。
   代价是：撑高视口会派发一次 `resize`，而**任何对视口敏感的逻辑都会跟着跑一次**。

   检索页的自动加载就是这样被误触发的 —— 一撑高，整个列表都落进了「视口」，
   脚本立刻预取下一页，截图于是拍成了 32 张卡片；更别扭的是 clip 的矩形是在撑高
   **之前**量好的，量的框和最终的像素对不上，成品看起来像「按钮没了、卡片变多了」
   这种自相矛盾的结果。

   排查时先怀疑过探针元素的定位方式（绝对定位 vs 流内包裹），换了两次都不对，
   最后是「本地请求数是 16 张、截图里却有 32 张」这个数量差把真相指了出来 ——
   **数字对不上时先怀疑被测对象被改了，而不是先怀疑测量工具**。

   **现在的做法**：截动态页面时先用 `--eval` 把状态冻住再截。例如摘掉分页按钮的
   `.active` 类，`hasMore()` 立刻变 false，自动加载失去触发条件，
   拍出来的就是稳定的第 1 页。

8. **往页眉里插一项，缩进与分隔符都不能硬编码**
   给全站 6,379 个页眉加 RSS 入口时，插入前要先在 `</li>` 之后补回一项之间原本的
   分隔文本（换行 + 空行 + 缩进），而这个文本**每个页面都不一样**：首页与详情页是
   两套模板生成的，详情页的页眉还被 `gen_doodle_pages.py` 重排过，`<li>` 的缩进
   首页是 16 空格、详情页是 14 空格。

   - 第一版把分隔符硬编码成 `"\n            \n"`（12 空格）。本次插入看着完全正常，
     但 `--revert` 回滚后详情页会**多出多余空行** —— 说明还原不是逐字节的，
     而这种脚本必须做到「插进去再撤销 = 原样」，否则以后没法安全地重跑。
   - 第二版改成从页面里就地取样，但取样区间写成了「上一个 `</li>` 到本项的 `<li>`」，
     把**本项自己的缩进也算进了分隔符**。于是插入时分隔符 + 新项缩进叠了两份，
     实测新 `<li>` 变成 28 空格，比相邻项深一倍，页面看着像是莫名其妙多缩进了一级。

   最终版把取样区间收敛到**本项行首为止**（本项缩进不算分隔符），并单独按缩进量
   生成新项。验收方式是拿 48 个页面抽查：补丁可逐字节回滚、重复应用无任何改动。

   **教训**：凡是「在原文本里插一段」的改动，缩进、换行、分隔符一律**就地取样**，
   不要照抄自己在某一个页面上看到的字面量 —— 多模板生成的站点里，同一处结构在不同
   页面上的空白字符很可能不同，而这种差异在「插入」时看不见，只在「撤销」时暴露。

9. **检索接口的月份/日期筛选曾经是「恒真」的** —— 复刻原站接口时写了这么一句：

   ```python
   if month and str(mo) not in (month, "%02d" % mo):
       continue
   ```

   看着像是在兼容「前端可能传 `5` 也可能传 `05`」，但 `mo >= 10` 时
   `"%02d" % mo` 就等于 `str(mo)` 本身，等号**恒成立** —— 于是「筛选 10 月」
   会把 11、12 月一起放进来，日期同理。实测筛 `month=10&day=31` 返回 19 条，
   摊开一看全是 11、12 月的涂鸦；而界面上完全看不出异常，因为条数看着是合理的。

   **正确写法**是把**参数**去掉前导零再比，而不是把实际值补齐两位：

   ```python
   def _same_number(value, wanted):
       w = (wanted or "").lstrip("0")
       return str(value) == (w or "0")
   ```

   值得一提：浏览器侧那份实现（`search-api-shim.js`）从一开始就是对的 ——
   它写的是 `('0' + d[1]) !== month`，给实际值**加前缀**，12 会变成 `'012'`，
   不等，所以能正确排除。**同一个语义、两处实现，一处对一处错** ——
   这正是为什么 `verify_deploy.py` 要做三方对账。而且要注意：
   两处独立实现如果写成同一个错误（复算脚本第一版就是照抄了这个 bug），
   对账会「一致地错」，所以还必须拿 `serve.py` 的真实接口再对一次。

   **教训**：这类「恒真 / 恒假」的条件不报错、不崩、数字看着也像样，
   只能靠**构造能区分真假的用例**抓出来。如果验收用的筛选条件恰好都是个位数月份，
   这个 bug 会一直躺在那里 —— 测试用例要特意覆盖边界值（两位数月份、两位数日期、
   以及一个确定不该有结果的组合）。

10. **`&` 没编码 + bundle 里两处裸读 `.tagName` = 检索页整页空白**
    详情页的「This Doodle's Key Themes」把标签渲染成
    `<a href="/search/?topic_tags={标签}">`，其中 `&` 只做了 HTML 转义（`&amp;`），
    没有做**百分号编码**。于是「Plants & Flowers」写成
    `/search/?topic_tags=Plants &amp; Flowers`，浏览器解码后查询串裂成两个参数：
    `topic_tags=Plants ` 和 ` Flowers`。全站 13 处（26 个出现点，含 CTA 的 data-links）。

    第二个参数名在筛选表单里不存在，而原 bundle 的 `loadParams()` 里写着：

    ```js
    const e = this.filterElement.querySelector(`[name="${t[0]}"]`);
    if (e.tagName == a.TAG_INPUT) { ... }        // ← e 可能是 null
    e && e.tagName == a.TAG_SELECT && ( ... )    // ← 下一行却又判了空
    ```

    同一写法在 `SearchDoodleFilter` 的 `removeCheck` 循环里还有第二处。
    空指针一抛，`handleInitialLoad` 里排在后面的取数与渲染全部不执行 ——
    **页面完全正常、筛选控件都在，就是结果列表一片空白**。

    实测能触发的输入（都是接口参数但不是表单字段）：`?page=2`、`?limit=6`、
    `?order_by=date`、`?nationality_tags=Germany`。

    **修法分两层**：
    - 数据层：`_tools/fix_search_filters.py` 把那 3 个含标点的标签链接改写成
      面板的规范取值（`football%20soccer` / `plants%20flowers` / `television%20film`），
      从源头消灭坏 URL；
    - 运行层：新增 `site/vendor/search-filter-guard.js`，给 `.search-doodle__filter`
      这一个元素装自己的 `querySelector` —— 查 `[name=...]` 落空时返回一个**游离的
      `<input>`** 而不是 null。游离节点不插入文档，所以既不会被表单序列化进 URL、
      也不会进 `listValues`，两处调用点都等价于「这个参数不认识，忽略」。
      **不改 vendored 的 bundle**，只在外面加自己的一层。

    **教训**：`e && e.tagName == X` 这种「上一行裸读、下一行判空」的写法是典型的
    半截防御，`grep` 一遍 `\.tagName` 就能看出来。以及：属性值里的 `&` 必须是
    `%26` 而不是 `&amp;` —— HTML 转义解决不了查询串的歧义。

11. **筛选面板的词表和数据的词表，从来没有对齐过**（本轮最严重的一个）
    检索页 `/search/` 的筛选面板是**原站服务端渲染好的 HTML**，每个选项的 `value`
    来自原站 API 的词表；而详情页的主题 chip 和 `rss/doodles.json` 里的标记，来自
    本地这套抓取/生成的词表。两套词表互相不认识，而且**全都不报错**。

    发现的路径是先做了一次「面板取值集合 vs 数据取值集合」的差集，然后构造
    「带参数直接进页面」的用例去实测 —— 结果是：

    | 维度 | 面板里的取值 | 数据里的取值 | 后果 |
    |---|---|---|---|
    | `form_tags` | `animated gif` / `still image` / `interactive game` | `animated` / `still` / `game` | **整个筛选维度全废**，5 个选项里 3 个点进去 0 条 |
    | `topic_tags` | 150 个（去标点 slug 形式） | 95 个（原始标签） | 9 个标签点了不筛选，涉及 4,765 处链接 |
    | `color_tags` | `gray` | `grey` | 该选项 0 条 |

    为什么「点了不筛选」而不是「点了没结果」：`loadParams()` 只做 `toLowerCase()`
    然后按 `[name="topic_tags"][value=...]` 去表单里找复选框，**找不到就不勾选**；
    而实际取数用的是表单状态，不是 URL 参数。所以 URL 里那个筛选条件**根本没进入
    查询** —— 页面显示的是**未筛选的最新 16 条**。实测 `?topic_tags=Other`
    （站内 2,848 处链接）和 `?topic_tags=Space` 的返回内容一模一样。

    三个子问题分别修：
    - **同义不同名** → 把面板里的取值改成数据词表（`animated gif` → `animated`、
      `gray` → `grey`…），显示文本不动；
    - **面板缺选项** → 补 6 个（`other` / `birthday` / `new year` / `easter` /
      `diwali` / `badminton`），按字母序插进对应分组，条目结构整块克隆、缩进就地取样；
    - **panel 用 slug、数据用原标签** → 把标签匹配的归一化统一改成
      **「非字母数字折叠成空格」**（`serve.py` 的 `_norm_key`、
      `verify_deploy.py` 的 `norm_tag`、`shim` 的 `normTag` 三处同步），
      这样 `football soccer` 与 `Football/Soccer`、`plants flowers` 与
      `Plants & Flowers` 自然相等。**只对标签类字段生效** —— 标题子串匹配
      （`title_like`）仍走「只折叠空白」，否则标题里的 `'` 和 `/` 会被折叠掉。

    **教训**：凡是「A 系统生成的取值要去 B 系统里查」，就要有一条断言把两个集合对起来。
    这里的检查现在固化在 `fix_search_filters.py` 的回归检查里（`--root _deploy` 可指向
    部署包），并由 `verify_deploy.py` 作为发布门禁调用 —— **判据只写一份**，
    免得「修的时候一套、验的时候另一套」而互相放过。

12. **给 `/vendor/*` 配了 7 天缓存，等于让自己的离线化层改不动**
    `/vendor/` 下同时住着两拨文件：原站的第三方库（不会变），和我们自己的离线化层
    （`recaptcha-stub.js`、`search-api-shim.js`、`search-filter-guard.js` —— 与
    `rss/doodles.json` 的数据格式和检索语义**强耦合**）。而它们的文件名都不带内容
    哈希，浏览器无从分辨新旧。

    这个坑是**在自己的验收流程里踩到的**：改完 shim 后重建、重启，磁盘上的文件和
    源站返回的文件都确认是新的，但「带话题直接进页面」的前三个用例全部返回 0 条 ——
    因为浏览器把上一版 shim 缓存住了，拿**旧逻辑配新数据**跑。而在本地 Python 静态
    服务器上却一切正常：`http.server` 不认 `_headers`，每次都给新文件。
    这一差异本身就说明问题出在「谁在遵守缓存指令」上。

    现在 `/vendor/*` 改成 `public, max-age=0, must-revalidate`（命中 304 几乎没成本，
    换来「上线即生效」），并在 `verify_deploy.py` 里加了门禁：
    `/vendor/*` 的 `max-age` 超过 1 小时就判失败。

    **连带修掉的工具问题**：`verify_deploy_page.js` 原本复用固定的
    `--user-data-dir`，会把上一次运行的缓存一起带进来。现在每次运行用一次性配置目录
    （`<root>/.chrome-profile-deploy-<pid>`，结束即删），等于模拟首次访问，结果可复现。

    **教训**：验收脚本必须和「真实用户的缓存状态」解耦，否则它会同时扮演
    「发现问题的工具」和「掩盖问题的同谋」。要测缓存行为，就单独用 HTTP 头去测。

13. **无头浏览器被中断后留在原地，下一次运行会连上这个僵尸实例**
    `verify_deploy_page.js` 固定用调试端口 19227。上一次运行被中断时，无头 Chrome
    并不会随之退出，它仍占着 19227 和一个「停在旧页面」的标签页；下一次运行
    `waitDevtools()` 探测到这个端口有响应，就直接**连上那个僵尸实例**，
    于是 `Page.navigate` 打不到目标服务器 —— 现象是**脚本挂住、一行输出都没有**，
    而不是报错。实测卡了 23 分钟。

    排查方式：直接问 `http://127.0.0.1:19227/json/list`，看到一个
    `page` 停在 `http://127.0.0.1:8788/search/`（早就该关掉的那次运行），真相即出。
    清掉它用的是 CDP 的 `Browser.close`（只连调试端口，因此只会命中自己启动的
    无头实例，**不会碰用户自己的浏览器**）。

    现在 `verify_deploy_page.js` 启动前会 `pickFreePort()` 从 19227 往上找一个
    空闲端口并把选中的端口打出来；同时每次用独立 profile，双保险。

    **教训**：凡是「固定端口 + 可能被中断」的自动化，都要在启动前探测端口占用。
    僵尸实例最坏的地方是它**长得像正常运行**（端口有响应），失败方式却是静默挂起。

14. **没有 `404.html` 时，Cloudflare Pages 用 200 返回首页（软 404）**
    上线后反向检查发现 `/creating-a-doodle/` 和随便编的 `/__nonexistent__/`
    **都返回 200**，内容竟然是**根 `index.html` 那份 HTML**。
    Pages 的行为是：项目里没有 `404.html` 就把根首页当兜底，状态码照给 200。

    这对本项目有两个具体坏处：

    - `/creating-a-doodle/` 是**故意移除**的营销页（`remove_creating_doodle.py` 专门
      摘掉它和它的导航入口）。软 404 等于那条路由看起来"还在"，验收里没法判定；
    - 大量乱七八糟的地址都会被当成正常页，虽然 `robots.txt` 已经全站禁索引，
      但那是另一道闸门，不该互相顶替。

    **修法**：构建期生成一个 `_deploy/404.html`（与免责声明页同一套视觉语言，
    深色模式适配），并把它加进构建脚本与验收脚本的必需文件清单。
    加完之后两个路径都返回**真 404**。

    **教训**：「兜底页」不是可选项。任何静态托管都要显式放一个自己控制的 404，
    否则平台给的默认行为很可能是「静默地假装成功」—— 而这类问题在只看状态码 200
    的验收里永远不会暴露。

15. **zone 的 Browser Cache TTL 会把你写的 `max-age` 抬上去**
    上线实测：`_headers` 里 `/vendor/*` 写的是 `max-age=0, must-revalidate`，
    而线上拿到的是 **`max-age=14400, must-revalidate`**。

    不是配置没生效，是 **zone 的 Browser Cache TTL**（`zvi.onl` 上为 14400 秒）
    把**低于它的 `max-age` 抬高了**。实测出的作用范围很精确：

    | 路径 | 源站写 | 线上实际 | 原因 |
    |---|---|---|---|
    | `/vendor/*`（.js） | 0 | 14400 | 小于 zone 值 → 抬高 |
    | `/img/*`（.gif） | 31536000 | 原样 | 大于 zone 值 → 不动 |
    | HTML | 0 | 原样 | 不被边缘缓存（`DYNAMIC`），改不到 |
    | `.xml` / `.json` | 1800 / 86400 | 原样 | 扩展名不在默认缓存列表，同上 |

    **教训**：源站的头只是"意图"，中间那一层可能改写它 —— 所以缓存相关的断言
    必须**在真实边缘上量**（`verify_online.py`），不能只校验自己写的配置文件。
    另外：想真正做到「改完即刻生效」，要在 zone 设置里把 Browser Cache TTL 改成
    "Respect Existing Headers"；那是 zone 级设置，会波及其它站点，不该擅自动手。
    还有一个常见误解：**Cloudflare 的缓存键默认忽略查询串**，
    所以 `xxx.js?v=<hash>` 破不了缓存，要换**路径**（文件名带哈希）才行。

16. **订阅源里写的是本地地址，一上线所有链接都指向订阅者自己的电脑**
    `rss_common.py` 里的 `DEFAULT_BASE` 原本是 `http://127.0.0.1:8765`，
    `gen_rss.py` 调用 `feed_xml()` 时不传 base，于是这个本地地址被**烧进了产物**：
    线上 `rss.xml` 的 **6,375 条 `<link>` 和 `<enclosure>` 全是 `127.0.0.1:8765`**，
    订阅者点任何一条都指向他自己的机器 —— 链接、图片下载、`<guid>` 全废。

    为什么本地一直没发现：`serve.py` 的 `_send_feed()` 会在响应时按请求头的 Host
    把基址**改写**成实际到达的主机（第 440 行），所以本地无论用哪个端口都拿得到
    正确地址。**纯静态部署没有那一步**，烧进去的本地地址就原样发出了。

    **修法**：`DEFAULT_BASE` 改成对外域名（`https://google.zvi.onl`，可用
    `ZVI_PUBLIC_BASE` 覆盖），并把这 19,127 处存量替换掉。加了两个闸门：
    `verify_deploy.py` 静态检查部署包里的 `rss.xml`（不许有本地地址、必须是 https），
    `verify_online.py` 线上再查一遍（不许有 `127.0.0.1`、所有绝对地址必须以公开基址开头）。

    **教训**：凡是"会被下载走的产物"（订阅源、清单文件、导出的 CSV），
    里面的地址只能是**对外可达**的。判断标准很简单：这个东西离开服务器之后，
    里面的链接还对不对？另外要警惕一类"开发期看不出来"的掩盖机制 ——
    服务端在响应时做的改写，恰恰会盖住产物本身的问题。

### 目录里不参与交付的部分

| 路径 | 说明 |
|---|---|
| `_google_clone/` `_clone_search/` | 原站页面与运行时 bundle 的**原始抓取件**，是重建的基线，**请勿修改** |
| `_tools/` | 构建与校验脚本 |
| `_tools/.chrome-profiles/` | 截图用的一次性浏览器 profile（每页一个，约 65 MB），可随时删除 |
| `_tools/.chrome-profiles-offline/` `_tools/.chrome-profile-cdp/` `_tools/.chrome-profile-inspect/` `_tools/.chrome-profile-autoload/` | 断网探测 / CDP 探测 / 单页检查 / 自动加载验证用的浏览器 profile，可随时删除 |
| `_tools/probe_pages/` | 归因实验用的两个极简对照页面（裸页面 / 只加载 cookie 条） |
| `_shots/` | 人工比对用的截图（10 张关键路由 + 首页地图区的裁剪对比图 `_home_maparea*.png`） |
| `_shots_after/` | 本轮改造的前后对比图：页眉字标 `_logo_before/_logo_after.png`、Ø 六种画法比对 `_logo_variants.png`、检索页按钮 `_btn_compare.png` |
| `_shots_offline/` | 断网实测时同时产出的截图（用于确认断网下渲染不受影响） |
| `_shots_rss/` | RSS 模式页的四块实拍：`hero.png`（统计 + 订阅地址）、`download.png`（5 张下载卡）、`browse.png`（筛选 + 订阅条目）、`nav.png`（页眉第三项 RSS 入口） |
| `index.html` `doodle.html` `library.html` `styles.css` `doodles_data.js` `iframe_map.js` `涂鸦目录.csv` | 工程初期的一套**自建简化版**页面，与本次「原页克隆」路线不同，保留作为数据源（`doodles_data.js`、`iframe_map.js` 仍被构建脚本使用），但**不是**交付物 |
| `fonts/` `games/` `storybooks/` `vendor/` + 根目录图片 | 原始素材，被 `site/` 硬链接引用 |
| `_deploy/` | **部署产物**（约 1.99 GiB），由 `_tools/build_deploy.py` 生成，不进版本库 |
| `_deploy_src/` | 部署专用文件的**源**（`_redirects`、`_headers`、`robots.txt`、`search-api-shim.js`），会被铺进 `_deploy/` |

## 9. 上线部署

**已上线**：<https://google.zvi.onl>（Cloudflare Pages 项目 `doodles-mirror`，
2026-10-01 首次部署 14,307 个文件 / 461 秒；后续增量更新只传变化的文件，
实测 1 个文件 3 秒）。

`site/` 本身可以丢给任何静态服务器，但有几项能力只有服务端能提供（检索接口、
`/logos/**` 兜底、图片包实时打包）。要在**纯静态托管**上补齐它们，用
`_tools/build_deploy.py` 生成一个专门的部署包：

```bash
python _tools/build_deploy.py        # → _deploy/（约 25 秒）
python _tools/verify_deploy.py       # 六层验收，含三方语义对账
python _tools/verify_online.py       # 上线后：对着域名跑（边缘能力只有它能验）
```

`_deploy/` 相对 `site/` 的三处差异：

- **少了 123 个无引用资源**（273.4 MB）—— 成因见第 6 节第 7 条。排除后单文件最大
  从 33.67 MB 降到 5.73 MB，**Cloudflare Pages 的 25 MiB 硬限制自动满足**，
  那张图既不用压缩也不用挪到别处。因为是「引用方不在站内」而非「内容重复」，
  排除它是零风险的。
- **多了几个部署专用文件**：`_redirects`（`/logos/*` → 1×1 透明 PNG）、
  `_headers`（缓存策略 + 安全头）、`robots.txt`、`/disclaimer/`。
  这些的**源**在 `_deploy_src/` 里，`site/` 完全不受影响。
- **检索页多挂了一个 shim**：`vendor/search-api-shim.js`。

（另有 `site/vendor/search-filter-guard.js` 挂在**两套都在用**的检索页上 ——
它修的是原 bundle 的空指针，属于「离线克隆」本身的一部分，不是部署专用，
所以列在 `site/` 里而不是 `_deploy_src/`。三处构建期的改动 —— 修坏链接、
补筛选面板选项、统一标签匹配归一化 —— 由 `_tools/fix_search_filters.py` 完成，
同样落在 `site/`，`_deploy/` 镜像时自动继承。）

`verify_deploy.py` 现在是六层、**19 项**检查，其中两项是这轮新加的发布门禁：

- **筛选词表对齐**：把 `_deploy/` 交给 `fix_search_filters.py --check --root _deploy`
  跑同一份回归检查 —— 面板取值必须覆盖数据词表，全站 chip 链接取值必须落在面板内。
  判据只写一份，避免「修的时候一套、验的时候另一套」。
- **带参数直接进页面**：8 个用例真导航（URL → 填表 → 取数 → 渲染），
  并把渲染出的卡片与同一条件问 API 的结果**逐条对齐**。只问 API 是不够的 ——
  URL 参数要先被 bundle 填回筛选表单，这条链路断过两次且两次都是静默的。

### 检索接口为什么放进浏览器里

原站 `/search/` 靠服务端接口 `/live/api/v1/doodles` 取数据。纯静态下它 404，
前端拿到一坨 HTML 去 `JSON.parse` 抛异常，结果是**筛选控件都在、结果列表空白** ——
不报错，就是安静地坏掉。补这个洞有两条路：

- **在边缘跑 Pages Function** —— 免费档单次调用只有 **10 ms CPU**，而
  `JSON.parse` 3.2 MB 数据本身就要 20–40 ms，必然踩线；
- **把接口语义搬到浏览器里**（采用）—— shim patch `window.fetch`，就地用构建期
  已落盘的 `rss/doodles.json` 算出同样的响应。原站 bundle 一行都不用改，
  服务端 CPU 开销为零，而且 `site/` 从此在**任何**静态托管上都能跑检索
  （GitHub Pages、对象存储、甚至本地 `file://`）。

代价是筛选语义现在有了三处实现（`serve.py` 的 `query_doodles()`、
`rss_common.select()`、以及 shim）。`verify_deploy.py` 的**三方对账**就是为此存在的 ——
10 组筛选条件下，浏览器 shim、Python 独立复算、`serve.py` 真实接口的条数与排序
必须完全一致。第 8 节第 9 条那个「月份筛选恒真」的 bug 就是被它抓出来的。

同一条归一化规则（`norm_key` / `norm_tag` / `normTag`）也在这三处各有一份，
且被 `verify_deploy.py` 的「带参数直接进页面」用例覆盖 —— 第 8 节第 11 条
那套词表错配就是被它抓出来的。

完整的部署步骤、Token 最小权限、自定义域名、R2 图片包、故障排查，
见 **[DEPLOY.md](DEPLOY.md)**。

### 已知的性能短板与可选方案

首屏图片字节偏大（检索页 **7.04 MB**、首页 8.63 MB —— 卡片直接用原图、
无 `srcset`、无懒加载）。实测过的解决方案、压缩比数据、
以及 Cloudflare Pages **20,000 文件上限**带来的取舍，见
**[IMAGE-PERF.md](IMAGE-PERF.md)**。要点：不加付费档、纯构建期改动，
就能把首屏图片从 7.04 MB 降到约 0.7 MB。
