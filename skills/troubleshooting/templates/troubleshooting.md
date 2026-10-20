# 问题排查报告 / Troubleshooting Report

> 本模板适用于**根因 / 故障类**任务（定位为什么出错）。若任务是**行为确认型实验**（确认某行为在运行时是否发生），请改用 `templates/behavior-confirmation.md`。
> 配合 `troubleshooting` skill 使用。填写约定：
> - 结论必须区分 **【已证实】**（日志原文 / 对应版本代码）与 **【推测】**（依赖环境或配置，待证实）。
> - 场景③（客户问题）**严禁连接任何环境**；所有环境访问只通过 `scripts/`。
> - 证据标注来源位置：日志取自哪个 pod/节点/时间；代码取自哪个组件 / tag / 文件:行号。
> - 实际执行了写操作时，必须填写「修复执行记录」（只经 `scripts/apply.py`），复验结果回填「验证步骤」表。

---

## 问题描述 / Problem Description

- **集群ID / 名称**：<cluster-xxx / 生产集群A>
- **问题分类**：<【集群生命周期 / 节点 / Pod / 网络 / 存储 / 监控 / K8s组件 / 插件 / 安装升级 / 其他】>
- **组件 (component)**：<config.codehub 中的组件 key 或别名>
- **版本 (version)**：<v0.0.1 / 0.0.1；带后缀 -rc1 / -hotfix 视为独立版本>
- **场景**：<① 连跳板机排查 / ② 多仓库查代码 / ③ 客户问题(被动推理)>
- **错误码 / 当前状态**：<如 NO_PODS / CrashLoopBackOff / ImagePullBackOff / NEEDS_MANUAL_AUTH / ...>
- **发生时间**：<YYYY-MM-DD HH:mm> （时区：<+08:00>）
- **影响范围 / 优先级**：<受影响用户、模块或 SLA；P0~P3>

---

## 现象 / Symptom

- **报错原文（verbatim，不要改写）**：
  ```
  <粘贴原始报错、栈信息、日志片段>
  ```
- **复现步骤**：
  1. <步骤一>
  2. <步骤二>
- **预期行为**：<应该发生什么>
- **实际行为**：<实际发生了什么>

---

## 根因分析 / Root Cause

> 一句话结论先行，再展开依据。

- **根因一句话**：<……>
- **触发条件**：<什么输入 / 状态 / 时序会触发>
- **调用 / 数据流**：<组件 `文件:行号`，还原触发路径>
- **是否已在后续版本修复**：<有 `git diff <tagA> <tagB>` 对比依据才写；否则填"未核实">

---

## 关键证据 / Evidence（必须分级）

### 【已证实】Proven
> 来自日志原文或对应版本代码，附证据位置。

- **<结论点一>**
  - 证据位置：<pod `<pod>` / 节点 `<node>` / 时间段 `<since>` 的日志原文> 或 <组件 `<component>` @ `<tag>` 的 `<文件:行号>`>
  - 原文摘录：
    ```
    <关键日志行 / 代码段>
    ```

### 【推测】Suspected
> 依赖客户环境或配置、尚未坐实的部分。**说明还缺什么信息才能证实。**

- **<推测点一>**
  - 依据：<为什么这么猜>
  - 待证实：<需要哪份日志 / 哪个配置 / 哪个环境复现才能确认>

---

## 修复建议 / Remediation

1. 【建议一】<临时规避 (workaround) / 永久修复 (fix)>
2. 【建议二】<……>
3. 【建议三】<涉及代码改动时，注明改动文件与验证方式>

---

## 修复执行记录 / Remediation Execution Record

> 只有实际执行了写操作才填写（纯建议不填）。写操作只经 `scripts/apply.py`（白名单 + T1/T2R/T2U/T3 授权分层 + 强制先备份，见 SKILL.md 安全红线）；复验用 `scripts/verify.py`，闭环流程见 `modes/env-debug.md` 第三段。

