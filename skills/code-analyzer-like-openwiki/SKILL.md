---
name: code-analyzer-like-openwiki
description: "系统化分析代码库，提取架构模式、依赖关系与核心业务链路，生成含 Mermaid 图表的开放式技术 Wiki。Invoke when 用户要求分析代码库/生成技术文档/梳理架构/画架构图。"
---

# OpenWiki Codebase Analyzer — 代码库分析 → 技术 Wiki 生成

> **上一步**: 无（由用户请求触发）| **下一步**: 无（Wiki 文档生成完成后通知）

## 价值门禁

这不是"读几个文件写个 README"——是**四阶段递进式代码库分析**，从依赖图到系统架构，产出严格源码溯源的开放式技术 Wiki（含 Mermaid 交互图表），并可一键拉起 Docsify 本地预览站点（零 Node.js 依赖）。
所有架构推导与流程说明必须附带真实文件路径与行号（如 `src/auth/jwt.py:42-85`），严禁凭空构想。

## Core Execution Protocol (Workflow)

按以下严格的阶段性流程递进分析，不得颠倒顺序：

### Phase 1: Ingestion & Dependency Graphing
1. 过滤忽略文件（基于 `.gitignore`、构建产物及测试代码）。
2. 解析代码结构，提取类 (Classes)、函数 (Functions)、接口 (Interfaces) 及模块关系。
3. 构建全局依赖图（Dependency Graph），确定入口文件与关键核心模块。

### Phase 2: Bottom-Up Multi-stage Analysis
1. **[代码块级]** 遍历核心模块，生成函数/类级别的职责摘要及输入输出说明。
2. **[模块级]** 聚合关联模块，分析模块间交互逻辑，生成局部架构图与数据流。
3. **[系统级]** 结合入口文件与全局依赖图，梳理整体设计模式、技术栈架构与关键业务链路。

### Phase 2.5: Feature Story Mining（用例驱动分析）
从纯技术视角升维至**产品与业务视角**，建立 **"功能点 (Story) → 业务流程 (Workflow) → 代码链路 (Code Trace)"** 的完整映射。

1. **PR/Git 驱动识别 (PR-Driven Mining)**
   - 提取合入主干（Merged）且包含 `feat/feature/story` 的 PR 或 Commit。
   - **语义借用：** 使用 PR Title 和 Description 提取 Story 名称、用户角色 (Actor) 和业务目标 (Goal)。
   - **作用域限定：** 将该 PR 包含的变更文件列表 (Changed Files) 作为代码追踪的精确搜寻范围。
2. **异步链路拼接 (Async Lineage)**
   - 当分析包含 Kafka / RabbitMQ 等消息队列的代码时，扫描 Producer 的发送 API 与 Consumer 的监听注解。
   - **Topic/Queue 匹配：** Producer 发送的 Topic 与 Consumer 监听的 Topic 一致 → 判定为连续链路。
   - **Payload Schema 匹配：** 匹配 Event/DTO 的数据类型。
   - **链路重建：** 在内存中构建 Event Topology Graph，将 Producer 上游与 Consumer 下游在异步边界处连接。
3. **双源交叉校验 (Dual-Source Verification)**
   - 仅当 PR 描述中的功能在对应 Git Diff/源码中确实存在实现时，才可写入主流程。
   - 用 PR 涉及的真实代码 Diff 校验 PR 描述，防止大模型产生幻觉。

### Phase 3: Documentation Synthesis
按标准的 OpenWiki Schema 输出全套 Markdown 文档，并在涉及流程/架构处内嵌 Mermaid.js 图表。Feature Story 文档输出至 `stories.md`。

### Phase 4: Local Preview Generator（Docsify + Python，默认启用）

> **默认执行**：Wiki 文档生成后自动生成本地预览站点。
> **关闭方式**：用户请求中包含"不预览/不要浏览/跳过预览/no preview"等意图时跳过本阶段。

在 Wiki 文档生成完成后，自动生成 Docsify 站点文件并拉起本地预览服务，实现零 Node.js 依赖的跨平台交互式 Wiki 浏览。

#### Step 1: 依赖检测 (Dependency Check)
1. 检测系统是否已安装 Python 3：
   - 运行 `python3 --version`（macOS/Linux）或 `python --version`（Windows）
   - **已安装** → 进入 Step 2
   - **未安装** → 尝试自动安装：
     - macOS: `brew install python3`（若 brew 不可用则失败）
     - Linux (apt): `sudo apt-get install -y python3`
     - Linux (yum): `sudo yum install -y python3`
     - Windows: 提示用户安装（无法自动静默安装）
   - **自动安装失败** → 输出手动下载地址并终止 Phase 4：
     ```
     Python 未安装且自动安装失败，请手动下载安装：
     - 官方下载: https://www.python.org/downloads/
     - 安装后重新运行预览命令: python -m http.server 8000
     ```

#### Step 2: 生成 Docsify 站点文件
在输出目录 `<项目根>/docs/wiki/<YYYYMMDD-wiki>/` 下生成 3 个文件：

