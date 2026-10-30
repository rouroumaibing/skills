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
| [`software-story-design`](skills/software-story-design/SKILL.md) | 软件设计文档撰写：设计目的、设计逻辑、概念空间建模、核心数据结构、接口定义与一致性校验；覆盖新建模块、功能扩展、性能优化、重构方案与复杂系统逻辑视图建模（线程模型、状态机等） | `设计模块` `增加功能` `写设计文档` `@software-story-design` |
| [`software-ui-design`](skills/software-ui-design/SKILL.md) | 从0到1的UI设计全流程引导（面向不懂设计流程的用户）：问题与目标、用户与场景、竞品与参考、范围与功能清单、信息架构、核心任务流程、低保真布局、交互细节与状态、视觉设计系统（含可执行数值规格及其理由、无障碍、文案基调）、测试与迭代，共十个阶段，逐阶段挖掘对齐，最终产出 PRD + DRD 设计文档与可交互 HTML 原型（灰阶骨架→高保真）展示设计效果；多仓库协同场景下支持跨组件标注与对齐，并可按需把视觉规格导出为 DESIGN.md 供编码 agent 长期消费；另含「审计已有设计文档」用法——默认不改源文档，按职责边界清点、十个阶段覆盖度体检、跨层对账（文档正文/修订史/原型/真实代码）与原型运行时自检，产出 P0/P1/P2 分级发现清单 | `UI设计` `帮我设计界面` `带我走一遍UI设计流程` `UI设计文档` `设计文档审查` `@software-ui-design` |
| [`how-code-chain-work`](skills/how-code-chain-work/SKILL.md) | 在修改跨函数/文件/模块代码前，强制先产出带置信度标注的调用链梳理报告，防止接口/DI/事件总线/反射等动态分发导致的漏改；适用于跨文件重构、被多处调用的函数、影响面排查 | `梳理链路` `调用关系` `影响面` `会不会漏改` `这个函数谁在调` `改这个安不安全` `追功能入口` |
| [`harness-workflow`](skills/harness-workflow/SKILL.md) | AI 研发审计 skill：对产物对照目标契约与规则登记表做检查，出具带证据的审计报告（缺口 + 证据 + 修改建议 + 由谁决策），工程未实装时如实标注降级；检查器覆盖契约锁定与禁用词、范围越界与禁止项、风险下限推算、AI 可写区、证据包绑定、追溯矩阵断链、红绿顺序等；修改建议逐条附对抗推演与可信度分级（内置对抗推演案例库），报告归档到被审项目 audits/ 台账；不修改被审产物、只报不改，不做强制、不管合入、不引用或修改任何其它 skill | `审计` `审查` `检查对否` `artifact 校验` `契约核查` `证据核对` `缺口清单` `防 AI 虚报` `目标契约` `规则登记表` `升级触发清单` `对抗推演` `human-in-the-loop` `先定契约再动手` |
| [`clean-change-comments`](skills/clean-change-comments/SKILL.md) | 修改代码注释与文档正文时强制只描述当前状态、禁止变更叙述（原来是/改为/不再/已废弃/禁止xxx改用xxx）；变更历史进变更记录/commit，方案选型进决策记录；仅允许「防回退说明」与「非直觉业务规则/外部约束」两类一行注释；附变更叙述扫描脚本，用于清理已有文件中的残留 | `清理注释` `整理注释` `删掉变更叙述` `只描述现状` `注释规范` `变更记录` `决策记录` `提交前自查注释` `顺手清理残留注释` `@clean-change-comments` |
| [`troubleshooting`](skills/troubleshooting/SKILL.md) | 排查问题统一入口：连开发/测试环境查 pod/节点/机器日志、在多个本地代码库查代码、按客户报错与软件版本切换到对应版本代码定位问题（通常无法连客户环境）；三场景分流、证据分级（已证实/推测）、只读脚本与受保护环境确认；修复写操作只经 apply.py（白名单 + T1/T2R/T2U/T3 授权分层 + 强制备份，覆盖 kubectl/SSH/DB），verify.py 复验 PASS/FAIL、不过回滚 | `排查问题` `定位问题` `查日志` `查 pod` `客户报错` `版本定位` `多仓库查代码` `修复回滚` `troubleshooting` |
| [`skill-freshness-audit`](skills/skill-freshness-audit/SKILL.md) | 审计 skill 库的新鲜度 / 冗余 / 事实性过期：按 0→1 私有知识（persist）/ 1→100 流程代偿（sunset 候选）/ 治理边界（never-remove）分类，附可追溯行号证据；建议型，绝不自动修改 / 删除 / 下线任何 skill | `审计skill库` `检查skill是否过时` `skill保质期` `哪些skill冗余` `skill特有性检查` `生成skill审计清单` |
| [`automation-task-execution`](skills/automation-task-execution/SKILL.md) | AI 辅助自动化任务执行协议：缺口分析（不运行就把缺口按谁能补分流：AI 自取 / 人提供 / 运行时探针 / 可默认假设，B 类一轮集中提问）→ 真实探索（先跑真实请求、不写自动化代码）→ 执行方案 → 冒烟跑通一个最简用例 → 批量实现 → 经验沉淀；配失败分类与授权边界（哪些可自行修复、哪些必须先问人）、断言强度守恒（跑通 ≠ 正确）、上下文文件条目结构与定期复核；附三个只报告不改文件的脚本：断言强度回退检测（同一断言主体上精确比较被换成弱检查、字段断言消失、异常被吞、用例被跳过）、沉淀条目字段 / 时效校验、跨文档规则一致性核对 | `自动化任务` `生成自动化代码` `把手工用例转成自动化` `接口自动化` `UI 测试自动化` `E2E 脚本` `用例代码化` `冒烟跑通一个用例` `探索性执行` `缺口分析` `流程缺什么信息` |
| [`ambitious`](skills/ambitious/SKILL.md) | 按 Felix Rieseberg「高度·深度·广度」三维度把普通任务改写成更有野心的版本：先出方案（列明工具、野心目标、外部验证方式）、等用户确认再动手；强调"更深非更大"、外部裁判验证、环境前提（MCP/可装软件/可接数据），附何时不用与代价提示 | `/ambitious` `更有野心` `目标抬升` `深度改写` `外部验证` `把任务想高/想深/想宽` |

