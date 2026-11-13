---
name: skillormcp
description: "触发词：判定skill还是MCP、skill和mcp怎么选、落点审计、能力错位、下沉mcp、拆分建议、新增能力该放哪、skill是否在干MCP的活、发布前核对落点。| Decides whether a capability should be a Skill or an MCP, and audits skill/MCP placement for misplacement (a skill doing MCP's job — holding credentials, connecting to an intranet or bastion, keeping stateful sessions, exposing a universal tool, or performing external write operations); reports placement, a README draft, and a starter scaffold (skill or MCP); never edits or moves files. | ADVISORY ONLY：只报建议、不改文件、不移动任何东西。"
metadata:
  agent_created: true
  version: "9"
---

# skillormcp（判定 Skill / MCP 落点 + 审计错位，建议型 / Advisory）

## 定位声明（防漂移）

- **是什么**：一把"落点尺"——给定一项能力（或一段描述），判它该进 `skills/` 还是 `mcps/`；并对仓库里已存在的 skill 做**错位审计**（是不是在干 MCP 的活：持证、连内网、有状态连接、暴露万能 tool、对外写操作），以及对 mcp 做反方向审计（万能 tool、凭证作 tool 参数）。
- **不是什么**：不是强制机制、不是 CI 门禁；也**不判断"该能力是否适合外发 / 是否内部定制"**——那是人为审核范畴，AI 无法替你做这个分类。本 skill 只负责"落点"，不做发布范围裁决。它只产出建议 + 证据 + 起步脚手架，不移动、不修改任何文件；是否落地由人定夺。
- **为什么需要它**：当仓库同时存在 `skills/` 与 `mcps/` 两个目录（或打算这样组织）时，没有一把尺，后人容易把"连内网/持证/对外写"的活塞进 skill，造成能力错位——本 skill 就是这把尺，只管落点，发布范围由人审。

## 什么时候用

- 想新增一项能力，不确定做成 skill 还是 MCP。
- Review / 提交前，想核对某个 skill 是不是"能力错位"，或某个 mcp 是否命中反模式。
- 需要一段"该放哪 + 为什么 + README 草稿文案"，或落点确定后的起步脚手架（skill / MCP 二选一）。

## 核心判定轴：缺口类型

> 用"缺口类型"而非"AI 能力"做轴——模型会变强，缺口不会消失，规则才抗老化。

### 第一步：是否命中 MCP 六门槛（能力与强制）

命中任一 → 应进 `mcps/`（同时通常还需一个 skill 规定方法）。

| # | 门槛 | 一句话判断 |
| --- | --- | --- |
| 1 | 凭证不可见 | 凭证能让 Agent 看到吗？（含沙箱、环境变量）不能 → MCP，凭证只在服务端 |
| 2 | 有状态/常驻连接 | 需要会话、token 刷新、长连接、订阅推送？ |
| 3 | 执行位置受限 | 目标系统只有特定网络/机器能访问？（内网、跳板） |
| 4 | 必须硬拦截 | 写操作、生产环境的护栏能只靠"建议"吗？不能 → MCP 层权限/审批/审计 |
| 5 | 多客户端复用 | 多个 Agent/客户端要接入同一能力？ |
| 6 | 机器级契约 | 调用需要类型校验、结构化输入输出？ |

### 第二步：是否命中技能六门槛（方法与软约束）

命中任一 → 应进 `skills/`（+ 技能内脚本）。

| # | 门槛 | 一句话判断 |
| --- | --- | --- |
| 1 | 不可逆/对外写 | 做错了能回退吗？不能 → 技能（写确认门） |
| 2 | 凭证/敏感 | 会碰客户或生产密码？→ 技能管安全边界（只写"怎么用"，不写字面凭证） |
| 3 | 隐性方法 | 换个 Agent 会做错或漏做？ |
| 4 | 确定性逻辑 | 每次重写会不一致？（解析/对账/分页去重） |
| 5 | 团队一致性 | 多人多 Agent 要同一行为？ |
| 6 | 可发现性 | 不写下来就没人知道存在？ |

### 第三步：全不命中

按频率：高频团队复用 → 本地共享库/脚本；一次性低频 → 对话内联。若只是"事实/方法清单"，优先沉淀进**知识库**而非塞进技能；若核心难点在凭证注入，归**密钥管理**（MCP 服务端 / 密钥管理系统的引用名，而非字面凭证）。

