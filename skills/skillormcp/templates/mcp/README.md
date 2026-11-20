# <mcp-name>(MCP 模板)

> 复制本目录到 `mcps/<mcp-name>/` 后,替换所有 `<...>` 占位符。写完删除所有"填写说明"。

## 1. 一句话说明
<这个 MCP 连接什么系统、提供哪几类能力。一句话,不写流程。>

> 定位: 本 MCP 是 Agent 使用的自定义 SDK 的**能力面**(连接、凭证、权限、审计只在服务端);用法面(调用顺序、状态机、写前确认)写在配套技能里。

## 2. 为什么是 MCP(落点依据)
| 命中门槛 | 说明 |
|---|---|
| 凭证不可见 / 有状态 / 执行位置受限 / 必须硬拦截 / 多客户端复用 / 机器级契约 | <至少填一条,写具体原因> |

> 填写说明:一条都填不出来,说明不该做 MCP,改用本地脚本或技能。

## 3. 配套技能
- 技能名:`skills/<skill-name>`
- 分工:MCP 负责连接、权限、执行、审计(SDK 能力面);技能负责何时调用、调用顺序、写操作前的人工确认(这份 SDK 的使用说明书)。
- **触发条件(硬性)**:只要本 MCP 含写操作、多步流程(如 prepare→apply 两步)、调用顺序依赖或用法存在歧义,配套技能就是**必须**项——写 `SKILL.md` 承载用法面,并配 `allowed-tools` 白名单圈定 Agent 可用的 tool;纯只读、单步、无歧义的 MCP 可豁免(豁免理由写在这里)。
- CI 阻断破坏:契约基线(见 §5)进仓后,CI 跑 `contract_check.py check`,破坏面未按「升 major + `--allow-breaking` + 提交说明」处理的直接挂提交。
- 方法与流程不写在本 README 或 tool description 里,统一写在技能中。

## 4. Tool 清单(按任务意图聚合,少而精)
| Tool | 读/写 | 风险级别 | 用途(一句话) | 输入(业务参数) | 输出 |
|---|---|---|---|---|---|
| `<verb_noun>` | 读 | 低 | <用途> | `<param: type>` | <结构化结果; 列表自动截断分页> |
| `<verb_noun_write>` | 写 | 高 | <用途> | `<param: type>` + 写护栏(见 §5) | <结构化结果> |

规则:
- 读、写 tool 必须拆开,不得合并成一个带 `mode` 参数的 tool。
- **读写语义写进 tool annotations**(骨架已实现): 只读 tool 标 `readOnlyHint: true`,写 tool 标 `readOnlyHint: false`(不可逆 / 无法撤销的再开 `destructiveHint: true`);语义进协议层 annotations,不进参数、不进 description。契约回归(见 §5)会把 `readOnlyHint`/`destructiveHint` 翻转判为破坏。
- 不暴露"任意 SQL 执行""任意 shell"这类万能 tool(执行任意语句的接口一律不做);需要时做成白名单化的具体 tool。
- 凭证、密码、token 不得出现在任何 tool 参数中。

## 4.1 工具设计规范(Agent 调用成功率的关键)
tool 的 description、参数名和返回值,是模型唯一能看到的"使用说明",写得好坏直接决定被调用得对不对。

- **description 只写"用途 + 何时用/何时不用",不写流程**:例如"按订单号查询订单状态。查询库存请用 `get_inventory`"。流程与顺序归技能——方法写进 tool description 属于错位,会把"说明书"复制进 Agent 上下文且无法回归。
- **tool 只做薄委托(骨架已实现)**:tool 函数体只做三件事——边界校验、护栏调用、1–3 行委托 `request_api`;HTTP 客户端构造、响应整形、分页截断全部归骨架的 client 函数(`request_api`/`paginate`),不在 tool 里展开。tool 是契约面,越薄越稳;逻辑越长,契约回归越难做。
- **tool 数量超过 15 个必须拆层**:业务逻辑抽到 `client.py`(纯函数、经入参接收 settings/creds,可独立单测),tool 层只留"声明 + 校验 + 委托"与 `TOOL_REGISTRY` 映射表(tool 名 -> client 函数),并加脚本校验"每个 tool 恰好委托一个 client 函数、client 函数不直接 new HTTP 客户端"。没到 15 个不要预拆——为拆而拆会牺牲"一个文件看完全部"。
- **参数名要自解释**:用 `order_id`、`start_date`,避免 `id`、`d1`;每个参数都写类型、单位、取值范围和一个示例。
- **用枚举而非自由文本**:状态、环境、类型等有限取值,用 `enum` 约束,让校验在 schema 层完成。
- **返回精简的结构化结果**:只回传 Agent 下一步需要的字段,并带上人能读懂的名称(如返回 `owner_name` 而不仅是 `owner_uuid`)。大结果必须分页或截断,并在返回里说明总数和如何取下一页,避免撑爆上下文(骨架的 `paginate()` 已实现: `items/total/page/has_more/next_page`)。
- **错误信息要可行动**:不仅说"失败了",要说明原因和下一步,如"`env` 必须是 dev/staging/prod 之一,收到 `production`"。让 Agent 能自行纠错重试。
- **tool 数量克制**:同一 MCP 建议不超过十几个;功能相近的合并为按意图聚合的 tool,而不是一个 API 一个 tool。

