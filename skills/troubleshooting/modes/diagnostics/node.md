# 场景清单：节点故障（地图，非轨道）

> 按证据选路，不按顺序走全程；用户可随时改道。
> **最小充分集**：Agent 自行下结论前必须看完；之外的项按需查。

## 现象 → 证据源映射

| 现象 | 第一证据源 | 第二证据源 | 最小充分集 |
|---|---|---|---|
| 节点 NotReady / kubelet 异常 | `logs.py node-journal <node> kubelet --since 1h` | `logs.py describe-node <node>` 看 Conditions | kubelet journal + node Conditions |
| 磁盘压力（DiskPressure / Evicted） | `logs.py node-exec --scenario node <node> df -h` | `logs.py node-find <node> --newer 1h --name "*.log"` 找暴涨文件 | df + describe-node 的 DiskPressure |
| 内存压力（OOM / 系统卡顿） | `logs.py node-exec --scenario node <node> free -m` | `logs.py node-exec --scenario node <node> dmesg -T` 看 OOM killer 记录 | free + dmesg（或 describe-node MemoryPressure） |
| PID 耗尽（fork 失败） | `logs.py node-exec --scenario node <node> ps aux --sort=-%cpu` | `logs.py node-exec --scenario node <node> pidstat` | ps + kubelet journal |
| CPU 持续打满 | `logs.py node-exec --scenario node <node> top -bn1` | `logs.py node-exec --scenario node <node> vmstat 1 3` | top/vmstat 之一 + 时间线 |
| 容器运行时异常 | `logs.py node-journal <node> containerd`（或 docker） | `logs.py node-exec --scenario pod <node> crictl ps -a` | runtime journal + crictl ps |

## 通用命令
- `logs.py node-journal <node> <unit>`：kubelet / containerd / docker / kube-proxy 等 systemd 单元，`--grep` + `--context 3` 过滤
- `logs.py describe-node <node>`：Conditions、Allocatable、Taints、Events
- `logs.py node-find <node> --name "*.log" --newer 2h`：节点日志文件索引（扫描目录集内）
- `logs.py node-exec --scenario node <node> <command>`：白名单只读命令（df/free/vmstat/uptime/top/dmesg/pidstat/ps）
- `logs.py hop-check <node>`：先确认跳板机到节点免密可用

## 最小充分集（节点类问题下结论前必看）
1. `logs.py describe-node <node>`（Conditions + Events）
2. `logs.py node-journal <node> kubelet --since 1h`（或对应故障单元的 journal）
3. 资源面对应一项：`df -h` / `free -m` / `top -bn1`（按现象选）

## 常见误判提醒
- **告警节点常常是受害现场而非根因所在**：A 服务打挂节点后，节点上其他服务的报错是次生的。先看时间线（谁先异常），再定根因归属。
- 磁盘满不要只报「满了」：用 `node-find --newer 1h` + `df -h` 定位**是谁在写**（容器日志暴涨最常见）。
- NotReady 先分是 kubelet 自身挂了，还是 CNI/容器运行时依赖挂了——journal 里两者的报错位置不同。
