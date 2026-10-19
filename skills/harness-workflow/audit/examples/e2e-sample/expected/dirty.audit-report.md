# 审计报告：C-001 上传接口超时处理（dirty-project）

> 本文是端到端样例的**审计报告样稿**：证据全部来自 `run_e2e.py dirty` 的真实输出
> （原始输出见同目录 `dirty.evidence.md`），结论按 SKILL.md 的"证据层 / 报告层"分工写成。
> 归档位置示例：`audits/2026-10-19-C-001-upload-timeout.md`。

| 项 | 内容 |
| --- | --- |
| 审计对象 | 契约 C-001@v1 + 提交 `c0ffee1` 的 diff + CI 报告 |
| 运行模式 | 有仓库、无 CI 接线（跑 `audit/scripts/` 检查器，CI 红绿与 lint 未接） |
| 检测方式 | 工程实装（本包检查器）+ 人工归类 |
| 验证独立性 | **非独立验证**（检查与归类在同一会话内完成）；下文推算风险下限为 high，**按规定直接转人** |
| 采纳边界 | `governance/redlines.yaml` 的 `required_rules`：R-GOAL-001、R-IMP-001、R-RISK-001、R-VER-006 |

## 1. 结论

**不合规。采纳范围内的 4 条规则全部有发现**（R-GOAL-001、R-IMP-001、R-RISK-001、R-VER-006）。
另有 7 项发现落在**未采纳**规则上，按采纳边界只写成修改建议，不计入合规结论——
但其中第 A1 项（契约哈希对不上）动摇的是整份契约的可信度，建议优先采纳。

触发升级：**E-17**（推算风险下限 high > 声明 low）。该变更停止推进，按 high 重走放行，转人逐项核对。

## 2. 检查器结果（证据层）与归类（报告层）

| 检查器 | 退出码 | 触及规则 | 是否采纳 | 归类 |
| --- | --- | --- | --- | --- |
| check_contract（禁用词） | 1 | R-GOAL-001 | 是 | **缺口 G1** |
| check_contract（哈希 / 已锁定） | 1 | R-GOAL-002 | 否 | 建议 A1 |
| pre-receive-scope（范围） | 1 | R-IMP-001 | 是 | **缺口 G2** |
| pre-receive-scope（禁止项） | 1 | R-SEC-003 | 否 | 建议 A2 |
| risk_floor_check | 1 | R-RISK-001 | 是 | **缺口 G3** |
| static_risk_check | 1 | R-IMP-004 / R-IMP-003 | 否 | 建议 A3 |
| check-ai-writable | 1 | R-SEC-004 | 否 | 建议 A4 |
| ci_gate_check（提交绑定） | 1 | R-VER-006 | 是 | **缺口 G4** |
| ci_gate_check（覆盖率 / 变异分） | 1 | R-VER-001 / R-VER-002 | 否 | 建议 A5 |
| rule_map_check | 1 | R-PRC-006 | 否 | 建议 A6 |
| doc_consistency_check | 1 | R-PRC-007 | 否 | 建议 A7 |
| tdd_order_check | 42 | R-IMP-002 | 否 | 未接线，**未评估**（见第 5 节） |
| lint | 42 | R-IMP-005 | 否 | 未接线，**未评估**（见第 5 节） |

## 3. 缺口（已采纳规则，计入合规结论）

```
【缺口】G1 契约成功判据含禁用词，无法客观判定
【证据】check_contract.py --check-words：
        VIOLATION R-GOAL-001 forbidden word '适当' in '上传超时后适当返回错误码'
【建议改动】把 A-1 改成可量化判据（例：超时 30 秒后返回 504，且不产生半成品文件）；契约对 AI 只读，
        改动走 proposals/ 并由契约负责人确认
【对抗推演】AI 会把「适当」换成「适度 / 酌情」之类的同义词绕过词表；词表只是兜底，真正的防线是人审
        判据是否可量化。无同型案例
【建议可信度】纯推测
【由谁决策】人（契约负责人 alice）
【严重度】中
```

