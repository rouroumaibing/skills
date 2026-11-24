---
name: troubleshooting-knowledge
description: "触发词：提取知识、沉淀知识、写知识库、知识库整理、检索知识库、建知识条目、把这次排查结论存下来、知识库里有没有关于 X 的记录。知识提取与维护：把排查结论/文档事实抽成脱敏条目沉淀进 knowledge/（oss 可分发 / personal 不出库），可检索、更新、作废；想沉淀或检索知识就用，不用于排查过程本身。"
metadata:
  agent_created: true
  version: "20"
---

# Troubleshooting Knowledge（知识层）

## 河道与河流

大模型（排查框架）是**河道**，项目私人知识是**河流**。河道只决定水怎么流，不决定水是什么；河流只决定水是什么，不决定河道怎么走。本 skill 只描述知识层怎么组织、怎么检索、怎么抽取，一行具体事实都不写。

## 边界（双向不碰）

- 改本 SKILL.md（知识层逻辑）**不碰** `knowledge/` 里的事实文件；改事实文件**不碰**本 SKILL.md。理由：两者变更节奏完全不同，混在一起无法判断一次 diff 改的是规则还是事实。
- 消费方（河道）**只读消费**：扫描、取水，绝不写 `knowledge/`；抽取/维护只在本 skill 内进行。理由：让写入只有一个入口，diff 确认才不会旁路。
- `knowledge/` 只放客观事实 + 方法论，不写排查套路/执行逻辑（那些归河道）。
- 字段 / 枚举 / confidence / 脱敏与防注入规则 / 校验项 / 粒度上限：**`references/schema.md`**（数值以 `scripts/validate.py` 常量为准）；消费方路由与执行框架：**`references/consumer-contract.md`**。本文件不复制这些细则——复制即漂移。

## 依赖

Python **3.8+**（脚本含 `from __future__ import annotations`）+ PyYAML（`pip install -r requirements.txt`）。PyYAML 缺失时脚本给明确安装提示，不会伪装成"解析失败"。

## 条目格式

- 条目 = `knowledge/<scope>/<domain>-<topic>.md`，从 `assets/entry-template.md` 复制，替换全部 `{{填写：…}}` 标记；残留会被 validate 判 error。
- 模板**零注释**（HTML 与 YAML 注释都没有）：消费方整篇读入条目，注释对模型可见，是绕过密钥/注入扫描的隐蔽通道。字段取值与填写说明见 `assets/entry-template-guide.md`（非条目，不被扫描）。
- **条目只有一种形态**：链路/系统型通用排查参考；frontmatter 不含 `type` 字段（写了判 error）。具体报错（503、CrashLoopBackOff 之类）不单独建条目，走对应的通用排查参考——错误码属于哪条链路由消费方模型按语义自行判断，这类归类 AI 可直接识别，库里不维护「错误码 → 条目」映射表；把线索写进对应条目的 `error_codes` / `symptoms` 与「异常归因速查」表即可。正文固定结构、R#/L#/P# 锚点、confidence 定义、粒度软上限、校验项清单：**全部见 `references/schema.md`**，此处不复述。
- `frontmatter` 必带 `schema: 1`；`evidence` 必填非空（每项 run/what，可选 covers）。理由：知识层只收有出处的事实，推测由河道分级标注、不落库。
- **条目不写具体时间**：条目是长期可复用的通用参考、不对应某一次排查；正文与 `evidence.run` / `source` 用「文档名+版本+章节」「观测方式/证据位」指认来源，不写日期、访问日期、排查时间戳、`截至某日`。理由：具体时间会把通用事实绑成一次性个案、随时间失效。frontmatter 不含 `updated` 字段（残留判 error），validate 全文扫描日期与日志时间戳判 error（放行版本号 / 端口范围 / UUID），不做过期复核。细则见 `references/schema.md`「不写具体时间」。

## 分层与 scope

- `knowledge/oss/` 开源通用，可随 skill 分发；`knowledge/personal/` 个人项目，**不出库**，分发前剔除。scope 必须与所在目录一致，由 validate 机械校验；oss 条目的 `superseded_by` / `related` 禁止指向 personal/。
- 库为空（`knowledge/` 下无 .md）是正常的初始状态，直接走新建流程，不用报错也不用先造条目。
- 草稿写 `/tmp/kb-draft/`，**绝不放 `knowledge/` 下**：落盘前的半成品会被全库递归扫描到，把坏条目混进正式库。

## 入库门槛（两道闸，写入前必过）

知识 = 被验证过的信念；写下来但未验证的是**假说**。生成成本≈0 而验证昂贵——闸门若设在「写入」，产出速率必然脱钩验证带宽，库会退化成假说堆（历史教训：批量生成的 39 条 doc-sourced 从未被运行时证实或证伪，300+ 待复核无人触发）。因此写入前过两道闸：

- **闸一·增量判定（决定写什么）**：先问「模型的参数知识是否已可靠覆盖这条内容」。能从训练知识或上游文档稳定复述的百科式内容（组件架构完整介绍、背景说明）→ **不写**：它与参数知识竞争且必输（参数知识可现场生成、可查上游最新文档，冻结快照只会陈旧）。值得写的只有参数知识拿不到或不可靠拿到的——**环境特例**（真实取值、已验证组合，personal 主战场）、**实测踩坑**（真实事故、脚本 bug）、**易错点/边缘语义/版本陷阱/不变式**（强模型也会说错的地方）。触发方式**事件驱动**：真实排查命中、知识冲突报告回流、新踩坑才新增，**禁止按领域批量生成**。
- **闸二·假说/知识分层（决定什么叫知识）**：抽取 = 登记假说，运行时取证 = 升级为知识。`doc-sourced` 条目是有出处的假说，只作排查线索与待验证清单，不作结论依据；`verified` / `observed-once` 才是知识。confidence 定义与消费语义见 `references/schema.md` 与 `references/consumer-contract.md`。
- **纪律不进库**：排查命令串、执行顺序、流程纪律归河道（判据见 `references/schema.md`「事实/流程边界判据」）。理由：纪律的价值 = 使用频率 × 注入强制性——skill 每次 run 强制加载，库里命中才读，偶发注入保不住强制性。