> **凭证红线（分级）**：无论落点，凭证不进对话、不进技能正文、不作为 tool 参数；由密钥管理注入 MCP 服务端或 gitignored 配置。**提及/引用**凭证（说明怎么用、`os.environ`/`${...}` 引用、从 `_secret`/`secrets.` 安全源读取）是合规的，**字面凭证值**才是红线，**凭证作为 tool 参数**属于错位。

> **门槛 4/5/6（写操作 / 多客户端 / 结构化契约）**：扫描器可自动检出"写操作"信号（requests.post/DELETE、httpx.post、kubectl apply/delete、rm -rf、DROP TABLE、TRUNCATE、terraform apply、git push、docker rm、以及 `subprocess.run(["terraform","apply"])` 这类 argv 列表写法等）；"多客户端复用""机器级契约"为软判断，扫描器不自动判定，需人工确认。

## 两种用法

### 模式 A：顾问（给定能力描述）

输入一段能力描述，输出：
1. 建议落点（`skills/` / `mcps/` / 本地库 / 内联 / 知识库 / 密钥管理）；
2. 命中了哪条门槛（引用上面的表）；
3. 一段可直接贴进 README 的草稿文案（功能说明 + 触发词）；
4. 起步脚手架指针：
   - 落点=`skills/` → 复制 `templates/skill/SKILL.md`（frontmatter 三段式骨架）+ `templates/skill/README_INDEX_SNIPPET.md`（双 README 索引条目），替换 `<...>` 占位符即可。
   - 落点=`mcps/` → 复制 `templates/mcp/`（README.md / server.py / config.example.yaml / requirements.txt / .gitignore），替换 `<mcp-name>` 等占位符即可起步。

**示例（输入 → 输出要点）**：
> 输入："我想做一个能连公司内网 K8s、用跳板机 ssh 进去跑 kubectl 排障的能力。"
> 输出：落点建议 `mcps/`（命中 门槛1 凭证不可见、门槛3 执行位置受限、门槛4 必须硬拦截）；附 README 草稿；并提示参考 `templates/mcp/` 脚手架。

> 另一示例（落点=skills/）："我想做一个清理代码注释、只描述现状的 skill。"
> 输出：落点建议 `skills/`（命中 技能门槛3 隐性方法、门槛5 团队一致性）；附 README 草稿；并提示参考 `templates/skill/` 骨架。

### 模式 B：审计（给定 skill/mcp 目录或全仓）

调用 `scripts/audit_placement.py <目录或 skills/>`，扫描 `SKILL.md` 与 `scripts/` 等文本文件（含 `.env`/`.toml`/`.ini`/`.cfg`/`.conf`）：
- 匹配"MCP 形"信号：
  - **凭证**：字面凭证值（password=/secret=/api_key=/AKIA/sk-/私钥头/Bearer/JWT/连接串内嵌 `user:pass@host`/`Authorization: Basic`（兼容 `"Authorization":` 字典形式与 curl 裸形式）等，红线；右侧需为引号值、非引用、非类型标注、非安全源调用）vs 提及/引用（说明怎么用、`os.environ`/`${...}`/`_secret`/类型标注，提示，合规）；**遮罩仅在判定为字面值行上进行**，非字面值行（如 `token = _issue_token(...)`）不误遮。
  - **内网/连通性**：ssh/kubectl/完整四段私有 IP/跳板主机/vpn/专线（红线；版本号如 `10.15.2`/`3.10.4.5` 不误报）；
  - **有状态/常驻**：token 刷新/长连接/订阅/websocket（红线）；
  - **万能 tool**：execute_sql/run_shell/os.system/eval（不含 `model.eval()` 方法调用），红线；
  - **写操作**：requests.post/DELETE、httpx.post、kubectl apply/delete、rm -rf、DROP TABLE、TRUNCATE、terraform apply、git push、docker rm，以及 `subprocess.run(["terraform","apply"])` 这类 argv 列表写法（列表先拼接为字符串再匹配），红线。
