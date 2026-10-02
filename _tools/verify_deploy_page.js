#!/usr/bin/env node
/**
 * verify_deploy_page.js —— 部署包检索页的行为验收（真浏览器，headless）。
 *
 * 【这一层在防什么】
 *   search-api-shim.js 把检索接口的语义复刻到了浏览器里。它和 serve.py 的
 *   query_doodles() 是**两套独立实现**，只要有一处漂移（比如月份补零、方向
 *   排序、无日期条目垫底规则），线上和本地的检索结果就会悄悄分叉 —— 而截图
 *   和静态检查都看不出来。
 *
 *   这里在真浏览器里把 shim 算出的每一组结果摊开，交给 verify_deploy.py
 *   去和「Python 独立复算」以及「serve.py 服务端」两边对账。
 *
 * 它做的事：
 *   1) 打开 /search/，等 shim 把数据装好、确认它处在 local 模式；
 *   2) 读一次 UI 上真实渲染出的结果卡片数（端到端，不是只看接口）；
 *   3) 逐个套用筛选条件，读回 shim 算出的 total / 本页条数 / 是否还有下一页；
 *   4) 顺带确认分页不重不漏（page=1 与 page=2 的 slug 集合无交集）；
 *   5) **逐个真导航带筛选参数的 URL**，把渲染出的卡片和 API 结果逐条对齐
 *      （见 NAV_CASES 的说明 —— 这条链路断过两次，且两次都是静默的）；
 *   6) 收集 JS 异常与 4xx/5xx（那一次接口探测的 404 单独注明，不算故障）。
 *
 * 用法：
 *   node _tools/verify_deploy_page.js --port 8791
 * 输出：
 *   人读的部分直接打印；机器读的行以 "RESULT " 开头（JSON）。
 */
const { spawn } = require('child_process');
const http = require('http');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
// 会被 pickFreePort() 抬到一个空闲端口。为什么必须挑：上一次运行被中断时
// 无头 Chrome 会留在原地继续监听这个调试端口，下一次运行就会**连上那个僵尸
// 实例**而不是新起一个 —— 现象是脚本挂住、一行输出都没有。
let DEBUG_PORT = 19227;   // 与 verify_rss_page.js 的 19226 错开，避免抢端口

// 从 start 往上找一个没被占用的端口
function pickFreePort(start) {
  return new Promise((resolve) => {
    const net = require('net');
    let p = start;
    const tryOne = () => {
      if (p >= start + 20) return resolve(start);
      const s = net.connect({ host: '127.0.0.1', port: p });
      s.on('connect', () => { s.destroy(); p++; tryOne(); });
      s.on('error', () => resolve(p));
    };
    tryOne();
  });
}

const CHROME_CANDIDATES = [
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
];

function arg(name, dflt) {
  const out = [];
  process.argv.forEach((a, i) => {
    if (a === '--' + name && process.argv[i + 1]) out.push(process.argv[i + 1]);
  });
  return out.length ? out[out.length - 1] : dflt;
}
function findChrome() {
  for (const p of CHROME_CANDIDATES) if (fs.existsSync(p)) return p;
  console.error('找不到 Chrome / Edge');
  process.exit(2);
}
function getJSON(url) {
  return new Promise((resolve, reject) => {
    http.get(url, (res) => {
      let d = '';
      res.on('data', (c) => (d += c));
      res.on('end', () => { try { resolve(JSON.parse(d)); } catch (e) { reject(e); } });
    }).on('error', reject);
  });
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitDevtools(timeoutMs) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    try {
      const v = await getJSON(`http://127.0.0.1:${DEBUG_PORT}/json/version`);
      if (v && v.webSocketDebuggerUrl) return v;
    } catch (_) { /* 未就绪 */ }
    await sleep(300);
  }
  throw new Error('DevTools 端口未就绪');
}

