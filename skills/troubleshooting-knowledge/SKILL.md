---
name: troubleshooting-knowledge
description: "知识提取与知识库维护：把排查结论或文档事实抽成结构化条目沉淀进 knowledge/（oss=开源通用可分发 / personal=个人项目不出库；先查重再写入，写入前 diff 给用户确认；条目按正常链路→逐环节排查→异常归因组织）。触发词：提取知识、沉淀知识、写知识库、知识库整理、建知识条目、把这次排查结论存下来、检索知识库、知识库里有没有关于 X 的记录、梳理系统排查参考、请求链路、资源对应关系、排查参考；只要用户想沉淀或检索知识就应使用本 skill。不用于排查过程本身（流程/脚本/授权归排查框架）。| Knowledge extraction & KB maintenance: distills conclusions/doc facts into structured entries (normal path → per-hop checks → symptom table); dedupe-first, diff-confirmed. Not for running the troubleshooting process itself."
metadata:
  agent_created: true
  version: "9"
---

# Troubleshooting Knowledge（知识层）

## 河道与河流

大模型（排查框架）是**河道**，项目私人知识是**河流**。河道只决定水怎么流，不决定水是什么；河流只决定水是什么，不决定河道怎么走。本 skill 只描述知识层怎么组织、怎么检索、怎么抽取，一行具体事实都不写。

## 边界（铁律，双向不碰）

- 改本 SKILL.md（知识层逻辑）**不碰** `knowledge/` 里的事实文件；改事实文件**不碰**本 SKILL.md。
- 消费方（河道）**只读消费**：扫描、取水，绝不写 `knowledge/`；抽取/维护只在本 skill 内进行。
- `knowledge/` 只放客观事实 + 方法论，不写排查套路/执行逻辑（那些归河道）；**条目内禁止 HTML 注释**（隐蔽注入通道）。
- 条目 schema / 字段 / confidence 定义 / 脱敏与防注入规则 / 自动扫描项：见 **`references/schema.md`**；消费方路由与执行框架：见 **`references/consumer-contract.md`**。

## 依赖

Python3 + PyYAML（`pip install pyyaml`，或 `pip install -r requirements.txt`）；缺失时脚本会给出明确安装提示。

## 条目与分层

- 条目 = `knowledge/<scope>/<domain>-<topic>.md`，一律从 `assets/entry-template.md` 复制、替换全部 `{{填写：…}}` 标记（残留会被 validate 拒绝；模板已做到零注释，复制后只需替换标记）；写作说明与落盘自检清单见 `assets/entry-template-guide.md`（非条目，不被扫描）；条目分 `type: system`（多资源/多环节的链路型系统）/ `issue`（单个错误码或行为，省略=issue）；正文结构固定：**是什么 → 组件与架构 → 正常链路（资源匹配关系 R# / 流量走向 L# / 控制面下发 P# / 正常表现）→ 逐环节排查（每环正常/异常/区分点/取证落点）→ 异常归因速查 → 状态事件日志语义**——核心仍是先写正常链路、最后才异常罗列，细则与体量上限（issue 100 行/5KB、system 300 行/20KB）见 `references/schema.md`。`validate.py` 对正文结构（缺核心节）与锚点完整性（L/P 未覆盖、引用未定义 R#、速查环节列未定义编号）做 **warn 级**校验。
- `scope: oss`（开源通用，可随 skill 分发）/ `personal`（个人项目，**不出库**，分发前剔除）；scope 必须与所在目录一致，由 validate 机械校验；oss 条目的 superseded_by 禁止指向 personal/。
- frontmatter 必带 `schema: 1`（版本号；字段变更时升版本并迁移）；evidence 必填（非空、每项含 run/what，可选 covers 标注覆盖的正文位置）——**无证据不收**。

## 抽取流程（先查重，再写入；先校验草稿，最后才落盘）

1. **查重**：`python3 scripts/search.py <关键词…>`（可复现打分）检索同主题条目 → 命中走更新/合并（保留原 evidence、追加新 evidence；重叠两份合一份，另一份 deprecated + superseded_by），未命中走新建；
2. **起草**：条目草稿写 `/tmp/kb-draft/`（**绝不放 knowledge/ 下**——会被递归扫描到）；从 `assets/entry-template.md` 复制（零注释，只替换 `{{填写：…}}`），写作说明与自检见 `assets/entry-template-guide.md`；
3. **草稿校验**：`python3 scripts/validate.py --file /tmp/kb-draft/<x>.md --scope oss|personal`（或 `--draft-dir <目录>`）——**落盘前先校验**，坏条目不会先进库；失败 exit 1，按报错修正；
4. **diff 确认**：用 `diff -u` 对比既有条目（新建则与空文件比），把完整 diff 给用户确认——一条错误事实的代价远大于一次错误回答；
5. **落盘**：确认后复制为 `knowledge/<scope>/<domain>-<topic>.md`；
6. **全库复核**：`python3 scripts/validate.py`（evidence/schema/枚举（含 type）/scope 一致性/related 与 superseded_by 泄露校验/HTML 注释禁令/`{{填写` 残留/密钥扫描/注入扫描/作废链/doc-sourced 版本信息/verified 行内标记/体量软上限/**正文结构与锚点完整性 warn**；失败 exit 1）。
7. **从既有文档导入**（设计文档/README/Wiki）：只摘事实、`source` 记文档与章节、**不整段复制**（版权与注入风险）；导入条目 `confidence: doc-sourced`，运行时复核观测到后升级。

## 检索

`python3 scripts/search.py <关键词…> [--scope oss|personal] [--top N]`：输出打分命中的条目路径 + scope/confidence/status，只列 active（`--include-inactive` 放开）；长度≤3 的纯 ASCII 词按词边界匹配（避免 `gat` 误中 `gateway`），其余子串匹配，中文自然语句整词未命中时按 2-gram 回退。条目较长时用 `python3 scripts/read_entry.py <条目> --sections <节名…>` 按节切片读取，或用 `--hop L6[,L7]` 按环节号精准切片（预算友好）。消费方（河道）直接调用 `scripts/search.py`（或 import 其 `score_entry`），打分规则只在 `search.py` 维护一份；取水路由与读取路径见 `references/consumer-contract.md`。

## 分发

- **分发（剔除 personal）**：`python3 scripts/export_oss.py` 复制 skill 时剔除 `knowledge/personal/`，在导出物上跑校验并扫描 `personal/` 泄漏，exit 0 才算可分发（`personal` 不出库）。

## 作废与冲突回流

- 作废：`status: deprecated` + `superseded_by`（validate 校验指向存在、目标 active、无循环）；存疑未定论：`status: disputed`。
- 消费方冲突时输出**知识冲突报告**，格式见 `assets/conflict-report-template.md`（含条目内锚点，定位到节/行）；报告由用户喂回本 skill 按抽取流程处理——冲突不能只停在结论标注里。
