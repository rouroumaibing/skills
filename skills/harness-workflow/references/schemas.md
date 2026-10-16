# 数据格式与 Schema（被审产物侧，供检查器与审计方对齐）

> 本文件定义审计相关的数据交换格式，使不同人 / 不同项目写的脚本互不兼容的问题消失。所有格式以"机器可解析"为第一目标，人读说明放正文或注释。
> 在实装状态为"未实装"的项目里，这些格式仅作约定；脚本上线后按此校验。
> **每个 schema 都含 `schema_version` 字段**，脚本据此做前向兼容判断；字段演进时递增，旧脚本遇到未知 `schema_version` 应报错而非静默忽略。
> **`protected_paths` 取并集**：契约 front matter 与 `redlines.yaml` 两处的 `protected_paths` 合并去重，只能增加、不能减少任一侧已声明的受保护项。**这些列表的机器权威源是 `governance/redlines.yaml`**（见 `rule-registry.md` 末尾纪律），本文件与表格仅为镜像。
> **配置与运行时文件的位置约定（统一）**：治理配置 `governance/redlines.yaml`、`governance/roles.yaml` 置于 `governance/`；检查器 `audit/scripts/`；运行时产物 `.workflow/state.json`、`.workflow/audit.log`、`.workflow/probe-results.json` 置于 `.workflow/`。两处对 AI 均只读，下文引用一律使用上述完整路径，不再混用"根目录 / governance/ / audit/ / .workflow/"。
>
> **边界**：本文件只覆盖**被审产物**的格式（契约 / 追溯矩阵 / 证据包 / 交接 / 提案）与探针结论的**消费侧**约定。状态机、审计日志、放行记录、角色注册等治理运行时格式由消费项目自定（历史定义见 git 56ba8a7 的治理 schemas）。

---

## 1. 目标契约（机器可读面）

契约以 **YAML front matter** 作为脚本解析的**唯一权威来源**；其下的 Markdown 表格是同步视图（实装前手写保持同步、实装后由脚本从 front matter 渲染）。所有结构化判定字段（范围、判据、失败判据、非目标、不可触碰项、边界、风险、签字、待裁决）都定义在 front matter 中。

