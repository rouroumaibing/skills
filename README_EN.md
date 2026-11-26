# Skills — Skill Repository

A collection of reusable Skills covering code review, architecture analysis, interview prep, and experience distillation.

## Skills

All skills in this repository. Click links for full documentation.

| Skill | Description | Triggers |
|---|---|---|
| [`multi-agent-github-mr-review`](skills/multi-agent-github-mr-review/SKILL.md) | Adversarial dual-agent code review producing line-level DiffNotes, blocking merge until closure | `@某agent+MR链接` `GitHub MR 检视` `PR review` `本地生成代码后自检` `提交前代码审查` |
| [`code-analyzer-like-openwiki`](skills/code-analyzer-like-openwiki/SKILL.md) | Analyzes a codebase into an open technical Wiki with Mermaid diagrams (reference + onboarding modes); read-only | `分析代码库` `生成技术文档` `梳理架构` `画架构图` `新人上手` `onboarding` `第一篇PR` `技术教程` `了解项目` `代码库百科` `架构图` `依赖关系图` `feature story` `新人怎么上手这个项目` |
| [`project-to-interview-experiences`](skills/project-to-interview-experiences/SKILL.md) | Converts project source into an oral, defensive, quantified interview prep handbook | `项目转面试` `面试备战` `面试讲稿` `面试经验` |
| [`superpowers-project-experiences`](skills/superpowers-project-experiences/SKILL.md) | Distills reusable engineering knowledge from delivered plans, builds module index, generates regeneration prompts | `总结经验` `归档` `建立索引` `更新索引` |
| [`jd-hunting`](skills/jd-hunting/SKILL.md) | Full-cycle job hunting: resume parsing, role suggestions, fit scoring, tailored resume and interview prep | `找工作` `求职` `投递简历` `简历定制` `面试准备` |
| [`10x-learn`](skills/10x-learn/SKILL.md) | AI 10x deep-learning loop: multi-perspective research plus Feynman/retrieval practice, four stages ending in a one-page cheat sheet | `系统学习` `吃透某领域` `快速入门` `10xLearn` `深度调研` |
| [`software-story-design`](skills/software-story-design/SKILL.md) | Functional/logical design docs: concept-space modeling, core data structures, interface definitions, consistency validation | `设计模块` `增加功能` `写设计文档` `@software-story-design` |
| [`software-ui-design`](skills/software-ui-design/SKILL.md) | 0→1 UI design workflow for design novices, producing PRD + DRD and an interactive HTML prototype; also audits design docs (report-only) | `UI设计` `帮我设计界面/产品/App/平台` `带我走一遍UI设计流程` `UI设计文档` `这个产品的UI该怎么做` `帮我看看这套设计文档有没有问题` `设计文档审查` `@software-ui-design` |
| [`how-code-chain-work`](skills/how-code-chain-work/SKILL.md) | Forces a confidence-annotated call-chain report before cross-function/file/module changes; prevents dynamic-dispatch misses | `梳理链路` `调用关系` `影响面` `会不会漏改` `这个函数谁在调` `改这个安不安全` `追功能入口` `跨文件重构` `漏改风险` `排查问题` `复现bug` `为什么会出这个问题` `删除方法` `删除函数` `删除符号` `字段改造` `字段数据流` `字段读写` `只读不写` `@how-code-chain-work` |
| [`harness-workflow`](skills/harness-workflow/SKILL.md) | Audits AI dev artifacts against the goal contract and rule registry with evidence-backed reports, degradation flags and landing advice; report-only | `审计产物` `审查` `检查对否` `artifact 校验` `契约核查` `证据核对` `缺口清单` `目标契约` `规则登记表` `升级触发清单` `对抗推演` `human-in-the-loop` `先定契约再动手` `防 AI 虚报` `验证体系缺什么` `给项目加验证环节` `AI 写代码怎么验证` `审计报告怎么落地` `给建议排落地顺序` `先做哪个` |
| [`clean-change-comments`](skills/clean-change-comments/SKILL.md) | Comments state one-sentence function + I/O params, config comments state what the switch controls, history goes to commits; ships a residual scanner | `清理注释` `整理注释` `删掉变更叙述` `注释规范` `文档注释` `只描述现状` `不要写原来是/改为/不再` `变更记录` `决策记录` `决策时间` `提交前自查注释` `顺手清理残留注释` `代码里为什么有历史说明` `注释复述实现逻辑` `注释解释代码在做什么` `函数注释写过详` `AI生成注释啰嗦` `写注释只该一句功能加参数` `配置注释只说开关功能` `配置注释冗余` `配置注释过详` `配置只描述用户字段` `配置泄漏实现细节` `改代码` `写或改代码注释` `改文档正文时` `@clean-change-comments` |
| [`troubleshooting`](skills/troubleshooting/SKILL.md) | Unified troubleshooting entry: env logs, cross-repo code search, version-switched code location; writes go through apply.py whitelist + backup | `排查问题` `定位问题` `查日志` `查 pod` `客户报错` `某组件某版本出问题` `切换到某个版本看代码` `多仓库搜代码` `连环境看机器/集群/节点日志` |
| [`troubleshooting-knowledge`](skills/troubleshooting-knowledge/SKILL.md) | Distills troubleshooting conclusions into redacted knowledge entries in knowledge/ (oss/personal tiers); searchable, updatable, deprecable | `提取知识` `沉淀知识` `写知识库` `知识库整理` `检索知识库` `建知识条目` `把这次排查结论存下来` `知识库里有没有关于 X 的记录` |
| [`skill-freshness-audit`](skills/skill-freshness-audit/SKILL.md) | Audits a skill collection for freshness/redundancy/factual rot, classified as persist/sunset/never-remove; report-only | `审计skill库` `检查skill是否过时` `skill保质期` `哪些skill冗余` `skill特有性检查` `生成skill审计清单` `模型升级后复查skill` `清理/精简过时的skill` |
| [`automation-task-execution`](skills/automation-task-execution/SKILL.md) | AI-assisted task execution and code delivery protocol (code development as the primary scenario, automation tests as one deliverable type): gap analysis → real exploration → smoke test → batch implementation → knowledge capture, closed by a nine-item reconciliation | `代码开发` `写代码` `改代码` `重构` `代码交付` `部署脚本` `CLI` `配置工具` `自动化任务` `把手工流程转成自动化代码 / 用例` `接口自动化` `UI 自动化` `E2E 脚本` `冒烟跑通` `探索性执行` `缺口分析` `流程缺什么信息` `质量门` `代码纪律` `交付质量` |
| [`ambitious`](skills/ambitious/SKILL.md) | Rewrites tasks into more ambitious versions along altitude/depth/breadth; proposes a plan and waits for confirmation | `更有野心` `把任务想高/想深/想宽` `目标抬升` `深度改写` `外部验证` `/ambitious` `@ambitious` |
| [`skillormcp`](skills/skillormcp/SKILL.md) | Decides Skill vs MCP, audits placement misalignment and reports deployment shape; advisory only | `判定skill还是MCP` `skill和mcp怎么选` `落点审计` `能力错位` `下沉mcp` `拆分建议` `新增能力该放哪` `skill是否在干MCP的活` `发布前核对落点` `@skillormcp` |
| [`trigger-eval`](skills/trigger-eval/SKILL.md) | Subagent-proxy trigger evaluation for a local skill set, reporting hit rate / FP / FN and a confusion matrix; zero-login replacement for the official eval harness | `触发评测` `skill 触发测试` `trigger eval` `description 命中率` `误触发` `漏触发` `skill 冲突检测` |

