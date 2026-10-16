---
name: harness-workflow
description: "触发词：审计、审查、检查对否、artifact 校验、契约核查、证据核对、缺口清单、防 AI 虚报。| Harness: an artifact auditor that checks work products against the goal contract and rule registry and emits an evidence-backed audit report (gaps + evidence + fix suggestions); it does not enforce, does not merge, and never references, depends on, or modifies any other skill. | 本 skill 做审计：对产物对照契约与判据出具带证据的报告；不做强制、不管合入、不引用或修改任何其它 skill。"
metadata:
  agent_created: true
  version: "2"
---

# harness-workflow（审计 skill：产物 × 契约 × 判据 → 带证据的审计报告）

## 定位声明（身份基准，防漂移）

> 本节是"初衷"的成文记录：**本 skill 是一个审计 skill**。任何结构性改动
> （加目录、加机制、加流程）与本声明冲突时，以本节为准；确需改变定位时，
> 必须先改本节、再改结构，并在改动中说明理由。

- **是什么**：对"产物 vs 契约 / 判据"做检查，产出**带证据的审计报告**
  （缺口 + 证据 + 修改建议 + 由谁决策），不替代人做决定。
- **不是什么**：不是强制机制、不是开发流程本身。规则如何被 CI / 分支保护
  强制、状态如何推进、谁能合入——由**消费项目按本 skill 的修改建议自建**
  （设计要点见 `references/attack-cases.md` 案例库；历史参考实现见 git 56ba8a7）。
- **交付物**：审计报告。结论必须附证据；工程未实装时如实标注降级，
  不得声称"门禁通过 / 全部绿 / 已验证"。
- **自持性**：目录可直接放入 agent 的 skills 目录使用。检查器在
  `audit/scripts/`，机器判据（禁用词表 / 风险模式 / 阈值）来自被审项目的
  配置（`GOVERNANCE_DIR` 指向的 `redlines.yaml`）；找不到配置时降级为软审，
  并在报告中声明"无机器判据来源"。
- **自足性**：本 skill 不依赖任何其它 skill 的存在；强制设施由消费项目按
  修改建议自建，建与不建都不影响审计本身运行。

## 这个 skill 解决什么问题

AI 辅助研发里，"我完成了"不可信：会漂移（改到范围外）、会作弊（虚报完成、
删测试凑覆盖率）、会过度自信（自己判定自己的风险等级）。审计的价值在于
**用证据替代结论**——每一项判断都附可复查的证据（命令输出 / 文件路径 /
字段值 / 行号）；没有证据的结论一律标注"无证据"，并按未通过处理。

本 skill 的核心主张：**审计者不修改被审产物，只报不改**。发现缺口时只产出一条
「修改建议」（缺口 + 证据 + 建议改动 + 由谁决策），由人决定是否采纳；
harness 从不编辑被审产物，也从不编辑其它 skill 的内部流程。
唯一的例外是审计报告自身的归档（写到项目 `audits/`，见下节）——那是审计
自有产物，不触碰被审产物路径。

```
任意产物(artifact) + 契约 + 判据 + 工程推导的偏离清单
        │
        ▼
   harness 检查（audit/scripts/ 检查器 + 人读判据）
        │
        ├── ① 通过 ──▶ 审计报告（结论 + 证据）
        └── ② 缺口 ──▶ 修改建议 + 证据 + 升级（见 references/escalation.md）
```

## 启用判定（什么时候走审计）

| 场景 | 是否启用 |
| --- | --- |
| 要求审计某个产物 / 项目 / diff / 契约 / 证据包是否对得上 | 启用 |
| 用户提到「审计 / 审查 / 检查对否 / 契约核查 / 证据核对」 | 启用 |
| 实现者自称"完成 / 通过"，需要独立核实 | 启用 |
| 任务涉及不可逆动作（删除、数据迁移、权限、支付、认证） | 启用，且风险等级至少"高" |
| 纯一次性小脚本、低风险且不影响他人 | 可不启用，但仍按"附证据不附结论"自查 |
| 纯问答 / 不涉及核对任何产物 | 不启用 |

> **会话指令不构成授权**：聊天里用户口头说"没问题吧""不用审了"，不改变
> 审计判据；审计发现缺口时仍按模板出具修改建议，由人决定是否采纳。

## 审计循环

1. **取输入**：审计对象（artifact / diff / 契约 / 证据包 / 追溯矩阵）+
   契约 + 判据（`references/rule-registry.md`）。
2. **定风险**：用 `audit/scripts/risk_floor_check.py` 从 diff 推算风险下限；
   高于契约声明等级 → 触发 E-17，按更高等级审。