```yaml
---
contract_id: C-20261016-01
schema_version: 1
version: v1
contract_hash: ""        # 锁定后由工程写入规范化内容 sha256（见下方「契约哈希」）
task_name: "..."
owner: "alice"           # governance/roles.yaml 中注册的账号 ID
approver: "bob"
security_owner: "carol"
risk_level: low          # low | medium | high（枚举，映射见 rule-registry）
risk_rationale: "[依据风险分级表的哪一条]"
scope:                   # 范围表（S-*，单一来源，id + path + desc 三字段）
  - id: S-1
    path: path/or/moduleA
    desc: "[一句话客观描述,不含禁用词]"
  - id: S-2
    path: path/or/moduleB
    desc: "[...]"
protected_paths:         # 受保护路径（AI 只读）；与 redlines.yaml 取并集
  - contracts/
  - .workflow/
ai_writable_paths:        # AI 可写区；与 protected_paths 互不相交；范围检查豁免（见 R-IMP-001 / R-SEC-004）
  - proposals/
  - .handoff/
  - ai-reports/
non_goals:               # 非目标（N-*）
  - id: N-1
    desc: "[明确不做的事]"
    reason: "[原因]"
criteria:                # 成功判据（A-*，自动/人工；含 desc 与 method）
  - id: A-1
    desc: "500MB 视频上传成功率 >= 99%"
    type: auto           # auto | manual
    command: "audit/examples/test-upload"   # 只允许调用 governance/ 与 audit/ 下入口
    metric: "success_rate"   # 真实被测量，度量程序按 §1.1 输出该指标
    op: ">="
    value: 99
    unit: "%"
    min_samples: 1000     # 比率类最小样本数；由 METRIC|samples 读取
    method: "度量程序独占 METRIC| 发射；输出 METRIC|success_rate=<实测>;unit=% samples=<n>;退出码非0即失败"
    judge: 固定工程
  - id: A-2
    desc: "列表页播放起播时间 P95 <= 3 秒"
    type: auto
    command: "audit/examples/bench-play"
    metric: "p95"
    op: "<="
    value: 3000
    unit: "ms"
    method: "度量程序输出 METRIC|p95=<实测>;unit=ms samples=<n>"
    judge: 固定工程
  - id: A-3
    desc: "错误提示文案符合产品规范"
    type: manual
    method: "人对照规范逐条核对（说明对照文档与方式）；确认记录须进证据包 manual_signoff"
    judge: 人
failure_criteria:        # 失败判据（F-*）
  - id: F-1
    desc: "任一自动判据未通过"
forbidden_items:         # 不可触碰项（X-*），优先于 scope（见 R-SEC-003）
  - id: X-1
    target: "path/or/config"   # 可比对类型：文件 / glob / 符号；其余类型标 manual_review
    kind: "file"                # file | glob | symbol | manual_review（后两者由人核对）
    note: "为什么不可触碰；谁可批准例外"
boundaries:              # 边界情况（B-*）与对应判据
  - id: B-1
    scenario: "[场景]"
    expected: "[预期行为]"
    criterion: "A-1"      # 必须指向 §6 判据编号
open_questions_list:      # 待裁决项（Q-*，单一来源；§11 表格由本列表渲染）
  - id: Q-1
    question: "[例：视频转码失败是否自动重试]"
    proposed_by: "ai"
    options: ["A 重试 3 次", "B 不重试直接报错"]
    status: open          # open | resolved
    resolution: ""
open_questions: 0        # 派生计数：open_questions_list 中 status=open 的数量；为 0 才可锁定
locked_at: ""            # 锁定日期，锁定后由工程写入
signatures:             # 签字（锁定前必填，须为 governance/roles.yaml 注册且签名身份）
  owner:    { id: "alice", date: "", method: "gpg" }
  approver: { id: "bob", date: "", method: "gpg" }
# status / transitions 不在此：由 state.json 派生（格式由消费项目自定；历史定义见 git 56ba8a7）
---
```

**一致性规则**：front matter 的 `scope` / `criteria` / `boundaries` / `non_goals` / `failure_criteria` / `forbidden_items` / `open_questions_list` / `signatures` 必须与正文表格逐行一致；`check_contract.py` 比对两者，不一致即报错。front matter 为权威来源，正文表格是同步视图。正文表格的列必须与 front matter 的字段一一对应（如范围表 = `id` + `path` + `desc`），否则"逐行一致"无法机械比对。

> **契约哈希的范围（必读）**：锁定后，工程对"**规范化的 front matter（排除 `contract_hash`、`locked_at`、`signatures` 三个字段）+ 规范化正文的结构化表格**"取 sha256，写入 `contract_hash` 并记入审计日志。
> **规范性内容定义**：本契约的"规范性内容" = front matter 的全部字段（排除三字段）**加上正文中的结构化表格**（范围表、成功判据表、边界表、非目标表、不可触碰表、待裁决表——这些表本就由 front matter 渲染，是 front matter 的镜像视图）。**纯说明性散文段落（目标、背景、说明性文字）不计入哈希**——目的是避免"改一个错别字也强制升版本"，同时保证任何影响判定/范围/阈值的改动都会改变哈希。`status` 与推进历史不在本文件内，改由 `state.json` 派生（格式由消费项目自定；历史定义见 git 56ba8a7）。
> 规范化规则（脚本与人工都须遵守，保证不同实现得到相同的字节串）：
> 0. **YAML → JSON 转换（RFC 8785 只管 JSON，YAML 须先转 JSON）**：使用 **YAML 1.2 core schema**；**禁用锚点（`&`）、别名（`*`）、自定义 tag（`!`）**；**重复键报错**（不得 last-wins）；**不做隐式布尔 / 日期转换**（`yes`/`no`/`1.0`/`2026-01-01` 等均按字符串处理，除非显式加引号或类型标记）；标量优先用双引号包裹以避免歧义。转换后再走下面的 JSON 规范化。
> 1. **序列化格式 = 规范化 JSON（RFC 8785）**：对象键递归字典序排序；不输出无意义空白；标量用原值，**数字按 JSON 原生类型区分**（`99` 与 `"99"` 不相等）；**列表保持原顺序（不排序）**。
> 2. front matter：排除上述三字段后，序列化为规范化 JSON 字符串（UTF-8，无尾部换行）。
> 3. 正文：仅提取结构化表格区域（Markdown 表格块），按 front matter 相同字段顺序与内容规范化（行内 `|` 分隔、去除首尾空白）；纯散文段落丢弃。
> 4. 拼接 = 规范化 front matter 字符串 + `"\n"` + 规范化表格块，对该字节串取 sha256。
>
> 此后任一 front matter / 结构化表格内容变化都会使哈希与 `contract_hash` 不符，从而被检出。
> **`signatures` 仅为自述文本**：其中的 `method: "gpg"` 等只是声明，真实的签名凭据是**人经 GPG/SSH 的签名提交**或**平台审批 / 令牌记录**，不在本字段内；校验身份以签名提交 / 平台记录为准（身份权威见项目自建的 `governance/roles.yaml`）。

