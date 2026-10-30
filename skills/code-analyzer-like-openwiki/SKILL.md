---
name: code-analyzer-like-openwiki
description: "触发词：分析代码库、生成技术文档、梳理架构、画架构图、新人上手、onboarding、第一篇PR、技术教程、了解项目、代码库百科、架构图、依赖关系图、feature story、新人怎么上手这个项目。只要用户想理解或上手一个代码库（即使没明说“生成文档”），都应使用本 skill。| Systematic codebase analysis that produces an open technical Wiki with Mermaid diagrams, dependency graphs, and feature stories; supports an onboarding mode generating a newcomer tutorial (local setup, first PR, troubleshooting). 本 skill 只读不改、只产出分析文档与架构图，用于理解或上手代码库，不修改代码。"
metadata:
  version: "3"
---

# OpenWiki Codebase Analyzer — 代码库分析 → 技术 Wiki 生成

> **上一步**: 无（由用户请求触发）| **下一步**: 无（Wiki 文档生成完成后通知）

## 价值门禁

这不是"读几个文件写个 README"——是**四阶段递进式代码库分析**，从依赖图到系统架构，产出严格源码溯源的开放式技术 Wiki（含 Mermaid 交互图表），并可一键拉起 Docsify 本地预览站点（零 Node.js 依赖）。
所有架构推导与流程说明必须附带真实文件路径与行号（如 `src/auth/jwt.py:42-85`），严禁凭空构想。

## 运行模式 (Mode)

本 skill 提供两种产出形态，按用户意图自动判定：

- **`reference`（默认）**：现有四阶段产出代码百科（overview / architecture / domain-models / workflows / stories / api-reference）。用于"理解代码结构"，平铺为参考手册。
- **`onboarding`**：在 reference 的代码分析能力之上，叠加"新人上手教程轨道"。产出**按阅读顺序**的叙事文档，终点目标是：**纯新手（不限开发语言）能本地起环境、提第一个 PR、并上手解决项目里的一类实际问题**。

**模式触发规则**：

- 用户意图含「新人 / onboarding / 上手 / 第一篇PR / get started / 技术教程 / 了解项目 / 让新人看懂」等词 → `onboarding`。
- 否则 → `reference`（保持对其它代码库的通用性，行为不变）。

两种模式共用 Phase 1–2.5 与 Phase 4；**差异在 Phase 3（文档合成内容）与 companion 仓摄入范围**（见各 Phase 标注）。

## Core Execution Protocol (Workflow)

按以下严格的阶段性流程递进分析，不得颠倒顺序：

### Phase 1: Ingestion & Dependency Graphing
1. 过滤忽略文件（基于 `.gitignore`、构建产物及测试代码）。
2. 解析代码结构，提取类 (Classes)、函数 (Functions)、接口 (Interfaces) 及模块关系。
3. 构建全局依赖图（Dependency Graph），确定入口文件与关键核心模块。
4. **[onboarding] Companion Repos 摄入**：除主代码仓外，额外识别并摄入：
   - **设计文档仓**（含 ADR、设计文档、决策记录、DATA-MODEL 等）——这是"为什么这样设计"的唯一真相源；
   - **脚手架 / 部署 / env 仓**（含本地起环境脚本、生产部署、证书、网关配置）——这是"怎么本地跑起来"的真相源；
   - **各仓根 README / CONTRIBUTING / Makefile**。
   - 噪音过滤须**保留** ADR、设计文档、部署脚本、Makefile，不得当作"非核心"丢弃。reference 模式可仅摄入代码仓。

### Phase 2: Bottom-Up Multi-stage Analysis
1. **[代码块级]** 遍历核心模块，生成函数/类级别的职责摘要及输入输出说明。
2. **[模块级]** 聚合关联模块，分析模块间交互逻辑，生成局部架构图与数据流。
3. **[系统级]** 结合入口文件与全局依赖图，梳理整体设计模式、技术栈架构与关键业务链路。

### Phase 2.5: Feature Story Mining（用例驱动分析）
从纯技术视角升维至**产品与业务视角**，建立 **"功能点 (Story) → 业务流程 (Workflow) → 代码链路 (Code Trace)"** 的完整映射。