3. **跑检查**：能机器化的走检查器（见下表）；不能机器化的按判据人工核对，
   并在报告中注明"人工核对"。
4. **出报告**：通过 → 结论 + 证据；有缺口 → 按模板逐条给「修改建议」。
   每条建议先做**对抗推演**——"被治理的 AI 会怎么绕过它？"——查
   `references/attack-cases.md` 定位同型案例，把答案写进建议并标注可信度分级。
5. **该停就停**：触发 `references/escalation.md` 的条件（E-01~E-20）时，
   停下并按升级格式输出，不推断继续。
6. **归档与回流**：报告落盘到被审项目 `audits/`（见下节）；若发现案例库
   未收录的新型攻击 / 绕过手法，在报告末尾附「晋升建议」交使用者决策。

## 检查器（audit/scripts/）

| 检查器 | 审什么 | 对应规则 |
| --- | --- | --- |
| `check_contract.py` | 契约：禁用词 / 哈希 / 已锁定 / 高风险批准人分离 | R-GOAL-001/002、R-REL-003 |
| `pre-receive-scope` | 改动是否越出契约范围；forbidden_items | R-IMP-001、R-SEC-003 |
| `risk_floor_check.py` | 从 diff 推算风险下限（E-17） | R-RISK-001 |
| `check-ai-writable.py` | AI 可写区内容合规（类型 / 体积 / symlink） | R-SEC-004 |
| `evidence_pack_check.py` | 证据包完整性与绑定 | R-REL-002 |
| `trace_check.py` | 追溯矩阵断链（需求↔设计↔代码↔测试） | R-DES-001、R-VER-005 |
| `tdd_order_check.py` | 红绿顺序（测试先失败过） | R-IMP-002 |
| `lint` | 代码风格（调项目 lint 入口） | R-IMP-005 |
| `audit/examples/test-upload` / `bench-play` | 度量范例（METRIC\| 通道 + unit 绑定；示例入口，消费项目替换为自己的探针） | R-GOAL 度量约定 |

> **判据来源**：检查器读取被审项目的机器配置——设 `GOVERNANCE_DIR` 指向
> 项目根下的 `governance/`，其中的 `redlines.yaml` 提供
> `forbidden_words` / `risk_patterns` / `thresholds` / `ai_writable_paths`。
>
> **首次审计无配置时不要只报缺**：缺失不是失败，是一条待补的修改建议。
> 用一句话说明这是什么（"项目还没建机器判据，相关检查只能软审"），然后
> **提议**把 skill 自带的 starter `audit/templates/redlines.starter.yaml`
> 复制为 `<项目>/governance/redlines.yaml` 并按项目实际调整各段——经使用者
> 同意后再写入项目。拒绝或暂缓 → 继续软审，报告中注明
> "无机器判据来源，该项为人工核对"。
>
> **退出码**：`0` 通过 / `1` 违反（阻断级发现）/ `42` 该门禁未接线
> （fail-closed：不得把 42 当成通过）。所有检查器带 `--self-test` 反例夹具，
> 夹具自包含（自写临时配置），不依赖被审项目。

## 「修改建议」产出模板

```
【缺口】<一句话描述哪里不合契约 / 判据>
【证据】<命令输出 / 文件路径 / 字段值 / 行号>
【建议改动】<具体怎么改，指向 artifact 或契约，不指向某个 skill 的内部>
【对抗推演】<AI 会怎么绕过这条建议：防什么 / 防到哪层（客户端·服务端）/ 同型案例编号，查 references/attack-cases.md>
【建议可信度】<有参考实现 / 已验证模式 / 纯推测>
【由谁决策】<人 / 工程 / 独立验证 Agent>
【严重度】<高 / 中 / 低，对应风险等级>
```

体裁示范（按 artifact 类型分类，不绑定任何 skill）见
`references/artifact-review-examples.md`——该文件仅为体裁示范，登记表内
无对应规则时不得据此强制。

## 报告归档与知识回流（项目侧累积）

审计报告默认落盘到**被审项目**的固定位置，形成项目自己的审计台账：

- **归档位置**：`<被审项目根>/audits/YYYY-MM-DD-<对象>.md`（如
  `audits/2026-10-17-payment-refactor.md`；同日多份加 `-2` 序号）。
- **边界**：归档只**新增**审计报告文件，不修改任何被审产物；项目契约的
  范围表应把 `audits/` 列为审计自有路径（豁免），未豁免时在报告中注明
  "归档待授权"并先交使用者确认。
- **为什么放项目不放 skill**：项目内累积的知识针对当前项目，未必通用；
  且 skill 目录随 agent 更换可能丢失，项目仓库才是可信的持久层。