### 1.1 阈值表达式与指标输出格式（结构化，替代自由文本）

自由文本如 `"exit_code == 0"`、`"p95 <= 3000ms"` 无法被脚本可靠解析，统一改为结构化对象（见上面 `criteria` 示例的 `metric/op/value/unit/min_samples`）。

**指标输出格式（度量程序 → 脚本，且须防伪）**：自动类判据的度量程序必须**独占 `METRIC|` 前缀行的发射权**：

- 度量程序**捕获被测子进程的全部 stdout 且不透传 `METRIC|` 行**给脚本；或度量程序把结果写入只有它能写的受保护文件（`governance/` 下，AI 无写权限），脚本只读该文件。
- **被测代码（含 AI 写的测试）输出的任何 `METRIC|` 行一律被脚本忽略**——AI 无法通过 `print("METRIC|success_rate=100")` 伪造达标。脚本只接受来自度量程序通道（捕获流或受保护结果文件）的指标，不扫描被测子进程的 stdout。

指标行格式：

```
METRIC|success_rate=99.2;unit=% samples=1200
METRIC|p95=2870;unit=ms samples=500
```

- 度量程序退出码非 0 → 判据直接失败，不读取指标。
- 脚本读取 `metric=value`，按 `op` 与 front matter 的 `value` 比较；`metric` 名**必须与契约 `criteria[].metric` 一致**，否则判据不成立（防 AI 改名绕过）。
- **`unit` 必须携带且与契约 `criteria[].unit` 完全一致**（如 `ms` ≠ `s`、`%` ≠ 比率小数），否则判据不成立（防秒/毫秒混淆静默通过或失败）。
- **比率类判据**（如 `success_rate`）必须保证 `samples ≥ criteria[].min_samples`：`min_samples` **从 `METRIC|samples` 读取**（不另设来源）；`samples` 低于阈值或缺失 → 判据不成立（防"跑一个必过样本"伪造达标）。

**真实阈值不得藏进命令或 Makefile**：

- 阈值（如 `99%`）必须写在 front matter 的 `criteria[].value` 中，度量程序只负责输出实测 `metric`。
- **度量程序（基准 / 压测脚本）由人维护并列入 `governance/bench/`（受保护）**，AI 只提供被测输入，不得改度量程序——或至少"度量代码变更必须人审"（CI 在相应文件变更时要求人批准），防止 AI 改脚本让指标"永远达标"。

**判定命令与度量程序只允许调用 `governance/` 与 `audit/` 下的入口**：因此**不必把 `package.json` / `pyproject.toml` / `Makefile` 等通用文件整体列入 `protected_paths`**（AI 加依赖、改通用脚本不再触发 E-04）。如项目确需对这些文件的入口段做保护，采用**字段级校验 hook** 只比对 `scripts` / `tasks` 段，而非整文件；各级 Makefile 片段用 `**/Makefile` 匹配。此外，治理 / 检查器入口（`governance/`、`audit/` 下脚本）调用 pytest / jest 等测试运行器时，**使用自带且钉死的配置目录，忽略项目的 `conftest.py` / `pytest.ini` / `jest.config.*` / `pyproject.toml` 的 `tool` 段**（这些列入 `review_required_paths`，AI 可改但合入前必须人审），防止测试被改成"空转"（见 `rule-registry.md` 的 `review_required_paths`）。

