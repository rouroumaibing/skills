# troubleshooting skill 安装与使用

依赖：python3（≥3.8）、git、Python 包 **PyYAML + paramiko**（`pip install pyyaml paramiko`，paramiko 会一并装上 cryptography）。跳板机上的 kubectl **可选**——pod 类命令与按名解析节点才需要；节点通道传 IP / 配 `node_ips`、host 通道走隧道均不需它。

传输与密钥：
- SSH 用 **Paramiko**（纯 Python，不依赖本机 OpenSSH，因此不受 Windows `ControlMaster`/`ControlPath` 报 `getsockname failed: Not a socket` 影响）；密钥用 cryptography 生成，文件传输用 SFTP。
- **节点跳转仍在跳板机上用 `ssh`**：保留共享密钥模型，节点凭据不落在本机；非集群机器的 host 通道另走 direct-tcpip 隧道（见第 5 节）。
- 传输层带 30s keepalive（防长诊断会话被 NAT 空闲断连）、transport 级失败自动重连一次并重试（命令超时不重试，防重复执行）；结果记录耗时。

## 1. 安装
- 依赖：`pip install pyyaml paramiko`（或 `pip3 install pyyaml paramiko`）。若运行脚本报 `需要 PyYAML` / `需要 paramiko`，即为此依赖未装。
- 本仓库：直接放在 `skills/troubleshooting/`（已就位），仓库 README 索引表已登记。
- 其他 Agent / 项目：复制整个 `troubleshooting/` 目录到该 Agent 的 skills 路径，并在项目 `AGENTS.md`（或对应规则文件）加一句：
  `排查问题时，先阅读 <路径>/troubleshooting/SKILL.md 并遵循其中规则。`

## 2. 配置
1. 编辑 `config.yaml`（最小形态如下）：
   - `conn`：`public-ip` / `public-port` / `private-ip`（同一台跳板机的地址）。
     - `public-ip` 与 `private-ip` **至少一个必填**；**配了公网就优先走公网**——配公网即代表本机不能直连私网，纯内网可不配公网或把它填成同一内网地址。
     - `public-port` 是**公网地址的 ssh 端口**，只作用于公网链路（私网固定 22），缺省 22。
   - `codehub`：组件别名 -> 本地仓库路径（查最新代码用）。支持两种写法：
     - 简写：`components-1: ~/work/xxx`
     - map 形式（带别名与可选 selector）：
       ```yaml
       components-2:
         path: ~/work/xxx
         aliases: [订单, order]      # 可用别名匹配组件
         selector: app=order-svc     # 覆盖 pod label，优先级最高；未配置时按通用标签键 app=/k8s-app=/component= 依次尝试，再按 pod 名称子串兜底（平台无关）
       ```
   - `worktree.workdir`：客户报版本时只读 worktree 的工作目录。
   - `troubleshooting-knowledge`（可选）：知识库 skill 目录，指向独立的 `troubleshooting-knowledge` skill 根（跨平台路径，按第 4 节 `expand()` 规则展开）。配置后，troubleshooting 在定位问题时通过 `scripts/knowledge.py` **自动扫描**其 `knowledge/` 取水——不写死文件名；河道只读取水、永不反向写库。不配置则跳过知识库（纯靠框架与运行时取证）。
