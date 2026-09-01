# Skills — Skill Repository

A collection of reusable Skills covering code review, architecture analysis, interview prep, and experience distillation.

## Skills

All skills in this repository. Click links for full documentation.

| Skill | Description | Triggers |
|---|---|---|
| [`multi-agent-github-mr-review`](skills/multi-agent-github-mr-review/SKILL.md) | Adversarial code review with dual-agent verification, producing line-level DiffNotes and blocking merge until closure | `@某agent+MR链接` `GitHub MR 检视` `PR review` |
| [`code-analyzer-like-openwiki`](skills/code-analyzer-like-openwiki/SKILL.md) | Analyzes codebase to generate open technical Wiki with Mermaid diagrams, supports local preview | `分析代码库` `生成技术文档` `梳理架构` `画架构图` |
| [`project-to-interview-experiences`](skills/project-to-interview-experiences/SKILL.md) | Converts project source into oral, defensive, quantified interview prep handbook | `项目转面试` `面试备战` `面试讲稿` `面试经验` |
| [`superpowers-project-experiences`](skills/superpowers-project-experiences/SKILL.md) | Distills reusable engineering knowledge from delivered plans, builds module index, generates regeneration prompts | `总结经验` `归档` `建立索引` `更新索引` |
| [`jd-hunting`](skills/jd-hunting/SKILL.md) | Full-cycle job hunting: resume parsing, role suggestions, job search, fit scoring, tailored resume and interview prep | `找工作` `求职` `投递简历` `简历定制` `面试准备` |
| [`mini-code`](skills/mini-code/SKILL.md) | Safety-first code-slimming: classify redundancy first, hold the "no behavior change" bottom line, refuse test-less batch slimming and wrong test merges; proactively detects data-source fan-in redundancy and unnecessary abstractions (YAGNI); covers test-code slimming and acts as a safety guardrail (not initiator) for test-framework form refactoring; especially for AI-generated code | `精简代码` `去除冗余代码` `简化这段代码` `合并重复逻辑` `提取公共模块` `识别过度设计` `不必要的 class` `YAGNI` `合并同源读取` `测试代码精简` `clean up this code` `remove dead code` `consolidate duplicated I/O` `detect unnecessary abstraction` `slim test code` |
| [`software-story-design`](skills/software-story-design/SKILL.md) | Guides software design documents: purpose, design logic, concept-space modeling, core data structures, interface definitions, and consistency validation; covers new modules, feature extensions, performance optimization, refactoring, and complex system logical-view modeling (thread models, state machines) | `设计模块` `增加功能` `写设计文档` `@software-story-design` |

## Development Guide

### Standardization Rules

1. **Frontmatter**: `description` uses "Chinese trigger words + English functionality summary" bilingual format
2. **Terminology**: Body text in Chinese, but class names, APIs, config keys, and proper nouns remain in English (e.g., `ThreadPoolExecutor`, `JWT`, `epoll`)
3. **Environment Neutrality**: Use relative paths (e.g., `./src`) or placeholders (e.g., `<root_dir>`), no personal sensitive information

### Adding a New Skill

1. Create directory `skills/<skill-name>/`
2. Write `SKILL.md` (following the 3 rules above)
3. Update the Skills index table in this file