---

## 2. 追溯矩阵（Traceability Matrix）

格式：CSV 或结构化表格，**每行一个需求/判据/边界编号、一个设计 ID、一个实现、一个测试 ID**（一格只放一个 ID，禁止 `S-1 / A-1` 这种多 ID 拼接）。脚本据此检查断链（无来源的代码变更、无实现的需求）。

```csv
# schema_version=1
req_id,design_id,impl_file,impl_symbol,impl_commit,test_id,status
S-1,D-1,src/x.go,UploadService.Upload,abc1234,T-1,linked
A-2,D-2,src/y.go,Player.Play,abc1235,T-2,linked
```

字段说明：

| 字段 | 约束 |
| --- | --- |
| `req_id` | 契约内已定义的编号，须以 `S-` / `A-` / `B-` 开头（`S-*` 是范围锚点"在哪改"，`A-*` 是成功判据、`B-*` 是边界预期，三者共同构成"需求/判据/边界"来源；`N-*`/`X-*` 也可引用）。编号必须在契约 front matter 的 `scope` / `criteria` / `boundaries` 中定义，脚本读契约校验 |
| `design_id` | 设计 ID（D-*）。**必须在该设计文档的「设计条目」章节中定义**（格式：`D-<n>: <一句话>` 起头的条目）；未定义的 D-* 报错 |
| `impl_file` + `impl_symbol` + `impl_commit` | 实现定位三元组：文件 + 符号名（函数/类/方法）+ 提交哈希。**不用"文件:行"**（行号随改动漂移，符号名 + 提交才是稳定锚点）。`impl_symbol` 的解析方式：以语言惯用的"类型.方法"或"函数名"文本匹配，脚本按 `impl_file` 的语言选用对应解析器（Go 用 `Type.Method`、Python 用 `module.func` 等），解析失败则标记为 `symbol-unresolved` 而非静默通过。`impl_commit` 为最佳努力锚点：squash / rebase 后会失效，**应配合 tag / 分支钉死**，失效后须重钉 |
| `test_id` | 测试编号（T-*）。**T-* 的定义位置**：测试文件顶部的 `@test T-1` 注释或测试函数名后缀 `_T1`，脚本据此把测试与需求关联 |
| `status` | **由脚本推导**，不手填：全链齐=`linked`；缺设计=`design-missing`；缺实现=`impl-missing`；缺测试=`test-missing` |

- `trace_check.py` 扫描：存在实现无对应需求 → 报 R-VER-005；存在需求无对应实现 → 报 R-VER-005。
- `design_id` 必须在设计文档中定义：脚本同时读设计文档的"设计条目"清单做交叉校验（设计文档已纳入验证者输入集 R-VER-003）。
- **多对多**：一个需求对应多个实现/测试时，用多行表达（一行一个 ID），不用"一格多 ID"。

> **CSV 元数据的放置**：`schema_version` 以**注释行**（`# schema_version=1`）放在首行，不占用数据表头行，避免破坏列解析。

---

## 3. 证据包清单（Evidence Pack Manifest）

证据包是一个目录 + 一份 `manifest.json`，由脚本生成，AI 只能引用不能改。每条 item 增加 `sha256` / `commit` / `tool_version` 完整性字段，使"AI 只能引用不能改"可被校验（校验和不符即说明被篡改）。

