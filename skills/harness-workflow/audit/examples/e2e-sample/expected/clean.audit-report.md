# 审计报告：C-001 上传接口超时处理（clean-project）

> 端到端样例的**审计报告样稿（通过版）**：证据来自 `run_e2e.py clean` 的真实输出
> （原始输出见同目录 `clean.evidence.md`）。重点看"通过"该怎么写：只陈述有证据的部分，
> 并把没评估的部分如实列出，不写"门禁全绿"。
> 归档位置示例：`audits/2026-10-19-C-001-upload-timeout.md`。

| 项 | 内容 |
| --- | --- |
| 审计对象 | 契约 C-001@v1 + 提交 `c0ffee1` 的 diff + CI 报告 |
| 运行模式 | 有仓库、无 CI 接线（跑 `audit/scripts/` 检查器，CI 红绿与 lint 未接） |
| 检测方式 | 工程实装（本包检查器）+ 人工归类 |
| 验证独立性 | **非独立验证**（同一会话内完成）；契约风险等级 low、推算下限 low，不要求独立验证 |
| 采纳边界 | `required_rules`：R-GOAL-001、R-GOAL-002、R-IMP-001、R-RISK-001、R-VER-006 |

## 1. 结论

**采纳范围内的 5 条规则，检查器均通过，未发现缺口。**
这不等于"全部通过"：另有 2 个检查器未接线、5 条软规则未做人工核对（见第 3 节），
这些部分没有证据，不能写成已验证。

## 2. 证据（采纳范围内）

| 规则 | 检查器 | 退出码 | 证据行 |
| --- | --- | --- | --- |
| R-GOAL-001 | check_contract `--check-words` | 0 | `check_contract: PASS.` |
| R-GOAL-002 | check_contract `--check-hash` / `--check-locked` | 0 | `check_contract: PASS.`（状态 implementation，哈希与规范化内容一致） |
| R-IMP-001 | pre-receive-scope `--check-forbidden` | 0 | `pre-receive-scope: PASS.`（3 个变更全在契约范围或 AI 可写区内，未触禁止项） |
| R-RISK-001 | risk_floor_check（声明 low） | 0 | `risk_floor_check: PASS (no risk_pattern hit; floor=low).` |
| R-VER-006 | ci_gate_check `--commit c0ffee1` | 0 | `ci_gate_check: PASS (2 required step(s), 2 metric threshold(s), commit=c0ffee1).` |

## 3. 未评估项（如实声明）

- **tdd_order_check（R-IMP-002）、lint（R-IMP-005）**：退出码 42，项目未接线，两条规则均未采纳。
- **5 条软规则**：只能人工核对，本轮未做。
- **采纳范围外的检查器**（顺带跑了，仅供参考）：`static_risk_check`、`check-ai-writable`、
  `rule_map_check`、`doc_consistency_check` 均通过，对应规则未采纳，不计入合规结论。

## 4. 修改建议（可选）

```
【缺口】两个检查器未接线，红绿顺序与代码风格没有机器证据
【证据】tdd_order_check.py：requires --redgreen-log/--impl-commits/--criterion-tests … Exit 42 = not implemented.
        lint：not wired. Place governance/lint.env … Exit 42 = not implemented.
【建议改动】在 CI 里产出红绿日志与 lint 配置后，再决定是否把 R-IMP-002、R-IMP-005 加进采纳清单与映射台账
【对抗推演】无同型案例；自行推演：只接线不采纳，等于有证据但不拦合入
【建议可信度】纯推测
【由谁决策】人（契约负责人 alice）
【严重度】低
```

## 5. 升级与晋升

无升级触发；无新型绕过需要晋升。
