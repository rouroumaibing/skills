# <被测对象与行为> 行为确认报告 / Behavior-Confirmation Report

> 配合 `troubleshooting` skill 的「行为确认型」任务使用（用户问"某行为在运行时会不会发生 / 是否会被… / 确认…"）。
> 填写约定：
> - **标题须自描述**：把 `<被测对象与行为>` 换成实际内容（如"Volcano Job ttlSecondsAfterFinished 自动清理 行为确认"），不要只留类型标签——标题即检索键。
> - 这是**实验报告**，不是根因报告：目标是用运行时证据证伪/证实一个行为，而非定位故障。
> - **结论必须先于过程（结论先行），且只放 verdict**：`结论先行` 只写"是否成立 + 一句话"；所有分析、证据、caveat 移入对应板块，不要在结论里堆砌。区分 **【已证实】**（日志原文 / `get` 结果 / 代码）与 **【推测】**（环境不可达、仅靠代码推断）。
> - **代码级确认是可选并行**：组件源码可达（codehub/upstream）即分析佐证（有更好），不可达则跳过（没有也没关系），不是环境不可达时的 fallback，不阻塞主流程。
> - **对照组必填**：用一个"不设关键变量的同构 Job"证明行为由该变量驱动，而非其他 GC 机制（queue / namespace GC 等）。
> - **残留检查必填**：清理后必须 `get` 关联对象（如 vcjob / PodGroup / Pod），证明无孤儿资源。
> - **授权与破坏性预分析**（详见 SKILL.md 安全红线分层，通用覆盖 kubectl/SSH/DB 所有写通道）：创建自清理测试/对照资源在独占测试集群属 T1（默认放行）；若实验会修改/删除既有对象，先按 T1/T2R/T2U 做预分析——T2R 强制先备份后执行（不需单独授权），T2U 需显式授权 + 先备份后执行；拿不准→T2U。环境访问只通过 `scripts/`；实验中的写操作统一经 `scripts/apply.py`（白名单 + 分层 + 强制备份），复验用 `scripts/verify.py`，不过按备份回滚。

---

## 结论先行 / Conclusion First

- **是否成立**：<【已证实】该行为在运行时确实发生 / 未发生 / 【推测】环境不可达，仅代码层确认>
- **一句话**：<……>（此处只给判定，不展开证据与条件；细节见下文）

---

## 过程（时间线）/ Timeline

| 时间 (时区 +08:00) | 事件 | 证据 / 命令 |
|---|---|---|
| HH:mm:ss | Job 创建（ttlSecondsAfterFinished=N） | `kubectl apply -f -` / `scripts/logs.py ...` |
| HH:mm:ss | Job 进入 Completed / Failed 终态 | `get vcjob <ns>/<name>` |
| HH:mm:ss | 等满 TTL + margin | — |
| HH:mm:ss | `get vcjob` 返回 NotFound（已被自动清理） | `get vcjob,pg,pod -n <ns>` |

---

## 环境 / Environment

- **集群 / 环境**：<dev / test 跳板机 / 集群ID>
- **组件部署位置**：<如 volcano 部署在 `kube-system`（非 `volcano-system`）>
- **版本**：<镜像 tag，如 `volcano-controllers:1.23.9`；或组件 @ `<tag>`>
- **关键配置**：<queue 名称、schedulerName、minAvailable 等影响行为的字段>

---

## 授权分层与破坏性预分析 / Auth Tiers & Destructive Pre-Analysis

> 每次写操作前必做预分析并声明层级（详见 SKILL.md 安全红线）。简表：

| 层级 | 判定 | 授权 | 备份前置 |
|---|---|---|---|
| T1 非破坏性 | 不修改既有状态（自清理测试/对照资源、get/logs、代码检索、本地脚本） | 默认放行 | 不需要 |
| T2R 破坏性·可恢复 | 改既有但可还原（删测试pod、patch label、scale 0↔N、restart） | 不需单独授权 | 强制先备份后执行 |
| T2U 破坏性·不可恢复 | 删数据(PVC/PV)、集群级(RBAC/CRD/NS)、外部副作用 | 需显式授权 | 强制先备份后执行 |
| UNCERTAIN | 可恢复性拿不准 | 默认升格 T2U | 同上 |

- 备份前置：T2R/T2U 强制；可恢复=真备份（`get -o yaml` 存盘），不可恢复=导出当前态供审计/取证（非可还原）。
- T2R 的"不需授权"仅在**独占测试/开发集群**成立；共享/近生产集群升为需授权。
- 本实验的测试/对照 Job 若自带短 ttl / teardown 且不改既有对象 → T1，默认放行。

---

## 测试 Job / Test Job

- **manifest 摘要**（仅列与本次验证相关的字段）：
  - apiVersion / kind：<batch.volcano.sh/v1alpha1, Job>
  - 关键字段：<ttlSecondsAfterFinished: N、replicas、restartPolicy、command、queue>
  - namespace：<default>
- **授权层级**：<T1 默认放行 / T2R 先备份后执行 / T2U 已获授权>
- **完整 manifest 见附录或 `--dry-run` 演示**。

---

## 关键证据 / Evidence

### 1. 控制器日志（证明清理动作）
- 证据位置：<组件 `<controller-pod>` 的日志，时间段 `<since>`>
- 原文摘录：
  ```
  <关键日志行；注意：gc-controller 的 "Cleaning up Job" 多在 klog V(4)，默认日志级别不打印，
   若需此证据须提高控制器日志级别；否则"对象被删除"这一事实本身即为铁证>
  ```

### 2. 清理后残留检查（证明无孤儿）
- 命令：`<get vcjob,pg,pod -n <ns> --no-headers>`
- 结果：<NotFound / No resources found> → **Job、PodGroup、Pod 全部清空（前台级联删除）**

### 3. 对照组（隔离因果：证明行为由目标变量驱动）
- **对照组 Job**：<同构 Job，但不设 ttlSecondsAfterFinished（或设为对照值）>
- 观察：<对照组在相同时长后**未被清理**> → 证明清理由 `ttlSecondsAfterFinished` 驱动，而非 queue / namespace 级 GC 或其他机制。
- 对照组授权层级同上；验证完一并回收。

---

## 补充说明 / Notes

- **计时起点**：<如 Status.State.LastTransitionTime（进入终态时刻）；注意时钟 skew 可能导致延迟清理>
- **删除方式**：<前台级联删除（DeletePropagationForeground），连带清 pods>
- **代码佐证**：<若有对应版本源码，给出关键函数/行号佐证；无则说明"源码不可达，未做代码佐证">
- **环境不可达时的降级**：<若 env-debug 连不上，改走 code-search 用对应版本代码证伪，并在结论标【推测】>

---

## 结论 / Conclusion

- <重述是否成立、关键证据链、适用边界>

---

## 复核清单 / Checklist

- [ ] 标题自描述（点明被测对象与行为）
- [ ] 结论先行，且只放 verdict（分析/证据已移入对应板块）
- [ ] 时间线完整（创建 → 终态 → 等 TTL → NotFound）
- [ ] 环境指纹齐全（部署 ns、版本 tag、关键配置）
- [ ] 代码佐证：可达则已分析，不可达则说明已跳过（可选并行）
- [ ] 写操作前已做破坏性预分析并声明层级（拿不准→T2U）
- [ ] 破坏性操作已先备份后执行
- [ ] 对照组已建且证明因果（非其他 GC 驱动）
- [ ] 残留检查已做（关联对象全清空，无孤儿）
- [ ] 环境不可达时已降级为代码证伪并标注【推测】
