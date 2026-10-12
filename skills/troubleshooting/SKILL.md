---
name: troubleshooting
description: "触发词：排查问题、定位问题、查日志、查 pod、客户报错、某组件某版本出问题、切换到某个版本看代码、多个仓库里找代码、连环境看机器/集群/节点日志、查 k8s pod。只要用户提到排查/定位问题/查日志/查pod/客户报错/版本定位/多仓库搜代码，就应使用本 skill，即使用户没明确说 troubleshooting。统一入口：(1) 连跳板机（config.conn 的公网/私网 IP）查集群 pod/节点日志；(2) 在多个本地代码库（config.codehub）查代码；(3) 根据客户反馈的报错与软件版本，切到对应版本只读 worktree（config.worktree.workdir）定位问题（通常无法连客户环境）。| Troubleshooting unified entry: SSH to the bastion (config.conn public/private IP) to inspect cluster pod/node logs, search across local code repos (config.codehub), and locate issues by switching to the customer's reported component+version read-only worktree (config.worktree.workdir) — typically without access to the customer environment. Use this skill whenever the user mentions troubleshooting, locating a bug, reading logs, inspecting pods, customer-reported errors, version-specific code lookup, or cross-repo code search — even if they don't explicitly say 'troubleshooting'. @troubleshooting"
metadata:
  agent_created: true
  version: "6"
---

# Troubleshooting

所有路径相对本 skill 目录。先判断场景，再**只读取对应的 mode 文件**，不要三个都读。

## 依赖与初始化

- **依赖**：`python3`（≥3.8）、`PyYAML`、`paramiko`、`git`，以及跳板机上的 `kubectl`。SSH 传输用 Paramiko（纯 Python，不依赖本机 OpenSSH，因此没有 Windows `ControlMaster`/`ControlPath` 的 `getsockname failed` 问题）；节点跳转仍在跳板机上用 `ssh`。脚本为只读 Python（配置用 PyYAML 解析）。
- **首次配置**：编辑 `config.yaml` 填入 `conn`（跳板机公网/私网 IP，SSH 用户默认 `root`）、`codehub`（组件→本地仓库路径，map 形式可带 `aliases`/`selector`）、`worktree.workdir`（版本 worktree 目录）。可选覆盖键：`conn.user` / `conn.namespace` / `conn.context` / `conn.scan_dirs`（详见 per-skill README）。**不要写任何密码/密钥**，认证走 SSH 公钥免密。**首次免密用 `scripts/setup_ssh.py`**：`local`（本机→跳板机）/ `bastion`（跳板机→节点）/ `status`（体检），另有 `export-bundle` / `import-bundle` 用于免密凭据的分发与导入（见 README 第 3 节），幂等可重复运行；两类密钥的分发与防覆盖策略见同目录 `README.md` 第 3 节。注意：`setup_ssh.py` 是本 skill **唯一允许写操作**的配置工具，由运维人员手动运行，排查过程不要调用它。
- **环境访问只通过 `scripts/`**：Agent 不得直接执行 `ssh` / `kubectl`；脚本内部已是「ssh 到跳板机 IP → 在跳板机上执行 kubectl」，连接信息只来自 `config.conn`，Agent 不得自己拼接 IP 或主机名。详见下方「安全红线」。

## 场景路由

| 用户情况 | 读取 |
|---|---|
| 要连跳板机、看 pod/节点/机器日志 | modes/env-debug.md（内部再按证据分流到 modes/diagnostics/<场景>.md） |
| 要在多个本地仓库里查代码，不涉及特定客户版本 | modes/code-search.md |
| 客户反馈了问题，只有报错/版本，连不上客户环境 | modes/customer-issue.md |

场景不明确时，用一句话向用户确认，不要猜。
场景③中**严禁**尝试连接任何环境。

## 通用原则

1. 证据分级：结论中必须区分【已证实】（来自日志原文或对应版本代码）和【推测】。
2. **问题驱动两段式**（场景①的总流程，详见 modes/env-debug.md）：
   - **线索抽取**：从问题里抽出关键字/组件/节点/容器/时间，**先显式列出再查**（抽错可被一眼纠正，防静默跑偏）；
   - **第一段·全量发现**（无边界）：`pods`（默认 `-A` 全部 namespace）、`events -A --warnings`、`node-find` 建节点日志索引——目标是「不漏」；
   - **第二段·精确下钻**（有边界）：按线索 `logs --grep 关键字 --context N`、按组件 selector、按节点 `node-log`——目标是「不炸」；
   - **迭代**：新证据产生新线索，回到第一段。证据落入某类场景（Pod/节点/网络）时，再读 `modes/diagnostics/<场景>.md` 深挖。