| # | 时间 | 通道 | 命令（apply.py 原样） | 层级 | 授权人/方式 | 备份文件 | 执行结果 | 复验结果 | 回滚 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | <HH:mm> | kubectl | `apply.py kubectl delete pod <pod> -n <ns>` | T2R | 独占测试集群/免授权 | `<backup_dir>/<ts>-kubectl-*.yaml` | `WRITE_APPLIED` | `PASS`（verify.py） | 不需要 |
| 2 | <HH:mm> | db | `apply.py --confirm --confirm-by <授权人> db --snapshot "SELECT ..." <pod> "UPDATE ..."` | T2U | <用户显式确认> | `<backup_dir>/<ts>-db-*.snap` | `WRITE_APPLIED` | `FAIL→ROLLBACK_APPLIED→PASS` | `apply.py rollback <备份>` |

- 备份目录：`config.remediation.backup_dir`（缺省 `~/.cache/troubleshooting-backup`，权限 700）。
- node / db 通道无自动回滚：按备份内的 `--snapshot` 快照手工恢复，并在此记录恢复过程。
- 拿不准层级 → 按 T2U 处理（先要授权再执行）；T3（控制面 namespace 删除、批量 delete、`DROP DATABASE` 等）任何授权不可覆盖。

---

## 验证步骤 / Verification（可执行验证表）

> 每条都可直接粘贴执行；批量断言用 `python3 scripts/verify.py --file checks.yaml`，逐条输出 PASS/FAIL（全部 PASS 才收尾）。
> 「结果」列由执行人回填：`PASS` / `FAIL`（FAIL 时按 env-debug.md 第三段回滚后重验，把回滚动作记入「修复执行记录」）。

| # | 验证命令（可粘贴执行） | 期望（PASS 条件） | 结果 |
|---|---|---|---|
| 1 | `python3 scripts/logs.py logs <component> --since 15m --grep "ERROR"` | 无新增 ERROR（或命中数为 0） | <PASS/FAIL> |
| 2 | `python3 scripts/connect.py` | `BASTION_OK` + `KUBECTL_OK` | <PASS/FAIL> |
| 3 | `python3 scripts/verify.py --file <checks.yaml>` | `status=VERIFY_PASS` | <PASS/FAIL> |
| 4 | <回归验证 / 相邻版本 diff 验证，如 `git diff <tagA> <tagB> -- <file>`> | <……> | <PASS/FAIL> |

---

## 预防措施 / Prevention

> 避免同类问题复发。

- <监控 / 告警：补充哪类指标或日志告警>
- <代码防护：哪处应加校验 / 兜底 / 重试>
- <流程改进：发布 / 配置 / 巡检规范>
- <建议向客户 / 团队索取的信息，具体到文件、命令或日志片段>：<……>

---

## 附录：脚本状态码 / Status Codes（可选留痕）

> 用到 `scripts/` 时记录关键状态码，便于复盘与协作。

| 状态码 | 出现环节 | 说明 / 处理 |
|---|---|---|
| <NEEDS_MANUAL_AUTH / KUBECTL_FAIL / UNKNOWN_COMPONENT / NO_PODS / TOO_MANY_PODS / HOP_FAIL / NOT_FOUND / AMBIGUOUS_TAG / NO_LOCAL_REPO> | <connect / logs / resolve_version> | <提示手动处理 / 已确认组件等> |

---

## 复核清单 / Checklist

- [ ] 场景判断正确（场景③未尝试连接任何环境）
- [ ] 结论区分了【已证实】与【推测】
- [ ] 每条证据都标了来源位置（pod/节点/时间 或 组件/tag/文件:行号）
- [ ] NOT_FOUND 时未用 nearby 相近版本自行替换，已请用户确认
- [ ] 涉及特定版本时，代码取自对应 worktree，而非本地当前分支
- [ ] 缺失信息已明确列出，未编造看似合理的根因
- [ ] 写操作均经 `scripts/apply.py`，且执行前已有备份文件落盘（kubectl 自动 / node/db 显式 --snapshot）
- [ ] T2U 写操作有显式授权记录（授权人/方式写入备份头与修复执行记录）；T3 未被尝试
- [ ] 复验结果 PASS（verify.py）；FAIL 已回滚并重验，或已停下汇报未反复试错