## 5. 护栏(硬约束,在 MCP 层强制)
| 项 | 约定 |
|---|---|
| 传输 | `MCP_TRANSPORT` 双传输: `stdio` 本地单租户; `http` 远程多租户——绑定内网地址,启动校验拒绝 `0.0.0.0`/`::` 通配与公网 IP |
| 权限分级 | 读 tool 默认开放;写 tool 需要 <角色/审批人> 授权 |
| 写护栏(二选一) | `write_policy: gate` = 按写类别拆分的 env 开关(`<前缀><写类别大写>=1` 才放行,**缺失/未开启即拒** PermissionError;写类别是服务端常量,不接受 Agent 指定) / `write_policy: token` = `prepare`(预览+一次性限时令牌)与 `apply`(校验消费)两步;写拒绝与令牌失效一律 PermissionError |
| 白名单 | 目标范围限定为 <库/表/命名空间/主机清单>,清单放配置,不写死在代码 |
| 限流 | <每分钟/每会话调用上限>; 读、写分开计量,各一份配额 |
| 审计 | `@audited` 定义即应用: 每次 tool 调用留痕(时间、tool、参数摘要、状态 ok/denied/error);敏感键的值只留哈希前缀,日志不含凭证 |
| 契约回归 | `skills/skillormcp/scripts/contract_check.py`:用 MCP 客户端经**实际传输**(stdio 子进程 / Streamable HTTP)完成 initialize + tools/list 出快照(名/schema/是否写/破坏性/版本),与基线 diff。删 tool、删必填、类型或枚举收窄、读写标注(`readOnlyHint`/`destructiveHint`)翻转判**破坏**:须阻断提交或升 major + `--allow-breaking` 显式放行;新增 tool / 放宽放行。禁止 import 服务端模块做静态内省代替真实 tools/list |
| 超时/幂等 | 每个写 tool 设超时(request_api 统一超时);支持幂等键,避免重试造成重复写 |

## 6. 凭证与配置
- 凭证只在服务端获取,Agent 侧永远看不到,只传业务参数;http 调用时凭证**只进请求头**(默认 `Authorization: Bearer`,声明 `header_name` 后用自定义请求头)。
- 配置文件:复制 `config.example.yaml` 为 `config.yaml`(已 gitignore),只放非敏感项和凭证的**引用声明**。

### 6.1 凭证获取模式(credential_refs,骨架已实现)
每个引用(ref)独立声明,互不借用:

```yaml
credential_refs:
  <ref_key>:                 # static: 直接读 env 回退链
    mode: static
    env: [<PRIMARY_ENV>, <BACKUP_ENV>]   # 401 刷新时链内下移, 只在同 ref 内回退, 链用尽即报错
  <oauth_ref_key>:           # oauth2_cc: client_credentials 换 token, 缓存到期前 30s, 401 强制重换
    mode: oauth2_cc
    token_url: https://<idp-host>/token
    scopes: [<scope>]
    pair:                    # 成对凭证: 每个成员各有独立 env 链
      client_id:
        - <CLIENT_ID_ENV>
      client_secret:
        - <CLIENT_SECRET_ENV>
```

- **回退边界**:回退只发生在**同一 ref 自己的链内**(static 的 `env` 链、pair 成员各自的链);一个 ref 链用尽直接报错,绝不借用其它 ref 的 env——避免"A 没了偷偷用 B"造成越权。
- **401 处理**:`request_api` 遇 401 → 刷新该 ref 凭证(static 链下移 / oauth 重新换 token)→ **重试一次**;仍 401 抛 `PermissionError`,不无限重试。
- **凭证获取模式选择**:`mode: static | oauth2_cc` 按目标系统支持情况选择;成对凭证(client_id/client_secret)各走各的链。

### 6.2 统一配置(骨架已实现,照此扩展)
- **一处收口**:所有配置由 `Settings` 数据类统一承载,`Settings.load()` 一次加载、一次校验。
  - **必填项缺失直接抛错**(白名单为空 / 凭证声明缺失 / `write_policy` 缺失或非法 / 配置文件不存在),不静默用默认值——
    否则「配置没写」会变成「行为悄悄变了」。
  - 业务连接参数(`base_url` / `http_host` 等)落在 `Settings.extra`,用 `settings.require("base_url")` /
    `settings.get("base_url", default)` 读取;**不要把连接参数硬编码进 tool 代码**。
- **函数经入参传变量**:内部函数(`check_target` / `check_write_gate` / `issue_token` / `consume_token` /
  `request_api` / `CredentialManager`)一律把 `settings` / `creds` 作为入参接收,不做模块级隐式读取。
  - 好处:测试可注入替身配置与 env 字典,多套环境互不污染。
