# 子代理 Prompt 模板

所有浏览器子代理通过 `Task` 调用 `TRAE-browseruse-external`，在用户本机 Chrome（已登录会话，CDP 端口 9222）执行。填入 {变量} 后发起。遇阻一律 Fast-Fail 返回，不挂起等待用户。

## 模板 A：招聘网站岗位检索（第三步）

你在用户本机 Chrome（已登录会话）中检索招聘岗位，只检索不投递。

- 目标站点：{站点名}
- 搜索结果页 URL：{搜索结果页 URL，含岗位+地点参数}（直接访问此页，非首页）
- 岗位关键词：{岗位名}（近义：{近义岗位名列表}）
- 工作地点：{地点}
- 时间范围：近 3 个月（以 {今日日期} 为基准）；时间不明单列"待核实"
- 抓取上限：前 30 条
- 登录状态：用户已登录，无需输入凭据

步骤：
0. 环境探针（执行前必做）：
   - 执行 OS 适配的等价命令检测 CDP 可达性：
     - macOS/Linux: `curl -s http://localhost:9222/json/version`
     - Windows: `Invoke-WebRequest -Uri http://localhost:9222/json/version -UseBasicParsing` 或 `curl.exe`
   - 不可达即 Fast-Fail 返回，回报：`状态：CDP 不可达 | 建议：Chrome 未以 9222 启动，转 WebSearch`
   - 可达后，**仅当本次任务需要登录态数据时**，访问 `about:blank` 检查登录态 cookie 是否存在（不需要登录态数据时跳过此检查）
   - 探针通过才进入 step 1
   - **返回报文必含 sub_status 字段**（阶段 3 新增）：子代理禁止直接写 00-index.md，在返回报文中携带 sub_status，由主对话统一写入

**子代理返回报文格式**（模板 A/B/C 统一）：

```json
{
  "status": "DONE | FAST_FAIL | BLOCKED",
  "sub_status": "BOSS 站检索完成，智联检索中",
  "site": "BOSS直聘",
  "valid_jobs": 12,
  "closed_jobs": 3,
  "pending_jobs": 2,
  "jobs": [...],
  "exclude_sites": ["BOSS"]
}
```

**责任分离原则**：主对话拥有 00-index.md 的最高控制权。子代理禁止直接 Write/Update 00-index.md，避免多 Task 或异步环境下的文件覆盖写冲突。

1. 新开标签页导航到 {搜索结果页 URL}；**重定向检测**——若重定向后 URL 含 `login`/`passport`/`sso`，或出现登录弹窗，立即 Fast-Fail 返回，回报 `状态：未登录 | 站点：{站点名}`，交主流程加黑名单
2. 按近义岗位名补全搜索；时间筛选选最接近"3 个月"的筛选项
3. 逐条抓取：公司全称、岗位标题、薪资、地点、发布时间、详情页 URL
4. 翻页直到无新结果或达 30 条
5. 验证码/滑块且无法自主绕过 → **保存已抓数据并立即返回**（不挂起等待），回报 `状态：触发验证码 | 已抓取：N 条 | 建议：手动通过后重跑或转 WebSearch`
6. 连续 2 次失败 → 回报"该站失败跳过"

产出（强制摘要格式，禁止返回 DOM snapshot 原文）：

### 检索结果摘要
- 站点：{站点名}
- 有效岗位数：{N} 条
- 已关闭岗位数：{M} 条（附关闭原因统计）
- 待核实岗位数：{K} 条

### 岗位清单（结构化，最多 10 条，超出截断）
| 公司全称 | 岗位标题 | 薪资 | 地点 | 发布时间 | JD 摘要(≤50字) | 详情URL |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| ... | ... | ... | ... | ... | ... | ... |

（单个 Task 每次向主对话汇报的列表上限不得超过 10 条，超出的部分直接截断，主对话按需翻页或触发新 Task）

### 异常
- {站点} 反爬拦截，仅 WebSearch 摘要 {N} 条
- {站点} 登录态丢失，Fast-Fail

**字段抽取约束**：子代理只能抽取以下 5 个固定字段 + 详情页链接，禁止返回其他内容：
- job_title（岗位名）
- salary（薪资范围）
- city（工作地点）
- experience_req（经验要求）
- core_skills_top3（核心技能 Top3）

**禁止行为**：
- ❌ 返回 DOM snapshot 原文
- ❌ 返回子代理内部推理过程
- ❌ 候选阶段返回完整 JD 文本（每条 JD 摘要 ≤50 字）
- ❌ 单次返回超过 10 条岗位（超出截断）

"待核实"区单列时间不明条目。禁止点击"立即沟通/投递"按钮。

## 模板 B：官网招聘页检索（第四步）

