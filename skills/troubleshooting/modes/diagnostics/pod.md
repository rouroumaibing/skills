# 场景清单：Pod 故障（地图，非轨道）

> 按证据选路，不按顺序走全程；用户可随时改道。
> **最小充分集**：Agent 自行下结论前必须看完；之外的项按需查。

## 现象 → 证据源映射

| 现象 | 第一证据源 | 第二证据源 | 最小充分集 |
|---|---|---|---|
| CrashLoopBackOff | `logs.py logs <comp> --previous -c <容器>` 退出前最后输出 | `logs.py describe <pod>` 看 Last State / exit code / restart count | previous 日志 + describe 的 Last State |
| Pending（一直没调度） | `logs.py describe <pod>` 看 Events（FailedScheduling 原因） | `logs.py pods -A -o wide` 看节点资源余量 | describe Events + 一次节点容量确认 |
| OOMKilled | `logs.py describe <pod>` 看 Last State OOMKilled 与 limits | `logs.py logs <comp> --previous` 看 OOM 前内存/GC 迹象 | describe 的 limits/OOMKilled + previous 日志 |
| ImagePullBackOff | `logs.py describe <pod>` 看 image 名与 imagePullSecrets | `logs.py events --warnings --for <pod>` 看 Failed 原因 | describe 的 image 字段 + events |
| Evicted | `logs.py describe <pod>` 看 eviction message | 转 node.md 查节点磁盘/内存 | describe 的 eviction message + 节点资源一项 |
| 重启计数大但日志干净 | `logs.py describe <pod>` 看 liveness/readiness 探针配置 | `logs.py node-of <pod>` → `node-journal <node> kubelet` | 探针配置 + restart count 时间线 |

## 通用命令
- `logs.py logs <comp> --since 1h --grep "ERROR|Exception|panic" --context 3`
- `logs.py logs <comp> --previous -c <容器>`（崩溃前实例，需指定 `-c`）
- `logs.py describe <pod>`、`logs.py events --warnings --for <pod>`
- `logs.py pods <comp>`：看 RESTARTS 列与 AGE，对齐事件时间线
- 容器运行时层（节点上，按场景放权）：`logs.py node-exec --scenario pod <node> crictl ps -a`、`crictl logs <container-id> --tail 100`
- 精简镜像无 shell、或业务容器 crash 需要容器网络视角时：`logs.py pause-pid <node> --name <pod> --namespace <ns>` 自动探测 containerd/docker 并返回 pause 容器宿主机 PID，再 `logs.py nsenter-net <node> <pid> <cmd>`（详见 network.md「容器视角」；手动回退链也在该节）

## 最小充分集（Pod 类问题下结论前必看）
1. `logs.py describe <pod>`（Last State / limits / Events）
2. 崩溃现场日志：`logs.py logs <comp> --previous -c <容器>`（Pending 类无日志则跳过）
3. `logs.py events --warnings --for <pod>`，确认事件时间线与 pod 重启时间对得上

## 常见误判提醒
- **节点级问题会伪装成 Pod 异常**（磁盘满、kubelet 抖、runtime 异常）——pod 侧证据不足时转 node.md，别在 pod 上反复打转。
- 重启计数大但日志干净：优先怀疑探针（liveness/readiness）配置错误或依赖服务不可达，而不是应用代码。
- OOMKilled 要分清是容器 limit 太低、应用内存泄漏、还是节点级内存压力（转 node.md 的 free/dmesg）。
