#!/usr/bin/env node
/**
 * cdp_inspect.js —— 用 CDP 检查某个本地页面「渲染后到底是什么状态」。
 *
 * 【为什么需要它】
 *   批量截图只能证明「页面有内容」，证明不了「某个模块是否真的初始化成功」。
 *   典型场景：首页的地图模块依赖 Google Charts，若 charts 被桩掉，
 *   地图区会安静地留一块空白 —— 截图看整页是"正常"的，只有把 DOM 指标
 *   和 JS 异常摊开才能发现。
 *
 * 它一次给出三样证据：
 *   1) JS 运行时异常 / console error —— 桩件导致的异常会在这里暴露；
 *   2) 指定 DOM 表达式的结果 —— 例如某容器 innerHTML 长度、子元素数量、尺寸；
 *   3) 按 CSS 选择器裁剪的截图 —— 只拍可疑模块，便于逐像素比对。
 *
 * 依赖：Node 22+ 内置全局 WebSocket。零 npm 依赖。
 *
 * 用法：
 *   node _tools/cdp_inspect.js --port 8765 --url /
 *       [--eval "<js表达式>"] 可重复
 *       [--clip "<css选择器>"] [--clip-out _shots/x.png]
 *       [--click "<css选择器>"] 可重复：在 --click-at 毫秒后依次点击
 *       [--click-at 6000]     首次点击的时机（默认 dwell 的一半）
 *       [--dwell 8000]
 *
 * 【为什么需要 --click】
 *   只加载页面证明不了按钮能用。「Load more」这类交互必须真的点下去、
 *   再看点击后的 DOM 状态与 URL —— 否则「静态首屏正常」会掩盖
 *   「一交互就抛异常」这种更严重的缺陷。
 */

const { spawn } = require('child_process');
const http = require('http');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const DEBUG_PORT = 19223;

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

/**
 * 默认检查项：这些表达式覆盖了「模块是否真的初始化」的常见判据。
 * 用 --eval 追加自定义项。
 */
const DEFAULT_EVALS = [
  ['typeof google',                              'google 是否定义'],
  ['typeof (window.google && google.charts)',    'google.charts 是否定义'],
  ['typeof (window.google && google.charts && google.charts.load)',
                                                 'google.charts.load 是否定义'],
  ['document.querySelectorAll(".full-bleed-map").length', '首页地图模块数量'],
  ['(function(){var e=document.querySelector(".mapChart");if(!e)return "无.mapChart 节点";return "innerHTML长度="+e.innerHTML.length+" 子元素="+e.childElementCount+" 高="+Math.round(e.getBoundingClientRect().height);})()',
                                                 '.mapChart 实际内容'],
  ['(window.google && google.charts && google.charts.load && google.charts.load.toString().length) || 0',
                                                 'load 函数体长度（桩件通常很短）'],
];

