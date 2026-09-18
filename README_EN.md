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
| [`10x-learn`](skills/10x-learn/SKILL.md) | AI 10x-speed deep-learning loop: five-perspective STORM research plus the Feynman technique / retrieval practice, run as a ten-step closed loop through four stages (入门→资源→吃透→回顾), ending in a one-page cheat sheet | `系统学习` `吃透某领域` `快速入门` `10xLearn` `深度调研` |
| [`mini-code`](skills/mini-code/SKILL.md) | Safety-first code-slimming: classify redundancy first, hold the "no behavior change" bottom line, refuse test-less batch slimming and wrong test merges; proactively detects data-source fan-in redundancy and unnecessary abstractions (YAGNI); covers test-code slimming and acts as a safety guardrail (not initiator) for test-framework form refactoring; especially for AI-generated code | `精简代码` `去除冗余代码` `简化这段代码` `合并重复逻辑` `提取公共模块` `识别过度设计` `不必要的 class` `YAGNI` `合并同源读取` `测试代码精简` `clean up this code` `remove dead code` `consolidate duplicated I/O` `detect unnecessary abstraction` `slim test code` |
| [`software-story-design`](skills/software-story-design/SKILL.md) | Guides software design documents: purpose, design logic, concept-space modeling, core data structures, interface definitions, and consistency validation; covers new modules, feature extensions, performance optimization, refactoring, and complex system logical-view modeling (thread models, state machines) | `设计模块` `增加功能` `写设计文档` `@software-story-design` |
| [`software-ui-design`](skills/software-ui-design/SKILL.md) | Guides a complete UI design workflow from 0 to 1 for users new to design: goal definition, user & scenario research, competitor reference, scope convergence, information architecture, core task flows, low-fi layout, interaction & four-state design, visual design system (numeric specs with rationale, accessibility, copy tone), and testing — mining each stage with concrete questions and self-checks, producing PRD + DRD documents plus an interactive HTML prototype (grayscale skeleton → hi-fi) that visualizes the design instead of relying on text alone; supports cross-component annotation and alignment for multi-repo collaboration, and can export the visual spec to DESIGN.md on demand for long-term consumption by coding agents; also covers auditing an existing design-doc set — role boundary inventory, ten-stage coverage check, cross-layer reconciliation (document text / revision history / prototype / real code) and prototype runtime self-check, returning a graded P0/P1/P2 finding list without modifying source documents | `UI设计` `帮我设计界面` `带我走一遍UI设计流程` `UI设计文档` `设计文档审查` `@software-ui-design` |
| [`how-code-chain-work`](skills/how-code-chain-work/SKILL.md) | Forces a confidence-annotated call-chain report before modifying cross-function/file/module code; prevents missed side-effects from dynamic dispatch (interface/DI/event-bus/reflection); use before refactors, multi-caller changes, and impact analysis | `梳理链路` `调用关系` `影响面` `会不会漏改` `这个函数谁在调` `改这个安不安全` `追功能入口` |
| [`quick-note`](skills/quick-note/SKILL.md) | Two-tier decision logging: appends lightweight breadcrumbs (choice / rationale / alternatives / status) while implementing, and generates numbered formal ADRs when a decision is made or overturned by the user or by the AI itself after analysis; a bidirectional supersede chain links the two tiers so "why it was written that way → why it changed" stays traceable. The target location is resolved in order, first match wins (user-specified directory → existing `docs/` → reuse another ADR directory in the repo → create new); when an existing directory is reused, its numbering / naming / field / index conventions are followed; multi-repo workspace roots are supported, and a write falling outside version control is announced before it happens | `记录决策` `顺手记` `留痕` `ADR` `决策记录` `记录到某目录` `决策写进某目录` `breadcrumbs` `@quick-note` |

## Development Guide

### Standardization Rules

1. **Frontmatter**: `description` uses "Chinese trigger words + English functionality summary" bilingual format
2. **Terminology**: Body text in Chinese, but class names, APIs, config keys, and proper nouns remain in English (e.g., `ThreadPoolExecutor`, `JWT`, `epoll`)
3. **Environment Neutrality**: Use relative paths (e.g., `./src`) or placeholders (e.g., `<root_dir>`), no personal sensitive information
4. **Only whitelisted fields at the top level**: the top level allows only `name` / `description` / `license` / `allowed-tools` / `metadata` / `compatibility`. Repository-private fields (`agent_created`, `version`, etc.) must be nested under a top-level `metadata:` key instead of occupying a top-level key.

### Adding a New Skill

1. Create directory `skills/<skill-name>/`
2. Write `SKILL.md` (following the 4 rules above)
3. Update the Skills index table in this file
