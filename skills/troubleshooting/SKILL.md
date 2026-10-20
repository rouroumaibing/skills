---
name: troubleshooting
description: "触发词：排查问题、定位问题、查日志、查 pod、客户报错、某组件某版本出问题、切换到某个版本看代码、多个仓库里找代码、连环境看机器/集群/节点日志、查 k8s pod。只要用户提到排查/定位问题/查日志/查pod/客户报错/版本定位/多仓库搜代码，或要求执行集群/节点/DB 写操作（删 pod、scale 扩缩容、rollout restart、systemctl 重启、修数据等修复/回滚类诉求），就应使用本 skill，即使用户没明确说 troubleshooting。统一入口：(1) 连跳板机（config.conn 的公网/私网 IP）查集群 pod/节点日志；(2) 在多个本地代码库（config.codehub）查代码；(3) 根据客户反馈的报错与软件版本，切到对应版本只读 worktree（config.worktree.workdir）定位问题（通常无法连客户环境）。| Troubleshooting unified entry: SSH to the bastion (config.conn public/private IP) to inspect cluster pod/node logs, search across local code repos (config.codehub), and locate issues by switching to the customer's reported component+version read-only worktree (config.worktree.workdir) — typically without access to the customer environment. Use this skill whenever the user mentions troubleshooting, locating a bug, reading logs, inspecting pods, customer-reported errors, version-specific code lookup, or cross-repo code search — even if they don't explicitly say 'troubleshooting'. Remediation writes (kubectl/SSH/DB) go only through scripts/apply.py (whitelist + T1/T2R/T2U/T3 tiers + mandatory backup), re-verified by scripts/verify.py (PASS/FAIL). @troubleshooting"
metadata:
  agent_created: true
  version: "7"
---

# Troubleshooting

所有路径相对本 skill 目录。先判断场景，再**只读取对应的 mode 文件**，不要三个都读。

## 依赖与初始化

- **依赖**：`python3`（≥3.8）、`PyYAML`、`paramiko`、`git`，以及跳板机上的 `kubectl`。SSH 传输用 Paramiko（纯 Python，不依赖本机 OpenSSH，因此没有 Windows `ControlMaster`/`ControlPath` 的 `getsockname failed` 问题）；节点跳转仍在跳板机上用 `ssh`。排查脚本均为只读 Python（配置用 PyYAML 解析）；**运行期唯一写操作入口是 `scripts/apply.py`**（白名单 + 授权分层 + 强制先备份），复验用 `scripts/verify.py`（PASS/FAIL）。
- **首次配置**：编辑 `config.yaml` 填入 `conn`（跳板机公网/私网 IP，SSH 用户默认 `root`）、`codehub`（组件→本地仓库路径，map 形式可带 `aliases`/`selector`）、`worktree.workdir`（版本 worktree 目录）。可选覆盖键：`conn.user` / `conn.namespace` / `conn.context` / `conn.scan_dirs`（详见 per-skill README）。**不要写任何密码/密钥**，认证走 SSH 公钥免密。**首次免密用 `scripts/setup_ssh.py`**：`local`（本机→跳板机）/ `bastion`（跳板机→节点）/ `status`（体检），另有 `export-bundle` / `import-bundle` 用于免密凭据的分发与导入，幂等可重复运行；两类密钥的分发与防覆盖策略见同目录 `README.md` 第 3 节。注意：`setup_ssh.py` 是**配置期**的写工具（由运维人员手动运行，排查过程不要调用它）；**运行期**唯一写操作入口是 `scripts/apply.py`（修复场景，受写命令白名单 / T1/T2R/T2U/T3 授权分层 / 强制先备份三重约束，详见下方「安全红线」）。
- **环境访问只通过 `scripts/`**：Agent 不得直接执行 `ssh` / `kubectl`；脚本内部已是「ssh 到跳板机 IP → 在跳板机上执行 kubectl」，连接信息只来自 `config.conn`，Agent 不得自己拼接 IP 或主机名。**写操作只经 `scripts/apply.py`**（kubectl / node / db 三通道），复验只经 `scripts/verify.py`。详见下方「安全红线」。

## 场景路由