1. **PR/Git 驱动识别 (PR-Driven Mining)**
   - 优先提取合入主干（Merged）且包含 `feat/feature/story` 的 PR 或 Commit。
   - **语义借用：** 使用 PR Title 和 Description 提取 Story 名称、用户角色 (Actor) 和业务目标 (Goal)。
   - **作用域限定：** 将该 PR 包含的变更文件列表 (Changed Files) 作为代码追踪的精确搜寻范围。
   - **[repo-agnostic 回退]** 若提交信息为**日期快照式**或无 `feat` 前缀（如 SDP 的 `20261028`），**不强行匹配 PR 标题**；改为从 Phase 2 已溯源的关键业务链路归纳 Story（名称/Actor/Goal 由链路语义抽取），并将该链路涉及的源码文件作为代码追踪范围。此回退路径必须仍过下方双源校验。
2. **异步链路拼接 (Async Lineage)**
   - 当分析包含 Kafka / RabbitMQ 等消息队列的代码时，扫描 Producer 的发送 API 与 Consumer 的监听注解。
   - **Topic/Queue 匹配：** Producer 发送的 Topic 与 Consumer 监听的 Topic 一致 → 判定为连续链路。
   - **Payload Schema 匹配：** 匹配 Event/DTO 的数据类型。
   - **链路重建：** 在内存中构建 Event Topology Graph，将 Producer 上游与 Consumer 下游在异步边界处连接。
3. **双源交叉校验 (Dual-Source Verification)**
   - 仅当 PR 描述（或回退路径下的业务链路归纳）在对应 Git Diff/源码中确实存在实现时，才可写入主流程。
   - 用 PR 涉及的真实代码 Diff 校验 PR 描述，防止大模型产生幻觉。

### Phase 3: Documentation Synthesis
按标准的 OpenWiki Schema 输出全套 Markdown 文档，并在涉及流程/架构处内嵌 Mermaid.js 图表。Feature Story 文档输出至 `stories.md`。

### Phase 3b: Onboarding Synthesis（仅 `onboarding` 模式）

在 reference 文档之外，**额外**按"阅读顺序"而非"类型分组"产出新人教程轨道。所有 setup 步骤须可复现，并在 Phase 4 前经自验证（见下）。

产出文件（命名带序号以保证顺序，**与 reference 的 6 篇文档共存于同一目录**）：

- **`00-preface.md`**：受众声明 = 纯新手、不限开发语言；读完能做什么（本地起环境 / 提第一个 PR / 上手解决一类问题）；文档使用约定与阅读顺序说明。
- **`01-mental-models.md`**：核心心智模型（**溯源 ADR / 设计文档**）：三层能力/接入/自身边界；hub 是唯一权限权威；WS 替代消息队列的设计取舍；镜像仓库可替换；Hub-Spoke 执行模型。每条附设计文档或源码 `file:line`。
- **`02-concept-primers.md`**：跨语言 primer（够用就行，**不假设读者懂该语言**）：Go modules / Vue3 SPA / K8s-Kind / Gin-GORM / Keycloak 各自的"是什么、在这项目里干嘛、新人最该注意什么"。
- **`03-local-setup.md`**：基于 companion env 仓的逐步起环境（如 `local-kind-dev/deploy-local.sh`），每步含「预期结果」与「验证命令」，末尾给可达的本地访问地址与冒烟校验清单。
- **`04-reading-path.md`**：新人第一个该读的 5 个文件及顺序、为什么，附 `file:line` 与一句话职责。
- **`05-first-pr.md`**：一个具体、安全、低风险的首任务（如补文档 / 加测试 / 小修），含完整操作步骤 + 验证 gate（依仓而定：`go build/vet/test`、`pnpm build/test`）。
- **`06-troubleshooting.md`**：已知雷区与排障（如 WS 回连 404=target 未注册、列宽装不下状态枚举、buildkitd 配置不热生效、证书/SAN），每条给「现象 → 原因 → 解决 → 相关 `file:line`」。
- **`07-whos-who.md`**：模块 / 子系统归属与「去哪问」，可引用设计文档中的 owner / 决策记录。