```json
{
  "schema_version": 1,
  "contract_id": "C-20261016-01",
  "contract_sha256": "f1e2d3c4b5a6...",
  "version": "v1",
  "commit_sha": "abc1234",
  "base_sha": "def5678",
  "runner_identity": "ci:governance-gate",
  "governance_commit": "tree:def5678",
  "contract_hash": "f1e2d3c4b5a6...",
  "generated_at": "2026-10-16T18:00:00+08:00",
  "items": [
    {
      "type": "gate_report",
      "path": "reports/gate.json",
      "corresponds_to": ["A-1","A-2"],
      "generated_by": "coverage-gate",
      "sha256": "a1b2c3d4...",
      "commit": "abc1234",
      "tool_version": "coverage-gate/2.3.1"
    },
    {
      "type": "trace_matrix",
      "path": "reports/trace.csv",
      "corresponds_to": ["S-1"],
      "generated_by": "trace_check.py",
      "sha256": "d4e5f6a7...",
      "commit": "abc1234",
      "tool_version": "trace_check.py/1.0.0"
    },
    {
      "type": "review_report",
      "path": "reports/verifier.md",
      "corresponds_to": ["*"],
      "generated_by": "launch_verifier.py",
      "sha256": "g7h8i9j0...",
      "commit": "abc1234",
      "tool_version": "launch_verifier.py/0.9.0"
    },
    {
      "type": "manual_signoff",
      "path": "reports/manual-signoff.json",
      "corresponds_to": ["A-3"],
      "generated_by": "human",
      "sha256": "k1l2m3n4...",
      "commit": "abc1234",
      "actor": "approver",
      "tool_version": "human/na"
    }
  ],
  "required_present": ["gate_report","trace_matrix","review_report"],
  "ai_drafts": [
    {
      "type": "deviations",
      "path": "ai-reports/deviations.md",
      "generated_by": "ai",
      "sha256": "j0k1l2m3...",
      "commit": "abc1234",
      "tool_version": "ai-context/na",
      "adopted": false
    }
  ]
}
```

`evidence_pack_check.py` 校验：所有 `required_present` 类型都存在；每个 `corresponds_to` 的判据 ID 在契约中存在；每个 item 的 `sha256` 与实际文件一致（防止 AI 改了证据还谎称未改）；`contract_sha256` 与契约 `contract_hash` 一致（证明证据对应被锁定的那一份）。

**绑定校验（`--check-bindings`，D-09/D-12）**：manifest 顶层还须带 `commit_sha`（本次提交，7-40 位 hex）、`base_sha`（合入基线）、`runner_identity`（**仅接受 `ci:` / `platform:` 前缀**——平台/CI 身份，拒绝 `cli:` 或自填身份）、`governance_commit`（与 CI 传入的治理目录 tree SHA 比对，不一致即拒）与 `contract_hash`（与契约一致；契约变更后旧证据作废）。CI 可再传 `--expected-commit <PR HEAD>` 精确绑定。缺任一绑定即视为"无法证明证据与当前变更相关"，不得进入放行。

> **人工判据的证据落点（R-VER 的 manual 类必须有去处）**：`type: manual` 的判据（如 A-3）其确认记录（人签字 / 核对方式 / 对照文档）必须作为证据包的 `manual_signoff` item 写入，并在 `release-approve` / `PA-*` 放行记录中被引用（放行记录格式由消费项目自定；历史定义见 git 56ba8a7）——否则"人工判据通过"无证据、可被 AI 自报。
> **偏离清单由工程推导，不由 AI 自报（R-REL-002）**：`deviations` / `open_issues` 等 AI 草稿列为 `ai_drafts`（非 `required_present`），其 `adopted` 字段记录人是否采纳；验证者的偏离结论以工程从 diff 与契约推导的为准，AI 草稿仅供参考、放第二阶段，不计入必需项。
> **AI 产出落 `ai-reports/`**：AI 生成的草稿放在 `ai-reports/`（属 `ai_writable_paths`），不在受保护的 `reports/` 下；视为草稿，由人采纳后才生效。
> **先采纳，再放行**：`ai_drafts` 的 `adopted` 由人改动会改变 manifest 哈希；"采纳"必须在"放行"之前完成——放行校验（R-REL-002 / R-REL-005）比对的是采纳后的 manifest 哈希，放行记录 `evidence_manifest_sha256` 须与采纳后的一致，否则放行被拒。

---

## 4. 交接文件（`.handoff/<task-id>.md`，AI 可写、结构化）