| 用户情况 | 读取 |
|---|---|
| 要连跳板机、看 pod/节点/机器日志 | modes/env-debug.md（内部再按证据分流到 modes/diagnostics/<场景>.md） |
| 要在多个本地仓库里查代码，不涉及特定客户版本 | modes/code-search.md |
| 客户反馈了问题，只有报错/版本，连不上客户环境 | modes/customer-issue.md |
| 用户问「某行为在运行时**会不会发生 / 是否会被… / 确认…**」等**行为确认型**问题 | 默认走 modes/env-debug.md **上环境证伪**并套用 `templates/behavior-confirmation.md` 出报告；**代码级确认作为可选并行旁证**：组件源码可达（codehub/upstream）即分析佐证，不可达则跳过，非 fallback |

场景不明确时，用一句话向用户确认，不要猜。
场景③中**严禁**尝试连接任何环境。
**行为确认型默认上环境**：当问题是"运行时到底发不发生"，runtime 证伪比静态读码权威（K8s/Operator 领域代码与现实常因 addon 魔改、CRD 行为、finalizer 而偏离）。能连环境就先连，**代码级确认作为可选并行旁证**：能取到对应版本源码就做代码佐证（有更好），取不到就跳过（没有也没关系），不阻塞主流程；结论可标【已证实(运行时+代码)】，仅当环境也不可达时才标【推测】并说明缺什么证据。最终交付必须套用 `templates/behavior-confirmation.md`（结论先行 / 时间线 / 环境指纹 / 关键证据含对照组与残留检查）。

## 通用原则

1. 证据分级：结论中必须区分【已证实】（来自日志原文或对应版本代码）和【推测】。
2. **问题驱动两段式 + 修复验证闭环**（场景①的总流程，详见 modes/env-debug.md）：
   - **线索抽取**：从问题里抽出关键字/组件/节点/容器/时间，**先显式列出再查**（抽错可被一眼纠正，防静默跑偏）；
   - **第一段·全量发现**（无边界）：`pods`（默认 `-A` 全部 namespace）、`events -A --warnings`、`node-find` 建节点日志索引——目标是「不漏」；
   - **第二段·精确下钻**（有边界）：按线索 `logs --grep 关键字 --context N`、按组件 selector、按节点 `node-log`——目标是「不炸」；
   - **迭代**：新证据产生新线索，回到第一段。证据落入某类场景（Pod/节点/网络）时，再读 `modes/diagnostics/<场景>.md` 深挖。
   - **第三段·修复-验证闭环**（定位到根因、需动手修时）：授权 → 备份 → `apply.py` 执行 → `verify.py` 复验 → 不通过回滚；写操作只经 apply.py（T1/T2R/T2U/T3 分层见「安全红线」）。
3. **人机协作**：简单问题 Agent 自动跑完给结论；复杂问题中用户可随时插话改道——Agent 不得以「流程没走完」为由拒绝转向。但 Agent 自行下结论前，必须走完对应场景清单的「最小充分集」。
4. 缺信息就问，不替用户选组件、选版本。
5. 输出结论时给出证据位置：日志取自哪个 pod/节点/时间，代码取自哪个组件/tag/文件:行号。
6. **交付强制套用对应场景模板（避免自由散文）**：本 skill 的产出是结构化报告，不是散文。行为确认型 → `templates/behavior-confirmation.md`；根因/故障类 → `templates/troubleshooting.md`。落笔前**先读对应模板、按它的板块填空**，不要自创格式。理由：模板保证跨 run / 跨 agent 的结论形态一致、证据可比对；自由格式极易漏掉关键证据（如对照组、残留检查、环境指纹）。环境不可达、只能用代码检索证伪时，须在结论明确标注【推测】并说明缺什么运行时证据。

## 安全红线

- 只通过 scripts/ 下的脚本访问跳板机，**不得直接执行 ssh、kubectl**；脚本内部已是 ssh 到跳板机 IP 后执行 kubectl，Agent 不得自己拼接 IP 或主机名（连接信息只来自 `config.conn`）。
- 除 `apply.py` 外所有脚本均为只读；`apply.py` 是唯一运行期写操作入口，受「写命令白名单 + 授权分层（T1/T2R/T2U/T3）+ 强制先备份」三重约束，任一不过即在触达环境前拒绝；不得修改脚本绕过限制。
    - **白名单**：`config.remediation.write_whitelist`，结构化声明表——命令切 token 后按 verb→kind→name→flag 逐字段比对，拒绝时报精确到字段的原因；T3 反模式为结构判断、最先评估。
    - **备份/快照**：kubectl 通道自动 `get -o yaml`；node 通道写命令另行按写白名单放行（如 `systemctl restart <unit>`），node/db 通道强制先 `--snapshot` 只读快照（快照命令同样白名单化）。
