# 防爬拦截与 L1.5 路径 — jd-hunting

> 本文件是 `SKILL.md` 的防爬策略参考。

## 防爬拦截处理总表

| WebFetch 结果 | 判定 | 处理 | 站点特征 |
|:---|:---|:---|:---|
| 成功 + 含关闭关键词 | 已关闭 | 剔除 | 所有站点 |
| 成功 + 404/重定向 | 已关闭 | 剔除 | 所有站点 |
| 成功 + 内容空 | 已关闭 | 剔除 | 所有站点 |
| 成功 + 时间超期（三方平台） | 已关闭 | 剔除 | BOSS/智联/猎聘/前程 |
| 成功 + 时间超期（政府/国企/企业官网） | 存量超期 | 保留候选 | 政府/国企/企业官网 |
| 成功 + JD 内容正常 | 有效 | 保留 | 所有站点 |
| 403/Captcha（防爬） | 待核实 | **保留候选** | BOSS/智联等强反爬 |
| 超时/网络错误 | 待核实 | **保留候选** | 所有站点 |

**关键原则**：防爬拦截/网络错误**不能**判定为"已关闭"——岗位可能仍然有效，只是抓不到。只有明确关闭信号才能剔除。

## WebFetch 防爬红线

招聘平台（BOSS 直聘、智联招聘等）有严格反爬机制，WebFetch 详情页大概率遇到 403 Forbidden、验证码或 JS 渲染阻塞。

| 情况 | 处理 |
| :--- | :--- |
| WebFetch 返回 403 / Captcha / JS 渲染阻塞 | 直接视为失败，标注"待核实（反爬拦截）" |
| 单站点连续 2 次 WebFetch 失败 | 该站 WebFetch 阶段终止，仅保留 WebSearch 摘要 |
| 严禁 | 频繁重试 WebFetch 导致 IP 被封 |

## 强反爬站点默认策略

| 站点 | WebSearch 能拿到 | WebFetch 详情页 | 默认策略 |
| :--- | :--- | :--- | :--- |
| BOSS 直聘 | 索引页摘要（公司/岗位/薪资） | 大概率 403/Captcha | WebSearch 摘要为主，详情页标"待核实（强反爬，建议 L2 或手动）" |
| 智联招聘 | 索引页摘要 | 可能失败 | WebSearch 摘要为主，WebFetch 失败标"待核实" |
| 猎聘 | 索引页摘要 | 可能失败 | 同上 |
| 国家队平台 | 完整信息 | 通常可抓 | WebFetch 正常抓取 |
| 企业官方招聘站 | 完整信息 | 通常可抓 | WebFetch 正常抓取 |

## L1.5 浏览器子代理路径（browser_use）

在 L1（WebSearch/WebFetch）和 L2（TRAE-browseruse-external + Chrome CDP 9222）之间，L1.5 路径：`browser_use` 子代理 + 反检测注入。

**触发条件**（全部满足）：
1. L1 失败：WebFetch 返回 403/Captcha/需登录/空内容
2. `chrome_cdp: no`（L2 不可用）
3. 用户同意使用 browser_use 子代理浏览器

**stateless 特性声明**：
- browser_use 子代理每次调用独立，浏览器实例随子代理返回关闭
- 不可跨调用复用会话/登录态
- 子代理返回 = 任务结束 = 浏览器关闭

**禁止行为**：
- ❌ 不可用 `browser_wait_for` 等待用户操作（子代理无法暂停等待用户）
- ❌ 不可假设前一次子代理的登录态在新调用中可用

**推荐模式**：
- ✅ 单子代理串行处理所有链接，一次调用完成全部核查
- ✅ 子代理内部完成反检测注入 → 串行访问所有链接 → 一次性返回全部结果

**已知限制**：
- 需反检测注入（见下）
- 无法复用登录态（全新浏览器实例）
- BOSS 直聘等站点可能随反爬升级再次拦截
- 不适合投递操作（无登录态）

### 反检测注入技巧

```javascript
// 反检测注入（add_init_script 注册，导航前生效）
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN','zh','en']});
window.chrome = {runtime: {}};
```

- **BOSS 直聘反爬机制**：前端 JS 检测 `navigator.webdriver === true` → 重定向至 `verify.html` 安全验证页
- **适用范围**：BOSS 直聘已验证有效；其他站点（猎聘/智联）待验证
- **时效性**：此技巧可能随站点反爬升级失效，需定期验证
- **注入机制（Page-Level 预注入）**：
  - ❌ 不可仅用 `browser_evaluate` 单次注入——跨域跳转/新页面加载时 `window` 作用域会被清空，`navigator.webdriver` 恢复默认值 `true`，导致关键跳转节点失效
  - ✅ 必须使用 `add_init_script`（Playwright）或 `evaluateOnNewDocument`（Puppeteer）实现 **Page-Level 预注入**：所有页面加载及 iframe 创建的第一时间自动注入，确保跨域跳转后反检测仍有效
  - 注入时机：在子代理启动浏览器后、第一次 `browser_navigate` 之前注册 `add_init_script`，此后所有导航自动生效