2. **可选覆盖键**（不写则用默认值，保持 config 极简）：
   - `conn.user`：SSH 用户，默认 `root`。
   - `conn.namespace` / `conn.context`：跳板机 kubectl 的 namespace / context，缺省用跳板机当前。注意：`logs.py` 的查询默认 `-A` 全 namespace 发现，`-n <ns>` 显式收窄优先，业务 pod 不在 default namespace 也**无需**再配 `namespace`。
   - `conn.scan_dirs`：节点日志**扫描目录集**（发现面默认开放、读取面按此收敛），缺省为 `/var/log`、`/var/log/pods`、`/var/log/containers`、`/data/logs`（`/var/lib/kubelet` 含节点 pki 私钥，刻意不进缺省集；确需读取再显式加入）。`node-log` 的读取路径与 `node-find` 的搜索范围都受此集约束；**集外路径确需读取时用 `node-log --any-path` 显式越界**（必回显告警，可审计）。收窄此集即可收紧边界；**敏感日志目录（secure / audit 等）请勿列入**，避免内容进入模型上下文。
   - `conn.node_ips`：节点名 -> IP 静态映射（可选）。kubectl 不可用/未安装时，节点通道靠它把节点名解析为 IP；也可直接在 `<node>` 传 IP 绕过解析。
   - `conn.sshpassword`：连**非集群机器**的共享密码列表（可选），按顺序逐个尝试直到成功。用于 `host-exec` / `host-log` 经跳板机 direct-tcpip 隧道直连任意机器；user 取 `conn.user`（默认 root）、port 默认 22。**只写本地 config.yaml，该文件不入库**（仓库只提交 `config.yaml.example` 模板），建议权限 600。
   - `conn.host_credentials`（高级覆盖，可选）：`host -> {user, password, port}`，为特定机器定制 user/port 或单密码；配置后**优先于**共享 `sshpassword`。多数场景只需 `sshpassword`，无需逐机配置。
   - `conn.connect_timeout` / `conn.request_timeout`：SSH 连接超时、kubectl `--request-timeout`（单位秒），默认 `10` / `10`。`probe_timeout` / `cache_ttl` 为历史兼容键，公网优先后不再使用，可忽略。
   - `remediation.backup_dir`：apply.py 写操作的备份目录（本地，自动创建，权限 700），缺省 `~/.cache/troubleshooting-backup`。
   - `remediation.shared_cluster`：缺省 `true`（fail-safe：按共享/近生产集群处理，T2R 写操作也需 `--confirm` 显式授权；**空值（`shared_cluster:`）与无法识别的值同样按 `true` 处理**）；**独占测试集群必须显式设 `false`** 才放宽为 T2R 先备份后执行。
   - `remediation.write_timeout`：apply.py 写命令/快照的远端执行读超时（秒），缺省 `180`。排查脚本的默认读超时是 `connect_timeout+5`（15s），对 `kubectl delete pod`（优雅等待 30s）这类长写命令会误报失败——写通道单独放大。
   - `remediation.write_whitelist`：可选，按通道（kubectl/node/db）**整体覆盖**内置写命令白名单（结构化声明条目，非正则；kubectl 条目 `{verb, kind, tier[, sub][, flags]}`、node 条目 `{cmd, tier[, subs]}`、db 条目 `{keyword, tier}`，字段语义见 `scripts/apply.py` 文件头 docstring）。kubectl 条目的 `verb` 与实际命令严格比对（登记 `{verb: delete}` 不会放行 patch/expose 等同形状命令）。T3 反模式（控制面 namespace 删除、`--all` 批量删除、`rm -r`、`DROP DATABASE`/GRANT/REVOKE 等）由脚本内置结构判断兜底拒绝，config 覆盖不影响。
3. **跳板机不写密码**，认证靠 SSH 公钥免密，用 `scripts/setup_ssh.py` 一键配置（**幂等，可重复运行**；传输用 Paramiko，无需本机 OpenSSH / `~/.ssh/config`）。**例外**：仅 host 通道连非集群机器时，在 `conn.sshpassword` 写共享密码列表（该文件不入库）：
   - `python3 scripts/setup_ssh.py local`：本机 → 跳板机。生成个人密钥（若无，cryptography 生成 OpenSSH 格式）、把公钥**追加**到跳板机 `authorized_keys`。首次可能需输入一次跳板机密码（仅交互终端）。
   - `python3 scripts/setup_ssh.py bastion`：跳板机 → 节点。在跳板机上确保共用密钥存在，并把其公钥**追加**到各节点（缺省用 `kubectl` 自动发现，或 `--nodes <ip...>` 指定）。
   - `python3 scripts/setup_ssh.py status`：体检当前免密状态（本机密钥 / 到跳板机 / 到节点）。
   - `python3 scripts/setup_ssh.py --help`：查看全部子命令（含 `export-bundle` / `import-bundle`）。

