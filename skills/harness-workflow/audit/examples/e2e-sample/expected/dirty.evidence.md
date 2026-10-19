# 证据附录：dirty-project

> 由 `run_e2e.py dirty` 生成：每个检查器的命令与原始输出，供审计报告逐条引用。


## 契约：禁用词 / 哈希 / 批准人（R-GOAL-001 R-GOAL-002 R-REL-003）

- 命令：`check_contract.py --contract contracts/C-001.md --check-words --check-hash --check-approver`（cwd = dirty-project）
- 退出码：**1**（预期 1）
- 样例设计：dirty：criteria 含「适当」；锁定后范围表被改，哈希对不上

```
VIOLATION R-GOAL-001 forbidden word '适当' in '上传超时后适当返回错误码'
VIOLATION R-GOAL-002 hash mismatch stored=02864dff39a3b238e214bfecee2d34143db43a1347198ff802e0e09c57389724 computed=104e887bac1cebd4f00815cc3d7045d03367cc9381e7abb49788c558c428e5c6
check_contract: 2 violation(s).
```

## 契约：是否已锁定（R-GOAL-002）

- 命令：`check_contract.py --contract contracts/C-001.md --check-locked --state .workflow/state.json`（cwd = dirty-project）
- 退出码：**1**（预期 1）
- 样例设计：dirty：state.json 仍是 draft

```
VIOLATION R-GOAL-002 contract not locked/intact: not-locked draft
check_contract: 1 violation(s).
```

## 改动范围 / 禁止项（R-IMP-001 R-SEC-003）

- 命令：`pre-receive-scope --changes audit-input/changes.json --contract contracts/C-001.md --check-forbidden`（cwd = dirty-project）
- 退出码：**1**（预期 1）
- 样例设计：dirty：改了范围外的 src/auth 与禁止项 config/prod.env

```
VIOLATION R-IMP-001 out-of-scope: src/auth/session.py
VIOLATION R-IMP-001 out-of-scope: config/prod.env
VIOLATION R-SEC-003 forbidden-file target=config/prod.env touched=config/prod.env
pre-receive-scope: 3 violation(s).
```

## 风险下限（R-RISK-001）

- 命令：`risk_floor_check.py --changes audit-input/changes.json --diff audit-input/change.diff --contract-risk-level low`（cwd = dirty-project）
- 退出码：**1**（预期 1）
- 样例设计：dirty：改了 auth 目录并新增 DROP TABLE，下限为 high，契约却声明 low（E-17）

```
RISK-FLOOR[R-RISK-001] hit path_glob=**/auth/** at src/auth/session.py => floor=high > declared=low
RISK-FLOOR[R-RISK-001] hit pattern=\b(ALTER|DROP)\s+TABLE\b|\bTRUNCATE\b at src/auth/session.py: +    db.execute("DROP TABLE sessions") => floor=high > declared=low
risk_floor_check: VIOLATION (declared low below inferred floor high) -> escalate E-17.
```

## 测试完整性反模式（R-IMP-004 R-IMP-003）

- 命令：`static_risk_check.py --changes audit-input/changes.json --diff audit-input/change.diff`（cwd = dirty-project）
- 退出码：**1**（预期 1）
- 样例设计：dirty：新增 except Exception: pass 与 @pytest.mark.skip

```
STATIC-RISK[empty-catch] python empty except (two-line form) at src/upload/handler.py:1: except Exception: / pass
STATIC-RISK[skip] pytest skip/skipif/xfail mark at tests/test_upload.py:1: @pytest.mark.skip
static_risk_check: 2 finding(s) -> VIOLATION (R-IMP-004 / R-IMP-003). Remove skip marks / empty catches / empty asserts, or obtain human exemption recorded in proposals/.
```

## AI 可写区（R-SEC-004）

- 命令：`check-ai-writable.py --changes audit-input/changes.json --repo .`（cwd = dirty-project）
- 退出码：**1**（预期 1）
- 样例设计：dirty：ai-reports/ 下新增了 .sh 可执行内容

```
VIOLATION[R-SEC-004] ai-reports/run.sh: disallowed-type:.sh, executable-content
check-ai-writable: 1 violation(s).
```

## CI 门禁报告（R-VER-006 R-VER-007 R-VER-001 R-VER-002）

- 命令：`ci_gate_check.py --report audit-input/ci-report.json --commit c0ffee1`（cwd = dirty-project）
- 退出码：**1**（预期 1）
- 样例设计：dirty：报告属于旧提交（陈旧/重放）；覆盖率 71 < 80；变异分 55 < 60

```
VIOLATION report 'commit' = '0ldc0de' != expected 'c0ffee1' (stale or replayed report)
VIOLATION metric 'coverage' = 71 < threshold 80
VIOLATION metric 'mutation_score' = 55 < threshold 60
ci_gate_check: 3 violation(s) -> VIOLATION (R-VER-001/002/006/007).
```

## 规则映射台账（R-PRC-006）

- 命令：`rule_map_check.py `（cwd = dirty-project）
- 退出码：**1**（预期 1）
- 样例设计：dirty：采纳了 R-VER-006 却无映射；台账里的 R-GOAL-002 未被采纳（替项目立法）

```
VIOLATION rule-map enforces 'R-GOAL-002' but it is not adopted in redlines.yaml 'required_rules' (enforcing unadopted rules)
VIOLATION redlines.yaml adopts hard rule 'R-VER-006' but rule-map has no mapping for it (adopted without enforcement mapping)
rule_map_check: 2 violation(s) -> VIOLATION (R-PRC-006).
```

## 文档一致性（R-PRC-007）

- 命令：`doc_consistency_check.py --root docs`（cwd = dirty-project）
- 退出码：**1**（预期 1）
- 样例设计：dirty：docs/README.md 有死链

```
DOC-ISSUE dead-link in README.md -> missing-spec.md
doc_consistency_check: 1 finding(s) -> VIOLATION (R-PRC-007).
```

## 红绿顺序（R-IMP-002）

- 命令：`tdd_order_check.py `（cwd = dirty-project）
- 退出码：**42**（预期 42）
- 样例设计：两个项目都没接 CI 红绿证据：42 = 未接线，不是通过

```
tdd_order_check: requires --redgreen-log/--impl-commits/--criterion-tests (red-green evidence store produced by CI). Exit 42 = not implemented.
```

## 代码风格（R-IMP-005）

- 命令：`lint `（cwd = dirty-project）
- 退出码：**42**（预期 42）
- 样例设计：两个项目都没接 lint：42 = 未接线，不是通过

```
lint: not wired. Place governance/lint.env (LINTER=ruff|eslint) and a pinned config under governance/lint/. Exit 42 = not implemented.
```