> **自验证（外部验证轴，强制）**：Phase 3b 完成后、Phase 4 前，agent 必须**实际校验**教程中的可执行指令——确认被引用的文件路径真实存在、setup 命令在仓库中真实可用、验证 gate 命令拼写正确、本地访问地址与端口与 env 脚本一致。无法在本环境执行的步骤（如需真实 K8s 集群）须显式标注「需人工/真实环境确认」，**严禁假装通过**。这是"新人能上手"在当前环境下的最强可验证代理；最终验收以真实新人走查为准（由用户指定评判者走查并反馈）。

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
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/docsify@latest/lib/themes/vue.css">
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
  <script src="https://cdn.jsdelivr.net/npm/docsify@latest/lib/docsify.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/docsify@latest/lib/plugins/search.min.js"></script>
  <script type="module">
    import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@latest/dist/mermaid.esm.mjs';
    mermaid.initialize({ startOnLoad: true });
    window.mermaid = mermaid;
  </script>
  <script src="https://cdn.jsdelivr.net/npm/docsify-mermaid@latest/dist/docsify-mermaid.js"></script>

  <!-- 右上角「重新生成」按钮：调用本地 wiki_server.py 的 /refresh 端点刷新依赖 -->
  <button id="wiki-refresh-btn" style="position:fixed;top:12px;right:12px;z-index:9999;padding:6px 12px;border:1px solid #4a90d9;border-radius:6px;background:#fff;color:#4a90d9;cursor:pointer;font-size:13px;box-shadow:0 1px 4px rgba(0,0,0,.15)">⟳ 重新生成</button>
  <span id="wiki-toast" style="position:fixed;top:14px;right:120px;z-index:9999;font-size:13px;color:#333"></span>
  <script>
    (function () {
      var btn = document.getElementById('wiki-refresh-btn');
      var toast = document.getElementById('wiki-toast');
      if (!btn) return;
      btn.onclick = function () {
        btn.disabled = true; btn.textContent = '刷新中…';
        fetch('/refresh').then(function (r) { return r.json(); }).then(function (j) {
          toast.textContent = (j.status === 'pass')
            ? '✅ 已更新为最新'
            : '⚠️ ' + (j.message || j.status);
        }).catch(function (e) {
          toast.textContent = '❌ 刷新失败: ' + e.message;
        }).then(function () {
          btn.disabled = false; btn.textContent = '⟳ 重新生成';
          setTimeout(function () { toast.textContent = ''; }, 5000);
        });
      };
    })();
  </script>
</body>
</html>
```

> **Runtime Dependencies (`@latest` + 安全网)** — 本 skill 有意使用 `@latest`（当前解析：docsify 5.1.0 / mermaid 12.2.0 / docsify-mermaid 2.0.1），使生成的 Wiki 始终拉取各库最新版本，零版本维护。
>
> 安全网：同目录的 `wiki_refresh.py` 每次刷新都会重新解析 `@latest` 并做**资产级健康检验**（CDN 可达 + 含预期全局符号）；若 `@latest` 资产不可用，自动**回退到上次已知可用版本**（首次无历史时种子为 `4.13.1` / `10.9.8` / `2.0.1`），保证 Wiki 永远可看。
>
> 诚实边界：资产级检验无法在没有无头浏览器时验证「新大版本是否破坏模板 API」。当 docsify / mermaid 发布破坏性大版本时，`index.html` 模板（如 `window.$docsify` 配置、`search` 插件路径、`mermaid.initialize`）可能失效——此时安全网会检测到并回退，但 Wiki 不会自动适配新大版本，需人工更新模板。升级前务必实际打开页面确认侧边栏 / 搜索 / 至少一个 Mermaid 图正常渲染。

**`_sidebar.md`** — 自动生成的导航侧边栏（根据实际生成的文档编排）：

- **`reference` 模式**（按类型分组）：

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

- **`onboarding` 模式**（按阅读顺序，含 00–07 教程轨道 + reference 文档作为深入参考）：

```markdown
* **新人上手教程（建议按顺序读）**
  * [00 · 序言：你能做到什么](00-preface.md)
  * [01 · 核心心智模型](01-mental-models.md)
  * [02 · 跨语言概念 primer](02-concept-primers.md)
  * [03 · 本地起环境](03-local-setup.md)
  * [04 · 该先读哪 5 个文件](04-reading-path.md)
  * [05 · 你的第一个 PR](05-first-pr.md)
  * [06 · 排障与雷区](06-troubleshooting.md)
  * [07 · 模块归属与去哪问](07-whos-who.md)