- 对 **Python 的 mcp 目录**（识别到 FastMCP/mcp.run/@mcp.tool/@server.tool/server.tool）额外做**反方向审计**：暴露万能 tool、凭证作 tool 参数（内联 keyword 形式 `password=db_pass` 也检）。**tool 函数参数名分两级**（无视类型标注）：**强**（password/secret/api_key/private_key/access_token/client_secret/bearer/credential 等，几乎确定是凭证）→ 红线 **MCP反模式**；**弱**（裸 `token`、`credentials`，可能是分页/会话 token 等合法用途）→ 降级为 **提示** **MCP可疑参数**，结论中请人工确认；**白名单**（`page_token`/`next_token`/`cursor`/`csrf_token`）明确不报。优先用 **AST 解析 `@*.tool` 装饰函数**的参数名；**若文件语法错误 AST 失败，回退正则粗扫并明确提示"该文件无法 AST 解析，已回退正则（可能漏检跨行签名），请人工复核"**——绝不静默通过（退出码不再为 0）。**非 Python（TypeScript/JavaScript 等）源文件不做反向参数审计**（AST 仅支持 Python；团队只用 Python，忽略其它语言），普通规则（写操作/内网/万能 tool）仍生效；但报告会在「人工确认项」明确给出提示"非 Python 的 MCP 文件, 反向参数审计未执行, 请人工复核"，不会与"未发现红线"混同。命中反模式/写操作/万能 tool 时给出 **MCP 设计人工确认清单（逐条）**：① 一个 API 一个 tool（避免万能 tool）？② 方法是否写进 tool description（应留给 skill）？③ 是否纯计算也做成了 MCP（本可做成 skill）？④ 写 tool 是否带限时一次性审批令牌 + 审计日志？
- 对 **SKILL.md** 含写操作/有状态/万能 tool 信号但全文无"真正确认步骤"短语（如"执行前需经用户确认"）时，提示补充确认门（不可逆操作需人工确认）；仅出现裸"确认"字样不构成确认门。
- 报告对**字面凭证值脱敏**（只显 `key=***`，引号内整串含空格口令、连接串密码、`Authorization` 字典/裸形式均遮），不回显密钥原文。
- 输出发现清单（file:line + 严重度 + 类别 + 脱敏后信号），并给结论：
  - 命中"凭证字面值"(红线) → **必须移除硬编码凭证，改用密钥管理（环境变量/密钥库注入）**，这不是"下沉 MCP"能解决的；
  - 命中其它红线 → **疑似能力错位/反模式，建议下沉 `mcps/` 或拆分写操作护栏**；
  - 含内网/私有地址 → **仅作事实信号提示，由人确认该内容是否适合放在公开仓库**（本 skill 不做此项裁决）。

> 注：只豁免扫描器【自身包路径】（其 SKILL.md/scripts/templates 不纳入审计，保证自审干净，退出码 0）。**被审计 skill 自带的 `templates/` 同样会被扫描**——不存在"按目录名全局豁免"的绕过口。

## 审计报告格式

```
[skill/mcp 名] | 结论: 落点建议(skills/mcps/本地库/内联/知识库/密钥管理) 或 错位告警 | 置信: 高/中/低
命中门槛: <第X步第Y条>
证据: <file:line 逐字引用(字面凭证已脱敏)>
人工确认项: <含内网/私有地址信号, 是否适合公开仓库由人判断; 非Python文件或解析失败回退的提示>
README草稿: <一段文案> 或 脚手架指针: <templates/...>
```

## 诚实口径 / 不自欺不变量

- **只报不改**：绝不自动移动/修改文件；处置权留给人。
- **行号可追溯**：摘录逐字引用原文，输出前回查行号与原文对得上，不要凭记忆报行号。
- **置信封顶**：未做实证的判定置信最高「中」；治理/红线类（凭证外发、对外写）命中即可高置信告警。
- **发布范围不裁决**：本 skill 不判断"能力是否内部/定制/应外发"——那是人为审核，AI 无法替你分类。扫描器命中内网/私有地址时只作事实信号提示，结论交由人确认。
- **自身包豁免，非全局跳过**：扫描器只豁免其自身包路径（自审干净），被审计 skill 自带的 `templates/` 同样会被扫描；不要将"自审干净"误当成"全局不审计"。
- **自检**：`scripts/audit_placement.py --self-test` 用内置正向/反向 AST/脱敏/后缀/反例/绕过口/argv 列表/字典 Authorization/确认门断言；改匹配规则后必须先过 self-test（含解析失败回退、参数名强弱分级与白名单）。

## 自我归类

- 本 skill 类别：**0→1**（落点判定规则 + 审计模板 + 起步脚手架），persist。
- 默认**纳入审计**（可被自身审计，仅跳过扫描器自身包路径）；自评只标类别、不产处置建议。