```
【缺口】G2 改动越出契约范围
【证据】pre-receive-scope --check-forbidden（契约范围：src/upload、tests）：
        VIOLATION R-IMP-001 out-of-scope: src/auth/session.py
        VIOLATION R-IMP-001 out-of-scope: config/prod.env
【建议改动】回退这两处改动；确需改动则先按 E-02 提契约变更申请
【对抗推演】AI 会把范围外文件"重命名"或借符号链接绕过路径匹配。范围检查已覆盖重命名、符号链接、
        路径穿越等边界；剩余攻击面是检查器本身被同 PR 改掉，须 CI 从目标分支取检查器（RT-03、RT-04、RT-01）
【建议可信度】已验证模式（RT-03）
【由谁决策】人（批准人 bob）
【严重度】高
```

```
【缺口】G3 风险等级被低报：契约声明 low，diff 推算下限为 high
【证据】risk_floor_check.py --contract-risk-level low：
        RISK-FLOOR[R-RISK-001] hit path_glob=**/auth/** at src/auth/session.py => floor=high > declared=low
        RISK-FLOOR[R-RISK-001] hit pattern=\b(ALTER|DROP)\s+TABLE\b|\bTRUNCATE\b at src/auth/session.py:
        +    db.execute("DROP TABLE sessions") => floor=high > declared=low
        risk_floor_check: VIOLATION (declared low below inferred floor high) -> escalate E-17.
【建议改动】把契约风险等级改为 high（经 proposals/ + 人审），并按 high 走放行：批准人与负责人分离、
        人逐项核对 auth 路径改动
【对抗推演】AI 会把高风险改动拆进"低风险"路径，或用字符串拼接 / 动态构造 SQL 绕过正则。
        已知盲区在 SKILL.md 与 starter 中已声明（拼接、动态 SQL、注释行隔开的半句），靠人审（RT-13）
【建议可信度】已验证模式（RT-13）
【由谁决策】人（批准人 bob）
【严重度】高
```

```
【缺口】G4 R-VER-006 无有效证据：CI 报告不属于被审提交
【证据】ci_gate_check.py --commit c0ffee1：
        VIOLATION report 'commit' = '0ldc0de' != expected 'c0ffee1' (stale or replayed report)
        （报告里的 type-check=pass 只能证明旧提交，不能当作对 c0ffee1 的证明）
【建议改动】在 c0ffee1 上重跑 CI 并产出新报告；CI 接线时必须传 --commit，否则陈旧报告只会被警告、不会被拦下
【对抗推演】AI 会复用一份旧的通过报告。"放行五重绑定"不变量要求绑 target_commit==HEAD，
        但它针对放行记录而非 CI 报告，两者只是近似，没有同型案例
【建议可信度】纯推测
【由谁决策】工程（CI 维护者）
【严重度】高
```

## 4. 修改建议（未采纳规则：不计合规结论，仅供项目决定是否采纳）

```
【缺口】A1 契约已被改动或未锁定（R-GOAL-002，未采纳）
【证据】check_contract.py --check-hash：
        VIOLATION R-GOAL-002 hash mismatch stored=02864dff39a3… computed=104e887bac1c…（完整值见证据附录）
        check_contract.py --check-locked：
        VIOLATION R-GOAL-002 contract not locked/intact: not-locked draft
【建议改动】采纳 R-GOAL-002；锁定契约并让 CI 在每次合入前校验哈希；本次需由人确认范围表被改的原因
【对抗推演】AI 在锁定后改范围表，让越界改动"变合法"；哈希把规范化后的 front matter 与正文表格一起绑住，
        同型案例见"契约对 AI 只读"（RT-15）
【建议可信度】已验证模式（RT-15）
【由谁决策】人（契约负责人 alice + 批准人 bob）
【严重度】高
```

```
【缺口】A2 触碰契约禁止项 config/prod.env（R-SEC-003，未采纳）
【证据】pre-receive-scope --check-forbidden：
        VIOLATION R-SEC-003 forbidden-file target=config/prod.env touched=config/prod.env
【建议改动】回退该文件改动；生产配置变更走独立变更流程
【对抗推演】AI 会借重命名或大小写差异触碰禁止项；检查器覆盖重命名与路径穿越（RT-03）
【建议可信度】已验证模式（RT-03）
【由谁决策】人（批准人 bob）
【严重度】高
```