**`index.html`** — Docsify 入口（含 Mermaid 渲染 + 代码高亮 + 全文搜索）：

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <title>OpenWiki Architecture Docs</title>
  <meta http-equiv="X-UA-Compatible" content="IE=edge,chrome=1" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0, minimum-scale=1.0">
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/docsify@4/lib/themes/vue.css">
</head>
<body>
  <div id="app"></div>
  <script>
    window.$docsify = {
      name: 'OpenWiki',
      repo: '',
      homepage: 'README.md',
      loadSidebar: true,
      subMaxLevel: 3,
      search: { placeholder: '搜索文档...' }
    };
  </script>
  <script src="https://cdn.jsdelivr.net/npm/docsify@4/lib/docsify.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/docsify/lib/plugins/search.min.js"></script>
  <script type="module">
    import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.mjs';
    mermaid.initialize({ startOnLoad: true });
    window.mermaid = mermaid;
  </script>
  <script src="https://cdn.jsdelivr.net/npm/docsify-mermaid@2/dist/docsify-mermaid.js"></script>
</body>
</html>
```

**`_sidebar.md`** — 自动生成的导航侧边栏（根据实际生成的文档编排）：

```markdown
* **概览 (Overview)**
  * [项目概览与技术栈](overview.md)

* **系统架构 (Architecture)**
  * [架构设计与分层](architecture.md)
  * [领域模型与实体关系](domain-models.md)

* **业务链路 (Workflows & Stories)**
  * [核心工作流](workflows.md)
  * [Feature Stories](stories.md)

* **API 参考 (API Reference)**
  * [模块定义与源码索引](api-reference.md)
```

**`README.md`** — 聚合首页，按顺序引入各文档章节：

```markdown
# OpenWiki — [项目名]

> 自动生成于 YYYY-MM-DD | 源码溯源技术 Wiki

[include: overview.md]
[include: architecture.md]
[include: domain-models.md]
[include: workflows.md]
[include: stories.md]
[include: api-reference.md]
```

> 实际生成时将各 `.md` 文件内容拼接至 `README.md`（Docsify 不支持 include 语法，需物理拼接）。

#### Step 3: 启动本地预览服务
在输出目录下运行 Python 内置 HTTP 服务器（全平台通用）：

```bash
cd <项目根>/docs/wiki/<YYYYMMDD-wiki>/
python3 -m http.server 8000   # macOS/Linux
python -m http.server 8000    # Windows
```

#### Step 4: 输出预览卡片
在 Skill 回复结尾输出：

```
OpenWiki 文档已成功生成并启动本地预览！

本地查看交互式 Wiki 网页：
1. 终端切换到文档目录: cd docs/wiki/<YYYYMMDD-wiki>/
2. 启动服务: python3 -m http.server 8000
3. 浏览器访问: http://localhost:8000
   （支持 Mermaid 架构图渲染、侧边栏导航、全文搜索）
```

## Output Location

生成的 Wiki 文档存放到**项目根目录**下，路径规则：

```
<项目根>/docs/wiki/<YYYYMMDD-wiki>/
```

- **目录名格式**：`<time(xxxxxxxx)-wiki>`，其中 `xxxxxxxx` 为当前日期的 8 位数字（年月日）。
- **示例**：`docs/wiki/20260730-wiki/`
- **多次生成**：同一天多次分析覆盖同一目录；不同日期生成各自独立目录。
- **目录结构**：

```
docs/wiki/20260730-wiki/
├── overview.md              # Overview & Getting Started
├── architecture.md          # System Architecture（含全局架构图）
├── domain-models.md         # Domain Models & Core Concepts（含 ER 图）
├── workflows.md             # Key Workflows & Call Chains（含时序图）
├── stories.md               # Feature Stories（含异步链路时序图）
├── api-reference.md         # API & Module Reference（含源码索引）
├── index.html               # Docsify 入口（Phase 4 默认生成）
├── _sidebar.md              # Wiki 导航侧边栏（Phase 4 默认生成）
└── README.md                # 聚合首页（Phase 4 默认生成）
```

> 若项目根目录下 `docs/wiki/` 不存在，自动创建。

## Wiki Output Schema

生成的 Wiki 文档必须严格包含以下结构化章节：

1. **Overview & Getting Started**
   - 项目核心目标与技术栈选型
   - 环境要求与本地极简启动指南
2. **System Architecture**
   - 全局架构图 (Mermaid `graph TD`)
   - 分层设计理念（如 MVC、DDD、微服务）
3. **Domain Models & Core Concepts**
   - 核心数据结构与实体关系图 (Mermaid `erDiagram`)
4. **Key Workflows & Call Chains**
   - 1-3 个最核心业务链路的时序图 (Mermaid `sequenceDiagram`)
   - 关键代码执行路径推演
5. **Stories & Features**（用例驱动维度）
   - 以 Feature Story 为粒度，建立 "功能点 → 业务流程 → 代码链路" 映射
   - 每个 Story 包含：业务场景、时序图（含异步消息 Broker）、执行追踪表、异常处理
   - 详见 [Feature Story Schema](#feature-story-document-schema)
6. **API & Module Reference**
   - 核心模块定义与公共 API 规格说明
   - **真实源码索引：** 所有核心逻辑必须标注源码文件路径与对应行号范围

## Feature Story Document Schema

每个生成的 Feature Story 遵循以下结构：

```markdown
### Feature Story: [功能名称]

