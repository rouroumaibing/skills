# Skills — 技能仓库

本仓库包含一组可复用的 Skill，覆盖代码检视、架构分析、面试备战和经验提炼等场景。
A collection of reusable Skills covering code review, architecture analysis, interview prep, and experience distillation.

## Skills / 技能索引

以下是本仓库包含的所有 skill，点击链接查看完整文档。
All skills in this repository. Click links for full documentation.

| Skill | 功能说明 | 触发词 |
|---|---|---|
| [`multi-agent-github-mr-review`](skills/multi-agent-github-mr-review/SKILL.md) | 双 agent 对抗式代码审查，产出行级 DiffNote 并阻塞合入直到闭环 | `@某agent+MR链接` `GitHub MR 检视` `PR review` `本地生成代码后自检` `提交前代码审查` |
| [`code-analyzer-like-openwiki`](skills/code-analyzer-like-openwiki/SKILL.md) | 分析代码库生成含 Mermaid 图表的开放式技术 Wiki（reference + onboarding 双模式），只读不改 | `分析代码库` `生成技术文档` `梳理架构` `画架构图` `新人上手` `onboarding` `第一篇PR` `技术教程` `了解项目` `代码库百科` `架构图` `依赖关系图` `feature story` `新人怎么上手这个项目` |
| [`project-to-interview-experiences`](skills/project-to-interview-experiences/SKILL.md) | 将项目源码转化为可口述、防砸盘、带量化数据的面试备战讲稿 | `项目转面试` `面试备战` `面试讲稿` `面试经验` |
| [`superpowers-project-experiences`](skills/superpowers-project-experiences/SKILL.md) | 从已交付计划提炼可复用工程知识，建立模块索引，生成重生成提示词 | `总结经验` `归档` `建立索引` `更新索引` |
| [`jd-hunting`](skills/jd-hunting/SKILL.md) | 求职全流程辅助：简历解析→岗位推荐→适配评分→定制简历与面试问答 | `找工作` `求职` `投递简历` `简历定制` `面试准备` |
| [`10x-learn`](skills/10x-learn/SKILL.md) | AI 十倍速深度学习闭环：五视角调研 + 费曼/测试效应，四阶段产出一页速查表 | `系统学习` `吃透某领域` `快速入门` `10xLearn` `深度调研` |
| [`software-story-design`](skills/software-story-design/SKILL.md) | 功能/逻辑设计文档撰写：概念空间建模、核心数据结构、接口定义与一致性校验 | `设计模块` `增加功能` `写设计文档` `@software-story-design` |
| [`software-ui-design`](skills/software-ui-design/SKILL.md) | 零设计基础用户的 0→1 UI 设计工作流，产出 PRD+DRD 与可交互 HTML 原型；另含设计文档审查（只报不改） | `UI设计` `帮我设计界面/产品/App/平台` `带我走一遍UI设计流程` `UI设计文档` `这个产品的UI该怎么做` `帮我看看这套设计文档有没有问题` `设计文档审查` `@software-ui-design` |
| [`how-code-chain-work`](skills/how-code-chain-work/SKILL.md) | 跨函数/文件/模块改动前强制产出带置信度的调用链报告，防接口/DI/事件分发漏改 | `梳理链路` `调用关系` `影响面` `会不会漏改` `这个函数谁在调` `改这个安不安全` `追功能入口` `跨文件重构` `漏改风险` `排查问题` `复现bug` `为什么会出这个问题` `删除方法` `删除函数` `删除符号` `字段改造` `字段数据流` `字段读写` `只读不写` `@how-code-chain-work` |
| [`harness-workflow`](skills/harness-workflow/SKILL.md) | 对照目标契约与规则登记表审计 AI 研发产物，出带证据报告、降级标注与落地建议，只报不改 | `审计产物` `审查` `检查对否` `artifact 校验` `契约核查` `证据核对` `缺口清单` `目标契约` `规则登记表` `升级触发清单` `对抗推演` `human-in-the-loop` `先定契约再动手` `防 AI 虚报` `验证体系缺什么` `给项目加验证环节` `AI 写代码怎么验证` `审计报告怎么落地` `给建议排落地顺序` `先做哪个` |
| [`clean-change-comments`](skills/clean-change-comments/SKILL.md) | 注释只写一句功能 + 输入/输出参数、配置注释只说开关功能，历史与决策落 commit；附残留扫描脚本 | `清理注释` `整理注释` `删掉变更叙述` `注释规范` `文档注释` `只描述现状` `不要写原来是/改为/不再` `变更记录` `决策记录` `决策时间` `提交前自查注释` `顺手清理残留注释` `代码里为什么有历史说明` `注释复述实现逻辑` `注释解释代码在做什么` `函数注释写过详` `AI生成注释啰嗦` `写注释只该一句功能加参数` `配置注释只说开关功能` `配置注释冗余` `配置注释过详` `配置只描述用户字段` `配置泄漏实现细节` `改代码` `写或改代码注释` `改文档正文时` `@clean-change-comments` |
| [`troubleshooting`](skills/troubleshooting/SKILL.md) | 排查统一入口：连环境查日志、多仓库查代码、按版本切代码定位；写操作走 apply.py 白名单 + 备份 | `排查问题` `定位问题` `查日志` `查 pod` `客户报错` `某组件某版本出问题` `切换到某个版本看代码` `多仓库搜代码` `连环境看机器/集群/节点日志` |
| [`troubleshooting-knowledge`](skills/troubleshooting-knowledge/SKILL.md) | 把排查结论抽成结构化脱敏知识条目沉淀进 knowledge/（oss/personal 两层），可检索、更新、作废 | `提取知识` `沉淀知识` `写知识库` `知识库整理` `检索知识库` `建知识条目` `把这次排查结论存下来` `知识库里有没有关于 X 的记录` |
| [`skill-freshness-audit`](skills/skill-freshness-audit/SKILL.md) | 审计 skill 库的新鲜度/冗余/事实性过期，按 persist/sunset/never-remove 分类，只报不改 | `审计skill库` `检查skill是否过时` `skill保质期` `哪些skill冗余` `skill特有性检查` `生成skill审计清单` `模型升级后复查skill` `清理/精简过时的skill` |
| [`automation-task-execution`](skills/automation-task-execution/SKILL.md) | 自动化任务执行协议：缺口分析→真实探索→冒烟跑通→批量实现→经验沉淀，九对账收尾 | `自动化任务` `生成自动化代码` `把手工用例转成自动化` `接口自动化` `UI 测试自动化` `E2E 脚本` `用例代码化` `冒烟跑通一个用例` `探索性执行` `缺口分析` `流程缺什么信息` `质量门` `代码纪律` `交付质量` |
| [`ambitious`](skills/ambitious/SKILL.md) | 按「高度·深度·广度」把任务改写成更有野心的版本，先出方案、外部验证、等确认再动手 | `更有野心` `把任务想高/想深/想宽` `目标抬升` `深度改写` `外部验证` `/ambitious` `@ambitious` |
| [`skillormcp`](skills/skillormcp/SKILL.md) | 判定能力该做成 Skill 还是 MCP 并审计落点错位，输出部署形态；只报建议不改文件 | `判定skill还是MCP` `skill和mcp怎么选` `落点审计` `能力错位` `下沉mcp` `拆分建议` `新增能力该放哪` `skill是否在干MCP的活` `发布前核对落点` `@skillormcp` |
| [`trigger-eval`](skills/trigger-eval/SKILL.md) | 用独立子 agent 当评审，对一批本地 skill 做触发判定评测，出命中率/FP/FN 与混淆矩阵；零登录替代官方评测 harness | `触发评测` `skill 触发测试` `trigger eval` `description 命中率` `误触发` `漏触发` `skill 冲突检测` |

