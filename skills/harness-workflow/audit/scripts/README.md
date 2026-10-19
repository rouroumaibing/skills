# audit/scripts/README.md —— 检查器详述（SKILL.md 表格的"细节"落点）

> SKILL.md 的检查器表每器只留一行（查什么 / 接线 / 退出码）。本文件承载
> 逐检查器的退出码细节、豁免 / 警告 / 盲区、以及跨检查器共用的匹配语义、
> 配置有效性校验。改本文件须与 SKILL.md 表格及 `references/rule-registry.md`
> 的「判据脚本（本包）」列保持同步。

## 0. 通用约定

**退出码（三态，fail-closed）**
- `0` 通过；
- `1` 违反（阻断级发现）；
- `42` 该门禁未接线 / 判据文件损坏 / 非法 YAML / 零有效规则 / diff 未覆盖
  `changes` 列出的文件等 —— fail-closed，**不得把 42 当成通过**，崩溃不得
  被误读为"违规"。
- 所有检查器带 `--self-test` 反例夹具，夹具自包含（自写临时配置），不依赖
  被审项目。

**证据层 vs 报告层（机制承载）**
- 检查器只产出**证据层**退出码，不读 `required_rules`——它们不知道项目采纳
  了什么，也不该知道（证据与结论分层）。
- 采纳边界在**报告层**兑现：审计者把检查器发现映射到登记表规则后，已采纳
  规则的发现计入合规结论，未采纳规则的发现一律写成「修改建议」（治理设计
  要点类发现天然落在这层）。"建议模式"不是把检查器退出码改成 0，而是报告
  不给出合规判定、只给建议清单并明示"非合规判定"。

**判据来源（GOVERNANCE_DIR）**
- 检查器读取被审项目的机器配置：设 `GOVERNANCE_DIR` 指向项目根下的
  `governance/`，其中的 `redlines.yaml` 提供 `forbidden_words` /
  `risk_patterns` / `thresholds` / `ai_writable_paths`。
- 首次审计无配置时不要只报缺：缺失不是失败，是一条待补的修改建议。提议把
  skill 自带的 starter `audit/templates/redlines.starter.yaml` 复制为
  `<项目>/governance/redlines.yaml` 并按项目实际调整，经使用者同意后再写入。
- 配置缺失时检查器按各自"未接线"语义降级（通常 42），并在报告中声明
  "无机器判据来源"。

**软规则的采纳语义**
- 5 条软规则（AI 行为规范）即使被项目列入采纳清单，也不会成为合入必需检查——
  `rule_map_check` 禁止软规则进 `required_rules`（进必需清单 = 假强制）。它们
  只做人工核对，报告如实标注"人工核对"。

**两个 `required_rules` 键的关系**
- `redlines.yaml` 的 `required_rules` 是**采纳清单（权威）**——项目声明哪些
  登记规则对它生效；`rule-map.yaml` 是它的**逐条强制映射台账**（可选）。两者
  同名但角色不同，`rule_map_check` 机制化保证一致：台账 ⊆ 采纳（强制未采纳 =
  替项目立法，违反）、硬采纳 ⊆ 台账（采纳了却无强制映射，违反）、采纳为空而
  台账非空（违反）。

---

## 1. check_contract.py —— R-GOAL-001/002、R-REL-003

审什么：
- 契约禁用词（`--check-words`：读 `redlines.yaml` 的 `forbidden_words`）；
- 契约哈希完整性（`--check-hash`）；
- 契约是否已锁定（`--check-locked`：需项目 `state.json`）；
- 高风险批准人分离（`--check-approver`：需项目自建 `roles.yaml`）。

接线与退出码细节：
- `--check-approver`：高风险下"已注册"半条缺失 → 退出 42 fail-closed；
  `owner == approver` 半条不受影响（正常判）。
- `--check-locked`：需 `state.json`；缺失 / 损坏 = 无法证明已锁定 → 违反
  exit 1，fail-closed。
- `--check-words`：配置存在但无 `forbidden_words` → 打警告（该项本就是软审
  降级），不阻断、不 exit 42。

---

## 2. pre-receive-scope —— R-IMP-001、R-SEC-003

审什么：改动是否越出契约范围（scope）；命中 `forbidden_items` 即违反。
接线：`forbidden_items` 来自 `redlines.yaml`（缺省无 forbidden 即不拦）。
退出码：0 / 1（无 42 路径——范围检查是纯内容判据，不依赖可选设施）。

---

## 3. risk_floor_check.py —— R-RISK-001

审什么：从 diff 推算风险下限（触发 E-17：声明等级低于推算下限 → 升级）。
接线：需项目 `redlines.yaml` 提供 `risk_patterns` / `thresholds`。
退出码：0 / 1 / 42。
零规则处理：本检查器是**阻断门禁**（R-RISK-001 合入必需），零有效规则评估 =
无证据 → 42 fail-closed（详见 §7 配置有效性校验）。

---

## 4. check-ai-writable.py —— R-SEC-004