## 开发规范 / Development Guide

### 标准化规范

1. **Frontmatter**：`description` 为"中文核心触发词 + 英文功能摘要"双语组合
2. **术语保留**：正文中文叙述，类名/API/配置 Key/专有名词保留英文原文（如 `ThreadPoolExecutor`、`JWT`、`epoll`）
3. **环境中立**：路径用相对路径（如 `./src`）或占位符（如 `<root_dir>`），无个人敏感信息
4. **Frontmatter 顶层只放白名单字段**：顶层仅允许 `name` / `description` / `license` / `allowed-tools` / `metadata` / `compatibility`。本仓库私有字段（`agent_created`、`version` 等）一律挂到顶层 `metadata:` 之下，不要占顶层 key。
5. **描述自包含**：`description` 应强化本 skill 自身职责（"本 skill 做 X / 不涉及 Y"），不写成与某个具体兄弟 skill 的对比（如"与 X 区别：…"）。需要表达边界时用"不涉及…""不做…"等自包含措辞，避免点名其它 skill。

### 新增 Skill

1. 创建目录 `skills/<skill-name>/`
2. 编写 `SKILL.md`（遵循上述 5 条规范）
3. 按「测试约定」生成临时测试并跑通（测试文件**不提交**）
4. 更新本文件 Skills 索引表格
5. 提交前用 skill-creator 的校验器确认通过

### 测试约定

- Skill 本体与仓库都**不保留测试代码**——测试是临时证明件，用完即删、不随 skill 分发、不进版本控制，可随时重新生成、不约束 skill 迭代。
- 触发时机：新增 skill，或改动 skill 的匹配 / 安全 / 执行链路逻辑时，在临时目录（如 `/tmp/tests-<skill-name>/`）生成测试并跑通：
  1. **离线回归**（必备）：不连任何环境，直接 import skill 脚本，覆盖匹配、校验、安全逻辑的回归点；
  2. **端到端模拟**（skill 有外部交互时，如 SSH / kubectl / db）：本地假服务 + 桩，走真实 CLI 入口验证，全部产物留在 /tmp，不触碰真实环境。
- 两层测试全绿（exit 0）才算交付完成；测试**结论**（项数、场景、结果）写进交付说明或 commit message，测试**文件**本身不留存。
