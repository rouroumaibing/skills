#!/usr/bin/env node
'use strict';
// software-ui-design 自带：「产出前上下文门」机械化兜底（自包含、可移植，仅用 Node 标准库）。
// 检查 PRD 的 §1（问题与目标 / 给谁用）与 §4（范围与功能清单 / 第一版做什么）
// 是否非空、且非纯占位。空白或纯占位即 FAIL（退出码 1），对应「缺失就 halt 逼问」的精神。
// 用法: node scripts/check-context-complete.js <PRD.md路径>
// 注意：脚本只是「地板」，真正拦住含糊需求的靠行为层的主动追问（见 SKILL.md「产出前上下文门」）。

const fs = require('fs');

const PRD = process.argv[2];
if (!PRD) {
  console.error('[check-context] 用法: node scripts/check-context-complete.js <PRD.md路径>');
  process.exit(2);
}
if (!fs.existsSync(PRD)) {
  console.error(`[check-context] PRD 文件不存在: ${PRD}`);
  process.exit(1);
}

const text = fs.readFileSync(PRD, 'utf8');

// 按 markdown 标题切分章节
const lines = text.split(/\r?\n/);
const sections = {};
let curKey = '__preamble__';
let curLines = [];
for (const line of lines) {
  const m = /^(#{1,6})\s+(.*)$/.exec(line);
  if (m) {
    sections[curKey] = curLines.join('\n');
    curKey = m[2].trim();
    curLines = [];
  } else {
    curLines.push(line);
  }
}
sections[curKey] = curLines.join('\n');

function findSection(keywords) {
  for (const k of Object.keys(sections)) {
    const kl = k.toLowerCase();
    for (const kw of keywords) {
      if (kl.includes(kw.toLowerCase())) return sections[k];
    }
  }
  return null;
}

// 占位判定：纯占位（待定/待补充/TBD/TODO/【假设】收尾/待确认收尾）且去掉后几乎无实义内容 → 视为未澄清
const PLACEHOLDER = /(待定|待补充|以后再想|tbd|todo|\[待补充\]|【假设】\s*$|待确认\s*$)/i;

function meaningfulLen(body) {
  return body
    .replace(PLACEHOLDER, '')
    .replace(/[：:，,。.\s]/g, '')
    .length;
}

function checkSection(name, body) {
  if (body === null || body === undefined) {
    console.error(`[check-context] FAIL: 未找到章节「${name}」（PRD 缺少该节 = 未澄清）`);
    return false;
  }
  const stripped = body.replace(/\s+/g, '').replace(/[#>*_`\-]/g, '');
  if (stripped.length < 4) {
    console.error(`[check-context] FAIL: 章节「${name}」内容过短/空白`);
    return false;
  }
  if (PLACEHOLDER.test(stripped) && meaningfulLen(stripped) < 6) {
    console.error(`[check-context] FAIL: 章节「${name}」仅为占位（待定/待补充/TBD 等），未真正澄清`);
    return false;
  }
  return true;
}

const s1 = findSection(['问题与目标', '1.', '定位', '目标用户', '给谁']);
const s4 = findSection(['范围与功能清单', '4.', 'must-have', '第一版', '功能清单']);

let ok = true;
ok = checkSection('§1 问题与目标（给谁用）', s1) && ok;
ok = checkSection('§4 范围与功能清单（第一版做什么）', s4) && ok;

if (!ok) {
  console.error('\n[check-context] 上下文门未过：生成 HTML 原型前必须先澄清「给谁用 / 第一版做什么」。');
  process.exit(1);
}
console.log('[check-context] 上下文门通过。');
process.exit(0);
