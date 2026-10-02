#!/usr/bin/env node
/**
 * cdp_probe.js —— 用 Chrome DevTools Protocol 精确判定「页面自己发了哪些外部请求」。
 *
 * 【为什么需要它】
 *   用「拦截代理」时，代理会把**浏览器进程自身**的后台请求（校时、组件更新、
 *   账号一致性检查）和**页面发起**的请求混在一起。仅看主机名分不开这两类，
 *   而按「相对 about:blank 的增量」去减又会因为页面存活时长不同而误判。
 *
 *   CDP 的 Network.requestWillBeSent 只在**页面目标**上触发：
 *   浏览器进程自己的请求压根不会出现在这里。因此这是一份与时间无关的、
 *   精确到「谁发起」的清单 —— 唯一权威的判据。
 *
 * 依赖：Node 22+ 内置的全局 WebSocket，无需 npm install。
 *
 * 用法：
 *   node _tools/cdp_probe.js --port 8765
 */

const { spawn } = require('child_process');
const http = require('http');
const fs = require('fs');
const os = require('os');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');

const CHROME_CANDIDATES = [
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
];

const PAGES = [
  ['01-home', '/'],
  ['02-search', '/search/'],
  ['03-search-pac', '/search/?title_like=pac'],
  ['04-search-topic', '/search/?topic_tags=Birthday'],
  ['05-about', '/about/'],
  ['07-doodle-still', '/doodle/celebrating-the-staurikosaurus/'],
  ['08-doodle-game', '/doodle/30th-anniversary-of-pac-man/'],
  ['09-doodle-mobile', '/doodle/fourth-of-july-2022/'],
  ['10-game-raw', '/games/30th-anniversary-of-pac-man/pacman10-hp.html'],
];

/**
 * 找出某个游戏目录的入口页面。
 * 目录里的 _entry.txt 记录了原始地址，例如：
 *     game_url: https://google.com/logos/2015/ponyexpress/ponyexpress15.html
 * 本地入口就是该 URL 的文件名。没有 game_url 时退化为「目录里唯一的 html」。
 * 24 个目录当年就没抓到 HTML（只有 _entry.txt），返回 null。
 */
function resolveGameEntry(dir) {
  const ent = path.join(dir, '_entry.txt');
  let htmls = [];
  try {
    htmls = fs.readdirSync(dir)
      .filter((f) => f.toLowerCase().endsWith('.html') &&
                     f.toLowerCase() !== 'index.html');
  } catch (_) { return null; }
  if (fs.existsSync(ent)) {
    const m = fs.readFileSync(ent, 'utf8').match(/game_url:\s*(\S+)/);
    if (m) {
      const cand = m[1].replace(/\/+$/, '').split('/').pop();
      if (htmls.includes(cand)) return cand;
    }
  }
  if (htmls.length === 1) return htmls[0];
  if (htmls.length > 1) return htmls.sort((a, b) => a.length - b.length)[0];
  return null;
}

/**
 * 大样本模式：在 10 个代表页之外，再抽 N 个详情页 + 若干互动游戏。
 * gamesAll=true 时把**所有**能解析出入口页的互动涂鸦都测一遍。
 * 用确定性随机（种子固定），保证可复现。
 */
function buildSample(siteDir, n, gamesAll) {
  const out = PAGES.slice();
  const rnd = mulberry32(arg('seed', '20260925') >>> 0);

  // 详情页：site/doodle/<slug>/index.html
  const doodleRoot = path.join(siteDir, 'doodle');
  let slugs = [];
  try {
    slugs = fs.readdirSync(doodleRoot, { withFileTypes: true })
      .filter((d) => d.isDirectory() &&
                     fs.existsSync(path.join(doodleRoot, d.name, 'index.html')))
      .map((d) => d.name);
  } catch (_) {}
  slugs.sort();
  const pickSlugs = shuffle(slugs, rnd).slice(0, n);
  pickSlugs.forEach((s, i) =>
    out.push([`s${String(i + 1).padStart(3, '0')}-${s}`.slice(0, 40),
              `/doodle/${s}/`]));

  // 互动游戏：逐个解析入口页
  const gamesRoot = path.join(siteDir, 'games');
  const gameEntries = [];
  const unresolved = [];
  try {
    for (const d of fs.readdirSync(gamesRoot, { withFileTypes: true })) {
      if (!d.isDirectory()) continue;
      const dir = path.join(gamesRoot, d.name);
      const name = resolveGameEntry(dir);
      if (name) gameEntries.push(`/games/${d.name}/${name}`);
      else unresolved.push(d.name);
    }
  } catch (_) {}
  gameEntries.sort();
  const pickGames = gamesAll ? gameEntries : shuffle(gameEntries, rnd).slice(0, n);
  pickGames.forEach((u, i) =>
    out.push([`g${String(i + 1).padStart(3, '0')}-` + u.split('/')[2].slice(0, 34),
              u]));

  console.log(`互动涂鸦：可解析入口页 ${gameEntries.length} 个，` +
              `本次测试 ${pickGames.length} 个；` +
              `${unresolved.length} 个目录当年未抓到 HTML（跳过）`);

  out.push(['zz-offline', '/offline/']);
  return out;
}