审什么：落在 `ai_writable_paths` 下的新增 / 修改文件，须为数据文件
（md/json/yaml/yml/txt/csv）、单文件 ≤ `ai_writable_max_bytes`（默认 64KB）、
禁止符号链接与可执行内容。其余路径不在本脚本职责内（服务端权限由项目自建）。

接线：`ai_writable_paths` 是**目录前缀**（如 `ai-reports/`、`docs/`），
**不是 glob**。

⚠️ **glob 写法告警（v13）**：条目若出现 `* ? [` 元字符，脚本打 WARNING 且该项
**什么都匹配不到**——因为匹配按目录前缀，不按 glob 解释。`"docs/**"` 实际
匹配不到 `docs/` 下任何文件（漏检）。应写成 `docs/` 前缀。starter 已改
`docs/`。

退出码：0 / 1 / 42（redlines 缺失 / 损坏 / pyyaml 缺失 → 42）。

---

## 5. evidence_pack_check.py —— R-REL-002

审什么：证据包完整性与绑定（artifact ↔ 契约 ↔ 证据绑定关系）。
接线：内置判据，读证据包 manifest。
退出码：0 / 1（无 42 路径）。

---

## 6. trace_check.py —— R-DES-001、R-VER-005

审什么：追溯矩阵断链（需求 ↔ 设计 ↔ 代码 ↔ 测试 四向可追溯）。
接线：内置判据，读追溯矩阵。
退出码：0 / 1（无 42 路径）。

---

## 7. tdd_order_check.py —— R-IMP-002

审什么：红绿顺序（测试应先失败过，再实现）。
接线：**需 CI 接线**——读 CI 产出的红绿证据日志（
`--redgreen-log` / `--impl-commits` / `--criterion-tests`）。无日志 → 退出 42。
退出码：0 / 1 / 42（未接线）。
说明：自测目前仍退 42（CI 日志来源由消费项目提供，skill 侧不伪造）。

---

## 8. static_risk_check.py —— R-IMP-004、R-IMP-003（空断言部分）