3. **人机协作**：简单问题 Agent 自动跑完给结论；复杂问题中用户可随时插话改道——Agent 不得以「流程没走完」为由拒绝转向。但 Agent 自行下结论前，必须走完对应场景清单的「最小充分集」。
4. 缺信息就问，不替用户选组件、选版本。
5. 输出结论时给出证据位置：日志取自哪个 pod/节点/时间，代码取自哪个组件/tag/文件:行号。

## 安全红线

- 只通过 scripts/ 下的脚本访问跳板机，**不得直接执行 ssh、kubectl**；脚本内部已是 ssh 到跳板机 IP 后执行 kubectl，Agent 不得自己拼接 IP 或主机名（连接信息只来自 `config.conn`）。
- 所有脚本均为只读；不得修改脚本绕过限制。节点日志仅允许读取 `config.conn.scan_dirs`（缺省 `/var/log`、`/var/log/pods`、`/var/log/containers`、`/data/logs`、`/var/lib/kubelet`）下的路径，且带尾斜杠校验防目录前缀绕过；`--any-path` 是唯一显式越界开关，用时必回显告警（可审计，不静默）。`node-exec` 只放行按场景（node/network/pod）白名单化的只读诊断命令；`nsenter-net` 为复合门（纯数字 PID + network 白名单内部命令，`-t <pid> -n` 构型由脚本拼装），用于容器网络命名空间内的只读取证。
- 不得读取 `~/.ssh/` 下的任何内容。
- 状态码为 `NEEDS_MANUAL_AUTH`、`HOP_FAIL` 时：**只提示用户在自己终端手动执行对应命令，然后等待**，不得自行重试绕过。
- 节点日志（`node-log` / `node-journal`）依赖跳板机对节点的免密；若 `HOP_FAIL`，只提示用户在跳板机侧配置对该节点的免密（`ssh root@<node>`），Agent 不自行处理密钥分发。
- **本 skill 没有 production 确认门（protected）**：脚本只读，但跳板机/节点均以 `root` 登录，`root` 本身不构成只读边界。请只把它指向开发/测试跳板机；若必须连生产，调用前请自行确认。节点日志路径若指向软链接，`root` 下 `tail` 会跟随链接读到目录外文件，脚本已在节点上 `readlink -f` 解析真实路径后重新校验扫描目录集；读取目录集外路径属显式越界（`--any-path`），请确认可信。

## 脚本状态码速查

| 状态 | 含义 | 动作 |
|---|---|---|
| NEEDS_MANUAL_AUTH | 本机到跳板机未连通 / 未免密 | 提示用户运行 `python3 scripts/setup_ssh.py local`，等待 |
| KUBECTL_FAIL | 跳板机上 kubectl 不可用 | 汇报给用户，不要重试 |
| UNKNOWN_COMPONENT | 组件名不在 config.codehub | 展示 known 列表，请用户选 |
| NO_PODS | selector 没匹配到 pod | `logs` 已默认扫描全部 namespace；先 `pods` 子命令确认 selector 是否命中，`-n <ns>` 收窄重试，仍无则检查 codehub 的 `selector` 配置 |
| TOO_MANY_PODS | 匹配 pod 超过 20 个 | 用 `--pod <name>` 指定单个 pod，或 `-n <ns>` 收窄后再读日志 |
| HOP_FAIL | 跳板机到节点未免密 | 提示用户在跳板机侧配置对该节点的免密 |
| NOT_FOUND | 该组件无此版本 tag | 展示 nearby，**请用户确认**，不得自行替换版本 |
| AMBIGUOUS_TAG | 多个 tag 指向不同 commit | 汇报，请用户决定 |
| NO_LOCAL_REPO | codehub 里该组件路径不存在或不是 git 仓库 | 确认 config.codehub 的本地路径，仓库需已 clone 到该路径 |