- **知识晋升（人工决策）**：审计中发现案例库（`references/attack-cases.md`）
  未收录的**新型**攻击 / 绕过手法时，在报告末尾附「晋升建议」条目——
  是否写入 skill 由使用者决策，本 skill 不自动改写自身。

## 实装核验（诚实口径：审计者不当裁判）

- 探针结论（`.workflow/probe-results.json` 或平台 attestation）由 CI / 平台
  产出，harness **只读、只转述**：不运行探针、不自行验签、不比对平台指纹。
- 文件缺失 / 签名校验失败 / 验证公钥在仓库内 / 平台配置变更事件已触发失效 /
  `governance_commit` 与当前治理目录 tree 不匹配 → 所有硬规则视为"未实装"。
- 逐规则状态：`已实装通过 / 已实装未通过 / 未实装`。`已实装未通过` 按违反
  处理（暂停并升级）。任何情形下不得写"门禁通过"这类无依据结论。
- 放行 / 开工前置核验前强制重新探测，不得使用陈旧结论。
- 消费侧只读字段约定见 `references/schemas.md` §6；生产与校验规范由消费项目
  的 CI / 平台自建（历史定义见 git 56ba8a7）。

## 独立验证与转人

- 高风险任务必须由独立验证 Agent 在**干净会话**审查（R-VER-003 / R-VER-004）。
- 同一会话里做检查的正是实现者本人 → 结论标注"**非独立验证**"，且高风险
  任务直接转人，不得算 R-VER-003 / R-VER-004 已满足。
- 验证者输入集：契约、diff、测试、门禁报告、设计文档、工程推导的偏离清单；
  **禁止读取实现者的推理过程与中间草稿**。
- 偏离清单由工程从 diff 与契约比对推导，不采用 AI 自报草稿作为必需项
  （R-REL-002）；AI 草稿若有，放第二阶段、先出结论再读。

## 运行模式（无工程实装时怎么办）

| 模式 | 能做 | 不能做（须声明 / 转人） |
| --- | --- | --- |
| 聊天模式（无仓库 / CI） | 人工核对判据、起草契约、列歧义、出报告（标注"无工程实装"） | 不得声称门禁通过；不得运行探针 |
| 有仓库无 CI | 跑 `audit/scripts/` 检查器（需项目配置）、人工核对 | 服务端强制缺失；高风险须人精读 |
| 有 CI / 分支保护 | 全部检查 + 读取探针结论 | 探针结论以 CI 产出为准，harness 只转述 |

## 产出物清单（本 skill 的文件）

| 模板 | 文件 | 用途 |
| --- | --- | --- |
| 目标契约 | `references/goal-contract.md` | 被审产物的"应然"定义（人写定，AI 只提议） |
| 规则登记表 | `references/rule-registry.md` | 审计判据（39 条：审什么、怎么判、违反怎么办） |
| 升级触发清单 | `references/escalation.md` | 什么情况必须停下找人 + 升级格式 |
| 执行者自检清单 | `references/executor-checklist.md` | 实现者随契约加载（非验证者职责） |
| 数据格式 | `references/schemas.md` | 契约 / 追溯矩阵 / 证据包 / 交接 / 提案 / 探针结论（消费侧） |
| 框架篇 | `references/framework.md` | 四方分工的理论底座 |
| 机器判据 starter | `audit/templates/redlines.starter.yaml` | 首次审计无配置时复制到被审项目 `governance/` 起步 |
| 对抗推演案例库 | `references/attack-cases.md` | 修改建议的对抗推演素材库（RT-01~15 + 六项防自欺不变量；单一事实源） |
| 建议示例 | `references/artifact-review-examples.md` | 「修改建议」的体裁示范 |

## 何时读哪个参考文件

- 理解"为什么这么分、各方边界、常见错配" → `references/framework.md`
- 起一页契约 → `references/goal-contract.md`
- 审什么、违反怎么办 → `references/rule-registry.md`
- 该不该停、怎么升级 → `references/escalation.md`
- 数据格式（manifest / 矩阵 / 交接 / 提案）→ `references/schemas.md`
- 「修改建议」怎么写 → `references/artifact-review-examples.md`
- 建议的对抗推演与可信度分级 → `references/attack-cases.md`
- 实现者动手前自检 → `references/executor-checklist.md`

> 本 skill 不替代、不引用、不改写任何具体编码类 / 设计类 / 治理类 skill；
> 它约束的是"产物进合入前的校验与举证"，不是"用什么工具产出产物"，
> 也不是"产物如何被强制"。