审什么：diff **新增行**中的测试完整性反模式（内置模式 + 项目追加规则）：
1. 跳过标记：pytest.skip/skipif/xfail、pytest.skip()、t.Skip、it/describe.skip、
   xit/xdescribe/xcontext、@Disabled、@Ignore（java/kotlin）、[Ignore（C#）、
   #[ignore（rust）、unittest.skip 等（新增跳过标记一律违规——删除标记不算）；
2. 吞异常的空捕获：`except ...: pass / ...`（单行 + 相邻两行形态——black/ruff
   格式化会把单行拆成两行，v12 起两行形态可检出）、`contextlib.suppress(...)`、
   `catch (...) {}` 等；
3. 空断言凑数：`assert True` / `assertTrue(true)` / `expect(true).toBe(true)` 等。

接线：**内置模式无需配置**。`redlines.yaml` 可选：
- `static_risk_allowlist`：正则列表，命中视为项目显式豁免（应经人审，E-06）；
- `static_risk_extra_rules`：正则列表，**项目追加自有反模式规则的唯一入口**，
  命中即违规。两者损坏 → 退出 42。
- `*.md/*.txt/*.rst` 散文豁免（与 risk_floor_check 同口径）。

diff 解析（fail-closed，v10 加固）：
- 文件头只在 hunk 外识别；hunk 内以 `+` 开头的行（含内容本身以 `++` 开头的新增
  行，如 `+++it.skip(...)`）一律按新增内容扫描，不再被误当文件头（防伪文件头
  劫持）；
- 兼容 `noprefix` / `mnemonicPrefix` / `quotepath` 八进制转义 / `/dev/null`；
- `changes.json` 列出的文件在 diff 中完全无归属 → 退出 42。

注释行处理（v13 对齐保守方向）：`except` 头行与空体行（pass/...）之间，可夹
**至多一行注释**（新增或上下文注释），v13 起统一**检出**（方向保守）；不靠
人审。

已知盲区（正则不可检出，靠人审；报告须注明"机器推算仅供参考"）：
- 多行空 catch：头体之间夹**两行及以上**注释 / 代码、或空体为真正业务代码
  不识别；
- 动态构造的跳过（`mark.skipif` 条件为真值变量）、RSpec skip/pending、
  go `if err != nil {}` 空分支、`assert x or True` 类恒真表达不识别——自研
  框架的自定义跳过 DSL 请用 `static_risk_extra_rules` 补。

退出码：0 / 1 / 42（判据损坏、diff 未覆盖 changes 列出的文件等）。

---

## 9. ci_gate_check.py —— R-VER-006/007、R-VER-001/002

审什么：CI 门禁报告——
- 必需 step 全 pass（默认 `type-check` / `dep-secret-scan`，`ci_required_steps`
  可追加）；
- `coverage` / `mutation_score` 阈值（含 [0,100] 合理性区间，NaN/Infinity 拒收）；
- `--commit` 提交绑定（陈旧 / 重放报告 → 1，报告缺 commit → 42）。

接线：**需 CI 接线**——报告缺失 / 损坏 / 必需 step 未跑 → 退出 42。
未传 `--commit` 仅警告不阻断——提交绑定是否生效取决于 CI 接线是否传参，报告
须注明。

退出码：0 / 1 / 42。

---

## 10. rule_map_check.py —— R-PRC-006

审什么：规则映射台账——
- `required_rules` 逐条有映射、ID 与登记表对齐、无重复；
- 机制可执行：**script 指针须解析后落在仓库根内 + 普通文件 + 可执行位**；
  **ci 机制须带非空 `ci_step`**；
- 软规则不进必需清单；
- 与 `redlines.yaml` 的 `required_rules` 采纳清单**双向一致**：采纳清单
  **未知 ID / 重复 ID 同样违反**（拼写错误会悄悄缩小审计范围）；台账非空而无
  采纳声明（redlines 缺失或未声明）= 替项目立法，违反。

接线：**需项目自建 `rule-map.yaml`**（缺失 / 损坏 → 退出 42）。

⚠️ **ID 规整（文档提示）**：`required_rules` 条目经 `.strip()` 归一——
`"R-GOAL-001 "` 尾随 / 前导空白会被忽略，与 `"R-GOAL-001"` 视为同一 ID。
这是 YAML 自然去尾随空白 + 显式 `.strip()` 的共同结果，非刻意豁免；登记表
比对照此归一。

退出码：0 / 1 / 42。

---

## 11. doc_consistency_check.py —— R-PRC-007

审什么：文档一致性——
- 死链（**先剥离 fenced code block** 再匹配，避免示例链接误报）；
- 冲突 / 修订痕迹（merge conflict markers）；
- 孤儿脚本 / 孤儿文档（不被任何文档引用）。

接线：**内置判据无需配置**。
退出码：0 / 1（无 42 路径）。

---

## 12. lint —— R-IMP-005

审什么：代码风格（调项目 lint 入口）。
接线：**需项目接线**——`governance/lint.env`（`LINTER=ruff|eslint`）+ 钉死
配置置于 `governance/lint/`。未接线 → 退出 42。
说明：自测目前仍退 42（lint 入口由消费项目提供）。

---

## 13. examples/test-upload、bench-play —— R-GOAL 度量约定

**演示脚本，非检查器**。度量范例（`METRIC|` 通道 + `unit` 绑定），是示例入口，
消费项目替换为自己的探针（见 `audit/examples/README.md`）。

---

## 14. 匹配语义与盲区（risk pattern 跨检查器共用）

报告须注明"机器推算仅供参考"。
- `path_glob`：支持 `**` 跨段、大小写不敏感；尾部 `**` 为目录语义
  （`**/auth/**` 不命中名为 `auth` 的文件）。
- 内容模式只扫 diff 新增行；路径范围用 `skip_paths` 黑名单（只按散文扩展名
  跳过 `*.md`/`*.txt`/`*.rst`、**其余文件全扫**——白名单会漏检未列入的扩展名，
  按目录名跳过会让 `docs/` 下的代码与配置文件成为绕过口）或 `only_paths`
  白名单（`path_glob` 附在 pattern 上属旧写法，仍兼容）；diff 无文件头时保守全扫。
- 相邻新增行拼接折叠空白后扫描：单行内 `"DROP   TABLE"` 与跨行拆分
 （`+DROP` 下一行 `+TABLE x`）可检出；拼接只限相邻行，隔上下文 / 删除行不拼。
- 已知盲区（正则不可检出，靠人审）：字符串 / 标识符拼接（`'DR'+'OP TABLE'`）、
  运行时动态构造 SQL、相邻新增行之间夹注释行（注释文本把两个半句隔开）。
- starter 已含 `DELETE FROM` 默认规则（宁误报，命中后由人核 WHERE）。
- 路径范围与规则集由项目在 `risk_patterns` 按实际语言调整。

---

## 15. 配置有效性校验（redlines.yaml）

"没有规则被评估" ≠ "PASS"。
- `redlines.yaml` 语法合法但零有效规则——缺 `risk_patterns` 键、空列表、写成
  字符串、条目缺 `pattern` 与 `path_glob`、**键存在但值无效**（`pattern`
  空 / 非字符串 / 纯空白，`path_glob` 空 / 非字符串或空列表）、仅有
  `skip_paths`/`only_paths` 的范围条目（缺主体字段，评估不了任何东西）、空文件、
  非法正则——一律退出 42 并注明"风险下限未评估"。
- 无法识别的条目键名打警告（含近似键名提示，如 `patern` → `pattern`、
  `only_path` → `only_paths`，警告不改变退出码）。
- 纯 `path_glob` 配置是合法形态，正常评估。

---

## 16. 两个检查器零规则处理为何不同（均为有意设计）

- `risk_floor_check.py` 是**阻断门禁**（R-RISK-001 合入必需），零规则评估 =
  无证据 → 42 fail-closed；
- `check_contract.py --check-words` 是**软审辅助**（禁用词人核兜底），零规则 →
  警告 + 降级为人工核对，报告如实标注。