- 节点日志仅允许读取 `config.conn.scan_dirs`（缺省 `/var/log`、`/var/log/pods`、`/var/log/containers`、`/data/logs`、`/var/lib/kubelet`）下的路径，且带尾斜杠校验防目录前缀绕过；`--any-path` 是唯一显式越界开关，用时必回显告警（可审计，不静默）。
- `node-exec` 只放行按场景（node/network/pod）白名单化的只读诊断命令；`nsenter-net` 为复合门（纯数字 PID + network 白名单内部命令，`-t <pid> -n` 构型由脚本拼装），用于容器网络命名空间内的只读取证。
- 不得读取 `~/.ssh/` 下的任何内容。
- 状态码为 `NEEDS_MANUAL_AUTH`、`HOP_FAIL` 时：**只提示用户在自己终端手动执行对应命令，然后等待**，不得自行重试绕过。
- 节点日志（`node-log` / `node-journal`）依赖跳板机对节点的免密；若 `HOP_FAIL`，只提示用户在跳板机侧配置对该节点的免密（`ssh root@<node>`），Agent 不自行处理密钥分发。
- **本 skill 没有 production 确认门（protected）**：排查脚本只读，但跳板机/节点均以 `root` 登录，`root` 本身不构成只读边界。请只把它指向开发/测试跳板机；若必须连生产，调用前请自行确认（**连生产属 T2「显式确认后放行」，非 T3 禁止**）。节点日志路径若指向软链接，`root` 下 `tail` 会跟随链接读到目录外文件，脚本已在节点上 `readlink -f` 解析真实路径后重新校验扫描目录集；读取目录集外路径属显式越界（`--any-path`），请确认可信。
- **写操作授权分层（T1/T2R/T2U/T3，通用框架，覆盖 kubectl / SSH(node) / DB 所有写通道；以「是否修改既有环境状态 / 可恢复性」为锚，非「是否写」）**：
  - **T1 非破坏性**（不修改既有状态：建自清理测试/对照资源（如 `kubectl create job`）、读 `get`/`logs`/代码检索、本地脚本、出报告）→ **默认放行**。
  - **T2R 破坏性·可恢复**（改既有但可还原。kubectl：删测试 pod、patch label/annotation、scale 0↔N、rollout restart、删可重建无状态资源；node(SSH)：`systemctl restart/start/stop <unit>`；db：通常无 T2U 以下写操作）→ **不需单独授权，但强制「先备份后执行」**（kubectl 通道 `apply.py` 自动 `get -o yaml` 存盘；node/db 通道强制 `--snapshot`）。*仅在独占测试/开发集群成立；共享/近生产集群（`remediation.shared_cluster=true`）升为需 `--confirm` 授权。*
  - **T2U 破坏性·不可恢复**（删数据 PVC/PV、含唯一状态资源、集群级 RBAC/CRD/Namespace、外部副作用；node：`rm`/`truncate`/`chown` 文件、journal 回收；db：任何写 SQL——UPDATE/DELETE/DDL/TRUNCATE 等）→ **需显式授权（`--confirm`，`--confirm-by` 留痕）+ 先备份后执行**（语义=导出当前态供审计/取证，非可还原）。
  - **UNCERTAIN 拿不准可恢复性** → **默认升格 T2U（需授权+备份）**；apply.py 对白名单命中但无更细层级规则的命令一律按 T2U 处理。
  - **T3 全局禁止**（任何授权不可覆盖）：通道绕过（直连 `ssh`/`kubectl`/自拼 IP，或绕过 apply.py 直接写）、脚本篡改、凭据窃取（读 `~/.ssh`）、自动绕过确认（`NEEDS_MANUAL_AUTH`/`HOP_FAIL` 自处理）、越界读取（`scan_dirs` 外 / 滥用 `--any-path` 不告警）；命令级反模式（删控制面 namespace、`delete <kind> --all` 批量删除、`rm -rf /`/mkfs/dd 写盘、`DROP DATABASE`、GRANT/REVOKE）由 apply.py 的 T3 表最先评估、直接拒绝。
  - **修复-验证闭环**（定位到根因、需动手修时，详见 modes/env-debug.md 第三段）：**授权判定 → 备份 → 执行（`apply.py`）→ 复验（`verify.py` 输出 PASS/FAIL）→ 不通过回滚（`apply.py rollback <备份>`）→ 再复验**。复验全部 PASS 才收尾；node/db 通道无自动回滚，按备份快照手工恢复并如实记录。
  - **通用铁律**：所有写操作（kubectl / SSH / DB）只经 `scripts/apply.py` 一个入口——白名单外本地即拒（`NOT_WHITELISTED`，不产生连接），层级不符拒绝执行（`NEEDS_AUTH_CONFIRM`/`TIER_T3_FORBIDDEN`），备份失败不执行（`BACKUP_FAILED`）。每次写操作前必做「破坏性预分析」并声明层级（目标是否已存在 / 是否集群级 / 是否自清理 / 是否跨租户）；拿不准→T2U。备份前置对 T2R/T2U 强制；T2R 在独占测试集群不需人工授权，但创建测试/对照 Job 须自带短 ttl 或 teardown 方案，验证完一并回收。

