/* ============================================================================
 * 检索接口的前端复刻（search API shim）
 * ----------------------------------------------------------------------------
 * 【解决什么问题】
 * 原站检索页 /search/ 靠一个服务端接口 /live/api/v1/doodles 取数据。
 * 本地跑 serve.py 时接口在；一旦把 site/ 丢给纯静态托管（Cloudflare Pages、
 * 对象存储、甚至 file://），接口就 404，前端拿到一坨 HTML 去 JSON.parse，
 * 结果是「筛选控件在、结果列表空白」—— 检索页彻底失效。
 *
 * 【做法】
 * 本脚本把接口语义原样搬到浏览器里：patch window.fetch，凡是指向
 * /live/api/v1/doodles 的请求，就地用 /rss/doodles.json（构建期已落盘的全量
 * 数据，6,375 条）算出同样的响应，构造一个 Response 还给调用方。
 * 原站的 bundle 一行都不用改。
 *
 * 【语义只有一份】
 * 这里的筛选/排序/分页规则，与 serve.py 的 query_doodles()、
 * _tools/rss_common.py 的 select() 是同一套语义的三处实现。
 * 任何一处改动都要同步另外两处，并用 _tools/verify_deploy.py 的
 * 「三方对账」把结果对回来 —— 否则线上和线下的检索结果会悄悄分叉。
 *
 * 【为什么先试真接口】
 * 本文件同时会进 site/ 的部署包。若服务端接口可用（本地 serve.py、或将来
 * 真接了后端），必须优先用它，不能被本地数据顶掉；只有接口确实不可用时才
 * 降级。所以第一次调用会先探一次，之后记住结论。
 *
 * 【CPU 成本】
 * 零。全部计算发生在访客浏览器里，服务端只需要把这一个 JS 文件当静态资源发出去。
 * 这也是它相对「写一个 Pages Function 在边缘解析 3.2 MB JSON」的决定性优势：
 * Cloudflare 免费档单次调用只有 10ms CPU，JSON.parse 这个量级的数据本身就要
 * 吃掉 20-40ms，属于必然踩线。放到浏览器侧就没有这个天花板。
 * ========================================================================== */