> **Story ID:** STORY-XXX-001
> **Core Entry:** `src/controllers/xxx.controller.ts -> handleXxx()`
> **Affected Subsystems:** `ServiceA`, `ServiceB`, `MQ`, `ServiceC`

#### 1. 业务场景描述 (Business Context)
- **Actor:** 触发该流程的角色（如未登录用户 / Cron 任务）
- **Goal:** 该流程要达成的终态业务目标
- **Pre-conditions:** 触发该流程的前置条件

#### 2. 核心执行时序图 (Feature Sequence Diagram)
（Mermaid sequenceDiagram，异步链路须画出 Message Broker 节点，
 标注 Publish 与 Consume；步骤表格标明 [MQ Produce] 与 [MQ Consume] 锚点）

#### 3. 核心步骤与代码追踪 (Execution Trace)
| 步骤 | 环节类型 | 业务逻辑说明 | 关键代码/函数 (文件+行号) | 影响数据/状态 |
|------|----------|-------------|--------------------------|--------------|
| Step 1 | HTTP API | 请求入参校验与鉴权 | `src/controllers/auth.ts:15` | - |
| Step 2 | MQ Produce | 投递消息至消息队列 | `src/producers/event.ts:30` | Topic: `user-events` |
| Step 3 | MQ Consume | 订阅消息并更新状态 | `src/consumers/notify.ts:45` | DB: `users` 表更新 |

#### 4. 边界条件与异常处理 (Edge Cases & Errors)
- **异常 A:** 处理说明及对应代码捕获位置（如 `src/errors/auth.ts:12`）。
```

## Execution Constraints & Safeguards

| 约束 | 说明 |
|------|------|
| **Strict Source Grounding** | 所有架构推导与流程说明必须附带真实的文件路径（例如 `src/auth/jwt.py:42-85`），严禁凭空构想未在源码中出现的逻辑。 |
| **Token Efficiency** | 优先基于符号表（Symbol Table）和依赖关系大纲进行全局分析；仅在撰写特定模块细节时读取该文件完整源码。 |
| **Diagram Rules** | Mermaid 图表必须保持语法简明，节点命名清晰，避免出现语意闭环或语法解析错误。 |
| **Noise Reduction** | 忽略纯 UI 样式文件、自动生成的代码、配置文件及测试用例，将 Token 资源集中于核心业务逻辑。 |
| **Async Lineage** | 包含 Kafka/RabbitMQ 的链路必须扫描 Producer/Consumer 的 Topic 匹配，在 Mermaid 中画出 Broker 节点，步骤表标明 `[MQ Produce]`/`[MQ Consume]` 锚点。 |
| **Dual-Source Verification** | Feature Story 的每个功能点必须经 PR 描述 + Git Diff 双源交叉校验，仅当源码中确实存在实现时才写入主流程。 |

## Common Mistakes

| 错误 | 后果 | 修复 |
|------|------|------|
| 跳过 Phase 1 直接读源码 | 缺乏全局视角，遗漏关键依赖 | 先构建依赖图，再按需深入 |
| 架构图节点过多导致 Mermaid 渲染失败 | 图表不可读 | 每图节点 ≤15，超限拆分为子图 |
| 文档不附源码路径 | 无法溯源验证 | 每个核心结论附 `文件路径:行号范围` |
| 一次性读取所有文件源码 | Token 溢出 | 符号表优先，按需读取细节 |
| 分析测试/UI/配置文件 | Token 浪费在非核心逻辑 | Phase 1 即过滤，聚焦业务代码 |
| 文档输出到任意位置而非 `docs/wiki/<YYYYMMDD-wiki>/` | 文档散落难找 | 严格按 Output Location 规则写入项目根目录 |
| 异步链路在 Producer 处断裂 | Story 时序图不完整，遗漏 Consumer 逻辑 | 扫描 Topic/Queue 匹配，在异步边界处连接链路 |
| Story 采信 PR 描述但源码无对应实现 | 大模型幻觉，文档失真 | 双源交叉校验：PR 描述 + Git Diff 均确认才写入 |
| Story 时序图未画出 Message Broker | 异步流程不可读 | 必须画出 Broker 节点，标注 Publish/Consume |
| Phase 4 未检测 Python 就直接启动服务 | 服务启动失败 | 先 `python3 --version` 检测，失败则提示手动下载 https://www.python.org/downloads/ |
| README.md 用 include 语法而非物理拼接 | Docsify 无法渲染内容 | Docsify 不支持 include，需将各 .md 内容直接拼入 README.md |
| _sidebar.md 链接与实际文件名不匹配 | 侧边栏导航 404 | 生成后校验每个链接对应的文件确实存在 |