你在用户本机 Chrome 中检索公司官网招聘页是否有匹配岗位，只浏览。

- 公司：{公司全称}
- 招聘页 URL：{URL}（未知则先 WebSearch 搜「{公司名} 招聘 官网」定位）
- 目标岗位：{岗位名列表}
- 登录状态：通常可浏览，投递需登录——只浏览

步骤：
0. 环境探针（执行前必做，阶段 3 新增）：
   - 检查 00-index.md 的 chrome_cdp 字段
   - chrome_cdp=no → 走 L1（WebSearch + WebFetch）
   - chrome_cdp=yes → curl 9222 再校验一次，通过则走 L2（浏览器子代理）
   - 探针通过才进入 step 1
   - 返回报文必含 sub_status 字段（同模板 A 责任分离）
1. 导航到招聘页
2. 按岗位关键词检索/浏览列表
3. 抓取匹配岗位：岗位名、地点、要求摘要、投递入口 URL
4. 官网常无发布时间，全部归入"待核实"
5. 无匹配 → 回报"无匹配岗位"

产出：
| 岗位名 | 地点 | 要求摘要 | 投递入口URL |

**返回报文格式**（含 sub_status，同模板 A 责任分离）：
```json
{
  "status": "DONE | FAST_FAIL | BLOCKED",
  "sub_status": "{公司名} 官网检索完成，匹配 {N} 个岗位",
  "company": "{公司全称}",
  "matched_jobs": N,
  "jobs": [
    {
      "job_title": "岗位名",
      "location": "地点",
      "requirement_summary": "要求摘要",
      "apply_url": "投递入口URL"
    }
  ]
}
```

## 模板 C：公司背景调查（第六步，仅高适配度岗位）

你在用户本机 Chrome 中查公司工商/司法/融资状况，只查不互动。

- 公司：{公司全称}
- **JD 宣称公司全称**：{JD宣称公司全称}（来自招聘页面显示的招聘主体）
- **JD 岗位描述摘要**：{JD岗位描述摘要}（用于判断是否宣称为大厂/自研项目）
- 来源：企查查/天眼查可浏览页、裁判文书网、信用中国
- 登录状态：部分信息需登录，拿不到标注"需登录查看"

步骤：
0. 环境探针（执行前必做，阶段 3 新增）：
   - 检查 00-index.md 的 chrome_cdp 字段
   - chrome_cdp=no → 走 L1（WebSearch + WebFetch）
   - chrome_cdp=yes → curl 9222 再校验一次，通过则走 L2（浏览器子代理）
   - 探针通过才进入 step 1
   - 返回报文必含 sub_status 字段（同模板 A 责任分离）
1. WebSearch 搜「{公司名} 企查查」定位可浏览页
2. 抓取：存续状态、成立年份、注册资本、融资阶段、社保人数、司法风险数、是否失信、经营范围、实际主体全称
3. 拿不到的字段标注"需登录"或"未公示"
4. 红线判定（任一命中即在汇总表标红警告）：
   - 失信被执行人 / 限制高消费
   - 社保人数为 0 且成立 >1 年
   - 司法风险数 > 50
5. 外包/挂靠判定（标黄，输出明确结论）：
   - 比较企查查"实际主体全称"与 {JD宣称公司全称}：
     - 实际主体名含"人力资源/劳务/外包/智联/微创/外企德科/中智"等字样 → `⚠️ 疑为外包/派遣岗（实际签约方：{实际主体}）`
     - 二者法人/股权无关联，但 {JD岗位描述摘要} 宣称为大厂项目 → `⚠️ 疑为挂靠岗（实际签约方：{实际主体}，与 JD 宣称 {JD宣称公司全称} 无股权关联）`
   - 主体一致且无上述字样 → `✅ 主体一致`

产出：
| 存续 | 成立 | 注册资本 | 融资 | 社保人数 | 司法风险 | 失信 | 实际主体 | 外包判定 |

**返回报文格式**（含 sub_status，同模板 A 责任分离）：
```json
{
  "status": "DONE | FAST_FAIL | BLOCKED",
  "sub_status": "{公司名} 背景调查完成",
  "company": "{公司全称}",
  "business_status": "存续/注销/吊销",
  "established_year": "成立年份",
  "registered_capital": "注册资本",
  "financing_stage": "融资阶段",
  "social_insurance_count": "社保人数",
  "judicial_risk_count": "司法风险数",
  "is_dishonest": "是/否",
  "actual_entity": "实际主体全称",
  "outsourcing_verdict": "✅主体一致 / ⚠️疑为外包 / ⚠️疑为挂靠"
}
```

## 模板 D：browser_use 子代理链接核查（L1.5 路径，阶段 4 新增）

你在 browser_use 子代理中核查岗位链接有效性，只核查不投递。

