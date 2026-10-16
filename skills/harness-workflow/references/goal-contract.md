# 模板一：目标契约（Goal Contract）

**用途**：每个任务一份。人写定并签字，锁定版本后才允许进入设计和实现。AI 只读，工程用它做范围检查、追溯检查和阈值引用。

**存放位置建议**：`contracts/<任务ID>.md`，对 AI 只读（属 `protected_paths`）；变更只能走第 12 节的变更流程。AI 若发现契约需变更，只能写入 `proposals/`，由人合并。

**使用约定**：方括号 `[ ]` 内为待填内容，填完后删除方括号。模板中的"工程校验"列指固定工程（脚本 / CI / hook）可以机械检查的项，不依赖 AI 判断。

> **关于禁用词（R-GOAL-001）**：禁用词表与适用范围（仅约束契约中与客观判定直接相关的字段：范围表、成功判据、边界情况）的**权威定义在 `rule-registry.md`**（搜索"禁用词适用范围"）。本模板仅声明：禁用词检查**由 `governance/redlines.yaml` 的 `forbidden_words` 控制，契约不得用任何开关关闭**；本模板的"使用约定""工程校验清单"等段落是模板元指令，不计入禁用词扫描。

---

## 机器可读面（YAML front matter — 脚本解析的唯一权威来源）

> **单一事实源**：下列 front matter 是脚本（`check_contract.py`）解析的**唯一权威来源**；其下的 Markdown 表格是人可读的同步视图。**在 `check_contract.py` 实装前**，表格须与 front matter 逐行一致（正文表格为权威来源的镜像，front matter 为判定基准）；**实装后**由脚本从 front matter 渲染正文表格，正文表格变为只读生成物，不再手写。所有结构化的判定字段（范围、判据、失败判据、非目标、不可触碰项、边界、风险、签字、待裁决）都在此定义。
>
> **契约哈希的范围（必读）**：锁定后，工程对"**规范化的 front matter（排除 `contract_hash`、`locked_at`、`signatures` 三个字段）+ 规范化正文的结构化表格**"按 RFC 8785 规范化 JSON 取 sha256，写入 `contract_hash` 并记入审计日志。**规范性内容 = front matter 全部字段（排除三字段）+ 正文中的结构化表格**（范围表 / 成功判据表 / 边界表 / 非目标表 / 不可触碰表 / 待裁决表）——这些表本就由 front matter 渲染；**纯说明性散文不计入哈希**（避免改错别字强制升版本）。完整规范化规则与 YAML→JSON 转换（YAML 1.2 core、禁用锚点/别名/自定义 tag、重复键报错、不隐式 bool/date 转换）见 `schemas.md` §1。此后任一 front matter / 结构化表格内容变化都会使哈希与 `contract_hash` 不符。`status` 与推进历史不在本文件内，改由 `state.json` 派生（格式由消费项目自定；历史定义见 git 56ba8a7）。
>
> **`signatures` 仅为自述文本**：真实的签名凭据是签名提交（GPG/SSH）或平台审批 / 令牌记录，不在本字段内（身份与签名凭据的权威见项目自建的 `governance/roles.yaml`）。