* **深入参考（按需查阅）**
  * [项目概览与技术栈](overview.md)
  * [架构设计与分层](architecture.md)
  * [领域模型与实体关系](domain-models.md)
  * [核心工作流](workflows.md)
  * [Feature Stories](stories.md)
  * [模块定义与源码索引](api-reference.md)
```

**`README.md`** — 聚合首页，按顺序引入各文档章节：

- **`reference` 模式**：

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

- **`onboarding` 模式**（教程轨道在前，深入参考在后）：

```markdown
# OpenWiki — [项目名] · 新人上手教程

> 自动生成于 YYYY-MM-DD | onboarding 模式 | 纯新手按顺序读到 [07 · 模块归属与去哪问] 即可动手

[include: 00-preface.md]
[include: 01-mental-models.md]
[include: 02-concept-primers.md]
[include: 03-local-setup.md]
[include: 04-reading-path.md]
[include: 05-first-pr.md]
[include: 06-troubleshooting.md]
[include: 07-whos-who.md]
[include: overview.md]
[include: architecture.md]
[include: domain-models.md]
[include: workflows.md]
[include: stories.md]
[include: api-reference.md]
```

> 实际生成时将各 `.md` 文件内容拼接至 `README.md`（Docsify 不支持 include 语法，需物理拼接）。onboarding 模式请按上方顺序拼接，保证 README 首页即为教程序章。

#### Step 3: 启动本地预览服务（含 /refresh 刷新端点）
将本 skill 的 `scripts/wiki_server.py` 与 `scripts/wiki_refresh.py` 复制到输出目录，并运行服务器（纯标准库，全平台通用）：

```bash
cd <项目根>/docs/wiki/<YYYYMMDD-wiki>/
cp <本skill目录>/scripts/wiki_server.py <本skill目录>/scripts/wiki_refresh.py .
python3 wiki_server.py 8000   # macOS/Linux
python wiki_server.py 8000    # Windows
```

> 该服务器在静态托管 Wiki 的同时提供 `GET /refresh` 端点，供浏览器右上角「重新生成」按钮调用：重新解析 `@latest`、做资产级健康检验、必要时回退到已知可用版本。

#### Step 4: 输出预览卡片
在 Skill 回复结尾输出：

```
OpenWiki 文档已成功生成并启动本地预览！

本地查看交互式 Wiki 网页：
1. 终端切换到文档目录: cd docs/wiki/<YYYYMMDD-wiki>/
2. 启动服务: python3 wiki_server.py 8000
3. 浏览器访问: http://localhost:8000
   （支持 Mermaid 架构图渲染、侧边栏导航、全文搜索）
4. 右上角「⟳ 重新生成」按钮：一键刷新依赖到 @latest（资产异常时自动回退）
```

> `onboarding` 模式额外提示：建议从 `00-preface.md` 开始按顺序阅读；若只想查架构细节，用侧边栏「深入参考」分组。

## Output Location

生成的 Wiki 文档存放到**项目根目录**下，路径规则：

```
<项目根>/docs/wiki/<YYYYMMDD-wiki>/
```

- **目录名格式**：`<time(xxxxxxxx)-wiki>`，其中 `xxxxxxxx` 为当前日期的 8 位数字（年月日）。
- **示例**：`docs/wiki/20260730-wiki/`
- **多次生成**：同一天多次分析覆盖同一目录；不同日期生成各自独立目录。
- **目录结构**（`onboarding` 模式，在 reference 的 6 篇基础上追加 00–07）：

```
docs/wiki/20260730-wiki/
├── 00-preface.md           # [onboarding] 序言：受众与终态目标
├── 01-mental-models.md     # [onboarding] 核心心智模型（溯源 ADR/设计文档）
├── 02-concept-primers.md   # [onboarding] 跨语言概念 primer
├── 03-local-setup.md       # [onboarding] 本地起环境（逐步 + 验证）
├── 04-reading-path.md      # [onboarding] 新人先读哪 5 个文件
├── 05-first-pr.md          # [onboarding] 第一个 PR 走查 + gate
├── 06-troubleshooting.md   # [onboarding] 排障与雷区
├── 07-whos-who.md          # [onboarding] 模块归属与去哪问
├── overview.md             # Overview & Getting Started
├── architecture.md         # System Architecture（含全局架构图）
├── domain-models.md        # Domain Models & Core Concepts（含 ER 图）
├── workflows.md            # Key Workflows & Call Chains（含时序图）
├── stories.md              # Feature Stories（含异步链路时序图）
├── api-reference.md        # API & Module Reference（含源码索引）
├── index.html              # Docsify 入口（Phase 4 默认生成，含「重新生成」按钮）
├── _sidebar.md             # Wiki 导航侧边栏（模式感知：分组 / 阅读顺序）
├── wiki_server.py          # 本地预览服务器 + /refresh 端点（从本 skill 复制）
└── wiki_refresh.py         # 依赖刷新 + 健康检验 + 回退（从本 skill 复制）
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

