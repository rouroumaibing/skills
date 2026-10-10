# 场景清单：容器网络（地图，非轨道）

> 按证据选路，不按顺序走全程；用户可随时改道。
> **最小充分集**：Agent 自行下结论前必须看完；之外的项按需查。

## 五层分层法（从下往上，逐层排除）

Pod 内 → Pod 间 → Service → DNS → 出口。
多数"网络不通"其实是 DNS 或 NetworkPolicy，不要一上来就查 CNI。

## 现象 → 证据源映射

| 层 | 验证手段（均在节点上执行） | 通过标准 |
|---|---|---|
| Pod 内 | `logs.py node-exec --scenario network <node> ip -br addr` | 节点网卡/地址正常，CNI 网桥存在 |
| Pod 间 | `logs.py node-exec --scenario network <node> ping -c 2 <podIP>` | 能 ping 通对端 Pod IP |
| Service | `logs.py node-exec --scenario network <node> ss -tulnp`；`iptables -t nat -L -n` | NodePort/kube-proxy 监听在，KUBE-SVC/ENDPOINTS 链有规则 |
| DNS | `logs.py node-exec --scenario network <node> dig <svc>.<ns>.svc.cluster.local`；`nslookup <svc>` | 能解析出 ClusterIP |
| 出口 | `logs.py node-exec --scenario network <node> ping -c 2 <外部IP>`；`traceroute <外部IP>` | 节点出网正常 |

配合集群侧：`logs.py pods -A -o wide` 确认目标 pod 真实状态与所在节点；`logs.py node-of <pod>` 定位节点。

## 容器视角（nsenter-net：进容器网络命名空间）

节点侧命令看到的是**节点命名空间**；以下情况需要真正的容器视角：

- 精简镜像（distroless / busybox）没有 shell 和工具，`kubectl exec` 不可用；
- 业务容器 crash 但 pod 未重建——netns 由 pause 容器持有，依然可进；
- 验证 NetworkPolicy（策略按容器源 IP 评估，节点侧测试走的是不同路径）。

取证路径（两步，均在节点上；运行时自动探测）：

```bash
# 第 1 步：自动探测 containerd/docker，返回 pause 容器宿主机 PID
logs.py pause-pid <node> --name <pod> --namespace <ns>
#   → runtime=containerd pid=12345（或 runtime=docker pid=6789）

# 第 2 步：进容器网络命名空间执行 network 白名单内只读命令
logs.py nsenter-net <node> 12345 dig @10.96.0.10 <svc>.<ns>.svc.cluster.local
```

多节点节点上 pause 容器多于一个时 `pause-pid` 会列出全部 sandbox 并提示用
`--name`/`--namespace` 收窄；两个 runtime 都探测失败时报 `NO_PAUSE_CONTAINER`。

手动回退链（pause-pid 不可用或需要人工核对时）：

```bash
# containerd / CRI-O
logs.py node-exec --scenario pod <node> crictl pods
logs.py node-exec --scenario pod <node> crictl inspectp <sandbox-id>   # info.pid

# docker shim（k8s <= 1.23）
logs.py node-exec --scenario pod <node> docker ps -a --filter name=k8s_Pause
logs.py node-exec --scenario pod <node> docker inspect <容器id或名> --format '{{.State.Pid}}'
```

`nsenter-net` 是复合门：PID 仅接受纯数字，内部命令仍受 network 白名单约束，
`-t <pid> -n` 构型由脚本拼装，无法注入 `-m`（挂载 ns）等其他 flag。

**DNS 陷阱**：netns 不改变 `/etc/resolv.conf` 的读取——它属于 mount namespace，
`nsenter-net <node> <pid> dig xxx` 读到的是**宿主机**的 resolv.conf。复现容器 DNS 视图必须：
1. 显式 `dig @<clusterDNS>`（clusterDNS 见 kubelet 配置，通常为 10.96.0.10）；
2. ndots:5 语义手动模拟：先试 `<svc>.<ns>.svc.cluster.local` 全名，再试容器搜索域补全的短名。

## 通用命令（node-exec，--scenario network）
- `ss -tulnp`：谁在监听哪些端口（Service/NodePort/kube-proxy）
- `iptables -t nat -L -n` / `iptables -L -n`：KUBE-SVC / KUBE-FIREWALL 等链是否存在、规则是否为空
- `conntrack -L`：连接跟踪表（NAT 回包路径排查）
- `dig` / `nslookup`：DNS 解析（**优先查这层**）
- `ping -c 2 <目标>`：连通性（`-c` 上限 5，`-w` 上限 9 秒）
- `traceroute <目标>`：出口路径
- `ip -br addr` / `ip route show`：节点侧地址与路由

## 最小充分集（网络类问题下结论前必看）
1. 分层定位：从 DNS 开始逐层向上（或按现象直接切入对应层），**记录断在哪一层**；
2. 断点层的一条直接证据：如 DNS 层的 `dig` 失败原文、Service 层的 `ss`/`iptables` 输出、Pod 间层的 ping 结果；
3. `logs.py pods -A -o wide` 确认涉及 pod 都在 Running（排除 pod 本身异常伪装成网络问题）。

## 常见误判提醒
- 「服务不通」先查 DNS 再查路由：CoreDNS 异常或 NetworkPolicy 拦截的表现和 CNI 故障极易混淆。
- Service 不通时分清是 **endpoints 为空**（selector 没匹配上 pod，回到 pods 层）还是 **kube-proxy/iptables 规则缺失**（节点层）——两者的修复完全不同。
- 节点上 ping 不通 Pod IP 时，先确认目标 pod 与本节点不在错误网络命名空间/网桥，再怀疑 CNI。