```yaml
---
contract_id: C-YYYYMMDD-序号
schema_version: 1
version: v1
contract_hash: ""        # 锁定后由工程写入规范化内容的 sha256（见上方规则）
task_name: "[一句话]"
owner: "[governance/roles.yaml 中注册的账号 ID]"
approver: "[governance/roles.yaml 中注册的账号 ID，可与 owner 相同，但风险为高时必须不同]"
security_owner: "[账号 ID；安全相关升级 E-04/E-09 的裁决人]"
risk_level: low          # low | medium | high（枚举见 rule-registry 映射表）
risk_rationale: "[依据风险分级表的哪一条]"
scope:                   # 范围表（S-* 编号的单一来源，id + path + desc 三字段，须与正文 §3 一致）
  - id: S-1
    path: "[目录/文件/服务名]"
    desc: "[一句话范围描述，客观判定，不含禁用词]"
protected_paths:         # 受保护路径（对 AI 只读）；与 redlines.yaml 取并集，只能增加不能减少
  - contracts/
  - .workflow/
ai_writable_paths:        # AI 可写区（提议/交接），由人合并进契约；AI 不得直接改受保护项；范围检查豁免（R-SEC-004 约束其内容）
  - proposals/
  - .handoff/
  - ai-reports/
non_goals:               # 非目标（N-*，单一来源）
  - id: N-1
    desc: "[明确不做的事]"
    reason: "[原因]"
criteria:                # 成功判据（A-*，自动/人工，单一来源；含 desc 与判定方法）
  - id: A-1
    desc: "[通过/不通过条件描述，例：500MB 视频上传成功率 ≥ 99%]"
    type: auto           # auto | manual
    command: "[audit/examples/test-upload]"   # 只允许调用 governance/ 与 audit/ 下入口
    metric: "success_rate"   # 度量程序按 schemas §1.1 约定格式输出该指标（不把真实阈值藏进命令）
    op: ">="             # == | != | <= | >= | < | >
    value: 99
    unit: "%"
    min_samples: 1000     # 比率类判据的最小样本数；由 METRIC|samples 读取
    method: "度量程序独占 METRIC| 发射；输出 METRIC|success_rate=<实测>;unit=% samples=<n>；比较失败即不通过"
    judge: 固定工程
  - id: A-3
    desc: "[错误提示文案符合产品规范]"
    type: manual
    method: "人对照规范逐条核对（说明核对方式与对照文档）；确认记录须作为 evidence_pack 的 manual_signoff 项（见 schemas §3）"
    judge: 人
failure_criteria:        # 失败判据（F-*，单一来源）
  - id: F-1
    desc: "[任一自动判据未通过]"
forbidden_items:         # 不可触碰项（X-*，单一来源）；优先于 scope（见 §5）
  - id: X-1
    target: "[路径/配置/符号，须为可比对类型]"
    kind: "[file | glob | symbol | manual_review]"   # manual_review 由人核对，不机械匹配
    note: "[为什么不可触碰；谁可批准例外]"
boundaries:              # 边界情况（B-*，单一来源）与对应判据的映射
  - id: B-1
    scenario: "[场景]"
    expected: "[预期行为]"
    criterion: "[A-?]"
open_questions_list:      # 待裁决项（Q-*，单一来源；正文 §11 表格由本列表渲染）
  - id: Q-1
    question: "[例：视频转码失败是否自动重试]"
    proposed_by: "ai"
    options: ["A 重试 3 次", "B 不重试直接报错"]
    status: open          # open | resolved
    resolution: ""        # status=resolved 时填选项与裁决人
open_questions: 0        # 本字段为派生计数：open_questions_list 中 status=open 的数量；为 0 才可锁定
locked_at: ""            # 锁定日期，锁定后由工程写入
signatures:             # 签字（锁定前必填，须为 governance/roles.yaml 注册且签名身份）
  owner:    { id: "", date: "", method: "gpg" }
  approver: { id: "", date: "", method: "gpg" }
# status / transitions 不在此：由 state.json 派生（格式由消费项目自定；历史定义见 git 56ba8a7）
---
```

---

### 0. 元信息

| 字段 | 内容 |
| --- | --- |
| 契约 ID | \[C-YYYYMMDD-序号\] |
| schema 版本 | \[v1，随 schemas.md 演进递增\] |
| 版本 | \[v1\]，每次变更递增，锁定后不可原地修改 |
| 契约哈希 | 锁定后由工程写入"规范化的 front matter（排除 contract_hash/locked_at/signatures）+ 规范化正文结构化表"的 sha256（RFC 8785，见 §0 顶部说明） |
| 任务名称 | \[一句话\] |
| 负责人（对目标负责的人，governance/roles.yaml 账号 ID） | \[账号 ID\] |
| 批准人（可放行合入的人，governance/roles.yaml 账号 ID） | \[账号 ID，可与负责人相同，但风险等级为高时必须不同\] |
| 安全负责人（安全相关升级的裁决人） | \[账号 ID\] |
| 创建日期 / 锁定日期 | \[日期\] / \[日期\] |
| 状态 | 由 `state.json` 派生（draft / locked / changed / closed），不在本文件内 |

### 1. 目标

用一到三句话写出完成后世界上有什么不同。必须包含一个可观察的结果。

> \[例：用户在 App 内上传不超过 500MB 的视频后，60 秒内可在列表页播放。\]

### 2. 背景与动机

\[为什么现在做；不做的代价。只写事实，不写方案。\]

### 3. 范围（In Scope）

| 编号 | 对应路径或模块 | 描述 |
| --- | --- | --- |
| S-1 | \[目录/文件/服务名\] | \[一句话客观描述，不含禁用词\] |
| S-2 |  |  |

**规则**：AI 改动的文件必须落在本表"对应路径或模块"内，工程在每次提交时检查（**豁免 `ai_writable_paths` 下的 `proposals/`、`.handoff/`、`ai-reports/`**），越界即触发升级（见升级清单 E-03）。front matter 的 `scope` 必须与本表逐行一致（front matter 为权威来源，每行带 `id` + `path` + `desc`，与本表列一一对应）。