- **tool 不暴露配置**:`build_mcp(settings)` 用闭包把配置与凭证管理器注入各 tool;
  **不要把 `settings` 写成 tool 参数**——tool 签名就是给 Agent 看的 schema,
  配置一旦成为参数就会泄漏进上下文,等于把内部结构交给模型改。
- **HTTP 唯一出口**:所有对目标系统(含 IdP token 端点)的调用走 `request_api`——统一超时
  (`request_timeout_seconds`)、统一 TLS 校验(`verify_tls`,默认开,关闭须显式写出)、凭证注入、401 重试。
  禁止在 tool 里直接 new HTTP 客户端。`request_api(transport=...)` 是测试注入点:外部依赖(httpx)
  可注入假传输对象,在无网络环境回归 401 刷新/重试/凭证注入,不必 mock httpx 本身。
- **有外部依赖时测试可注入假传输**:提供 `get_transport(method,url,**kw)` 的替身对象传入
  `request_api` 的 `transport` 参数即可;不注入则走真实 httpx.Client。生产代码不传该参数。
- 运行时状态(待确认令牌、限流窗口、凭证缓存/淘汰标记)由 `build_mcp` 创建的实例/闭包持有,便于测试隔离。
- 配置路径:`MCP_CONFIG` 环境变量指定,缺省当前工作目录下的 `config.yaml`。

## 7. 部署与连通
- 传输:`MCP_TRANSPORT=stdio`(本地单租户,默认) / `MCP_TRANSPORT=http`(远程多租户)。
- http 模式:配置 `http_host`(内网 IP 或内网主机名,**禁止 `0.0.0.0`/`::` 通配与公网 IP**,启动校验不过即失败)
  与 `http_port`;必须置于认证/网关之后(<认证方式>),不允许匿名访问。
- 部署位置:<必须能访问目标系统的网络/机器>
- 依赖与运行:`pip install -r requirements.txt && python server.py`

## 8. 接入方式
```json
{
  "mcpServers": {
    "<mcp-name>": { "command": "python", "args": ["<path>/server.py"] }
  }
}
```

## 9. 验收清单(提交前自检)
- [ ] 至少命中一条 MCP 门槛,并写明原因
- [ ] 读、写 tool 已拆分;无万能 tool
- [ ] 每个 tool 都有 annotations:只读标 `readOnlyHint: true`,写标 `readOnlyHint: false`(不可逆再开 `destructiveHint: true`);未标注的 tool 会被契约回归按写侧处理(保守)
- [ ] 所有 tool 参数中无凭证与连接配置
- [ ] tool 函数体只做"边界校验 + 护栏 + 1–3 行委托 `request_api`";HTTP/整形/分页逻辑在骨架 client 函数里;tool 数量超过 15 时已拆 `client.py` + 映射表并配脚本校验
- [ ] 有配套技能承载用法面(调用顺序 / 状态机 / 写前确认);方法未写进 tool description——写进去的部分已挪走
- [ ] `contract_check.py snapshot` 已产出基线并提交进仓;提交前 `check` 无破坏,或破坏已升 major + `--allow-breaking` 放行并写进提交说明
- [ ] 有外部依赖时的测试用假传输注入(`request_api(transport=...)`),不 mock httpx 本身
- [ ] `write_policy` 二选一已配置:gate 开关缺失即拒 / token 两步令牌;写拒绝与令牌失效抛 PermissionError
- [ ] credential_refs 每个 ref 有独立回退链;成对凭证各走各的链;401 刷新重试一次逻辑未被改坏
- [ ] HTTP 调用全部走 `request_api`(无绕过出口);超时与 verify_tls 统一管理
- [ ] http 传输绑定内网地址,启动校验拒绝 0.0.0.0/::;远程部署有认证
- [ ] 高风险写操作有审计日志;审计中敏感值已哈希脱敏
- [ ] description 只写用途,方法已放进配套技能
- [ ] 代码、配置、日志、README 中无真实凭证与内网地址
- [ ] 异常与超时有明确返回,不泄露堆栈和内部路径
- [ ] SDK 类比自检: 能力面(凭证/连接/权限/审计)只在服务端, 用法面(顺序/状态机/确认门)在配套技能; 未把方法写进 tool description
- [ ] 统一配置自检: 配置由 `Settings` 一处收口并校验必填项; 内部函数经入参接收 settings/creds; tool 签名里没有 settings/config 等配置参数; 连接参数未硬编码在 tool 代码里
- [ ] 列表类 tool 输出已走 `paginate()` 截断分页
- [ ] 已提供一条端到端试跑样例(见 `server.py` 底部注释)

## 10. 降级评估(定期复核)
平台或上游是否已原生提供同等连接能力?若是,评估本 MCP 是否仍值得维护。
按缺口类型而非"AI 现在能不能"复核; 若使用频率 / 复用客户端数低于维护成本, 评估改为人工代理旁路或短时效凭证一次性, 不必长期运行专用服务——但凭证仍不交给 Agent。
