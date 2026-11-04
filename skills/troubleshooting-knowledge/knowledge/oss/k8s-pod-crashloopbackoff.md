---
schema: 1
domain: k8s
scope: oss
components: [kubelet]
symptoms: [崩溃重启, 反复重启]
error_codes: [CrashLoopBackOff, OOMKilled]
versions: []
aliases: [crash, 崩, 容器退出码, 就绪探针]
status: active
confidence: doc-sourced
evidence:
  - run: Kubernetes v1.31 官方文档（pod-lifecycle / pod termination 语义，访问于 2026-11-04）
    what: "状态机与退出码语义为平台通用事实；OOMKill 的证据位置：describe pod 的 Last State 段 Terminated 下的 Reason 与退出码"
    covers: [正常链路, "逐环节排查 ①②④", 异常归因速查]
updated: 2026-11-04
source: Kubernetes 官方文档（社区通用事实，随 skill 分发）
---

# k8s Pod CrashLoopBackOff 排查参考

## 正常链路（容器生命周期与重启回路）
- **生命周期状态机**：容器进程启动 → Running → 进程退出（退出码标明原因：`0`=正常；`1`=应用自身异常退出；`137`=SIGKILL（常为 OOM 或 liveness 杀）；`143`=SIGTERM）→ kubelet 按 restartPolicy 决定是否重启。
- **重启回路**：连续失败时 kubelet 按指数退避重启（10s→20s→40s…），**退避上限约 5 分钟**（触顶后每约 5 分钟重试一次）；`CrashLoopBackOff` 就是这个回路的对外状态汇总。
- **探针交互（正常时序）**：startupProbe 通过之前不跑 liveness/readiness；liveness 失败 → 杀容器重启；readiness 失败 → 从 Service endpoints 摘流量但不杀容器。
- **正常表现**：pod `Running`、`READY 1/1`、`RESTARTS` 稳定不增长。

## 逐环节排查
- **① 进程退出**：正常 = 稳定运行或正常退出；异常 = 上一次容器非零退出。证据位：`describe pod` 的 **`Last State: Terminated`** 段下 `Reason` 与 `Exit Code`——OOMKill 的权威判定位置是 Last State，Events 只可能有伴随消息，不是判定依据；`logs --previous` 里可见 panic/启动报错栈。
- **② kubelet 重启回路**：正常 = 失败后按退避重启；异常 = `RESTARTS` 持续增长、`STATUS` 停在 `CrashLoopBackOff`。证据位：`get pod` 的 `RESTARTS`/`STATUS` 列。
- **③ 探针交互**：正常 = startup → readiness 依次通过；异常 = liveness 在启动完成前反复失败 → 反复杀容器形成循环（退出码 137 但非 OOM）。证据位：`describe pod` 的探针配置段与 Events 里的 `Unhealthy` 消息。
- **④ 资源约束**：正常 = 内存用量在 limit 内；异常 = 触顶被 cgroup 杀（OOMKilled/137）。证据位：`describe pod` 的 `Limits` 段；节点侧内核 OOM 日志。

## 异常归因速查
| 环节 | 现象 | 归因类别 | 证据位 |
|---|---|---|---|
| ① 进程退出 | Exit Code 137 + `Reason: OOMKilled` | 内存超 `resources.limits.memory` 被 cgroup 杀 | `Last State` 段 + `Limits` 段 |
| ③ 探针交互 | Exit Code 137 无 OOMKilled | liveness 探针失败杀容器 | Events `Unhealthy` + 探针配置 |
| ① 进程退出 | Exit Code 1 | 应用自身异常退出（panic/配置缺失） | `logs --previous` 报错栈 |
| ① 进程退出 | Exit Code 143 | 收到 SIGTERM（正常终止流程或被主动终止） | Events 终止消息 + 终止方身份 |

## 配置语义（客观事实）
- `resources.limits.memory` 决定 cgroup 内存上限；OOMKill 发生在触顶时，不依赖应用自身逻辑。
- livenessProbe 失败 → 杀容器；readinessProbe 失败 → 摘流量但不杀。二者语义不同，不要混为一谈。