- **适用场景**：L1（WebFetch）失败 + `chrome_cdp: no`（L2 不可用）+ 用户同意
- **输入参数**：`{待核查链接列表}`（含公司名、岗位名、URL）
- **stateless 声明**：本子代理为无状态单次执行，不可跨调用复用

步骤：
0. 反检测注入（必做，Page-Level 预注入）：
   - 通过 `add_init_script`（Playwright）或 `evaluateOnNewDocument`（Puppeteer）注册以下 4 行 JS：
     ```javascript
     Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
     Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
     Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN','zh','en']});
     window.chrome = {runtime: {}};
     ```
   - ❌ 不可仅用 `browser_evaluate` 单次注入（跨域跳转后失效）
   - 注册成功后进入 step 1，此后所有 `browser_navigate` 自动生效
1. 串行处理所有待核查链接：
   - `browser_navigate` 到第一个 URL
   - 检测是否重定向至 verify.html / login 页
   - 抓取岗位状态（在招/已关闭/需登录）
   - 记录结果，继续下一个链接
2. 所有链接处理完毕后一次性返回

**禁止行为**：
- ❌ 不可用 `browser_wait_for` 等待用户登录
- ❌ 不可假设前一次调用的登录态可用
- ❌ 不可中途返回（必须处理完所有链接）

**返回报文格式**（含 sub_status，同模板 A 责任分离）：
```json
{
  "status": "DONE | DONE_WITH_CONCERNS | BLOCKED",
  "sub_status": "L1.5 核查完成",
  "results": [
    {
      "company": "{公司名}",
      "job_title": "{岗位名}",
      "url": "{URL}",
      "job_status": "在招 | 已关闭 | 需登录 | 无法判定",
      "evidence": "≤50字判定依据"
    }
  ]
}
```

## 模板 E：海投岗位批量检索（第八步，阶段 5 新增）

你在 L1 主路径下批量检索指定岗位方向的招聘岗位，只检索不投递。

- 岗位方向：{岗位方向}（如云原生/SRE/运维开发/AI Agent/架构师/Go 后端）
- 查询变体：{2-3 个查询变体}（如"云原生运维 杭州 招聘"、"K8s 运维 杭州"、"容器运维 杭州"）
- 跨站点：BOSS/猎聘/智联/前程/企查查/企业官网
- 地点过滤：{用户地点} + {半小时车程半径}
- 去重键集合：{exclude_keys}（主对话传入的 03+04+05+06 已收集岗位"公司+岗位"键集合）
- 时间范围：近 3 个月
- 抓取上限：前 30 条

步骤：
0. 环境探针（L1 路径，无需 CDP 检查）：
   - 确认 WebSearch 可用（执行一次测试查询）
   - 确认 WebFetch 可用（可选，失败则仅用 WebSearch 摘要）
   - 探针通过才进入 step 1
   - 返回报文必含 sub_status 字段（同模板 A 责任分离）
1. 对每个查询变体执行 WebSearch，收集列表页 URL
2. WebFetch 抓取列表页，提取岗位条目（公司/岗位/薪资/地点/链接）
3. 对每条岗位的详情页 URL 执行 WebFetch，提取 JD 正文 + 验证有效性（4 信号）+ 判定链接四态（实测路径：是否含完整 JD 正文）
4. 按去重键集合过滤已收集岗位（公司名归一化 + 岗位名相似度 > 80%）
5. 按地点过滤（模糊退化机制：精确地址/地标/区级/城市级四级匹配）
6. 强反爬站点返回 403/Captcha → 标"待核实"，不剔除
7. 汇总本方向有效岗位，落盘返回

**stateless 声明**：本子代理为无状态单次执行，不可跨调用复用。

**禁止行为**：
- ❌ 不可返回 DOM snapshot 或 JD 原文
- ❌ 不可返回超过 10 条岗位（超出截断）
- ❌ 不可编造链接（无链接标"⚠️ 无直接链接"）
- ❌ 不可靠 LLM 幻觉估计距离

**返回报文格式**（STRICT JSON ARRAY，同模板 A 责任分离，含 sub_status）：
```json
{
  "status": "DONE | DONE_WITH_CONCERNS | BLOCKED",
  "sub_status": "{岗位方向} 检索完成，有效 {N} 条",
  "results": [
    {
      "company": "公司全称",
      "title": "岗位标题",
      "salary": "薪资范围",
      "location": "工作地点",
      "exp_edu": "经验/学历要求",
      "link": "投递链接",
      "link_status": "✅可直接浏览 | ⚠️需登录 | ⚠️无直接链接 | ❌已关闭/失效"
    }
  ]
}
```

**严格禁止**：返回任何 HTML/DOM 结构、JD 详细描述文本、网页原文。仅返回上述 7 个字段。
