# jd-hunting 标准化 + README 更新实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 jd-hunting SKILL.md frontmatter 双语化，并在 README.md 和 README_EN.md 索引表格中新增该 skill

**Architecture:** 3 个文件的独立编辑，无依赖关系

**Tech Stack:** Markdown

## Global Constraints

- 不改动 SKILL.md 正文（已符合规范）
- 触发词保留中文原文
- 无绝对路径、无个人敏感信息

---

### Task 1: SKILL.md frontmatter 双语化

**Files:**
- Modify: `skills/jd-hunting/SKILL.md` (line 3)

- [ ] **Step 1: 替换 description**

将第 3 行：
```
description: "Chinese job-hunting assistant: resume parsing, role suggestions, multi-site job search via Chrome, fit scoring, tailored resume + interview prep. Invoke when user wants 找工作/求职/投递简历/简历定制/面试准备."
```
替换为：
```
description: "触发词：找工作、求职、投递简历、简历定制、面试准备。Full-cycle job hunting assistant: resume parsing, role suggestions, multi-site job search, fit scoring, tailored resume and interview prep."
```

- [ ] **Step 2: 验证**

Run: `head -4 skills/jd-hunting/SKILL.md`
Expected: 第 3 行以 `触发词：找工作` 开头

- [ ] **Step 3: Commit**

```bash
git add skills/jd-hunting/SKILL.md
git commit -m "refactor: standardize jd-hunting frontmatter to bilingual format"
```

---

### Task 2: README.md 索引表格加行

**Files:**
- Modify: `README.md` (line 16, after superpowers-project-experiences row)

- [ ] **Step 1: 在表格末尾追加一行**

在第 16 行（`superpowers-project-experiences` 行）之后插入：
```
| [`jd-hunting`](skills/jd-hunting/SKILL.md) | 求职全流程辅助：简历解析→岗位推荐→招聘网站检索→适配度评分→定制简历与面试问答 | `找工作` `求职` `投递简历` `面试准备` |
```

- [ ] **Step 2: 验证**

Run: `grep jd-hunting README.md`
Expected: 匹配到含 `求职全流程辅助` 的行

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: add jd-hunting to README skills index"
```

---

### Task 3: README_EN.md table add row

**Files:**
- Modify: `README_EN.md` (line 14, after superpowers-project-experiences row)

- [ ] **Step 1: Append row to table**

在第 14 行（`superpowers-project-experiences` 行）之后插入：
```
| [`jd-hunting`](skills/jd-hunting/SKILL.md) | Full-cycle job hunting: resume parsing, role suggestions, job search, fit scoring, tailored resume and interview prep | `找工作` `求职` `投递简历` `面试准备` |
```

- [ ] **Step 2: Verify**

Run: `grep jd-hunting README_EN.md`
Expected: matches line containing `Full-cycle job hunting`

- [ ] **Step 3: Commit**

```bash
git add README_EN.md
git commit -m "docs: add jd-hunting to README_EN skills index"
```