> 本表是 R-GOAL-001 禁用词扫描的**目标字段之一**（仅客观判定的范围描述，不含"适当/尽量/合理/关键"等词）。

### 4. 非目标（Out of Scope）

| 编号 | 明确不做的事 | 原因 |
| --- | --- | --- |
| N-1 | \[例：不兼容 v1 版本客户端\] | \[原因\] |

（结构化同源见 front matter `non_goals`。）

### 5. 不可触碰项

| 编号 | 对象（target） | 类型（kind） | 说明 |
| --- | --- | --- | --- |
| X-1 | \[路径/配置/符号\] | \[file / glob / symbol / manual_review\] | \[为什么不可触碰；谁可批准例外\] |

默认不可触碰（无需填写，所有契约继承）：红线配置、状态文件、审计日志、门禁报告、契约文件本身、CI 配置、`governance/` 全套（scripts/hooks/redlines.yaml/roles.yaml）、`.workflow/`（state.json/audit.log/probe-results.json）。

（结构化同源见 front matter `forbidden_items`。）

> **优先级与可比对性（单一事实源在 R-SEC-003）**：`forbidden_items` **优先于 `scope`**——即使某改动落在 scope 路径内，只要触及 `forbidden_items` 列出的对象，仍触发 E-04 / 升级，不得因"在范围内"放行。工程在每次提交时**只对 `kind ∈ {file, glob, symbol}` 的项做机械匹配**；`kind = manual_review`（如公共接口、数据表等无法用路径比对的类型）列入放行清单，由人逐项核对，不机械放行。

### 6. 成功判据（验收标准）

每条必须满足：有唯一编号；写明"通过"和"不通过"的具体条件；指定判定方式；能自动判定的必须给出命令（**只调 `governance/` 与 `audit/` 入口**）与结构化阈值，且**度量程序独占 `METRIC|` 发射、输出真实被测量**（不把真实阈值藏进命令）——见 `schemas.md` §1.1。

| 编号 | 描述（通过/不通过条件） | 类型 | 判定命令或方法 | 结构化阈值（metric/op/value/unit/min_samples） | 判定者 |
| --- | --- | --- | --- | --- | --- |
| A-1 | \[例：500MB 视频上传成功率 ≥ 99%，失败时返回错误码 4xx 并附原因\] | 自动 | \[`audit/examples/test-upload`\] | success_rate >= 99 / % / min_samples=1000 | 固定工程 |
| A-2 | \[例：列表页播放起播时间 P95 ≤ 3 秒\] | 自动 | \[`audit/examples/bench-play`\] | p95 <= 3000 / ms | 固定工程 |
| A-3 | \[例：错误提示文案符合产品规范\] | 人工 | \[人对照规范逐条核对\] | 不适用（须进 evidence_pack 的 manual_signoff） | 人 |

**工程校验**：每条判据的"类型"必须为自动或人工；类型为自动时命令必须可执行、度量程序独占发射并按约定格式输出指标（**指标行须携带 `unit=` 且与契约 `unit` 一致**，见 `schemas.md` §1.1）、阈值结构化；类型为人工时必须指定人与方法，且**该人工判据的确认记录须作为证据包的 `manual_signoff` 项**（见 `schemas.md` §3），否则放行时无证据。front matter 的 `criteria` 必须与本表自动类判据逐行一致（front matter 为权威来源）。

> 本表是 R-GOAL-001 禁用词扫描的**目标字段之一**（仅客观判定的条件描述）。

### 7. 失败判据

出现以下任一情况，任务判定为失败，不得合入。

| 编号 | 失败条件 |
| --- | --- |
| F-1 | \[任一自动判据未通过\] |
| F-2 | \[出现未经批准的数据格式变更\] |

（结构化同源见 front matter `failure_criteria`。）

### 8. 边界情况

逐条写明预期行为。AI 不得自行补全本表，发现遗漏只能提出（写入 `proposals/`），由人裁决后写入。

| 编号 | 场景 | 预期行为 | 对应判据 |
| --- | --- | --- | --- |
| B-1 | \[例：上传中途断网\] | \[例：保留已传分片 24 小时，恢复后续传\] | \[A-?\] |
| B-2 | \[例：历史数据缺字段 duration\] | \[例：显示为"未知"，不报错\] | \[A-?\] |
| B-3 | \[例：并发 100 个上传\] | \[预期行为\] | \[A-?\] |