async function main() {
  const port = arg('port', '8765');
  // --url 支持两种写法：以 http(s) 开头的完整地址（用于线上域名），
  // 或者以 / 开头的路径（拼到本机 --port 上）。
  const rawUrl = arg('url', '/');
  const url = /^https?:\/\//i.test(rawUrl) ? rawUrl : `http://127.0.0.1:${port}${rawUrl}`;
  const dwell = parseInt(arg('dwell', '8000'), 10);
  const clip = arg('clip', '');
  const clipOut = path.resolve(ROOT, arg('clip-out', '_shots/_clip.png'));
  const evals = DEFAULT_EVALS.concat(args('eval').map((e) => [e, '自定义']));

  const chrome = findChrome();
  const profile = path.join(ROOT, '_tools', '.chrome-profile-inspect');
  fs.mkdirSync(profile, { recursive: true });

  // 测线上域名时，Chrome 用的是系统代理而不是环境变量，必须显式传
  const proxy = arg('proxy', '');
  const isLocal = /^https?:\/\/(127\.0\.0\.1|localhost)(:|\/|$)/.test(url);

  const proc = spawn(chrome, [
    '--headless=new', '--disable-gpu', '--no-sandbox', '--hide-scrollbars',
    '--disable-extensions', '--no-first-run', '--disable-sync',
    '--disable-component-update', '--disable-background-networking',
    '--window-size=1440,1200',
    ...(!isLocal && proxy ? ['--proxy-server=' + proxy] : []),
    `--remote-debugging-port=${DEBUG_PORT}`,
    `--user-data-dir=${profile}`,
    'about:blank',
  ], { stdio: 'ignore' });
  const cleanup = () => { try { proc.kill(); } catch (_) {} };
  process.on('exit', cleanup);

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
    const cdp = new CDP(ws);
    await cdp.send('Page.enable');
    await cdp.send('Runtime.enable');
    await cdp.send('Log.enable');

    // --dump-net：把「页面目标」发起的请求列出来。
    // filter 为空 -> 只列非 localhost；filter 为 all -> 全列；否则按子串过滤。
    const dumpNet = arg('dump-net', '');
    const netLog = [];
    // 4xx/5xx 始终收集：console 里那句 "Failed to load resource: 404" 不带 URL，
    // 光看它无法定位是哪个资源，所以直接从响应事件里把状态码和地址捞出来。
    const badResponses = [];
    await cdp.send('Network.enable');
    cdp.on('Network.requestWillBeSent', (p) => {
      netLog.push({ url: p.request.url, type: p.type,
                    initiator: (p.initiator && p.initiator.type) || '?' });
    });
    cdp.on('Network.responseReceived', (p) => {
      const r = p.response || {};
      if (r.status >= 400) badResponses.push({ status: r.status, url: r.url, type: p.type });
    });
    const isLocalUrl = (u) => {
      // 非 http(s) 协议（data: / blob: / about: / chrome-extension:）一律不算外部请求
      if (/^(data|blob|about|chrome-extension|filesystem|ws|wss):/i.test(u)) return true;
      try {
        const h = new URL(u).hostname.toLowerCase();
        return h === '127.0.0.1' || h === 'localhost' || h === '::1';
      } catch (_) { return false; }
    };

    const exceptions = [];
    const consoleErrors = [];
    const consoleWarns = [];

    cdp.on('Runtime.exceptionThrown', (p) => {
      const d = p.exceptionDetails || {};
      const desc = (d.exception && (d.exception.description || d.exception.value)) || d.text;
      exceptions.push(String(desc));   // 保留完整堆栈：只看第一行定位不到出错的代码
    });
    cdp.on('Runtime.consoleAPICalled', (p) => {
      const text = (p.args || [])
        .map((a) => a.value !== undefined ? a.value : (a.description || a.type)).join(' ');
      if (p.type === 'error') consoleErrors.push(text.slice(0, 300));
      else if (p.type === 'warning') consoleWarns.push(text.slice(0, 300));
    });
    cdp.on('Log.entryAdded', (p) => {
      const e = p.entry || {};
      if (e.level === 'error') consoleErrors.push(`[${e.source}] ${e.text}`.slice(0, 300));
    });

    console.log('='.repeat(78));
    console.log(`CDP 页面检查  ${url}`);
    console.log('='.repeat(78));

    await cdp.send('Page.navigate', { url });

    // 可选：先等页面稳定，依次点击若干元素，再等剩余时间。
    // 点击结果（命中/未命中）会打印出来 —— 选择器写错时不能静默当成"没反应"。
    const clicks = args('click');
    const clickAt = parseInt(arg('click-at', String(Math.round(dwell / 2))), 10);
    if (clicks.length) {
      await sleep(clickAt);
      for (const sel of clicks) {
        const r = await cdp.send('Runtime.evaluate', {
          expression: `(function(){var e=document.querySelector(${JSON.stringify(sel)});` +
                      `if(!e)return '选择器未命中';` +
                      `if(e.disabled)return '元素被禁用';` +
                      `e.click();return '已点击';})()`,
          returnByValue: true,
        });
        console.log(`\n■ 点击 ${sel} -> ${(r.result && r.result.value) || '?'}`);
        await sleep(1500);
      }
      await sleep(Math.max(0, dwell - clickAt - clicks.length * 1500));
    } else {
      await sleep(dwell);
    }

    console.log(`\n■ JS 运行时异常：${exceptions.length} 条`);
    exceptions.slice(0, 12).forEach((e) =>
      console.log('    ! ' + e.split('\n').slice(0, 5).join('\n      ')));

    console.log(`\n■ console error：${consoleErrors.length} 条`);
    consoleErrors.slice(0, 8).forEach((e) => console.log(`    ! ${e}`));
    if (consoleWarns.length) {
      console.log(`\n■ console warning：${consoleWarns.length} 条（仅列 3）`);
      consoleWarns.slice(0, 3).forEach((e) => console.log(`    · ${e}`));
    }

    console.log('\n■ DOM / 全局状态');
    for (const [expr, label] of evals) {
      let out;
      try {
        const r = await cdp.send('Runtime.evaluate', {
          expression: expr, returnByValue: true, awaitPromise: false,
        });
        if (r.exceptionDetails) {
          out = '求值异常: ' + String(r.exceptionDetails.text || '').slice(0, 120);
        } else {
          out = JSON.stringify(r.result && r.result.value);
        }
      } catch (e) { out = '调用失败: ' + e.message; }
      console.log(`    ${label.padEnd(24)} => ${out}`);
    }

    if (dumpNet) {
      let shown = netLog;
      if (dumpNet !== 'all') shown = netLog.filter((r) => !isLocalUrl(r.url));
      console.log(`\n■ 页面目标发起的请求：${netLog.length} 个` +
                  `（过滤 "${dumpNet}" -> ${shown.length} 个）`);
      shown.slice(0, 60).forEach((r) => console.log(`    · [${r.type}] ${r.url.slice(0, 150)}`));
      if (shown.length > 60) console.log(`    … 其余 ${shown.length - 60} 个略`);
    }

    if (badResponses.length) {
      console.log(`\n■ 4xx/5xx 响应：${badResponses.length} 条`);
      badResponses.slice(0, 20).forEach((r) =>
        console.log(`    ! ${r.status} [${r.type}] ${r.url.slice(0, 160)}`));
      if (badResponses.length > 20) console.log(`    … 其余 ${badResponses.length - 20} 条略`);
    }

    if (clip) {
      // Page.captureScreenshot 的 clip 用的是**视口坐标**。页面停在顶部时它与文档坐标
      // 恰好相等，所以早期截首屏模块一直是对的；但页脚这类文档坐标 y>9000 的元素
      // 远在视口之外，被钳制后就截成了别的区域。
      //
      // 做法：把视口一次性撑到 Chrome 的尺寸上限 16384（保持不滚动）。此时 scrollY=0，
      // 视口坐标与文档坐标重合，clip 自然落得准。
      //
      // 为什么不用 document.documentElement.scrollHeight 去算需要多高：本站的 html/body
      // 带 height:100%，它返回的是**视口高度**而不是内容高度 —— 实测撑到 14231 后它仍报
      // 14231，而页脚其实在 y=14734，于是又被挡在视口外。索性一步到位。
      //
      // 走过的两条弯路（都失败）：① 文档坐标 + captureBeyondViewport 会截回页面中段；
      // ② 撑高视口后 scrollTo 到元素，截图时滚动位置不保留，截成空白。
      const VH_MAX = 16384;
      await cdp.send('Emulation.setDeviceMetricsOverride', {
        width: 1440, height: VH_MAX, deviceScaleFactor: 1, mobile: false,
      });

      const box = await cdp.send('Runtime.evaluate', {
        expression: `(function(){var e=document.querySelector(${JSON.stringify(clip)});` +
                    `if(!e)return null;var r=e.getBoundingClientRect();` +
                    `return {x:r.x+window.scrollX,y:r.y+window.scrollY,w:r.width,h:r.height};})()`,
        returnByValue: true,
      });
      const b = box.result && box.result.value;
      if (!b || !b.w) {
        console.log(`\n■ 裁剪截图：选择器 ${clip} 未命中或尺寸为 0`);
      } else if (b.y + b.h > VH_MAX) {
        console.log(`\n■ 裁剪截图：目标底边 y=${Math.round(b.y + b.h)} 超出 Chrome ` +
                    `${VH_MAX}px 上限，无法撑高到该位置；改用 DOM 取证：` +
                    `--eval 'document.querySelector("...").innerText'`);
      } else {
        const shot = await cdp.send('Page.captureScreenshot', {
          format: 'png',
          clip: { x: b.x, y: b.y, width: Math.min(b.w, 1440), height: Math.min(b.h, 3000), scale: 1 },
        });
        fs.mkdirSync(path.dirname(clipOut), { recursive: true });
        fs.writeFileSync(clipOut, Buffer.from(shot.data, 'base64'));
        console.log(`\n■ 裁剪截图：${clip}  x=${Math.round(b.x)} y=${Math.round(b.y)} ` +
                    `w=${Math.round(b.w)} h=${Math.round(b.h)}  ->  ${clipOut}`);
      }
    }

    const bad = exceptions.length + consoleErrors.length;
    console.log('\n' + '='.repeat(78));
    console.log(bad === 0 ? '✅ 无 JS 异常、无 console error'
                          : `!! 共 ${bad} 条运行时错误需要处理`);
    console.log('='.repeat(78));

    ws.close(); cleanup();
    process.exit(0);
  } catch (e) {
    console.error('检查失败：', e.message);
    cleanup();
    process.exit(2);
  }
}

main();
