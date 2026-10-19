# 证据附录：clean-project

> 由 `run_e2e.py clean` 生成：每个检查器的命令与原始输出，供审计报告逐条引用。


## 契约：禁用词 / 哈希 / 批准人（R-GOAL-001 R-GOAL-002 R-REL-003）

- 命令：`check_contract.py --contract contracts/C-001.md --check-words --check-hash --check-approver`（cwd = clean-project）
- 退出码：**0**（预期 0）

```
check_contract: PASS.
```

## 契约：是否已锁定（R-GOAL-002）

- 命令：`check_contract.py --contract contracts/C-001.md --check-locked --state .workflow/state.json`（cwd = clean-project）
- 退出码：**0**（预期 0）

```
check_contract: PASS.
```

## 改动范围 / 禁止项（R-IMP-001 R-SEC-003）

- 命令：`pre-receive-scope --changes audit-input/changes.json --contract contracts/C-001.md --check-forbidden`（cwd = clean-project）
- 退出码：**0**（预期 0）

```
pre-receive-scope: PASS.
```

## 风险下限（R-RISK-001）

- 命令：`risk_floor_check.py --changes audit-input/changes.json --diff audit-input/change.diff --contract-risk-level low`（cwd = clean-project）
- 退出码：**0**（预期 0）

```
risk_floor_check: PASS (no risk_pattern hit; floor=low).
```

## 测试完整性反模式（R-IMP-004 R-IMP-003）

- 命令：`static_risk_check.py --changes audit-input/changes.json --diff audit-input/change.diff`（cwd = clean-project）
- 退出码：**0**（预期 0）

```
static_risk_check: PASS (no skip marks / empty catches / empty asserts in added lines; all listed changes attributed).
```

## AI 可写区（R-SEC-004）

- 命令：`check-ai-writable.py --changes audit-input/changes.json --repo .`（cwd = clean-project）
- 退出码：**0**（预期 0）

```
check-ai-writable: PASS (3 ai_writable files checked).
```

## CI 门禁报告（R-VER-006 R-VER-007 R-VER-001 R-VER-002）

- 命令：`ci_gate_check.py --report audit-input/ci-report.json --commit c0ffee1`（cwd = clean-project）
- 退出码：**0**（预期 0）

```
ci_gate_check: PASS (2 required step(s), 2 metric threshold(s), commit=c0ffee1).
```

## 规则映射台账（R-PRC-006）

- 命令：`rule_map_check.py `（cwd = clean-project）
- 退出码：**0**（预期 0）

```
rule_map_check: PASS (5 required rule(s), all mapped, pointers executable, registry-aligned, adoption-boundary consistent).
```

## 文档一致性（R-PRC-007）

- 命令：`doc_consistency_check.py --root docs`（cwd = clean-project）
- 退出码：**0**（预期 0）

```
doc_consistency_check: PASS (no dead links / conflict markers / orphans).
```

## 红绿顺序（R-IMP-002）

- 命令：`tdd_order_check.py `（cwd = clean-project）
- 退出码：**42**（预期 42）

```
tdd_order_check: requires --redgreen-log/--impl-commits/--criterion-tests (red-green evidence store produced by CI). Exit 42 = not implemented.
```

## 代码风格（R-IMP-005）

- 命令：`lint `（cwd = clean-project）
- 退出码：**42**（预期 42）

```
lint: not wired. Place governance/lint.env (LINTER=ruff|eslint) and a pinned config under governance/lint/. Exit 42 = not implemented.
```
