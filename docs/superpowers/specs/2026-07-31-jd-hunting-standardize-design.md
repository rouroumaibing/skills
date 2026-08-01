# jd-hunting Skill 标准化 + README 更新设计

> **日期**: 2026-07-31
> **状态**: design

## 目标

将新增的 `jd-hunting` skill 按 3 条标准化规范改造 frontmatter，并更新 README.md 和 README_EN.md 索引表格。

## 改动范围

### 1. `skills/jd-hunting/SKILL.md` — Frontmatter 双语化

**当前 description**:
```
Chinese job-hunting assistant: resume parsing, role suggestions, multi-site job search via Chrome, fit scoring, tailored resume + interview prep. Invoke when user wants 找工作/求职/投递简历/简历定制/面试准备.
```

**改为**:
```
触发词：找工作、求职、投递简历、简历定制、面试准备。Full-cycle job hunting assistant: resume parsing, role suggestions, multi-site job search, fit scoring, tailored resume and interview prep.
```

**正文不动**：已符合 3 条规范（中文叙述 + 英文术语保留如 `TRAE-browseruse-external`/`CDP`/`WebSearch`/`STAR`；无绝对路径；无个人敏感信息；跨平台中立）。

### 2. `README.md` — Skills 索引表格新增一行

在表格末尾（`superpowers-project-experiences` 行之后）追加：

```
| [`jd-hunting`](skills/jd-hunting/SKILL.md) | 求职全流程辅助：简历解析→岗位推荐→招聘网站检索→适配度评分→定制简历与面试问答 | `找工作` `求职` `投递简历` `面试准备` |
```

### 3. `README_EN.md` — Skills table add one row

在表格末尾（`superpowers-project-experiences` 行之后）追加：

```
| [`jd-hunting`](skills/jd-hunting/SKILL.md) | Full-cycle job hunting: resume parsing, role suggestions, job search, fit scoring, tailored resume and interview prep | `找工作` `求职` `投递简历` `面试准备` |
```

## 约束

- 不改动 SKILL.md 正文（已符合规范）
- 不改动 prompts.md 和 jb-web.md（不在本次范围内）
- 触发词保留中文原文（实际触发关键词）