```
【缺口】A3 新增吞异常与跳过标记（R-IMP-004 / R-IMP-003，未采纳）
【证据】static_risk_check.py：
        STATIC-RISK[empty-catch] python empty except (two-line form) at src/upload/handler.py:1: except Exception: / pass
        STATIC-RISK[skip] pytest skip/skipif/xfail mark at tests/test_upload.py:1: @pytest.mark.skip
【建议改动】移除 except: pass 与 skip 标记；确需豁免走人审并记入 proposals/
【对抗推演】AI 用跳过标记或空断言让测试"变绿"（RT-07、RT-08）。若项目采纳 R-IMP-003 / R-IMP-004，
        新增跳过标记会同时触发 E-06（回退并暂停）
【建议可信度】已验证模式（RT-07、RT-08）
【由谁决策】人（契约负责人 alice）
【严重度】高
```

```
【缺口】A4 AI 可写区出现可执行内容（R-SEC-004，未采纳）
【证据】check-ai-writable.py：
        VIOLATION[R-SEC-004] ai-reports/run.sh: disallowed-type:.sh, executable-content
【建议改动】删除 ai-reports/run.sh；AI 可写区只放数据文件（md / json / yaml / txt / csv）
【对抗推演】AI 在可写区放可执行脚本或指向受保护目录的符号链接，等运行时被别处调用（RT-04）
【建议可信度】已验证模式（RT-04）
【由谁决策】工程
【严重度】高
```

```
【缺口】A5 覆盖率与变异分低于阈值（R-VER-001 / R-VER-002，未采纳）
【证据】ci_gate_check.py：
        VIOLATION metric 'coverage' = 71 < threshold 80
        VIOLATION metric 'mutation_score' = 55 < threshold 60
【建议改动】补有意义的测试；注意本报告属旧提交（见 G4），需重跑后再判
【对抗推演】AI 删测试或加空断言凑覆盖率；变异分是对这类手法的校验（RT-08）
【建议可信度】已验证模式（RT-08）
【由谁决策】人（契约负责人 alice）
【严重度】中
```

```
【缺口】A6 采纳清单与规则映射台账不一致（R-PRC-006，未采纳）
【证据】rule_map_check.py：
        VIOLATION rule-map enforces 'R-GOAL-002' but it is not adopted in redlines.yaml 'required_rules' (enforcing unadopted rules)
        VIOLATION redlines.yaml adopts hard rule 'R-VER-006' but rule-map has no mapping for it (adopted without enforcement mapping)
【建议改动】要么把 R-GOAL-002 加进采纳清单（推荐，见 A1），要么从台账移除；给 R-VER-006 补映射。
        否则 G4 这样的缺口在 CI 里没有对应的必需检查
【对抗推演】无同型案例；自行推演：采纳了规则却没映射，等于"声明了要求、没人强制"
【建议可信度】纯推测
【由谁决策】人（契约负责人 alice）
【严重度】中
```

```
【缺口】A7 文档有死链（R-PRC-007，未采纳）
【证据】doc_consistency_check.py --root docs：
        DOC-ISSUE dead-link in README.md -> missing-spec.md
【建议改动】补上 missing-spec.md 或修正链接
【对抗推演】无同型案例；低风险
【建议可信度】纯推测
【由谁决策】工程
【严重度】低
```

## 5. 未评估项（如实声明，不得写成"通过"）

- **tdd_order_check（R-IMP-002）、lint（R-IMP-005）**：退出码 42，项目未接线；两条规则均未采纳。
  建议在 CI 里接入后再决定是否采纳。
- **5 条软规则（AI 行为规范）**：只能人工核对，本轮机器检查未覆盖。
- **R-REL-003（批准人分离）**：契约声明 low，该项不要求；若按 G3 升为 high，须重跑 `--check-approver`。

## 6. 晋升建议

无。本轮发现的手法均已在案例库 RT-01~15 中有同型条目，无新型绕过需要晋升。