### Onboarding Track Schema（`onboarding` 模式，按阅读顺序产出 00–07）

每篇文档除通用「严禁凭空构想、附 `file:line`」约束外，另有专用要求：

- **00-preface**：明确受众（纯新手、不限语言）、读完可达成的 3 个终态（起环境 / 第一个 PR / 解决一类问题）、阅读顺序与文档使用约定。
- **01-mental-models**：列出 4–6 个最关键心智模型，每条来自 ADR 或设计文档，附 `file:line`；解释"为什么这样设计"而非"是什么"。
- **02-concept-primers**：对 Go modules / Vue3 SPA / K8s-Kind / Gin-GORM / Keycloak 各写一段"是什么 + 在本项目干嘛 + 新手注意点"，不得假设读者懂该语言。
- **03-local-setup**：逐步操作，每步「命令 → 预期结果 → 验证命令」三段式；末尾给本地访问地址 + 冒烟清单。所有命令须通过 Phase 3b 自验证。
- **04-reading-path**：给出 5 个具体文件 + 顺序 + 每个为什么读，附 `file:line`。
- **05-first-pr**：一个低风险真实首任务，完整步骤 + 验证 gate（`go build/vet/test` 或 `pnpm build/test` 依仓而定）。
- **06-troubleshooting**：3–8 条真实雷区，每条「现象 → 原因 → 解决 → `file:line`」。
- **07-whos-who**：子系统/模块归属与"去哪问"，可引用设计文档 owner / 决策记录。

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
| **Mode Trigger** | 意图含「新人/onboarding/上手/第一篇PR/get started/技术教程/了解项目」→ `onboarding`；否则 `reference`，保持对其它代码库的通用性。 |
| **Companion Repo Ingestion** | `onboarding` 模式须摄入设计文档仓与 env/脚手架仓；缺失则"为什么 / 怎么跑"成空白。reference 模式可仅摄入代码仓。 |
| **Onboarding Self-Verification** | `onboarding` 模式下，Phase 3b 后必须实际校验教程中的文件路径、setup 命令、验证 gate 真实可用；无法在本环境执行的须标注「需人工/真实环境确认」，严禁假通过。 |
| **Primer Neutrality** | `02-concept-primers.md` 不得假设读者已懂对应语言/框架，只写"够用就行"的入门说明。 |

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
| **[onboarding] 只吃代码仓漏掉 -docs/-env** | "为什么 / 怎么跑"成空白，教程浮于代码表面 | Phase 1 即摄入 companion 仓（设计文档 + env/脚手架） |
| **[onboarding] 教程用参考索引式平铺而非阅读顺序** | 新人无从下手，不知先读什么 | 文件名带 `00-`~`07-` 序号，侧边栏按序编排 |
| **[onboarding] setup 步骤未验证直接写入** | 新人照做即报错，信任崩塌 | Phase 3b 后做自验证，路径/命令/gate 须真实可用 |
| **[onboarding] primer 假设读者懂语言** | 跨语言纯新手卡在基础概念 | `02-concept-primers.md` 不假设任何前置，只写够用入门 |

## 下一步

Wiki 文档生成完成后 → 通知用户查阅 `docs/wiki/<YYYYMMDD-wiki>/`。若用户请求含"不预览"意图 → 仅输出文档路径，不启动 Phase 4。
`onboarding` 模式额外建议用户从 `00-preface.md` 开始按顺序阅读，并以真实新人走查作为最终验收。