## 3. 免密模型与分发

两条链路、两类密钥，分发策略不同——核心是防止各人各生成一套、互相覆盖机器配置：

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

### 只读边界
- 排查脚本**全部只读**；唯一运行期写入口是 `scripts/apply.py`（kubectl / node / db 三通道）。
- **root 不构成只读边界**：跳板机/节点均以 root 登录。建议为跳板机建专用只读账号，或用 `conn.scan_dirs` 把可读范围收窄到业务日志目录。
- **没有 production 确认门**：只把本 skill 指向开发/测试跳板机；连生产属 T2，调用前请自行确认。

### 写操作：apply.py 三重约束（缺一不可）
1. **写命令白名单** `config.remediation.write_whitelist`：T3 反模式最先评估，任何授权不可覆盖。
2. **授权分层** T1 / T2R / T2U / T3：T2U 需 `--confirm`；T2R 缺省按共享集群处理，同样需 `--confirm`（独占集群设 `remediation.shared_cluster: false` 放宽）；`--confirm-by` 留痕进备份头。
3. **强制先备份，失败则不执行**：
   - kubectl 通道：自动 `get -o yaml`。
   - node / db 通道：强制 `--snapshot` 只读快照，快照目标须与写命令一致——systemctl 写对应同 unit 的 status；文件写对应同路径的 ls/ls -l/cat；db 只认 SELECT/SHOW/DESCRIBE/DESC，快照会话强制只读（psql `default_transaction_read_only=on` / mysql `SESSION TRANSACTION READ ONLY`）。
   - 拒绝真正执行的语句与带副作用函数：`EXPLAIN ANALYZE`，以及 dblink* / pg_terminate / cancel_backend / lo_* / set_config / pg_sleep / advisory lock / COPY 等。

### 复验与回滚
- 复验：`scripts/verify.py`，只允许调只读脚本，逐条输出 PASS/FAIL。
- 回滚：`apply.py rollback <备份>`。kubectl 通道自动；node / db 按快照手工恢复。回滚是**合并语义**——写操作新增的 label/annotation/字段不会被撤销，需完整还原时按备份内容手工 `replace --force`。

### 已知残余风险
- **`apply -f <清单>` 内容不进白名单/T3 逐条校验**：但会解析清单并拒绝敏感 kind（Secret / ServiceAccount / Role / ClusterRole / *Binding / Namespace / ResourceQuota / LimitRange / *WebhookConfiguration / Mutating / ValidatingAdmissionPolicy* / APIService / CustomResourceDefinition / CertificateSigningRequest / PersistentVolume / StorageClass 等），需 `--allow-sensitive-manifest` 二次显式放行；即便放行，使用前仍必须人工过目清单内容。
- **Secret 备份明文落盘**：kubectl 备份是资源原样导出，Secret 会明文落盘（目录 0700、文件 0600），按敏感数据处理。

