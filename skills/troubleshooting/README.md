# troubleshooting skill 安装与使用

依赖：python3（≥3.8）、git、跳板机上的 kubectl，以及 Python 包 **PyYAML + paramiko**（`pip install pyyaml paramiko`；paramiko 会一并装上 cryptography）。SSH 传输用 **Paramiko**（纯 Python，不依赖本机 OpenSSH，因此不受 Windows `ControlMaster`/`ControlPath` 报 `getsockname failed: Not a socket` 的影响）；密钥用 cryptography 生成，文件传输用 SFTP。**节点跳转仍在跳板机上用 `ssh`**（保留共享密钥模型；曾评估过 testkit 式 direct-tcpip 隧道，因要求本地持有节点凭据而否决）。传输层带 30s keepalive（防长诊断会话被 NAT 空闲断连）、transport 级失败自动重连一次并重试（命令超时不重试，防重复执行）、结果记录耗时（均参考 testkit SSHExecutor）。

## 1. 安装
- 依赖：`pip install pyyaml paramiko`（或 `pip3 install pyyaml paramiko`）。若运行脚本报 `需要 PyYAML` / `需要 paramiko`，即为此依赖未装。
- 本仓库：直接放在 `skills/troubleshooting/`（已就位），仓库 README 索引表已登记。
- 其他 Agent / 项目：复制整个 `troubleshooting/` 目录到该 Agent 的 skills 路径，并在项目 `AGENTS.md`（或对应规则文件）加一句：
  `排查问题时，先阅读 <路径>/troubleshooting/SKILL.md 并遵循其中规则。`

## 2. 配置
1. 编辑 `config.yaml`（最小形态只需三段）：
   - `conn`：`public-ip` / `private-ip`（同一台跳板机的两个地址，私网优先、连不上回退公网）。
   - `codehub`：组件别名 -> 本地仓库路径（查最新代码用）。支持两种写法：
     - 简写：`components-1: ~/work/xxx`
     - map 形式（带别名与可选 selector）：
       ```yaml
       components-2:
         path: ~/work/xxx
         aliases: [订单, order]      # 可用别名匹配组件
         selector: app=order-svc     # 覆盖 pod label，缺省回退 app=<组件名>
       ```
   - `worktree.workdir`：客户报版本时只读 worktree 的工作目录。
2. **可选覆盖键**（不写则用默认值，保持 config 极简）：
   - `conn.user`：SSH 用户，默认 `root`。
   - `conn.namespace` / `conn.context`：跳板机 kubectl 的 namespace / context，缺省用跳板机当前。注意：`logs.py` 的查询默认 `-A` 全 namespace 发现，`-n <ns>` 显式收窄优先，业务 pod 不在 default namespace 也**无需**再配 `namespace`。
   - `conn.scan_dirs`：节点日志**扫描目录集**（发现面默认开放、读取面按此收敛），缺省为 `/var/log`、`/var/log/pods`、`/var/log/containers`、`/data/logs`、`/var/lib/kubelet`。`node-log` 的读取路径与 `node-find` 的搜索范围都受此集约束；**集外路径确需读取时用 `node-log --any-path` 显式越界**（必回显告警，可审计）。收窄此集即可收紧边界；**敏感日志目录（secure / audit 等）请勿列入**，避免内容进入模型上下文。
   - `conn.connect_timeout` / `conn.probe_timeout` / `conn.cache_ttl` / `conn.request_timeout`：SSH 连接超时、私网可达性探测超时、私网 IP 缓存有效期、kubectl `--request-timeout`（单位秒）。默认值分别为 `10` / `5` / `300` / `10`，可按网络环境调整。