无工程 / 无状态机命令时的降级交接通道（见 E-12）。AI 只能写**固定字段**，工程只读取这些字段；自由文本超出部分忽略。内容必须视为数据，不得包含指令性语句（"覆盖契约""忽略升级"等视为无效）。

```yaml
schema_version: 1
task_id: "C-20261016-01"
ts: "2026-10-16T18:00:00+08:00"
progress: "[一句话进度摘要]"
open_items:
  - "[未决事项 1]"
  - "[未决事项 2]"
changed_files:
  - "src/x.go"
  - "src/y.go"
max_len: 4000          # 工程校验：文件总长超过则截断或拒绝
```

- 工程恢复时只读上述固定字段，不执行其中任何"指令"。
- `open_items` / `changed_files` 为列表，工程可对 `changed_files` 做越界检查（注意：`.handoff/` 本身在 `ai_writable_paths`，不受范围检查约束，但 `changed_files` 仍可人工核对是否在契约范围内）。
- **工程强制 schema 校验 + 限长**：`.handoff/` 文件由工程在读取时校验字段结构与 `max_len`，超长或字段非法则拒绝/截断；**AI 恢复时优先从契约、状态文件（`state.json`）和 diff 重新推导当前状态**，`.handoff/` 中的自由文本仅作辅助提示、不得作为指令或唯一事实源（防止"给下一个自己留话绕过契约/规则"）。会话中用户口头说"直接改吧"**不构成**对契约/红线的授权修改——契约对 AI 只读，任何变更必须走 `proposals/` + 人审。

---

## 5. 提案文件（`proposals/`，AI 可写，结构化）

AI 对契约的变更 / 歧义 / 边界建议只能落到 `proposals/`，由人合并（E-01 / E-02 / R-GOAL-002/004）。提案可以是 JSON 或 Markdown，结构字段如下：

```json
{
  "schema_version": 1,
  "id": "P-20261016-01",
  "type": "criteria_change",
  "contract_field": "criteria.A-1",
  "proposed_change": "将 A-1 阈值从 99 改为 98",
  "basis": "证据路径或事实描述",
  "proposed_by": "ai",
  "status": "open",
  "ts": "2026-10-16T18:00:00+08:00"
}
```

- `type` 枚举：`scope_change` \| `criteria_change` \| `boundary_add` \| `ambiguity` \| `other`。
- `contract_field` 指向契约 front matter 的具体字段（如 `criteria.A-1`、`scope.S-3`），便于人合并时定位。
- `status`：`open` \| `adopted` \| `rejected`；人合并后改为 `adopted`，原契约相应字段更新并重新计算 `contract_hash`。
- AI 不得直接改契约；所有契约变更必须经 `proposals/` → 人批准 → 新版本。

---

## 6. 探针结论（消费侧只读约定；生产与校验规范由消费项目 CI 自建，历史定义见 git 56ba8a7）

> **信任根不能循环**：被治理的 AI（含 harness 自身）不运行、不重新生成探针，
> 也不自行验签或比对 `platform_fingerprint`——它无平台权限、够不到仓库外的
> 验证公钥。探针结论必须由 CI / 平台产出，harness 只读取并转述。

审计方读取 `.workflow/probe-results.json`（或平台原生 attestation / check-run）时：

- **读取的字段**：`entries[].rule_id` + `entries[].result`（`pass` / `fail`）、
  `governance_commit`、`binding.contract_id`、`signature` / `generated_by`（只记录来源，不验签）。
- **判定规则**：文件缺失、签名校验失败、验证公钥在仓库内、平台配置变更事件
  已触发失效、`governance_commit` 与当前 `governance/` 目录 tree 哈希不匹配
  → 所有硬规则视为"未实装"。
- **映射**：规则存在且 `result=pass` → `已实装通过`；存在且 `result=fail` →
  `已实装未通过`（按违反处理，暂停并升级）；不存在 → `未实装`。
- **重新探测**：放行 / 开工前置核验前，若平台配置有变更或结论已老化，必须
  要求 CI / 人重新生成，不得使用陈旧结论。
- 审计报告据此逐条列示三态，**不得**在未实装时写"门禁通过 / 全部绿 / 已验证"。