### 各只读通道的保障
- **软链接**：root 下 `tail` 会跟随软链接读到目录外文件；脚本用 `readlink -f` 解析真实路径后重新校验扫描目录集。`node-log --any-path` 是唯一显式越界开关，每次回显告警（安全叙事从「防止」变为「透明可审计」），仅连开发/测试环境时使用。
- **node-exec**：按场景（node / network / pod）白名单放行只读命令（`df`/`free`/`ss`/`iptables -L`/`dig`/`crictl` 等），白名单外本地拒绝、不产生 SSH 往返；参数逐条校验（`iptables` 只允许 `-L`/`-S`，`ping` 限 `-c 1-5`）。
- **nsenter-net**：PID 仅接受纯数字，`-t <pid> -n` 由脚本拼装，无法注入其他 ns flag；内部命令复用 network 白名单。`/etc/resolv.conf` 属 mount ns 不切换，复现容器 DNS 需显式 `dig @<clusterDNS>`。
- **pause-pid**：运行时→工具的选择在脚本内以 Python 变量完成（paramiko 每次 `execute_on_node` 都是全新 shell，远端环境变量无法跨调用存活，shell 组合命令会绕过 allow-list）；多个 pause 容器用 `--name`/`--namespace` 收窄；手动回退链见 network.md。
- **地址选择**：公网优先。配 `public-ip` 就直接用（不探测私网、不缓存），未配才用 `private-ip`——不存在「私网探测缓存被伪造」的旧风险面。`public-ip` / `private-ip` 是唯一可信来源，Agent 不得自行拼接地址。
- **known_hosts**：读取用户 `~/.ssh/known_hosts`（只读、逐行容错，坏行与 `@cert-authority`/`@revoked` 标记行跳过并告警，其余照常生效——paramiko 原生 load 在首个坏行中止，会静默丢失其后条目）；AutoAdd 新增密钥只落盘到 skill 专用 `~/.cache/troubleshooting/known_hosts`（0600），用户真实文件永不重写（paramiko 重写会丢注释/空行且无法回写 CA 行），解析失败降级为告警不崩溃。CA 行被跳过意味着 `@cert-authority` 信任不生效，依赖 CA 签发主机密钥的环境需另行评估。
- **host 通道** `host-exec` / `host-log`：经跳板机 direct-tcpip 隧道在本机与任意非集群机器建立端到端 SSH，凭据来自 `conn.sshpassword` 共享列表（按顺序逐个尝试），或 `conn.host_credentials.<host>` 的按机器覆盖（明文密码，仅本地 config，不回显、不进日志）；host 名严格校验，命令复用 node-exec 白名单，只读、不接 apply.py 写通道。明文密码靠「不入库 + 文件权限」保密，按敏感数据对待。
- **tcpdump**（network 场景白名单）：必须带 `-c <count>`（上限 200），禁 `-w`/`-C`/`-W` 写盘与 `-Z` 切权限，只输出 stdout；过滤表达式按 bare token 放行，复杂过滤分多次按 host/port 收窄。判读步骤见 `modes/diagnostics/network.md`。

## 6. 验收
1. 连通性：`python3 scripts/setup_ssh.py status` 全 OK；`scripts/connect.py` → `BASTION_OK` +（`KUBECTL_OK` 或 `KUBECTL_UNAVAILABLE`，后者是 kubectl 未装时的正常警告）。
2. 两段式走一遍（全量发现 → 精确下钻）：
   - 发现：`scripts/logs.py pods -A -o wide`（或 `pods <component>`）、`scripts/logs.py events --warnings`
   - 下钻：`scripts/logs.py logs <component> --since 15m --grep ERROR --context 3`（默认跨 ns；试 `-n <ns>` 与 `--pod <name>` 收窄）
   - 节点：`scripts/logs.py node-find <node> --name "*.log" --newer 1h` 拿到文件后 `node-log <node> <文件>`；`describe-node <node>`；`node-exec --scenario node <node> df -h`
3. `scripts/resolve_version.py --list`，再解析一个真实组件和版本
4. 修复-验证闭环冒烟（不产生任何真实写操作）：
   - `python3 scripts/apply.py --dry-run kubectl delete pod <不存在的名字> -n <ns>` → `DRY_RUN`（T2R 计划输出）
   - `python3 scripts/apply.py --dry-run kubectl delete ns kube-system` → `TIER_T3_FORBIDDEN`（本地拒绝）
   - `python3 scripts/apply.py --dry-run node <node> systemctl restart kubelet` → `SNAPSHOT_REQUIRED`
   - `python3 scripts/verify.py --cmd "connect.py" --expect BASTION_OK` → `PASS`
5. 回放测试：拿一个已解决的客户问题，只给组件、版本和报错，看能否定位到当时的代码位置
