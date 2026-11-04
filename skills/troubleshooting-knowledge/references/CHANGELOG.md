# Schema CHANGELOG

条目 frontmatter 的 `schema` 字段记录条目遵循的版本；字段变更时在这里登记并说明迁移方式。

## schema 1（2026-11-04，初版）

- 字段：`schema` / `domain` / `scope` / `components` / `symptoms` / `error_codes` / `versions` / `envs` / `aliases` / `status` / `confidence` / `evidence` / `updated` / `source` / `superseded_by`。
- scope 两层：`oss`（开源通用，可分发）/ `personal`（个人项目，不出库），目录与字段一致性由 build_index 校验。
- confidence 三级：`verified` / `observed-once` / `doc-sourced`；evidence 必填（非空、每项含 run/what）。
- build_index 自动扫描：密钥特征（error）、`{{` 模板残留（error）、PII/注入句式（warn）、作废链完整性与循环引用（warn/error）、180 天未更新（warn）。
- 兼容：旧 `topics` 字段仍被检索脚本识别（降级为别名参与匹配），新条目请用拆分后的语义字段。

## 正文结构修订（2026-11-04，frontmatter schema 仍为 1）

- 条目正文固定结构改为「先正常、后异常」：正常链路（资源间字段匹配关系 + 流量走向 + 正常表现）→ 逐环节排查（每环正常表现/异常表现/取证落点）→ 异常归因速查表 →（可选）配置语义 / 边界与置信度说明。不再使用旧的「是什么/为什么/取证落点」开头。
- 边界判据澄清：链路/生命周期顺序是系统客观事实（请求物理上按此顺序流动），不算排查流程；判据约束的是排查命令串的执行顺序。
- 粒度软上限从 100 行/2KB 放宽到 100 行/5KB（链路型条目信息密度更高；中文 UTF-8 下 5KB ≈ 1700 字）。既有条目（envoy-gateway、k8s-pod-crashloopbackoff）已按新结构迁移，frontmatter 无变化。

## 校验收紧与机制调整（2026-11-04 第二轮评审，frontmatter schema 仍为 1）

- **index.yaml 机制移除**：build_index.py 改名 `scripts/validate.py`，只校验不生成索引——「索引+全扫合并」存在已删除文件仍可命中的隐患，条目量小全扫即足够；SKILL.md / 消费方契约同步。
- **条目内 HTML 注释 → error**：注释对消费方模型可见，是绕过密钥/注入扫描的隐蔽注入通道；密钥/PII/注入扫描范围改为全文含代码围栏（围栏不是免责区）。
- **注入词表扩充**（system prompt / 忽略以上 / 无视… / 你现在是 / curl … | sh 等）；手机号正则加前后非数字边界（防误伤时间戳等 11 位数字）。
- **evidence 每项可选 `covers` 字段**（本条证据覆盖的正文位置，内容→出处可追溯）；**行内标记 〔推论〕/〔待复核〕 约定**：verified 条目含标记 → warn。
- **doc-sourced 条目必须带版本信息**（versions 非空或 evidence.run 含文档版本与访问日期），否则 warn；**oss 条目 superseded_by 指向 personal/ → error**（私有信息不出库）。
- **速查表加「环节」列**（每行对应逐环节排查的某一环）；检索脚本对中文查询词加 **2-gram 回退**（整词未命中、≥2 gram 命中才计分，权重低于整词）。
- 新增 `scripts/read_entry.py`：按二级标题切片读取条目（目录模式 / 按节输出 / --max-bytes 截断），配合消费方契约的字节预算（默认 16KB）分段读取。
- 冲突报告模板加「条目内锚点」字段（定位到节/行）；依赖声明 `requirements.txt`（pyyaml）。