（结构化同源见 front matter `boundaries`，其中的 `criterion` 须指向本表或判据表的编号。）

> 本表是 R-GOAL-001 禁用词扫描的**目标字段之一**（仅客观判定的预期行为）。

### 9. 风险分级

**本表是风险分级标准的唯一事实源**：分级标准由人定义，AI 不得自评。以下为默认模板，按项目改写。等级枚举 `risk_level` 取值 `low | medium | high`，与表格"低/中/高"的映射见 `rule-registry.md`。

| 等级 | 判定条件（满足任一即归入该级，取最高） | 放行方式 |
| --- | --- | --- |
| 高 | 涉及数据迁移或删除；涉及认证、权限、支付；修改公共接口；不可逆操作；涉及个人信息 | 人精读相关 diff 后写"人放行记录"放行；独立验证 Agent 使用与实现者不同的模型 |
| 中 | 修改业务逻辑；新增依赖；修改数据库读取路径 | 独立 AI 验证 + 证据包，人看汇总后写"人放行记录"放行（对应升级项 E-16） |
| 低 | 仅文档、注释、测试、格式、纯重构且测试覆盖不降低 | 门禁全绿后由"批准人预授权的合入账号"自动合入（须存在有效的预授权 `PA-*` 记录，且 `scope` 为具体契约 ID、绑定 contract_hash）；**低风险自动合入额外硬条件见 R-REL-005：不改测试、不新增依赖、diff 体积 ≤ 阈值、公共符号 API diff 为空** |

本任务风险等级：\[高 / 中 / 低\]，依据：\[对应上表哪一条\]。

> **与 `governance/redlines.yaml` 的 `risk_patterns` 不是同一份事实**：本表是"本任务声明等级的判定标准"（唯一来源 = 本表）；`redlines.yaml` 的 `risk_patterns` 是"从 diff 推算风险下限的受控路径/模式列表"（唯一来源 = `redlines.yaml`），两者不得互相复制等级定义。工程用 `risk_patterns` 推算的下限高于本声明时触发升级 **E-17**（见 R-RISK-001）。

### 10. 权限定义

| 对象 | AI 权限 |
| --- | --- |
| 范围内源码 | 读写 |
| 范围内测试 | 读写（新增和修改均需通过变异测试，不得删除既有用例；先写失败测试再实现，见执行者自检清单第 4 条） |
| 契约文件、红线配置、状态文件、门禁报告、审计日志、CI 配置、`governance/` 全套、`roles.yaml`、`.workflow/`（state.json/audit.log/probe-results.json） | **只读**（属 `protected_paths`，完整清单以 `governance/redlines.yaml` 为准） |
| 项目自身的 `scripts/`、`hooks/` 等（非 `governance/` 下） | **正常读写**（不在受保护范围内，除非项目另行加入 protected_paths） |
| `proposals/`、`.handoff/`、`ai-reports/` | **可写**（AI 的提议 / 交接 / 草稿落在此，由人合并或采纳）；**范围检查豁免**（R-IMP-001），AI 写这些区域不视为越界；其内容与体积受 R-SEC-004 约束 |
| 范围外文件 | 只读，需修改时写入 `proposals/` 升级 |
| 外部网络、生产环境、密钥 | 无权限，需要时升级 |

> 契约 front matter 的 `protected_paths` 与 `governance/redlines.yaml` 的 `protected_paths` **取并集**（只能增加保护范围、不能减少）；AI 不得直接改任一受保护项，只能经 `proposals/` 提议。
> **判定命令与度量程序只允许调用 `governance/` 与 `audit/` 下的入口**：因此 `package.json` / `pyproject.toml` / `Makefile` 等通用文件**不必整体**列入受保护路径，AI 加依赖、改通用脚本不再触发 E-04；如需保护其入口段，用字段级 hook 只比对 `scripts`/`tasks` 段。
> **修改受保护路径本身（含治理脚本）的流程**：任何对 `protected_paths` / `governance/` 的变更，必须由人发起——经 `proposals/` 提交变更申请 → 人批准 → 由人（或 CI 以人身份）重新运行实装探针并更新 `.workflow/probe-results.json`（或 CI attestation），AI 不得自行修改或重新探针。
> AI 执行身份不得是"批准人预授权的合入账号"，也不得兼任 owner / approver / merge-executor（见 R-REL-004），否则低风险自动合入等于 AI 自行合入。
>
> **第三档路径 `review_required_paths`**：`conftest.py` / `pytest.ini` / `jest.config.*` / `pyproject.toml` 的 `tool` 段等测试运行器配置，AI 可改但合入前**必须人审**（PR 评审 + CODEOWNERS），不得自动合入；治理 / 检查器入口（`governance/`、`audit/` 下脚本）调用 pytest / jest 时**使用自带且钉死的配置目录，忽略项目配置**，防止测试被改成"空转"（见 `rule-registry.md` 的 `review_required_paths` 与 R-SEC 说明）。
> **路径档位冲突**：若同一路径同时出现在 `protected_paths` 与 `ai_writable_paths`，`protected_paths` 优先（受保护，AI 只读）；`ai_writable_paths` 不得包含可被构建 / 测试加载为可执行内容的文件（受 R-SEC-004 约束）。