(function () {
  'use strict';

  // 已经装过就不要再装（部署包里被重复注入时兜底）
  if (window.__zviSearchApiShim) return;
  window.__zviSearchApiShim = true;

  var API_RE = /\/live\/api\/v1\/doodles(?:[?#]|$)/;
  var DATA_URL = '/rss/doodles.json';

  // 原站 bundle 用的是 window.fetch 本尊；先抓住，避免被自己递归。
  var origFetch = window.fetch ? window.fetch.bind(window) : null;
  if (!origFetch) return;

  var dataPromise = null;   // Promise<rows[]>
  var apiUsable = null;     // null=未探测 / true=服务端接口可用 / false=已确认不可用

  /* ---------------------------------------------------------------- 数据 */

  function loadData() {
    if (!dataPromise) {
      dataPromise = origFetch(DATA_URL).then(function (r) {
        if (!r.ok) throw new Error('数据文件加载失败 ' + r.status);
        return r.json();
      }).then(function (j) {
        if (j && Object.prototype.toString.call(j.items) === '[object Array]') return j.items;
        if (Object.prototype.toString.call(j) === '[object Array]') return j;
        throw new Error('数据文件结构不符预期');
      }).catch(function (e) {
        dataPromise = null;   // 允许下次重试
        throw e;
      });
    }
    return dataPromise;
  }

  // 尽早开始加载，别等第一个接口调用才动手
  loadData().catch(function () { /* 失败交给真正的调用点报错 */ });

  /* ------------------------------------------------------------ 筛选语义 */

  // 对应 Python 侧 re.sub(r'[^a-z0-9]+', ' ', s.lower()).strip()
  //
  // 【为什么不是只折叠空白】
  // 检索页筛选面板里每个选项的 value 都是「非字母数字→空格」的 slug
  // （football soccer / plants flowers / children s day），而详情页主题 chip 与
  // rss/doodles.json 用的是原始标签（Football/Soccer / Plants & Flowers）。
  // 不折叠标点的话，从 chip 点进检索页会**静默筛不到东西** —— 不报错，
  // 只是把未筛选的全集当成结果显示出来。
  // 本函数只用于标签类字段；标题子串匹配另有处理（见 queryDoodles）。
  function normTag(s) {
    if (s === null || s === undefined) return '';
    return String(s).toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  }

  // 把若干参数的（可重复、逗号分隔）取值摊平成一个「命中集合」
  function collect(sp, keys) {
    var set = Object.create(null);
    var any = false;
    for (var i = 0; i < keys.length; i++) {
      var vals = sp.getAll(keys[i]);
      for (var j = 0; j < vals.length; j++) {
        var parts = String(vals[j]).split(',');
        for (var k = 0; k < parts.length; k++) {
          var t = normTag(parts[k]);
          if (t) { set[t] = 1; any = true; }
        }
      }
    }
    return any ? set : null;
  }

  // 对应 serve.py 的 _matches()
  function matches(field, wanted, rec) {
    if (!wanted) return true;
    var have;
    if (field === 'color') {
      have = [normTag(rec.color)];
    } else if (field === 'topic') {
      have = (rec.topics || []).map(normTag);
    } else if (field === 'form') {
      have = [normTag(rec.format)];
    } else if (field === 'region') {
      have = (rec.regions || []).map(normTag);
    } else {
      have = [];
    }
    for (var i = 0; i < have.length; i++) {
      if (have[i] && wanted[have[i]]) return true;
    }
    return false;
  }

  // 严格整数解析：Python 的 int('16abc') 会抛异常，parseInt 却会给 16，
  // 这种差异会让分页在异常输入下和线下对不上，所以这里自己判。
  function intParam(raw, dflt) {
    if (raw === null || raw === undefined) return dflt;
    var s = String(raw).trim();
    if (!/^-?\d+$/.test(s)) return dflt;
    var n = parseInt(s, 10);
    return isNaN(n) ? dflt : n;
  }

  function cmpDate(a, b) {
    for (var i = 0; i < 3; i++) {
      var x = a[i], y = b[i];
      if (x !== y) return x < y ? -1 : 1;
    }
    return 0;
  }

  function sortKeyOf(rec) {
    return String(rec.title || rec.slug || '').toLowerCase();
  }

  function cmpStr(a, b) {
    return a < b ? -1 : (a > b ? 1 : 0);
  }

  /* ------------------------------------------------------------ 查询复刻 */

  function queryDoodles(sp) {
    // 标题匹配保持「只去首尾空白 + 转小写」，与 serve.py 的 query_doodles() 一致。
    // 不能套 normTag —— 那会把标题里的 ' 和 / 也折叠掉，子串就找不到了。
    var titleLike = String((sp.get('title_like') || sp.get('q') || '')).trim().toLowerCase();

    var colors = collect(sp, ['color_tags']);
    var topics = collect(sp, ['topic_tags']);
    var forms = collect(sp, ['form_tags']);
    var regions = collect(sp, ['nationality_tags']);

    // art_style_tags / sort_tags / order_by 在本地数据里没有对应维度。
    // 与 serve.py 一致：勾了也不缩小结果集，而不是返回空 —— 后者看起来像坏了。
    var year = (sp.get('date_like_year') || '').trim();
    var month = (sp.get('date_like_month') || '').trim();
    var day = (sp.get('date_like_day') || '').trim();

    var page = Math.max(1, intParam(sp.get('page'), 1));
    var limit = intParam(sp.get('limit'), 16);
    limit = Math.max(1, Math.min(limit, 200));

    var direction = ((sp.get('sort_direction') || 'desc') || 'desc').toLowerCase();

    return function (all) {
      var rows = [];
      for (var i = 0; i < all.length; i++) {
        var r = all[i];
        if (titleLike) {
          var t = String(r.title || '').toLowerCase();
          var s = String(r.slug || '').toLowerCase();
          if (t.indexOf(titleLike) < 0 && s.indexOf(titleLike) < 0) continue;
        }
        if (!matches('color', colors, r)) continue;
        if (!matches('topic', topics, r)) continue;
        if (!matches('form', forms, r)) continue;
        if (!matches('region', regions, r)) continue;

        if (year || month || day) {
          var d = r.date;                       // [y, m, d] 或 null
          if (!d) continue;                     // 无确切日期的条目一律不参与日期筛选
          // 前端 select 的 value 可能是 1..12 或 01..12，两种都要认
          if (year && String(d[0]) !== year) continue;
          if (month && String(d[1]) !== month && ('0' + d[1]) !== month) continue;
          if (day && String(d[2]) !== day && ('0' + d[2]) !== day) continue;
        }
        rows.push(r);
      }

      // 排序：有确切日期的按日期排前，无日期的按标题升序垫底。
      // 两个方向都稳定可复现，且「无日期」永远不会插到前面来。
      var dated = [], undated = [];
      for (var j = 0; j < rows.length; j++) {
        (rows[j].date ? dated : undated).push(rows[j]);
      }
      undated.sort(function (a, b) { return cmpStr(sortKeyOf(a), sortKeyOf(b)); });
      // 稳定降序：不能写「先升序排一遍再整体 reverse()」—— reverse 会把日期
      // 相同的那批条目的相对顺序一并翻过来，而 Python 侧 sort(reverse=True)
      // 是稳定排序（相等项保持原序）。两边只要有一处错位，翻到第 2 页就会
      // 出现「整页内容差一条」的漂移。用取负的方式排，稳定性得以保留。
      dated.sort(function (a, b) {
        var c = cmpDate(a.date, b.date);
        return direction === 'asc' ? c : -c;
      });

      var ordered = dated.concat(undated);
      var total = ordered.length;
      var start = (page - 1) * limit;
      var chunk = ordered.slice(start, start + limit);

      return {
        result: chunk.map(toItem),
        next: start + limit < total,
        total: total,
        page: page,
        limit: limit
      };
    };
  }

  /* ------------------------------------------------------- 卡片图变体 */

  // 【为什么要在这里改 high_res_url】
  // 前端只用 high_res_url 渲染卡片（bundle 里它只出现 2 次，都在卡片场景：
  // 检索结果的 appendResults 与首页 discover 卡片），而 low_res_url 一次都没用。
  // 所以把 high_res_url 指向「卡片尺寸」的变体，就能在**不改 vendored bundle、
  // 也不改页面 HTML** 的前提下把首屏图片从 7.04 MB 压到约 1.7 MB。
  //
  // 阈值与目录必须与另外两处保持一致，否则卡片图会 404：
  //   _tools/gen_image_variants.py 的 THRESHOLD / WIDTH
  //   serve.py                     的 CARD_VARIANT_MIN / CARD_VARIANT_PREFIX
  // 判据用的是数据里的 bytes 字段（已核实与真实文件大小逐条相符），
  // 所以这里不需要额外请求、也不依赖任何清单文件。
  var CARD_VARIANT_MIN = 150 * 1024;
  var CARD_VARIANT_PREFIX = '/img-c/card/';

  function cardImage(r, fallback) {
    var b = r && r.bytes;
    if (!b || b <= CARD_VARIANT_MIN) return fallback;
    var name = r.image ||
      String(r.image_url || '').replace(/^\/img\//, '');
    if (!name) return fallback;
    return CARD_VARIANT_PREFIX + String(name).replace(/\.[^.]+$/, '') + '.webp';
  }

  // 响应条目形状：严格照抄 serve.py 的 item()
  function toItem(r) {
    var d = r.date || null;
    var img = r.image_url || (r.image ? '/img/' + r.image : '');
    var card = cardImage(r, img);
    return {
      name: r.slug,
      title: r.title,
      alternativeTitle: '',
      // 卡片场景用的是卡片尺寸变体；详情页不走接口，仍用 /img/ 下的原图
      high_res_url: card,
      low_res_url: card,
      // 检索结果列表读的是 run_date_array
      run_date_array: d ? [d[0], d[1], d[2]] : null,
      // 颜色轮那个模块读的是三个标量
      run_date_year: d ? d[0] : null,
      run_date_month: d ? d[1] : null,
      run_date_day: d ? d[2] : null,
      color_tags: r.color ? [r.color] : [],
      topic_tags: r.topics || [],
      form_tags: r.format ? [r.format] : []
    };
  }

  /* -------------------------------------------------------------- 拦截 */

  function localResponse(absUrl) {
    var sp = new URL(absUrl, window.location.origin).searchParams;
    var run = queryDoodles(sp);
    return loadData().then(function (rows) {
      var payload = run(rows);
      return new Response(JSON.stringify(payload), {
        status: 200,
        headers: { 'Content-Type': 'application/json; charset=utf-8' }
      });
    });
  }

  function looksLikeHtml(text) {
    var s = text.replace(/^\uFEFF/, '').replace(/^\s+/, '');
    return s.charAt(0) === '<';
  }

  window.fetch = function (input, init) {
    var url = '';
    try {
      url = (typeof input === 'string') ? input : (input && input.url) || '';
    } catch (e) { /* 取不到就按普通请求放行 */ }

    if (!API_RE.test(url)) {
      return origFetch.apply(window, arguments);
    }

    var absUrl = url;
    try {
      absUrl = new URL(url, window.location.origin).toString();
    } catch (e) { /* 保留原串 */ }

    // 已确认服务端接口不可用 —— 不再浪费一次 404
    if (apiUsable === false) return localResponse(absUrl);

    return origFetch.apply(window, arguments).then(function (res) {
      if (!res || !res.ok) {
        apiUsable = false;
        return localResponse(absUrl);
      }
      // 有些静态托管对未知路径返回 200 + index.html（SPA fallback），
      // 这种情况下 res.ok 是 true，但内容是 HTML，必须靠内容判。
      return res.clone().text().then(function (text) {
        if (looksLikeHtml(text)) {
          apiUsable = false;
          return localResponse(absUrl);
        }
        apiUsable = true;
        return new Response(text, {
          status: res.status,
          statusText: res.statusText,
          headers: res.headers
        });
      });
    }).catch(function () {
      apiUsable = false;
      return localResponse(absUrl);
    });
  };

  // 给自动化验收留一个显式入口：真浏览器里可以直接读这几个值来对账，
  // 不必去猜 shim 有没有生效。
  window.__zviSearchApi = {
    ready: function () { return loadData().then(function (r) { return r.length; }); },
    mode: function () {
      if (apiUsable === true) return 'server';
      if (apiUsable === false) return 'local';
      return 'unknown';
    },
    query: function (qs) {
      var sp = new URLSearchParams(qs || '');
      return loadData().then(function (rows) { return queryDoodles(sp)(rows); });
    }
  };
})();
