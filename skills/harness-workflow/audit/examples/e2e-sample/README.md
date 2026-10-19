# 端到端样例：把检查器串成一次完整审计

这个样例回答一个问题：**审计循环里的检查器、采纳边界、审计报告，合在一起是什么样子？**
它是两个迷你项目加一个运行器，既能当文档读，也能当集成测试跑。

```
python3 run_e2e.py                       # 两个场景全跑，逐项断言退出码（全符合预期则退出 0）
python3 run_e2e.py dirty --evidence out  # 只跑 dirty，并把每个检查器的原始输出写成证据附录
```

依赖：python3 与 pyyaml。运行器会把项目拷到临时目录再审，样例文件本身不会被改动。

## 目录

| 路径 | 作用 |
| --- | --- |
| `clean-project/` | 一切正常的项目：采纳 5 条规则，检查器全部通过 |
| `dirty-project/` | 埋了缺陷的项目：采纳 4 条规则，每条都有发现，另有 7 项落在未采纳规则上 |
| `run_e2e.py` | 按审计循环第 3 步「跑检查」依次发起 11 次检查器调用，断言退出码 |
| `expected/*.evidence.md` | 运行器产出的证据附录：每个检查器的命令、退出码、原始输出 |
| `expected/*.audit-report.md` | 审计报告样稿：证据层 → 报告层的归类与「修改建议」写法 |

每个项目的结构一样：

```
<project>/
  governance/        redlines.yaml（机器判据 + 采纳清单）、rule-map.yaml（映射台账）、roles.yaml
  contracts/         C-001.md（契约，front matter 里有 scope / forbidden_items / contract_hash）
  .workflow/         state.json（契约状态）
  audit-input/       changes.json、change.diff、ci-report.json（被审提交 c0ffee1 的输入）
  ci/                台账里 script 指针指向的 CI 入口占位脚本
  src/ tests/ docs/ ai-reports/   被审产物
```

## 审计循环怎么对应到这里

| 循环步骤（见 SKILL.md） | 样例里的对应物 |
| --- | --- |
| 1 取输入 | `contracts/C-001.md` + `audit-input/*` + `governance/redlines.yaml` 的采纳清单 |
| 2 定风险 | `risk_floor_check`：dirty 里推算下限 high，契约却声明 low，触发 E-17 |
| 3 跑检查 | `run_e2e.py` 的 11 行；退出码 0 / 1 / 42 |
| 4 出报告 | `expected/*.audit-report.md`：每条缺口按「修改建议」模板写，附对抗推演和可信度 |
| 5 该停就停 | dirty 报告里的 E-17 升级，以及"非独立验证 + 高风险 → 转人" |
| 6 归档与回流 | 报告头部给出 `audits/YYYY-MM-DD-<对象>.md` 的归档位置；无新型绕过则不晋升 |

## dirty 项目埋了什么

"是否采纳"决定一条发现是**缺口**（计入合规结论）还是**修改建议**（仅供参考）。
dirty 采纳的是 R-GOAL-001、R-IMP-001、R-RISK-001、R-VER-006。

| 缺陷 | 检查器 | 退出码 | 规则 | 是否采纳 | 报告归类 |
| --- | --- | --- | --- | --- | --- |
| criteria 含「适当」 | check_contract | 1 | R-GOAL-001 | 是 | 缺口 G1 |
| 锁定后范围表被改，哈希对不上；state 仍是 draft | check_contract | 1 | R-GOAL-002 | 否 | 建议 A1 |
| 改了范围外的 `src/auth`、`config/prod.env` | pre-receive-scope | 1 | R-IMP-001 | 是 | 缺口 G2 |
| 触碰禁止项 `config/prod.env` | pre-receive-scope | 1 | R-SEC-003 | 否 | 建议 A2 |
| 改 auth 目录并新增 `DROP TABLE`，契约却声明 low | risk_floor_check | 1 | R-RISK-001 | 是 | 缺口 G3 |
| 新增 `except Exception: pass` 与 `@pytest.mark.skip` | static_risk_check | 1 | R-IMP-004 / 003 | 否 | 建议 A3 |
| AI 可写区里放了 `.sh` 可执行内容 | check-ai-writable | 1 | R-SEC-004 | 否 | 建议 A4 |
| CI 报告属于旧提交 | ci_gate_check | 1 | R-VER-006 | 是 | 缺口 G4 |
| 覆盖率 71 < 80，变异分 55 < 60 | ci_gate_check | 1 | R-VER-001 / 002 | 否 | 建议 A5 |
| 采纳了 R-VER-006 却无映射；台账多出未采纳的 R-GOAL-002 | rule_map_check | 1 | R-PRC-006 | 否 | 建议 A6 |
| `docs/README.md` 有死链 | doc_consistency_check | 1 | R-PRC-007 | 否 | 建议 A7 |

两个项目里 `tdd_order_check` 与 `lint` 都退出 42：项目没接线。42 不是通过，报告里列在"未评估"。

## 这个样例没覆盖什么

- `evidence_pack_check`、`trace_check`：需要放行阶段的证据包和追溯矩阵，留作扩展。
- 软规则（5 条 AI 行为规范）：只能人工核对，机器检查不覆盖。
- `dirty-project/docs/README.md.in` 是运行时才落成 `README.md` 的：`doc_consistency_check`
  会扫描整个 skill 目录下的所有 `.md`，把带死链的文件直接放进来，会让 skill 自己的文档自检失败。
