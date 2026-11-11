#!/usr/bin/env node
'use strict';
// software-ui-design 自带：HTML 原型机器验证门（自包含、可移植，仅用 Node 标准库）。
// 三步 gate：① 内联脚本语法解析  ② DOM stub 渲染（抓运行时致命错）  ③ 标记层泄漏令牌扫描。
// 用法: node scripts/verify-prototype.js <html-file> [html-file ...]
// 退出码: 0=通过, 1=失败, 2=用法错误。
// 设计取舍：本脚本是「机器强制」门，对应仓库既有「工具层兜底」铁律，可移植（不硬编码本机 node 路径）。

const fs = require('fs');
const vm = require('vm');

const FILES = process.argv.slice(2);
if (FILES.length === 0) {
  console.error('[verify-prototype] 用法: node scripts/verify-prototype.js <html-file> [...]');
  process.exit(2);
}

// 这些令牌出现在「标记层（HTML 文本，非 <script> 内）」时几乎总是 bug：数据绑定失败漏出的 undefined、
// 数值计算失败漏出的 NaN、对象被直接 toString 的 [object Object]、以及未被替换的模板占位 ${...}。
const LEAK = ['undefined', 'NaN', '[object Object]'];
let fails = 0;
const fail = (f, m) => { fails++; console.error(`[FAIL] ${f}: ${m}`); };

function inlineScripts(html) {
  const out = [];
  const re = /<script\b([^>]*)>([\s\S]*?)<\/script>/gi;
  let m;
  while ((m = re.exec(html)) !== null) {
    if (/\bsrc\s*=/.test(m[1] || '')) continue; // 外链脚本不内联检查
    out.push(m[2]);
  }
  return out;
}
function stripScripts(html) {
  // 去掉 <script>...</script>，只留标记层，避免 JS 源码里的合法 ${} / undefined 误报
  return html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '');
}
function domStub() {
  const node = () => ({
    innerHTML: '', textContent: '', style: {}, dataset: {},
    children: [],
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    appendChild(c) { this.children.push(c); return c; },
    removeChild(c) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); },
    setAttribute() {}, getAttribute() { return null; },
    addEventListener() {}, removeEventListener() {},
    querySelector() { return node(); }, querySelectorAll() { return []; },
    getElementById() { return node(); }, append() {}, prepend() {}, focus() {}, blur() {}, click() {},
    parentNode: null,
  });
  const document = {
    documentElement: node(), // 真实浏览器恒有 <html>；原型常访问 document.documentElement.dataset（主题防白闪），缺它会假失败
    body: node(), head: node(),
    createElement: () => node(), createTextNode: (t) => ({ textContent: t }),
    getElementById: () => node(), querySelector: () => node(), querySelectorAll: () => [],
    addEventListener() {},
  };
  const window = {
    document, console,
    addEventListener() {}, location: { href: '', hash: '' },
    setTimeout: (fn) => { try { fn(); } catch (e) {} return 0; }, clearTimeout() {},
    requestAnimationFrame: (fn) => { try { fn(); } catch (e) {} return 0; },
  };
  return { document, window };
}
function scanLeaks(text, where, file) {
  for (const tok of LEAK) {
    if (text.indexOf(tok) !== -1) fail(file, `标记层含泄漏令牌 "${tok}" (${where})`);
  }
  if (text.indexOf('${') !== -1) fail(file, `标记层含未替换的 \`\${...}\` 占位 (${where})`);
}

for (const file of FILES) {
  if (!fs.existsSync(file)) { fail(file, '文件不存在'); continue; }
  let html;
  try { html = fs.readFileSync(file, 'utf8'); }
  catch (e) { fail(file, `读取失败: ${e.message}`); continue; }

  const scripts = inlineScripts(html);

  // Gate 1: 语法解析（vm.Script 仅编译不执行，抓致命语法错，比开浏览器快）
  for (let i = 0; i < scripts.length; i++) {
    try { new vm.Script(scripts[i], { filename: `${file}#script-${i + 1}` }); }
    catch (e) { fail(file, `内联脚本#${i + 1} 语法错误: ${e.message}`); }
  }

  // Gate 2: DOM stub 渲染（在隔离上下文执行，抓运行时致命错，如引号截断、未定义引用）
  const { document, window } = domStub();
  const sandbox = {
    document, window, console,
    localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
    setTimeout: window.setTimeout, clearTimeout, requestAnimationFrame: window.requestAnimationFrame,
    location: window.location,
    addEventListener() {}, removeEventListener() {},
    Math, Date, JSON, Array, Object, String, Number, Boolean, RegExp,
  };
  sandbox.window = sandbox; // 部分脚本用 window.xxx，也用全局 xxx
  vm.createContext(sandbox);
  for (let i = 0; i < scripts.length; i++) {
    try { vm.runInContext(scripts[i], sandbox, { filename: `${file}#script-${i + 1}` }); }
    catch (e) { fail(file, `内联脚本#${i + 1} 运行时抛错: ${e.message}`); }
  }

  // Gate 3: 标记层泄漏令牌扫描（只扫 HTML 文本，不扫 <script> 源码，避免误报）
  scanLeaks(stripScripts(html), 'markup', file);

  // Gate 4（增强）：若原型暴露 render()/init()/renderAll()/mount()，调用后复扫其产物
  for (const fn of ['render', 'init', 'renderAll', 'mount']) {
    if (typeof sandbox[fn] === 'function') {
      try {
        const ret = sandbox[fn]();
        const out = (ret && (ret.outerHTML || ret.innerHTML)) || (document.body && document.body.innerHTML) || '';
        scanLeaks(String(out), `fn:${fn}`, file);
      } catch (e) { fail(file, `调用 ${fn}() 抛错: ${e.message}`); }
    }
  }
}

if (fails > 0) {
  console.error(`\n[verify-prototype] 共 ${fails} 项失败，拒绝交付。`);
  process.exit(1);
}
console.log('[verify-prototype] 全部通过。');
process.exit(0);