**Audit rule**: Before adding or changing content under `skills/` and `mcps/`, you may invoke `@skillormcp` (or `/skillormcp`) to audit placement for misplacement (capability mismatch). The skill is advisory only — it reports, never edits.

## Development Guide

### Standardization Rules

1. **Frontmatter**: `description` uses "Chinese trigger words + one self-contained responsibility summary (technical terms in English)" format
2. **Terminology**: Body text in Chinese, but class names, APIs, config keys, and proper nouns remain in English (e.g., `ThreadPoolExecutor`, `JWT`, `epoll`)
3. **Environment Neutrality**: Use relative paths (e.g., `./src`) or placeholders (e.g., `<root_dir>`), no personal sensitive information
4. **Only whitelisted fields at the top level**: the top level allows only `name` / `description` / `license` / `allowed-tools` / `metadata` / `compatibility`. Repository-private fields (`agent_created`, `version`, etc.) must be nested under a top-level `metadata:` key instead of occupying a top-level key.
5. **Self-contained description**: `description` should reinforce this skill's own responsibilities ("this skill does X / does not cover Y"), not be written as a comparison against a specific sibling skill (e.g. "differences from X: ..."). Express boundaries with self-contained wording such as "does not cover ..." / "does not do ...", avoiding naming other skills.

6. **Single source of truth for mutable data**: when a skill owns runtime-written data (knowledge bases, hit-telemetry logs, etc.), the repository is the only source of truth; the corresponding paths in the installed copy must be symlinks into the repository, never independent copies — otherwise runtime writes land only in the copy and never reach the repository. When syncing the installed copy, sync static code only (SKILL.md / scripts / references), skip symlinks, and never overwrite a symlink back into a real directory.

### Adding a New Skill

1. Create directory `skills/<skill-name>/`
2. Write `SKILL.md` (following the 6 rules above)
3. Generate ephemeral tests per "Testing Convention" and run them green (test files are **not committed**)
4. Update the Skills index table in this file
5. Before committing, verify it passes the skill-creator validator
### Testing Convention

- Neither the skill body nor this repository keeps any test code — tests are ephemeral proof-of-capability artifacts: generated when needed, discarded after use, never shipped with the skill, never version-controlled; they can be regenerated at any time and never constrain skill iteration.
- When to test: when adding a skill, or changing a skill's matching / security / execution-chain logic, generate tests in a temp directory (e.g. `/tmp/tests-<skill-name>/`) and run them green:
  1. **Offline regression** (mandatory): no environment access, imports the skill's scripts directly to cover matching, validation and security regression points;
  2. **End-to-end simulation** (when the skill has external interactions, e.g. SSH / kubectl / db): local fake services + stubs through the real CLI entrypoints, everything under /tmp, no real environment touched.
- Both layers must pass (exit 0) before delivery; the test **result** (counts, scenarios, outcome) goes into the delivery note or commit message, while the test **files** themselves are not kept.
- **Trigger verification**: after changing a `description` or trigger-related logic, if skill-creator's built-in eval is unavailable (`scripts/run_loop.py` depends on the `claude -p` CLI; when not logged in it silently judges every query False and still exits 0 — fake-red disguised as green), fall back to the `trigger-eval` subagent-proxy method instead (see `skills/trigger-eval/SKILL.md`): run old and new rounds with the same query set and the same judge constraints, changing only the description under test; zero diff counts as no regression.
- **Test targets never leak into the skill body**: real services / environments are verification scaffolding only. Findings must be written back into the skill in **generalized form** — keep the conclusion (e.g. "credentials need server-side renewal"), never the observed object; environment names, service names, internal domains, instance IDs and concrete endpoints must not appear in SKILL.md, scripts, templates, comments or test samples. Pre-delivery check: `grep -ri <env-name> skills/<skill-name>/` must return 0 matches.