## 脚本状态码速查

| 状态 | 含义 | 动作 |
|---|---|---|
| NEEDS_MANUAL_AUTH | 本机到跳板机未连通 / 未免密 | 提示用户运行 `python3 scripts/setup_ssh.py local`，等待 |
| KUBECTL_FAIL | 跳板机上 kubectl 不可用 | 汇报给用户，不要重试 |
| UNKNOWN_COMPONENT | 组件名不在 config.codehub | 展示 known 列表，请用户选 |
| NO_PODS | selector 没匹配到 pod | `logs`/`pods` 已默认扫描全部 namespace，并**平台无关地**自动尝试通用标签键 `app=`/`k8s-app=`/`component=`，再按 **pod 名称子串**兜底；组件 label 特殊时在 codehub 配 `selector` 覆盖。注意：**托管控制面集群（云厂商托管版）不下发控制面 Pod**，此时 NO_PODS 属真阴性，应改用节点日志或运行时实验取证 |
| TOO_MANY_PODS | 匹配 pod 超过 20 个 | 用 `--pod <name>` 指定单个 pod，或 `-n <ns>` 收窄后再读日志 |
| HOP_FAIL | 跳板机到节点未免密 | 提示用户在跳板机侧配置对该节点的免密 |
| NOT_FOUND | 该组件无此版本 tag | 展示 nearby，**请用户确认**，不得自行替换版本 |
| AMBIGUOUS_TAG | 多个 tag 指向不同 commit | 汇报，请用户决定 |
| NO_LOCAL_REPO | codehub 里该组件路径不存在或不是 git 仓库 | 确认 config.codehub 的本地路径，仓库需已 clone 到该路径 |
| NOT_WHITELISTED (apply.py) | 写命令不在 remediation 白名单 | 本地拒绝（未连接）；确属合法修复由运维在 config 登记 |
| TIER_T3_FORBIDDEN (apply.py) | 命中 T3 反模式 | 任何授权不可覆盖，停止，不要变体重试 |
| NEEDS_AUTH_CONFIRM (apply.py) | T2U（或共享集群 T2R）未获授权 | 向用户说明操作与影响，获同意后加 `--confirm --confirm-by` 重试 |
| SNAPSHOT_REQUIRED (apply.py) | node/db 通道未提供 `--snapshot` | 补只读快照命令/SELECT 后重试 |
| BACKUP_FAILED (apply.py) | 备份/快照失败 | **不执行写操作**；排查备份前提后重试 |
| WRITE_APPLIED / WRITE_FAILED (apply.py) | 写操作成功 / 失败 | 成功即转 verify.py 复验；失败用备份回滚或取证 |
| ROLLBACK_APPLIED / ROLLBACK_UNSUPPORTED (apply.py) | 回滚完成 / 该通道无自动回滚 | 后者按备份快照手工恢复并记录 |
| VERIFY_PASS / VERIFY_FAIL (verify.py) | 复验全部通过 / 存在失败 | FAIL → 回滚 → 再复验；仍 FAIL 停下汇报，不得反复试错 |
