#!/usr/bin/env node
/**
 * verify_search_autoload.js —— 证明 /search/ 的「Show More」真的改成自动加载了。
 *
 * 【为什么不能只看截图】
 *   截图只能证明「页面上没有按钮了」，证明不了「滚动下去会自己续上下一页」。
 *   分页是运行时用 fetch 拉的，只有把「滚动前后的卡片数」摊开对比才算数。
 *
 * 它做四件事：
 *   1) 打开 /search/，等首页 16 张卡片落地；
 *   2) 断言 Show More 按钮**不可见**（display:none，视觉上等于删掉）；
 *   3) 反复「滚到底 → 等」，全程**不调用任何 click()**，看卡片数会不会自己涨；
 *   4) 顺带收集 JS 异常与 4xx/5xx —— 自动加载若踩到分页链路的坑，这里会露头。
 *
 * 用法：
 *   node _tools/verify_search_autoload.js --port 8765 [--rounds 6]
 *   node _tools/verify_search_autoload.js --path "/search/?title_like=halloween"
 */
const { spawn } = require('child_process');
const http = require('http');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const DEBUG_PORT = 19224;

const CHROME_CANDIDATES = [
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
];

function args(name) {
  const out = [];
  process.argv.forEach((a, i) => {
    if (a === '--' + name && process.argv[i + 1]) out.push(process.argv[i + 1]);
  });
  return out;
}
function arg(name, dflt) {
  const v = args(name);
  return v.length ? v[v.length - 1] : dflt;
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

async function main() {
  const port = arg('port', '8765');
  const rounds = parseInt(arg('rounds', '6'), 10);
  // --path 用于验证「带筛选条件的检索页」也照样自动加载
  const url = `http://127.0.0.1:${port}${arg('path', '/search/')}`;

  const chrome = findChrome();
  const profile = path.join(ROOT, '_tools', '.chrome-profile-autoload');
  fs.mkdirSync(profile, { recursive: true });

  const proc = spawn(chrome, [
    '--headless=new', '--disable-gpu', '--no-sandbox', '--hide-scrollbars',
    '--disable-extensions', '--no-first-run', '--disable-sync',
    '--disable-component-update', '--disable-background-networking',
    '--window-size=1440,1000',
    `--remote-debugging-port=${DEBUG_PORT}`,
    `--user-data-dir=${profile}`,
    'about:blank',
  ], { stdio: 'ignore' });
  process.on('exit', () => { try { proc.kill(); } catch (_) {} });

  const exceptions = [];
  const badResponses = [];
  let cdp = null;

  async function evaluate(expression) {
    const r = await cdp.send('Runtime.evaluate', {
      expression, returnByValue: true, awaitPromise: true,
    });
    if (r.exceptionDetails) {
      throw new Error('求值异常: ' + JSON.stringify(r.exceptionDetails).slice(0, 300));
    }
    return r.result && r.result.value;
  }

  const CARD_COUNT = 'document.querySelectorAll(".search-doodle__results-wrapper .doodle-card").length';
  const STATE = `(function(){
    var b=document.querySelector('.search-doodle__results-button');
    var l=document.querySelector('.search-doodle__results-loading');
    var w=document.querySelector('.search-doodle__results-wrapper');
    if(!b||!w) return {missing:true};
    var cs=getComputedStyle(b), r=w.getBoundingClientRect();
    return {
      cards: w.querySelectorAll('.doodle-card').length,
      btnDisplay: cs.display, btnVisible: cs.visibility,
      btnH: Math.round(b.getBoundingClientRect().height),
      hasMore: b.classList.contains('active'),
      loading: !!(l && l.classList.contains('active')),
      listBottom: Math.round(r.bottom),
      viewportH: window.innerHeight,
      scrollY: Math.round(window.scrollY)
    };
  })()`;

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
    console.log(`搜索页自动加载验证  ${url}`);
    console.log('='.repeat(78));

    await cdp.send('Page.navigate', { url });

    // 等首屏 16 张卡片落地
    let first = 0;
    for (let i = 0; i < 60; i++) {
      await sleep(250);
      first = await evaluate(CARD_COUNT);
      if (first > 0) break;
    }
    const s0 = await evaluate(STATE);
    console.log(`\n■ 首屏：卡片 ${first} 张`);
    console.log(`    Show More 按钮 display=${s0.btnDisplay} visibility=${s0.btnVisible}` +
                ` 高度=${s0.btnH}px  —— ${s0.btnDisplay === 'none' && s0.btnH === 0 ? '已隐藏（视觉上等于删除）' : '⚠ 仍然可见'}`);
    console.log(`    还有下一页（.active）=${s0.hasMore}`);

    // 全程不调用任何 click()，只滚动，看卡片数会不会自己涨。
    // 首屏就没有下一页时（空结果 / 结果不足一页）没有东西可加载，跳过滚动测试。
    const trail = [];
    if (s0.hasMore) {
      for (let i = 0; i < rounds; i++) {
        await evaluate('window.scrollTo(0, document.body.scrollHeight)');
        // 等这一页落地（loading 消失且卡片数稳定）
        let prev = -1, cur = await evaluate(CARD_COUNT);
        for (let t = 0; t < 40; t++) {
          await sleep(250);
          prev = cur;
          cur = await evaluate(CARD_COUNT);
          const st = await evaluate(STATE);
          if (cur === prev && !st.loading) break;
        }
        const st = await evaluate(STATE);
        trail.push({ round: i + 1, cards: st.cards, hasMore: st.hasMore });
        console.log(`  · 第 ${i + 1} 次滚到底 → 卡片 ${st.cards} 张` +
                    `（loading=${st.loading}，还有下一页=${st.hasMore}）`);
        if (!st.hasMore) break;
      }
    } else {
      console.log('  · 首屏就没有下一页（空结果或结果不足一页），无需自动加载');
    }

    const last = trail.length ? trail[trail.length - 1].cards : first;
    const grew = last > first;
    // 「没得加载」也是正确行为：判据是「该加载时必须自己加载」，
    // 而不是「任何页面都必须变多」——空结果页本来就没有下一页可拉。
    const verdict = grew ? '✓ 自动加载生效'
      : (s0.hasMore ? '✗ 还有下一页却没自动加载' : '✓ 无需加载（本来就没有下一页）');
    console.log(`\n■ 结论：未点击任何按钮，滚动 ${trail.length} 次后卡片 ${first} → ${last} 张  —— ${verdict}`);
    console.log(`\n■ JS 运行时异常：${exceptions.length} 条`);
    exceptions.slice(0, 8).forEach((e) => console.log('    ! ' + e.split('\n').slice(0, 4).join('\n      ')));
    console.log(`■ 4xx/5xx 响应：${badResponses.length} 条`);
    badResponses.slice(0, 8).forEach((r) => console.log(`    ! ${r.status} ${r.url.slice(0, 150)}`));

    const ok = (grew || !s0.hasMore) && s0.btnDisplay === 'none'
      && exceptions.length === 0 && badResponses.length === 0;
    console.log('\n' + (ok ? '✓ 全部通过' : '✗ 存在问题，见上'));
    if (!ok) process.exitCode = 1;
  } catch (e) {
    console.error('验证失败：' + e.message);
    process.exitCode = 1;
  } finally {
    try { proc.kill(); } catch (_) { /* 忽略 */ }
  }
}

main();
