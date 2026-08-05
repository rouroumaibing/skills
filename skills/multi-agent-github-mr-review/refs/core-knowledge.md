# 核心知识 — GitHub MR Review

> 本文件是 `multi-agent-github-mr-review/SKILL.md` 的核心知识参考。

## 三 agent 角色分离（铁律）

| 角色 | 职责 | 约束 |
|------|------|------|
| **检视 agent A** | Phase 3 逐文件审查，产出三级意见；Phase 9 复审修改 | ≠ MR 作者 |
| **验证 agent B** | Phase 4 对抗验证，逐条质疑检视意见 | ≠ 检视 agent A，≠ MR 作者 |
| **协调 agent** | Phase 1 触发+开 thread，Phase 7/10 通知，全程协调 | 可由 A 或 B 兼任，但不做检视/验证 |

> **no self-review 铁律**：检视 agent A ≠ 验证 agent B ≠ MR 作者。跨 family 优先。

## 三级意见分类

| 级别 | 含义 | 处理 |
|------|------|------|
| **P1 — Blocking** | bug / 安全漏洞 / 逻辑错误 / 数据丢失 | 必须修改，阻塞合入 |
| **P2 — Should Fix** | 代码质量 / 边界遗漏 / 性能问题 / 不一致 | 必须修改，阻塞合入 |
| **P3 — Suggestion** | 风格 / 可读性 / 可选优化 | 讨论后决定，不阻塞 |

## Inbound 五问（特性对抗分析）

> 复杂特性 MR（多文件 / 多 commit / 多专项技术）在 Phase 2 完成后、Phase 3 开始前执行五问分析，为检视提供方向和优先级。

| # | 问题 | 回答内容 | 作用 |
|---|------|----------|------|
| 1 | **对我们自己有益吗？** | 是否对家里 tracked feature 有益？是外部需求牵引还是内部演进？ | 方向判断，决定检视深度 |
| 2 | **内容是什么？** | N-in-1 PR？commit 结构？+/- 行数？文件分类（API/Shared/Web/Docs）？ | 检视范围和策略 |
| 3 | **值得 merge 和 intake 吗？** | CI 状态？rebase 需求？merge gate（AC 清单）？brand dictionary 保护路径？ | 阻塞项识别 |
| 4 | **我们自己有更优雅的解法吗？** | 架构合理性？安全策略？抽象层？ | 深度检视触发 |
| 5 | **需要作者确认/后续动作？** | rebase？拆 PR？跨家族 reviewer 指派？future hardening？ | 闭环前置条件 |

> **五问不是走过场**：每问必须附证据（CI log / commit SHA / AC checklist / 代码引用）。五问结果写入检视 thread，作为 Phase 3-10 的上下文。

## 复杂 PR 检视策略

| 场景 | 策略 |
|------|------|
| 文件数 > 20 或 +/- > 2000 | **增量检视**：按文件优先级排序（核心逻辑 > 测试 > 配置 > 文档），分批审查 |
| 多 commit / N-in-1 PR | **commit 结构分析**：评估粒度合理性，hotfix commit 单独标注，建议拆 PR（如需要） |
| 安全相关（commandPolicy / path traversal / crypto） | **专项安全检视**：触发深度安全分析模块 |
| 协议适配器 / 抽象层 | **接口契约检视**：7-method seam 完整性、vendor-neutrality 验证 |
| UI/UX 改动 | **审美 review**：指派 agent @gemini 看 UX（AC-G28 gate） |
| 跨 family review 需求 | **family 识别**：自动识别作者 family，推荐不同 family 的 reviewer（AC-G9 gate） |

## 与现有能力的复用关系

| 阶段 | 复用 | 新增 |
|------|------|------|
| Phase 1 触发 | `multi_agent_propose_thread`（开检视 thread） | 群内 @mention+MR链接 解析 |
| Phase 2 准备 | `worktree`（clone）、`gh pr diff` | 代码知识构建 |
| Phase 3 检视 | `receive-review` 的 VERIFY 三道门模式 | 逐文件结构化审查输出 |
| Phase 4 验证 | — | 对抗验证协议（逐条质疑） |
| Phase 5 辩论 | `collaborative-thinking` 收敛模式 | 3 轮上限+不一致以检视者为准 |
| Phase 6 提交 | `gh api` 行级 comment（merge-gate 模式） | DiffNote 批量提交 |
| Phase 7 通知 | `multi_agent_post_message` / `cross_post_message` | 群通知 MR 作者 |
| Phase 8 等修改 | `multi_agent_hold_ball`（事件驱动/轮询） | "已修改完成"监听 |
| Phase 9 复审 | `receive-review` Red→Green 模式 | 确认修改正确性 |
| Phase 10 闭环 | `gh api` resolve discussions | 门禁审批+完成通知 |
