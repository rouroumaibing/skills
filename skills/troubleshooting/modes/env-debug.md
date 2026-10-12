# 场景①：连跳板机查问题（可主动取证）

## 总流程：问题驱动两段式

排障一定始于一个具体问题（报错/告警/现象），问题自带锚点（关键字/组件/节点/容器）。
**不要无前提全量拉取，也不要把收敛成本推给用户反复追问。**

```
线索抽取 → 第一段·全量发现（无边界）→ 第二段·精确下钻（有边界）→ 证据→新线索→迭代
```

### 0. 线索抽取（显式回显）
从问题描述抽出：关键字 / 组件 / 节点 / 容器 / 大致时间。
**先把自己所用的线索原样打出来再查**——抽错可被用户一眼纠正，防止静默跑偏。
线索确实为空时，不要问用户，直接跑最便宜的广谱发现：`events --warnings` + `pods`（专找非 Running / 重启次数大的）。

### 1. 第一段·全量发现（无 ns / 目录边界，目标「不漏」）
1. `scripts/connect.py`：必须看到 BASTION_OK 和 KUBECTL_OK 再继续。
2. `scripts/logs.py pods [component]`：默认 `-A` 全部 namespace，`-o wide` 看状态/重启次数/所在节点；`-n <ns>` 可收窄。
3. `scripts/logs.py events --warnings`：默认 `-A`，按时间排序；`--for <pod>` 聚焦单个对象；`--max N` 控制条数。
4. 节点侧先建索引再读内容：`scripts/logs.py node-find <node> --name "*.log" --newer 2h`。
   扫描目录集默认 `/var/log`、`/var/log/pods`、`/var/log/containers`、`/data/logs`、`/var/lib/kubelet`（`conn.scan_dirs` 可覆盖），只列普通文件、不跟随软链接。

### 2. 第二段·精确下钻（按线索收敛，目标「不炸」）
- `scripts/logs.py logs <component> --since 30m --grep "ERROR" --context 3`：
  grep -C 风格输出（命中行带本地行号、上下文缩进、组间 `--`）；命中太多用 `--max-matches 10` 收敛。
  **默认跨 namespace 解析 pod**（修掉业务 pod 不在 default ns 时的 NO_PODS）；`-n <ns>` 收窄，`--pod <name>` 指定单个 pod。
- 多容器 pod：`-c <容器>` 只看某容器，或 `--all-containers` 全看；`--previous` 需配 `-c`（kubectl 限制）。
- 节点文件：`node-of <pod>` 找节点 → `hop-check` → `node-log <node> <文件>`。
  路径在扫描目录集外、且问题线索确证需要时，用 `--any-path` 显式越界（会回显告警，可审计）。
- `describe <pod>` / `describe-node <node>` / `node-journal <node> <unit>`。
- 节点只读诊断命令（按场景放权，白名单外一律拒绝）：
  `node-exec <node> <command...> --scenario node|network|pod`，如
  `--scenario node n1 df -h`、`--scenario network n1 ss -tulnp`、`--scenario pod n1 crictl ps -a`。
  注意 `--scenario` 放在 `<node>` 之前。
- 容器网络视角（精简镜像无 shell / 业务容器 crash / NetworkPolicy 容器侧验证）：
  `pause-pid <node> --name <pod>` 自动探测 containerd/docker 返回 pause 容器 PID
  → `nsenter-net <node> <pid> <network 白名单命令>`；详见 modes/diagnostics/network.md「容器视角」。

### 3. 场景深挖（发现的出口）
证据落入某类场景时，读对应清单**按证据选路**（清单是地图，不是轨道）：
- `modes/diagnostics/pod.md` — CrashLoopBackOff / Pending / OOMKilled / ImagePullBackOff / Evicted
- `modes/diagnostics/node.md` — kubelet / journald / 磁盘 / 内存 / PID
- `modes/diagnostics/network.md` — Pod 内 → Pod 间 → Service → DNS → 出口

### 4. 迭代
每次取证后用一两句话说明发现了什么；新证据产生新线索（新组件/新节点/新关键字）时，回到第 1 段。

## 人机协作
- 简单问题 Agent 自动跑完给结论；复杂问题用户可随时插话改道，**Agent 不得以流程没走完为由拒绝转向**。
- Agent 自行下结论前，必须走完对应场景清单的「最小充分集」。

## 约束
- 本 skill 只连一个跳板机，无需切换环境。
- 日志输出上限 300 行；命中太多用 `--max-matches` / 缩小 `--since` / 细化 `--grep`，不放宽上限。
- 日志里出现类名/栈信息时，转 modes/code-search.md 定位代码。
