#!/usr/bin/env node
/**
 * verify_rss_page.js —— /rss/ 页面的行为验收（真浏览器，headless）。
 *
 * 【为什么必须单独验一次】
 *   /rss/ 上的筛选有两套实现：
 *     · 页面里那份 JS（渲染订阅视图、算条数、拼打包链接）
 *     · serve.py 里的 rss_common.select（决定 zip 里实际装哪些图）
 *   两边只要有一处语义漂移，就会出现「页面说 128 张、下载下来 135 张」——
 *   而截图和静态检查都看不出来。这里把页面侧的条数与打包链接摊开，
 *   交给 verify_rss.py 去和服务端/源数据对账。
 *
 * 它做的事：
 *   1) 打开 /rss/，等订阅视图渲染出来；
 *   2) 全程不点任何按钮，只滚动，看列表会不会自己续载；
 *   3) 逐个套用筛选条件，读回「页面算出的条数」与「打包链接」；
 *   4) 顺带收集 JS 异常与 4xx/5xx。
 *
 * 用法：
 *   node _tools/verify_rss_page.js --port 8765 [--rounds 6]
 * 输出：
 *   人读的部分直接打印；机器读的行以 "RESULT " 开头（JSON）。
 */
const { spawn } = require('child_process');
const http = require('http');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const DEBUG_PORT = 19226;

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

// 要套用的筛选条件（键名与页面控件的 id 一致）
const FILTERS = [
  { ftopic: '' },
  { ftopic: 'Space' },
  { ftopic: 'Football/Soccer' },
  { fcolor: 'red' },
  { fform: 'animated' },
  { fyear: '2019' },
  { fq: 'halloween' },
  { ftopic: 'Space', fform: 'game' },
];

async function main() {
  const port = arg('port', '8765');
  const rounds = parseInt(arg('rounds', '8'), 10);
  const url = `http://127.0.0.1:${port}/rss/`;

  const chrome = findChrome();
  const profile = path.join(ROOT, '_tools', '.chrome-profile-rss');
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

  const STATE = `(function(){
    var l=document.getElementById('list');
    return {
      items: document.querySelectorAll('.zvi-rss-item').length,
      count: l ? Number(l.dataset.count||-1) : -2,
      text: document.getElementById('count').textContent,
      zip: document.getElementById('zipCard').getAttribute('href'),
      scope: document.getElementById('zipScope').textContent,
      size: document.getElementById('zipSize').textContent,
      sentinel: document.getElementById('sentinelText').textContent,
      bottom: Math.round(document.getElementById('sentinel').getBoundingClientRect().top),
      viewportH: window.innerHeight
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
    console.log(`RSS 模式页验证  ${url}`);
    console.log('='.repeat(78));

    await cdp.send('Page.navigate', { url });

    // 等订阅视图渲染（doodles.json 有 3 MB，给足时间）
    let s0 = null;
    for (let i = 0; i < 80; i++) {
      await sleep(300);
      s0 = await evaluate(STATE);
      if (s0 && s0.count > 0) break;
    }
    if (!s0 || s0.count <= 0) throw new Error('订阅视图没有渲染出来：' + JSON.stringify(s0));
    console.log(`\n■ 首屏：页面算得 ${s0.count} 条，DOM 里 ${s0.items} 条（分批渲染）`);
    console.log(`   ${s0.text}`);
    console.log(`   打包链接 ${s0.zip}  ·  ${s0.scope} · ${s0.size}`);

    // 滚动续载：全程不点任何按钮
    const trail = [];
    for (let i = 0; i < rounds; i++) {
      await evaluate('window.scrollTo(0, document.body.scrollHeight)');
      let prev = -1, cur = 0;
      for (let t = 0; t < 40; t++) {
        await sleep(200);
        prev = cur;
        cur = (await evaluate(STATE)).items;
        if (cur === prev) break;
      }
      const st = await evaluate(STATE);
      trail.push(st.items);
      console.log(`  · 第 ${i + 1} 次滚到底 → DOM 里 ${st.items} 条`);
      if (st.sentinel.indexOf('到底') >= 0) break;
    }
    const grew = trail[trail.length - 1] > s0.items;
    console.log(`■ 滚动续载：${s0.items} → ${trail[trail.length - 1]} 条  —— ` +
                (grew ? '✓ 自动加载生效' : '✗ 没有自动加载'));

    // 逐个套用筛选条件
    //
    // 【为什么要「等状态稳定」而不是「等到和上次不同」】
    // 关键字输入做了 180ms 防抖，控件 change 却是同步生效的。于是页面会先经过
    // 一个中间态（例如「选项已清空、关键字还没套上」= 全量 6375 条），
    // 若一看到变化就取数，读到的就是中间态而不是终态 —— 实测会误报成
    // 「页面 6375 / 源数据 28」。所以先跳过防抖窗口，再等连续两次读数相同。
    console.log('\n■ 筛选（页面 JS 与服务端 Python 是两套实现，两边必须算出同一个数）');
    for (const f of FILTERS) {
      await evaluate(`(function(){
        var set=${JSON.stringify(f)};
        ['ftopic','fcolor','fform','fyear'].forEach(function(id){
          var el=document.getElementById(id);
          el.value = set[id] || '';
          el.dispatchEvent(new Event('change', {bubbles:true}));
        });
        var q=document.getElementById('fq');
        q.value = set.fq || '';
        q.dispatchEvent(new Event('input', {bubbles:true}));
        return true;
      })()`);
      let st = null, prev = -1;
      for (let t = 0; t < 50; t++) {
        await sleep(t === 0 ? 400 : 150);   // 400ms 先越过 180ms 防抖窗口
        st = await evaluate(STATE);
        if (st.count === prev) break;
        prev = st.count;
      }
      const label = Object.keys(f).length
        ? Object.entries(f).map(([k, v]) => k + '=' + v).join('&') : '(全量)';
      console.log(`  · ${label.padEnd(32)} → ${String(st.count).padStart(5)} 条` +
                  `   zip=${st.zip}`);
      console.log('RESULT ' + JSON.stringify({
        filter: f, count: st.count, zip: st.zip, scope: st.scope,
        items: st.items, size: st.size,
      }));
    }

    console.log(`\n■ JS 运行时异常：${exceptions.length} 条`);
    exceptions.slice(0, 8).forEach((e) => console.log('    ! ' + e.split('\n').slice(0, 4).join('\n      ')));
    console.log(`■ 4xx/5xx 响应：${badResponses.length} 条`);
    badResponses.slice(0, 8).forEach((r) => console.log(`    ! ${r.status} ${r.url.slice(0, 150)}`));

    const ok = grew && exceptions.length === 0 && badResponses.length === 0;
    console.log('\n' + (ok ? '✓ 页面侧检查通过' : '✗ 存在问题，见上'));
    if (!ok) process.exitCode = 1;
  } catch (e) {
    console.error('验证失败：' + e.message);
    process.exitCode = 1;
  } finally {
    try { proc.kill(); } catch (_) { /* 忽略 */ }
  }
}

main();