### 11. 歧义与待裁决项

**锁定条件**：`open_questions_list` 中所有条目 `status=resolved` 时，契约才能锁定。工程检查此条件（`open_questions == 0`）。本表由 front matter 的 `open_questions_list` 渲染，单一事实源在 front matter。

| 编号 | 问题 | 提出者（人/AI） | 选项 | 裁决 | 裁决人 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| Q-1 | \[例：视频转码失败是否自动重试\] | AI | \[A 重试 3 次 / B 不重试直接报错\] | \[A\] | \[账号 ID\] | 已裁决 |

> AI 发现歧义或遗漏时，**不得直接写入本表**（本表在契约内，AI 只读）；应把问题写入 `proposals/`，由人裁决后合并进契约（见 E-01 / R-GOAL-002 / R-GOAL-004）。

### 12. 变更流程

- 契约锁定后，任何修改都生成新版本，旧版本保留。
- 变更必须由负责人发起，批准人确认，工程记录到变更日志与审计日志（含 `contract_hash` 变更前后值）。
- 变更后，工程重新计算范围、追溯矩阵和门禁阈值；已完成阶段中受影响的部分标记为"需重验"。
- AI 发现契约需要变更时，只能按升级清单 E-02 把变更申请写入 `proposals/`，不得自行修改。

| 版本 | 日期 | 变更内容 | 变更原因 | 发起人 | 批准人 |
| --- | --- | --- | --- | --- |
| v1 | \[日期\] | 初版 |  |  |  |

### 13. 工程校验清单（契约锁定前由脚本执行）

- [ ] 所有必填字段非空
- [ ] 范围表、成功判据、边界情况三处字段不含 R-GOAL-001 禁用词表中的词（定义见 `rule-registry.md`；本模板的说明段不计入）
- [ ] 所有内部引用编号存在（无死链）；`scope` 每行带 `S-n`、`criteria` 每行带 `A-n`、`boundaries.criterion` 指向存在的编号、`open_questions_list` 每项有 `id`
- [ ] 每条成功判据都有类型、判定者；自动类有可执行命令（**只调 `governance/` 与 `audit/` 入口**）、度量程序独占发射并输出真实指标（**指标行携带 `unit=` 且与契约 `unit` 一致**）、结构化阈值（metric/op/value/unit）含 `desc`；比率类有 `min_samples`（从 `METRIC|samples` 读取）
- [ ] 人工判据有 `method`，且确认记录将作为 evidence_pack 的 `manual_signoff` 项（见 `schemas.md` §3）
- [ ] 每条边界情况对应至少一条判据
- [ ] `open_questions_list` 中所有条目 `status=resolved`（派生 `open_questions == 0`）
- [ ] 风险等级已填写且有依据
- [ ] 负责人和批准人已在 `governance/roles.yaml` 注册且为签名/平台认证身份（在版本控制中有对应提交记录）
- [ ] front matter 与正文表格逐行一致（front matter 为权威来源；范围表列 = 编号 + 路径 + 描述）
- [ ] 锁定后 `contract_hash` 已写入且与规范化内容（front matter + 正文结构化表，RFC 8785）sha256 一致

### 14. 签字

| 角色 | 账号 ID | 日期 | 签字方式 |
| --- | --- | --- | --- |
| 负责人 |  |  | \[GPG/SSH 签名提交 / 平台审批\] |
| 批准人 |  |  |  |
| 安全负责人 |  |  |  |

---

> **执行者自检清单不在本文件内**：AI 作为执行者"动手前 / 中该怎么做"的叮嘱（复述契约、先写失败测试、附证据不附结论、遇触发升级等）已独立成 `references/executor-checklist.md`，由实现者随契约加载。本契约是每任务一份、锁定后只读的数据文件，不应把通用行为规则复制进每份契约。