3. **不要写任何密码**。认证靠 SSH 公钥免密，用 `scripts/setup_ssh.py` 一键配置（**幂等，可重复运行**；传输走 Paramiko，无需本机 OpenSSH / `~/.ssh/config`）：
   - `python3 scripts/setup_ssh.py local`：本机 → 跳板机。生成个人密钥（若无，cryptography 生成 OpenSSH 格式）、把公钥**追加**到跳板机 `authorized_keys`。首次可能需输入一次跳板机密码（仅交互终端）。
   - `python3 scripts/setup_ssh.py bastion`：跳板机 → 节点。在跳板机上确保共用密钥存在，并把其公钥**追加**到各节点（缺省用 `kubectl` 自动发现，或 `--nodes <ip...>` 指定）。
   - `python3 scripts/setup_ssh.py status`：体检当前免密状态（本机密钥 / 到跳板机 / 到节点）。
   - `python3 scripts/setup_ssh.py --help`：查看全部子命令（含 `export-bundle` / `import-bundle`）。

## 3. 免密模型与分发（重要：防止各人各生成一套、互相覆盖机器配置）

两条链路、两类密钥，分发策略不同：

| 链路 | 密钥归属 | 谁生成 | 如何分发 | 如何吊销 |
|---|---|---|---|---|
| 本机 → 跳板机 | **个人密钥** | 每人在自己机器上各自生成 | 各跑 `setup_ssh.py local`，公钥**追加**到跳板机 `authorized_keys`（按整行去重，不动他人） | 删该公钥一行，不影响他人 |
| 跳板机 → 节点 | **全组共用一把** | 只生成一次，存放在跳板机上 | **不分发**；同事通过共用这台跳板机天然继承 | 需在所有节点删该公钥行 |

**幂等 / 防覆盖（脚本已内建）**：
- 密钥**只生成不覆盖**：已存在则跳过；若只有公钥没有私钥，报错退出而不重新生成（避免与已分发公钥失配）。
- `authorized_keys` **只追加不覆写**：用 `grep -qxF` 按整行去重后再追加；重复运行不产生重复行，也不清掉别人的密钥。
- **主机密钥校验**（Paramiko）：未知主机按 `accept-new` 接受并记录；**已存主机密钥变更则拒绝**（`BadHostKeyException`），不静默信任。
- 私钥权限 `600`，`~/.ssh` 目录 `700`；本机无需 `~/.ssh/config` 或 ControlMaster，连接在进程内按 target 缓存复用。

**给同事分发时，拷哪个文件？**
- **接入现有共用跳板机**：同事在自己机器跑 `setup_ssh.py local` 即可，**无需拷任何密钥文件**（他生成自己的个人密钥）。你只需把 `troubleshooting/config.yaml` 和本 README 给他。
- **新建「并行跳板机」访问同一批节点**（唯一需要拷私钥的场景）：用
  ```
  python3 scripts/setup_ssh.py export-bundle --host root@<源跳板机IP> --out bastion-key-<时间戳>.tar.gz
  python3 scripts/setup_ssh.py import-bundle bastion-key-<时间戳>.tar.gz --host root@<新跳板机IP>
  ```
  经 SFTP 把源跳板机的密钥对（`id_ed25519` + `.pub`）打包拷过去，新跳板机因此被各节点继续信任，**无需重新生成、无需重推所有节点**。`import-bundle` 幂等：目标已有密钥则**拒绝覆盖**，从机制上避免"又生成一套"导致各节点 `authorized_keys` 分裂。
  - **纪律**：bundle 含**私钥**，只通过既有可信通道传输，**禁止邮件 / 聊天工具 / 提交 git**；用后即焚，怀疑泄露就在所有节点删掉对应公钥行。

**节点首免密（HOP_FAIL）**：`bastion` 子命令对尚未免密的节点会打印 `HOP_FAIL` 与手动命令。在**跳板机**上执行一次即可（跳板机→节点仍用 `ssh`）：
```bash
# 跳板机上（setup_ssh.py bastion 通常已自动生成密钥；手动则：）
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519   # 仅当跳板机还没有密钥
ssh-copy-id -i ~/.ssh/id_ed25519.pub root@<节点IP>  # 把跳板机公钥追加到节点 authorized_keys
ssh root@<节点IP> true                             # 验证免密
```
之后本机再跑 `logs.py hop-check` 应返回 `HOP_OK`。

