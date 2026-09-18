# Skills — 技能仓库

本仓库包含一组可复用的 Skill，覆盖代码检视、架构分析、面试备战和经验提炼等场景。
A collection of reusable Skills covering code review, architecture analysis, interview prep, and experience distillation.

## Skills / 技能索引

以下是本仓库包含的所有 skill，点击链接查看完整文档。
All skills in this repository. Click links for full documentation.

| Skill | 功能说明 | 触发词 |
|---|---|---|
| [`multi-agent-github-mr-review`](skills/multi-agent-github-mr-review/SKILL.md) | 双 agent 对抗式代码审查，产出行级 DiffNote 并阻塞合入直到闭环 | `@某agent+MR链接` `GitHub MR 检视` `PR review` |
| [`code-analyzer-like-openwiki`](skills/code-analyzer-like-openwiki/SKILL.md) | 分析代码库生成含 Mermaid 图表的开放式技术 Wiki，支持本地预览 | `分析代码库` `生成技术文档` `梳理架构` `画架构图` |
| [`project-to-interview-experiences`](skills/project-to-interview-experiences/SKILL.md) | 将项目源码转化为可口述、防砸盘、带量化数据的面试备战讲稿 | `项目转面试` `面试备战` `面试讲稿` `面试经验` |
| [`superpowers-project-experiences`](skills/superpowers-project-experiences/SKILL.md) | 从已交付计划提炼可复用工程知识，建立模块索引，生成重生成提示词 | `总结经验` `归档` `建立索引` `更新索引` |
| [`jd-hunting`](skills/jd-hunting/SKILL.md) | 求职全流程辅助：简历解析→岗位推荐→招聘网站检索→适配度评分→定制简历与面试问答 | `找工作` `求职` `投递简历` `简历定制` `面试准备` |
| [`10x-learn`](skills/10x-learn/SKILL.md) | AI 十倍速深度学习闭环：五视角 STORM 调研 + 费曼/测试效应，按 入门→资源→吃透→回顾 四阶段输出提示词与一页速查表 | `系统学习` `吃透某领域` `快速入门` `10xLearn` `深度调研` |
| [`mini-code`](skills/mini-code/SKILL.md) | 安全的代码精简技能：先分类冗余、守住"行为不变"底线，拒绝无测试兜底的批量精简与错误合并测试；主动识别数据源扇入冗余与不必要的抽象（YAGNI）；覆盖测试代码精简，并对测试框架形态重构充当安全护栏（不主动发起）；尤适用于 AI 生成代码的去重与去过度设计 | `精简代码` `去除冗余代码` `简化这段代码` `合并重复逻辑` `提取公共模块` `识别过度设计` `不必要的 class` `YAGNI` `合并同源读取` `测试代码精简` `clean up this code` `remove dead code` `consolidate duplicated I/O` `detect unnecessary abstraction` `slim test code` |
| [`software-story-design`](skills/software-story-design/SKILL.md) | 软件设计文档撰写：设计目的、设计逻辑、概念空间建模、核心数据结构、接口定义与一致性校验；覆盖新建模块、功能扩展、性能优化、重构方案与复杂系统逻辑视图建模（线程模型、状态机等） | `设计模块` `增加功能` `写设计文档` `@software-story-design` |
| [`software-ui-design`](skills/software-ui-design/SKILL.md) | 从0到1的UI设计全流程引导（面向不懂设计流程的用户）：问题与目标、用户与场景、竞品与参考、范围与功能清单、信息架构、核心任务流程、低保真布局、交互细节与状态、视觉设计系统（含可执行数值规格及其理由、无障碍、文案基调）、测试与迭代，共十个阶段，逐阶段挖掘对齐，最终产出 PRD + DRD 设计文档与可交互 HTML 原型（灰阶骨架→高保真）展示设计效果；多仓库协同场景下支持跨组件标注与对齐，并可按需把视觉规格导出为 DESIGN.md 供编码 agent 长期消费；另含「审计已有设计文档」用法——默认不改源文档，按职责边界清点、十个阶段覆盖度体检、跨层对账（文档正文/修订史/原型/真实代码）与原型运行时自检，产出 P0/P1/P2 分级发现清单 | `UI设计` `帮我设计界面` `带我走一遍UI设计流程` `UI设计文档` `设计文档审查` `@software-ui-design` |
| [`how-code-chain-work`](skills/how-code-chain-work/SKILL.md) | 在修改跨函数/文件/模块代码前，强制先产出带置信度标注的调用链梳理报告，防止接口/DI/事件总线/反射等动态分发导致的漏改；适用于跨文件重构、被多处调用的函数、影响面排查 | `梳理链路` `调用关系` `影响面` `会不会漏改` `这个函数谁在调` `改这个安不安全` `追功能入口` |
| [`quick-note`](skills/quick-note/SKILL.md) | 双层决策留痕：写实现代码时顺手追加 breadcrumbs（选择/原因/备选/状态），用户或 AI 拍板、推翻方案时生成编号正式 ADR，两层以取代链互连，把"当初为什么这么写 → 后来为什么改"留成可追溯链条；落点先判顺序、命中即停（使用者显式指定 → 记录根下已有 `docs/` → 复用仓内其它 ADR 目录 → 新建），复用既有目录时跟随其编号/命名/字段/索引约定，支持多仓并列的工作区根，落点不在版本控制内时先说明再写 | `记录决策` `顺手记` `留痕` `为什么要这么写` `ADR` `决策记录` `记录到某目录` `决策写进某目录` `breadcrumbs` `@quick-note` |

## 开发规范 / Development Guide

### 标准化规范

1. **Frontmatter**：`description` 为"中文核心触发词 + 英文功能摘要"双语组合
2. **术语保留**：正文中文叙述，类名/API/配置 Key/专有名词保留英文原文（如 `ThreadPoolExecutor`、`JWT`、`epoll`）
3. **环境中立**：路径用相对路径（如 `./src`）或占位符（如 `<root_dir>`），无个人敏感信息
4. **Frontmatter 顶层只放白名单字段**：顶层仅允许 `name` / `description` / `license` / `allowed-tools` / `metadata` / `compatibility`。本仓库私有字段（`agent_created`、`version` 等）一律挂到顶层 `metadata:` 之下，不要占顶层 key。

### 新增 Skill

1. 创建目录 `skills/<skill-name>/`
2. 编写 `SKILL.md`（遵循上述 4 条规范）
3. 更新本文件 Skills 索引表格