## 抽取流程（先查重，再写入；先校验草稿，最后才落盘）

1. **查重**：`python3 scripts/search.py <关键词…> --no-log` 检索同主题条目（查重属维护调用，`--no-log` 避免污染命中统计；真实排查取水时不加）。无命中时按脚本输出的 `hint=` 换别名/换种说法再试一次（纯关键词检索，同义与错误码对应靠 `aliases`），仍无命中才走新建；命中走「更新与合并」。
2. **起草**：先过「入库门槛」闸一（增量判定）——参数知识已可靠覆盖的内容不写；正文篇幅按「写作密度（防守面优先）」分配（见 `references/schema.md`）。然后从 `assets/entry-template.md` 复制到 `/tmp/kb-draft/`，替换全部标记；写作说明与自检见 `assets/entry-template-guide.md`。
3. **草稿校验**：`python3 scripts/validate.py --file /tmp/kb-draft/<x>.md --scope oss|personal`（或 `--draft-dir <目录>`）——坏条目不进库；失败 exit 1，按报错修正。
4. **diff 确认**：`diff -u` 比对既有条目（新建则与空文件比），除完整 diff 外，固定给出一段**结构化摘要**：新增/修改了哪些 R#/L#、`confidence` 取值、〔推论〕/〔待复核〕条数、每条新增结论的证据来源。让用户不必读完整 diff 就能抓住风险点。这是注入类攻击的实际防线——自动扫描只挡无意写入，挡不住有意构造的变体。
5. **落盘**：确认后复制为 `knowledge/<scope>/<domain>-<topic>.md`。
6. **全库复核**：`python3 scripts/validate.py`。校验项清单以 `scripts/validate.py` docstring 与 `references/schema.md` 为准（error 阻断 / warn 提醒）。
7. **从既有文档导入**（设计文档/README/Wiki）：只摘事实、`source` 记文档与章节、**不整段复制**（版权与注入风险）；导入条目 `confidence: doc-sourced`——本质是**登记假说**，文档陈述未被本环境运行时证实前按假说对待（见「入库门槛」闸二），升级靠运行时取证，复核触发靠冲突报告回流或真实排查命中，不做无触发的全局复核。

## 更新与合并（命中已有条目时）

- **追加 evidence，不删旧的**：旧证据是当时结论的出处，删掉会让历史结论失去依据；新证据追加一条并把 `covers` 指向它覆盖的 R#/L#。
- **confidence 升级**：`observed-once` → `verified` 需要一次新的、可指认观测点的运行时取证（`evidence.run` 写明）；`doc-sourced` 不因同一份文档反复阅读而升级。
- **重叠两份合一份**：保留证据更齐的一份为 `active`，另一份 `deprecated` + `superseded_by` 指过去；diff 里显式列出被合并的编号，别静默丢弃。
- **复核按命中触发，不做全局清零**：〔待复核〕项在条目被检索命中或冲突报告回流时才复核升级；命中数据以 `<skill 根>/.usage-log.jsonl` 为准（CLI 检索自动追加，查重加 `--no-log` 不计入，见 consumer-contract「命中遥测」）。从未被命中的条目不投入维护，进入降级/合并/作废评估名单。理由：保鲜是持续成本，维护预算有限，跟随真实使用走。

## 检索

`python3 scripts/search.py <关键词…> [--scope oss|personal] [--top N]`：输出打分命中的条目路径 + `hit=`（命中来源：fm / body / fm+body / 2gram）+ scope/confidence/status，只列 active（`--include-inactive` 放开）；长度≤3 的纯 ASCII 词按词边界匹配（避免 `gat` 误中 `gateway`），其余子串匹配，中文自然语句整词未命中时按 2-gram 回退。**无命中额外输出 `hint=` 提示换别名/换种说法重试**，别一次没命中就当库里没有。条目较长时用 `python3 scripts/read_entry.py <条目> --sections <节名…>` 按节切片，或用 `--hop L6[,L7]` 按环节号切片（预算友好）。消费方（河道）直接调用 `scripts/search.py`（或 import 其 `score_entry`），打分规则只在 `search.py` 维护一份；取水路由与读取路径见 `references/consumer-contract.md`。

## 分发

- **剔除 personal**：`python3 scripts/export_oss.py` 复制 skill 时剔除 `knowledge/personal/`，在导出物上跑 validate 并扫描 frontmatter 的 `personal/` 悬空引用，exit 0 才算可分发。默认导出到**系统临时目录**（不污染源目录），可用 `--src <skill 目录> --out <导出目录>` 指定。

## 作废与冲突回流

- 作废：`status: deprecated` + `superseded_by`（validate 校验指向存在、目标 active、无循环）；存疑未定论：`status: disputed`。
- 消费方冲突时输出**知识冲突报告**，格式见 `assets/conflict-report-template.md`（含条目内锚点，定位到节/行）；报告由用户喂回本 skill 按抽取流程处理——冲突不能只停在结论标注里。