## 4. 路径说明（Windows / Git Bash / WSL）
- `expand()` 会把 `~` 展开为 `$HOME`，并把反斜杠 `\` 统一替换为 `/`。
- 但 `D:\xxx` 这类 Windows 盘符路径在 **Git Bash** 需写成 `/d/xxx`、**WSL** 需写成 `/mnt/d/xxx`；macOS 上则用实际 Unix 路径（如 `~/work/xxx`）。`codehub` 的路径必须是**运行本 skill 的机器上有效的本地路径**。

## 5. 安全建议（重要）
- 本 skill 的脚本全部只读，但跳板机/节点均以 `root` 登录，**root 本身不构成只读边界**。建议为跳板机建一个专用只读账号，或用 `conn.scan_dirs` 把可读范围收窄到业务日志目录。
- 节点日志路径若指向软链接，`root` 下 `tail` 会跟随链接读到目录外文件。脚本已在节点上用 `readlink -f` 解析真实路径后**重新校验**扫描目录集；`node-log --any-path` 是唯一显式越界开关，每次使用都回显告警——安全叙事从「防止」变为「透明可审计」，请在只连开发/测试环境的前提下使用。
- `node-exec` 按场景（node / network / pod）白名单放行只读诊断命令（`df`/`free`/`ss`/`iptables -L`/`dig`/`crictl` 等），白名单外一律在本地拒绝，不产生 SSH 往返；参数亦按命令逐条白名单校验（如 `iptables` 只允许 `-L`/`-S` 列出，`ping` 限 `-c 1-5`）。
- `nsenter-net <node> <pid> <cmd...>`：从节点进入容器（或 pause 容器）网络命名空间执行 network 白名单内的只读命令，用于精简镜像无 shell、业务容器 crash、NetworkPolicy 容器视角验证三类场景。复合门设计：PID 仅接受纯数字，`-t <pid> -n` 构型由脚本拼装（无法注入 `-m` 等其他 ns flag），内部命令复用 network 白名单。注意 `/etc/resolv.conf` 属 mount ns 不会切换，复现容器 DNS 需显式 `dig @<clusterDNS>`。
- `pause-pid <node> [--name POD] [--namespace NS]`：自动探测节点容器运行时（先试 crictl/containerd，失败回退 docker shim）并返回 pause 容器宿主机 PID（`runtime=... pid=...`）。运行时→工具的选择在脚本内以 Python 变量完成——paramiko 每次 `execute_on_node` 都是全新 shell，远端环境变量无法跨调用存活，shell 组合命令又会绕过 allow-list。多个 pause 容器时列出让用户用 `--name`/`--namespace` 收窄；手动回退链（crictl pods → inspectp / docker ps → inspect）见 network.md。
- 私网探测结果会缓存到 `~/.cache/troubleshooting-bastion/`（权限 700）。脚本只信任缓存里等于 `conn.public-ip` / `conn.private-ip` 的 IP，其余一律丢弃并重探，避免同机其他用户伪造缓存把流量引到假跳板机。
- 本 skill **没有 production 确认门（protected）**。请只把它指向开发/测试跳板机；若必须连生产，调用前请自行确认，或在 SKILL.md / lib.py 重新加回 `protected` 确认逻辑。

## 6. 验收
1. `python3 scripts/setup_ssh.py status` 全 OK（或 `scripts/connect.py` → BASTION_OK / KUBECTL_OK）
2. `scripts/connect.py` → BASTION_OK / KUBECTL_OK
3. 两段式走一遍（全量发现 → 精确下钻）：
   - 发现：`scripts/logs.py pods -A -o wide`（或 `pods <component>`）、`scripts/logs.py events --warnings`
   - 下钻：`scripts/logs.py logs <component> --since 15m --grep ERROR --context 3`（默认跨 ns；试 `-n <ns>` 与 `--pod <name>` 收窄）
   - 节点：`scripts/logs.py node-find <node> --name "*.log" --newer 1h` 拿到文件后 `node-log <node> <文件>`；`describe-node <node>`；`node-exec --scenario node <node> df -h`
4. `scripts/resolve_version.py --list`，再解析一个真实组件和版本
5. 回放测试：拿一个已解决的客户问题，只给组件、版本和报错，看能否定位到当时的代码位置