class CDP {
  constructor(ws) {
    this.ws = ws; this.id = 0;
    this.pending = new Map(); this.listeners = new Map();
    ws.addEventListener('message', (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result);
      } else if (msg.method) {
        (this.listeners.get(msg.method) || []).forEach((f) => f(msg.params));
      }
    });
  }
  send(method, params = {}) {
    const id = ++this.id;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }));
  }
  on(method, fn) {
    if (!this.listeners.has(method)) this.listeners.set(method, []);
    this.listeners.get(method).push(fn);
  }
}

/**
 * 验收用的筛选条件组合。
 *
 * 【注意语义】这里的 date_like_year 走的是**确切上线日期**（run_date），
 * 与 RSS 页 select() 的 year 不是一回事 —— 后者含从 slug 推定的年份，
 * 覆盖面大得多。两处数字不该相等，也不该互相比对。
 * 每组都刻意挑一个能覆盖到特定代码路径的维度：
 *   color/form/topic 走的是多值集合匹配，year 走日期筛选，title 走子串匹配。
 */
const CASES = [
  { qs: '', label: '无筛选（全集）' },
  { qs: 'topic_tags=Space', label: '话题 Space' },
  { qs: 'topic_tags=Football%2FSoccer', label: '话题 Football/Soccer（自带斜杠）' },
  { qs: 'color_tags=red', label: '主色 red' },
  { qs: 'form_tags=animated', label: '格式 animated' },
  { qs: 'date_like_year=2019', label: '年份 2019（确切日期）' },
  { qs: 'title_like=halloween', label: '关键字 halloween' },
  { qs: 'topic_tags=Space&form_tags=game', label: 'Space + game（组合）' },
  { qs: 'date_like_month=10&date_like_day=31', label: '月日 10/31（含补零路径）' },
  { qs: 'page=2&limit=16', label: '第二页' },
];

/**
 * 导航用例：直接带筛选参数进入检索页。
 *
 * 【为什么只问 shim 的 API 不够】
 * 用户走的是「点 chip / 带参数进页面」这条路，它比 API 多两段：
 *   URL 参数 →（bundle 的 loadParams 填回筛选表单）→ 表单状态 → 取数 → 渲染
 * 这两段都断过，而且都是静默的：
 *   · `Plants & Flowers` 的 href 没做百分号编码，查询串裂成两个参数，
 *     第二个参数名在表单里不存在 → loadParams 裸读 .tagName 抛异常 →
 *     整个初始化中断 → 结果列表全白（不报错、界面看着正常）；
 *   · 面板取值与本地数据词表不一致时，参数在表单里找不到对应复选框 →
 *     不勾选 → 取数时**没有任何筛选条件** → 页面显示的是未筛选的最新 16 条。
 * 所以这里必须真导航，并把渲染出的卡片和同一条件问 API 得到的结果逐条对齐。
 */
const NAV_CASES = [
  { url: '/search/?topic_tags=plants%20flowers', qs: 'topic_tags=plants%20flowers',
    label: '话题 Plants & Flowers（原为未编码 & 的崩溃点）' },
  { url: '/search/?topic_tags=football%20soccer', qs: 'topic_tags=football%20soccer',
    label: '话题 Football/Soccer（标点 slug）' },
  { url: '/search/?topic_tags=television%20film', qs: 'topic_tags=television%20film',
    label: '话题 Television/Film（标点 slug）' },
  { url: '/search/?topic_tags=other', qs: 'topic_tags=other',
    label: '话题 Other（面板原本没有这个选项）' },
  { url: '/search/?topic_tags=birthday', qs: 'topic_tags=birthday',
    label: '话题 Birthday（面板原本没有这个选项）' },
  { url: '/search/?form_tags=animated', qs: 'form_tags=animated',
    label: '格式 animated（面板原取值叫 animated gif）' },
  { url: '/search/?page=2', qs: '',
    label: '手敲 page=2（非表单字段，理应优雅降级为第 1 页）' },
  { url: '/search/?nationality_tags=Germany', qs: '',
    label: '手敲 nationality_tags（非表单字段，理应优雅降级）' },
];