function mulberry32(a) {
  return function () {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function shuffle(arr, rnd) {
  const a = arr.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(rnd() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}


const DEBUG_PORT = 19222;

function arg(name, dflt) {
  const i = process.argv.indexOf('--' + name);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : dflt;
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
      res.on('end', () => {
        try { resolve(JSON.parse(d)); } catch (e) { reject(e); }
      });
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
    } catch (_) { /* 还没起来 */ }
    await sleep(300);
  }
  throw new Error('DevTools 端口未就绪');
}

/** 极简 CDP 客户端：只用到 send + on(event) */
class CDP {
  constructor(ws) {
    this.ws = ws;
    this.id = 0;
    this.pending = new Map();
    this.listeners = new Map();
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

function isLocal(u) {
  try {
    const h = new URL(u).hostname.toLowerCase();
    return h === '127.0.0.1' || h === 'localhost' || h === '::1';
  } catch (_) { return false; }
}

async function main() {
  const port = arg('port', '8765');
  const chrome = findChrome();
  const profile = path.join(ROOT, '_tools', '.chrome-profile-cdp');
  fs.mkdirSync(profile, { recursive: true });

  const proc = spawn(chrome, [
    '--headless=new', '--disable-gpu', '--no-sandbox', '--hide-scrollbars',
    '--disable-extensions', '--no-first-run', '--disable-sync',
    '--disable-component-update', '--disable-background-networking',
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

    // 只启用页面目标的 Network —— 浏览器进程的请求不会上报到这里
    await cdp.send('Network.enable');
    await cdp.send('Network.setCacheDisabled', { cacheDisabled: true });
    await cdp.send('Page.enable');

    let bucket = [];
    cdp.on('Network.requestWillBeSent', (p) => {
      bucket.push({
        url: p.request.url,
        method: p.request.method,
        type: p.type,
        initiator: (p.initiator && p.initiator.type) || '?',
      });
    });

    console.log('='.repeat(78));
    console.log('CDP 精确探测：只统计「页面目标」自己发起的请求');
    console.log(`本地站点 http://127.0.0.1:${port}/`);
    console.log('='.repeat(78));

    const sample = parseInt(arg('sample', '0'), 10);
    const gamesAll = process.argv.includes('--games-all');
    const pages = (sample > 0 || gamesAll)
      ? buildSample(path.join(ROOT, 'site'), sample, gamesAll)
      : PAGES;
    if (sample > 0 || gamesAll) {
      console.log(`模式：代表页 ${PAGES.length} 个 + 详情页抽样 ${sample} 个` +
                  `${gamesAll ? ' + 全部互动涂鸦' : ' + 互动涂鸦抽样 ' + sample + ' 个'}` +
                  `（种子 ${arg('seed', '20260925')}）`);
      console.log('='.repeat(78));
    }

    const results = [];
    let totalExternal = 0;
    let done = 0;

    for (const [name, p] of pages) {
      const url = `http://127.0.0.1:${port}${p}`;
      bucket = [];
      await cdp.send('Page.navigate', { url });
      await sleep((sample > 0 || gamesAll) ? 5000 : 7000);   // 等页面把该发的都发完

      const all = bucket.slice();
      const external = all.filter((r) => !isLocal(r.url) && !r.url.startsWith('data:') &&
                                          !r.url.startsWith('blob:') && !r.url.startsWith('about:'));
      totalExternal += external.length;
      results.push({ name, url, requests: all.length, external, ok: external.length === 0 });

      done++;
      if (sample > 0 || gamesAll) {
        // 大样本时只打印异常项，避免刷屏
        if (external.length) {
          console.log(`!! ${name}  外部请求 ${external.length} 个`);
          for (const r of external) {
            console.log(`        · [${r.initiator}/${r.type}] ${r.url.slice(0, 120)}`);
          }
        } else if (done % 10 === 0) {
          console.log(`   … 已完成 ${done}/${pages.length}（暂无外部请求）`);
        }
      } else {
        const flag = external.length === 0 ? 'OK ' : '!! ';
        console.log(`${flag}${name.padEnd(18)} 页面请求 ${String(all.length).padStart(5)} 个` +
                    `   外部请求 ${String(external.length).padStart(3)} 个`);
        for (const r of external) {
          console.log(`        · [${r.initiator}/${r.type}] ${r.url.slice(0, 120)}`);
        }
      }
    }

    console.log('\n' + '='.repeat(78));
    const heads = results.filter((r) => r.external.length);
    console.log(`页面数            : ${results.length}`);
    console.log(`外部请求总数      : ${totalExternal}`);
    console.log('='.repeat(78));
    if (totalExternal === 0) {
      console.log('\n✅ 所有页面在渲染期都没有发起任何外部请求（CDP 页面目标级证据）。');
    } else {
      console.log('\n以下页面发起了外部请求：');
      for (const r of heads) {
        console.log(`  · ${r.name}`);
        for (const x of r.external) console.log(`      ${x.url.slice(0, 140)}`);
      }
    }

    fs.writeFileSync(path.join(ROOT, '_tools', 'cdp_probe_result.json'),
                     JSON.stringify({ results }, null, 2), 'utf8');

    ws.close();
    cleanup();
    process.exit(totalExternal === 0 ? 0 : 1);
  } catch (e) {
    console.error('探测失败：', e.message);
    cleanup();
    process.exit(2);
  }
}

main();