**审计规则**：新增或调整 `skills/` 与 `mcps/` 内容前，可调用 `@skillormcp`（或 `/skillormcp`）审计落点是否错位（能力错位）。该 skill 只报建议、不改文件。

## 开发规范 / Development Guide

### 标准化规范

1. **Frontmatter**：`description` 为"中文触发词 + 一句自包含职责摘要（术语保留英文）"组合
2. **术语保留**：正文中文叙述，类名/API/配置 Key/专有名词保留英文原文（如 `ThreadPoolExecutor`、`JWT`、`epoll`）
3. **环境中立**：路径用相对路径（如 `./src`）或占位符（如 `<root_dir>`），无个人敏感信息
4. **Frontmatter 顶层只放白名单字段**：顶层仅允许 `name` / `description` / `license` / `allowed-tools` / `metadata` / `compatibility`。本仓库私有字段（`agent_created`、`version` 等）一律挂到顶层 `metadata:` 之下，不要占顶层 key。
5. **描述自包含**：`description` 应强化本 skill 自身职责（"本 skill 做 X / 不涉及 Y"），不写成与某个具体兄弟 skill 的对比（如"与 X 区别：…"）。需要表达边界时用"不涉及…""不做…"等自包含措辞，避免点名其它 skill。

6. **可变数据单一事实源**：skill 含运行时写入的数据目录（知识库、命中遥测日志等）时，仓库是唯一事实源；已安装副本的这些路径必须用软链指向仓库对应路径，禁止复制成独立实体——否则运行时写入只落在副本，永不回库。同步副本时只同步静态代码（SKILL.md / scripts / references），遇软链跳过，不要把软链覆盖回实体。

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
- **触发效果验证**：改动 `description` 或触发相关链路后，若无法使用 skill-creator 自带的评测（`scripts/run_loop.py`，依赖 `claude -p` CLI 登录，未登录会静默全判 False 且 exit 0——假红伪装真绿），改用 `trigger-eval` 的子 agent 代理法代替（见 `skills/trigger-eval/SKILL.md`）：同一查询集 + 同一评审约束跑新旧两轮，只换被测 description，零 diff 才算无回退。
- **测试靶子不进 skill 本体**：真实服务 / 环境只是验证脚手架。验证得出的结论必须以**通用形态**写回 skill——保留结论（如"凭证需服务端续期"），不留观测对象；环境名、服务名、内部域名、实例 ID、具体端点一律不得出现在 SKILL.md、脚本、模板、注释与测试样例里。交付前自检：`grep -ri <环境名> skills/<skill-name>/` 应为 0 命中。