async function main() {
  const port = arg('port', '8791');
  // --base 用来指向线上域名（https://google.zvi.onl）；默认走本机静态服务器
  const base = (arg('base', '') || `http://127.0.0.1:${port}`).replace(/\/$/, '');
  const url = `${base}/search/`;
  const proxy = arg('proxy', '');
  const remote = !/^https?:\/\/(127\.0\.0\.1|localhost)(:|\/|$)/.test(base);

  // 起浏览器之前先挑一个空闲的调试端口，避免连上残留的僵尸实例
  DEBUG_PORT = await pickFreePort(parseInt(arg('debug-port', '19227'), 10));

  const chrome = findChrome();
  // 【为什么用一次性的配置目录】
  // 复用同一个 user-data-dir 会把上一次运行留下的 HTTP 缓存也一起带进来。
  // 实测踩过：/vendor/* 当时给了 7 天缓存，浏览器拿旧的 search-api-shim.js
  // 配新数据跑，前三个「带话题进入检索页」的用例全部变成 0 条 —— 而磁盘上
  // 的文件、源站返回的文件都是新的，排查方向完全被带偏。
  // 每次全新配置 = 模拟首次访问，结果才可复现。
  const profile = path.join(ROOT, '_tools',
                            '.chrome-profile-deploy-' + process.pid);
  fs.mkdirSync(profile, { recursive: true });

  const proc = spawn(chrome, [
    '--headless=new', '--disable-gpu', '--no-sandbox', '--hide-scrollbars',
    '--disable-extensions', '--no-first-run', '--disable-sync',
    '--disable-component-update', '--disable-background-networking',
    '--window-size=1440,1000',
    // 测线上域名时，Chrome 走的是系统代理设置而不是环境变量，
    // 沙箱/公司网络里必须显式传进来，否则页面根本加载不出来。
    ...(remote && proxy ? ['--proxy-server=' + proxy] : []),
    `--remote-debugging-port=${DEBUG_PORT}`,
    `--user-data-dir=${profile}`,
    'about:blank',
  ], { stdio: 'ignore' });
  process.on('exit', () => { try { proc.kill(); } catch (_) {} });

  const exceptions = [];
  const badResponses = [];
  let cdp = null;
  let result = null;

  async function evaluate(expression) {
    const r = await cdp.send('Runtime.evaluate', {
      expression, returnByValue: true, awaitPromise: true,
    });
    if (r.exceptionDetails) {
      throw new Error('求值异常: ' + JSON.stringify(r.exceptionDetails).slice(0, 300));
    }
    return r.result && r.result.value;
  }

  try {
    await waitDevtools(25000);
    const list = await getJSON(`http://127.0.0.1:${DEBUG_PORT}/json/list`);
    const target = list.find((t) => t.type === 'page');
    if (!target) throw new Error('找不到 page 目标');
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => {
      ws.addEventListener('open', res);
      ws.addEventListener('error', rej);
    });
    cdp = new CDP(ws);
    await cdp.send('Page.enable');
    await cdp.send('Runtime.enable');
    await cdp.send('Log.enable');
    await cdp.send('Network.enable');
    cdp.on('Runtime.exceptionThrown', (p) => {
      const d = p.exceptionDetails || {};
      exceptions.push(String((d.exception && (d.exception.description || d.exception.value)) || d.text));
    });
    cdp.on('Network.responseReceived', (p) => {
      const r = p.response || {};
      if (r.status >= 400) badResponses.push({ status: r.status, url: r.url });
    });

    console.log('='.repeat(78));
    console.log(`检索页验证  ${url}　（调试端口 ${DEBUG_PORT}` +
                `${remote && proxy ? '，代理 ' + proxy : ''}）`);
    console.log('='.repeat(78));

    await cdp.send('Page.navigate', { url });

    // 等 shim 就绪：数据 3.2 MB，线上过代理可能要几十秒
    let shimN = 0;
    const tShim = Date.now();
    for (let i = 0; i < 150; i++) {
      await sleep(500);
      const ok = await evaluate(
        '!!(window.__zviSearchApi && window.__zviSearchApi.mode() !== "unknown")');
      if (ok) break;
    }
    shimN = await evaluate('window.__zviSearchApi ? window.__zviSearchApi.ready() : -1');
    const mode = await evaluate('window.__zviSearchApi ? window.__zviSearchApi.mode() : "missing"');
    console.log(`\n■ shim 状态：mode=${mode}，载入 ${shimN} 条数据` +
                `（就绪耗时 ${((Date.now() - tShim) / 1000).toFixed(1)}s）`);

    // 端到端：UI 上真实渲染出多少个结果卡片
    const uiLinks = await evaluate('document.querySelectorAll(\'a[href^="/doodle/"]\').length');
    const firstTitle = await evaluate(
      '(function(){var a=document.querySelector(\'a[href^="/doodle/"]\');' +
      'return a?a.getAttribute("href"):"";})()');
    console.log(`■ UI 渲染：${uiLinks} 个详情页链接，第一条 = ${firstTitle}`);

    // 逐组条件问 shim 要结果
    const cases = [];
    for (const c of CASES) {
      const q = 'window.__zviSearchApi.query(' + JSON.stringify(c.qs) + ')';
      const r = await evaluate(
        q + '.then(function(x){return {total:x.total, n:x.result.length, next:x.next,' +
        'page:x.page, limit:x.limit, names:x.result.map(function(i){return i.name;}),' +
        'first:x.result.length?x.result[0]:null};})');
      cases.push(Object.assign({ qs: c.qs, label: c.label }, r));
      console.log(`   ${String(r.total).padStart(5)} 条  ${String(r.n).padStart(3)} 条/页  ` +
        `next=${String(r.next).padEnd(5)} ${c.label}`);
    }

    // 分页不重不漏：page=1 与 page=2 的 slug 集合不应有交集
    const p1 = cases.find((c) => c.qs === '');
    const p2 = cases.find((c) => c.qs === 'page=2&limit=16');
    let overlap = -1;
    if (p1 && p2 && p1.names && p2.names) {
      const s1 = new Set(p1.names);
      overlap = p2.names.filter((x) => s1.has(x)).length;
      console.log(`\n■ 分页检查：page1(${p1.names.length}) 与 page2(${p2.names.length}) ` +
        `重复 ${overlap} 条（应为 0）`);
    }

    // items 形状是否与 serve.py 的 item() 对齐
    const shape = (p1 && p1.first) ? Object.keys(p1.first).sort().join(',') : '';
    console.log(`■ 条目字段：${shape}`);

    // ── 导航用例：带筛选参数真进页面，把渲染结果与 API 结果对一遍 ──────────
    console.log('\n■ 带参数直接进页面（URL → 填表 → 取数 → 渲染 全链路）');
    // 【超时为什么可配，且默认给得宽】
    // 每导航一次，shim 都要重新拉一遍 rss/doodles.json（3.2 MB）。
    // 本地静态服务器是毫秒级，线上过代理可能几十秒 —— 早期固定等 12 秒，
    // 结果线上 8 个用例里 7 个"渲染 0 张"，看着像功能坏了，其实是没等够。
    // 所以：默认 60 秒，并且把每一例的实际耗时打出来，慢是慢、坏是坏，一眼可分。
    const navWaitMs = parseInt(arg('nav-wait', '60000'), 10);
    const navCases = [];
    for (const c of NAV_CASES) {
      const exBefore = exceptions.length;
      const tStart = Date.now();
      await cdp.send('Page.navigate', { url: base + c.url });
      let hrefs = [];
      let lastErr = null;
      while (Date.now() - tStart < navWaitMs) {
        await sleep(500);
        try {
          hrefs = await evaluate(
            '(function(){var r=document.querySelector(".search-doodle__results-wrapper");' +
            'if(!r)return [];return Array.prototype.slice' +
            '.call(r.querySelectorAll(\'a[href^="/doodle/"]\'))' +
            '.map(function(a){return a.getAttribute("href");});})()');
        } catch (e) { lastErr = e && e.message; }
        if (hrefs && hrefs.length) break;
      }
      const elapsed = Date.now() - tStart;
      // 同一条件问一次 API，作为期望值
      let expect = null;
      let total = null;
      try {
        const r = await evaluate(
          'window.__zviSearchApi.query(' + JSON.stringify(c.qs) + ').then(function(x){' +
          'return {total:x.total,names:x.result.map(function(i){return "/doodle/"+i.name+"/";})};})');
        expect = r.names;
        total = r.total;
      } catch (_) { /* 拿不到就只报异常数 */ }

      const exN = exceptions.length - exBefore;
      const same = expect ? JSON.stringify(hrefs) === JSON.stringify(expect) : null;
      navCases.push({
        url: c.url, qs: c.qs, label: c.label,
        cards: hrefs.length, total, exN, match: same, ms: elapsed,
        first: hrefs[0] || '',
      });
      const mark = (exN === 0 && same !== false) ? '✓' : '✗';
      console.log(`   ${mark} 异常 ${exN} 条 · 渲染 ${String(hrefs.length).padStart(2)} 张` +
        ` · API total=${total === null ? '?' : total} · 与 API 一致=${
          same === null ? '?' : same} · 耗时 ${(elapsed / 1000).toFixed(1)}s` +
        `${lastErr ? ' · 求值异常 ' + lastErr.slice(0, 60) : ''} · ${c.label}`);
    }

    const navBad = navCases.filter((c) => c.exN !== 0 || c.match === false);

    result = {
      ok: exceptions.length === 0 && overlap === 0 && mode === 'local' && navBad.length === 0,
      mode, shimN, uiLinks, firstTitle, cases, overlap, shape, navCases,
      exceptions,
      // 接口探测那一次 404 是设计内的（先试服务端、失败才降级），单独标出来
      expectedProbe404: badResponses.filter((r) => r.url.indexOf('/live/api/v1/doodles') >= 0).length,
      badResponses: badResponses.filter((r) => r.url.indexOf('/live/api/v1/doodles') < 0),
    };

    console.log('\n' + '='.repeat(78));
    if (result.ok) {
      console.log('✅ 检索页在纯静态托管下工作正常（含带筛选参数直接进页面）');
    } else {
      console.log('!! 有问题：exceptions=' + exceptions.length + ' overlap=' + overlap +
        ' mode=' + mode + ' 导航用例失败=' + navBad.length);
    }
    if (result.exceptions.length) {
      console.log('   JS 异常：');
      result.exceptions.slice(0, 5).forEach((e) => console.log('     ' + String(e).slice(0, 200)));
    }
    if (result.badResponses.length) {
      console.log('   非预期 4xx/5xx：');
      result.badResponses.slice(0, 8).forEach((e) => console.log(`     ${e.status} ${e.url}`));
    }
    console.log('='.repeat(78));
    console.log('RESULT ' + JSON.stringify(result));
  } catch (e) {
    console.error('验证失败：' + (e && e.message));
    console.log('RESULT ' + JSON.stringify({ ok: false, error: String(e && e.message) }));
    process.exitCode = 1;
  } finally {
    try { proc.kill(); } catch (_) { /* ignore */ }
    await sleep(200);
    try { fs.rmSync(profile, { recursive: true, force: true }); } catch (_) { /* ignore */ }
    process.exit(process.exitCode || 0);
  }
}

main();
