---
name: github-mr-review
description: >
  GitHub MR 全链路代码检视流水线。10 阶段，强制多猫传球（检视猫≠验证猫≠MR作者），每个检视任务开独立 thread。
  Phase1 监听触发: 群内@猫猫+MR链接 → 解析MR URL → propose_thread 自动创建检视thread（不在原thread做检视）。
  Phase2 检视准备: clone代码 + gh pr diff 拿变更 + 构建代码知识（文件树+变更范围+依赖关系+MR上下文）。
  Phase3 代码检视: 检视猫A 逐文件审查（正确性/安全性/性能/可维护性），产出P1/P2/P3三级意见（附行号+证据+建议）。
  Phase4 意见验证: 验证猫B adversarial verify，逐条质疑检视猫A的意见 → CONFIRM/OVERTHROW/DOWNGRADE/UPGRADE（零推翻=走过场）。
  Phase5 辩论收敛: A↔B 最多3轮辩论，不一致以检视猫A为准（检视者有最终裁量权），输出最终意见集。
  Phase6 提交意见: 行级DiffNote批量提交到MR（gh api pulls/{N}/comments），有P1/P2则--request-changes阻塞合入。
  Phase7 通道通知: 群通知MR作者处理检视意见 + 意见摘要（P1/P2/P3计数）。
  Phase8 等修改: 监听"已修改完成"消息（事件驱动优先：github-review-feedback connector；轮询降级：hold_ball）。
  Phase9 审视修改: 检视猫A 逐条确认修改是否正确 → 已修复标记resolved → 未修复/引入新问题 → 回Phase6补充意见。
  Phase10 闭环: resolve discussions + 门禁审批（0 P1/P2 → APPROVE）+ 群通知完成。
  注意事项: (1)检视猫A≠验证猫B≠MR作者，跨family优先（no self-review铁律）。(2)接收到检视任务必须开新thread，不在原thread里做检视。(3)猫猫互相传球，不是一只猫完成所有阶段——A做检视+复审，B做对抗验证，协调猫做触发+通知+闭环。(4)优先复用猫咖现有功能：propose_thread开thread、worktree clone、hold_ball等修改、gh api行级comment。(5)所有猫共享GH账号时gh pr review --approve会被拒，用gh pr comment落logical-approve。(6)Phase4对抗验证的职责是质疑不是同意。(7)3轮辩论上限防止无限循环。(8)只有P1/P2阻塞合入，P3不阻塞。
  Use when: 群里/频道收到 @猫猫 + GitHub MR/PR 链接、operator 要求检视外部 MR、需要行级代码审查+对抗验证+阻塞合入。
  Not for: 猫咖内部开发 review（用 quality-gate → request-review → receive-review → merge-gate）、单猫快速看一眼 PR（直接读 diff）、只跑 CI 不做人工审查。
  Output: 行级 DiffNote 提交到 MR + 检视报告 + discussions resolved + 门禁审批 + 群通知闭环。
triggers:
  - "检视 MR"
  - "review MR"
  - "review PR"
  - "代码检视"
  - "帮我审查 MR"
  - "MR 链接"
  - "PR review pipeline"
  - "github-mr-review"
---

# GitHub MR Review — 10 阶段全链路代码检视

> **上一步**: 无（由群内 @猫猫+MR链接 触发）| **下一步**: 无（闭环后通知完成）

## 价值门禁

这不是"用 gh pr diff 看一眼"——是**双猫对抗式结构化代码审查**，产出可追溯的行级意见并阻塞合入直到闭环。
和猫咖内部 review 链的区别：内部链是 author 自驱（quality-gate → request-review → merge-gate）；
本 skill 是**外部触发**（群里 @猫猫+MR链接），检视对象是外部 MR，产出物是 MR 上的行级 DiffNote。

## 核心知识

### 三猫角色分离（铁律）

| 角色 | 职责 | 约束 |
|------|------|------|
| **检视猫 A** | Phase 3 逐文件审查，产出三级意见；Phase 9 复审修改 | ≠ MR 作者 |
| **验证猫 B** | Phase 4 对抗验证，逐条质疑检视意见 | ≠ 检视猫 A，≠ MR 作者 |
| **协调猫** | Phase 1 触发+开 thread，Phase 7/10 通知，全程协调 | 可由 A 或 B 兼任，但不做检视/验证 |

> **no self-review 铁律**：检视猫 A ≠ 验证猫 B ≠ MR 作者。跨 family 优先。

### 三级意见分类

| 级别 | 含义 | 处理 |
|------|------|------|
| **P1 — Blocking** | bug / 安全漏洞 / 逻辑错误 / 数据丢失 | 必须修改，阻塞合入 |
| **P2 — Should Fix** | 代码质量 / 边界遗漏 / 性能问题 / 不一致 | 必须修改，阻塞合入 |
| **P3 — Suggestion** | 风格 / 可读性 / 可选优化 | 讨论后决定，不阻塞 |

### Inbound 五问（特性对抗分析）

> 复杂特性 MR（多文件 / 多 commit / 多专项技术）在 Phase 2 完成后、Phase 3 开始前执行五问分析，为检视提供方向和优先级。

| # | 问题 | 回答内容 | 作用 |
|---|------|----------|------|
| 1 | **对我们自己有益吗？** | 是否对家里 tracked feature 有益？是外部需求牵引还是内部演进？ | 方向判断，决定检视深度 |
| 2 | **内容是什么？** | N-in-1 PR？commit 结构？+/- 行数？文件分类（API/Shared/Web/Docs）？ | 检视范围和策略 |
| 3 | **值得 merge 和 intake 吗？** | CI 状态？rebase 需求？merge gate（AC 清单）？brand dictionary 保护路径？ | 阻塞项识别 |
| 4 | **我们自己有更优雅的解法吗？** | 架构合理性？安全策略？抽象层？ | 深度检视触发 |
| 5 | **需要作者确认/后续动作？** | rebase？拆 PR？跨家族 reviewer 指派？future hardening？ | 闭环前置条件 |

> **五问不是走过场**：每问必须附证据（CI log / commit SHA / AC checklist / 代码引用）。五问结果写入检视 thread，作为 Phase 3-10 的上下文。

### 复杂 PR 检视策略

| 场景 | 策略 |
|------|------|
| 文件数 > 20 或 +/- > 2000 | **增量检视**：按文件优先级排序（核心逻辑 > 测试 > 配置 > 文档），分批审查 |
| 多 commit / N-in-1 PR | **commit 结构分析**：评估粒度合理性，hotfix commit 单独标注，建议拆 PR（如需要） |
| 安全相关（commandPolicy / path traversal / crypto） | **专项安全检视**：触发深度安全分析模块 |
| 协议适配器 / 抽象层 | **接口契约检视**：7-method seam 完整性、vendor-neutrality 验证 |
| UI/UX 改动 | **审美 review**：指派暹罗猫 @gemini 看 UX（AC-G28 gate） |
| 跨 family review 需求 | **family 识别**：自动识别作者 family，推荐不同 family 的 reviewer（AC-G9 gate） |

### 与现有能力的复用关系

| 阶段 | 复用 | 新增 |
|------|------|------|
| Phase 1 触发 | `cat_cafe_propose_thread`（开检视 thread） | 群内 @mention+MR链接 解析 |
| Phase 2 准备 | `worktree`（clone）、`gh pr diff` | 代码知识构建 |
| Phase 3 检视 | `receive-review` 的 VERIFY 三道门模式 | 逐文件结构化审查输出 |
| Phase 4 验证 | — | 对抗验证协议（逐条质疑） |
| Phase 5 辩论 | `collaborative-thinking` 收敛模式 | 3 轮上限+不一致以检视者为准 |
| Phase 6 提交 | `gh api` 行级 comment（merge-gate 模式） | DiffNote 批量提交 |
| Phase 7 通知 | `cat_cafe_post_message` / `cross_post_message` | 群通知 MR 作者 |
| Phase 8 等修改 | `cat_cafe_hold_ball`（事件驱动/轮询） | "已修改完成"监听 |
| Phase 9 复审 | `receive-review` Red→Green 模式 | 确认修改正确性 |
| Phase 10 闭环 | `gh api` resolve discussions | 门禁审批+完成通知 |

## 10 阶段流程

> 详细执行规范见 `refs/phase-details.md`。此处为速查。

```
Phase 1  监听触发    群内 @猫猫+MR链接 → 解析 MR URL → propose_thread 开检视 thread
Phase 2  检视准备    clone 代码 → gh pr diff → 构建代码知识（文件树+变更范围+依赖关系）
Phase 3  代码检视    检视猫 A 逐文件审查 → 产出 P1/P2/P3 三级意见（附行号+证据）
Phase 4  意见验证    验证猫 B adversarial verify → 逐条质疑 → 确认/推翻/降级每条意见
Phase 5  辩论收敛    A↔B 最多 3 轮辩论 → 不一致以检视猫 A 为准 → 输出最终意见集
Phase 6  提交意见    行级 DiffNote 批量提交到 MR → 设置 review blocking（REQUEST_CHANGES）
Phase 7  通道通知    群通知 MR 作者：有 N 条检视意见需处理 + 意见摘要
Phase 8  等修改      监听"已修改完成"消息 → hold_ball 等待（事件驱动优先）
Phase 9  审视修改    检视猫 A 逐条确认修改是否正确 → 仍有问题 → 回 Phase 6 补充意见
Phase 10 闭环       resolve discussions → 门禁审批（0 P1/P2 → APPROVE）→ 群通知完成
```

### Phase 1: 监听触发

```
触发条件: 群消息含 @猫猫 + GitHub MR/PR 链接
动作:
  1. 解析 MR URL → 提取 repo + MR number
  2. gh pr view {N} --json title,body,author,headRefName,baseRefName,additions,deletions,files
  3. cat_cafe_propose_thread(
       title: "[MR-Review] {owner}/{repo}#{N} {MR title}",
       reason: "外部 MR 检视任务，需独立 thread 追踪",
       preferredCats: [检视猫A, 验证猫B],
       projectPath: "<cat-cafe 工作区>",
       reportingMode: "final-only"
     )
  4. 等 operator 批准 proposal → thread 创建后 cross_post 任务描述
```

> **铁律**：不在原 thread 里做检视——开新 thread。原 thread 只负责触发+通知。
>
> **多库区分**：不同 repo 的 MR 必须各自开独立检视 thread。thread title 含 `{owner}/{repo}#{N}` 以区分来源库。同一 repo 的不同 MR 也各自开 thread。禁止在同一个检视 thread 里混合多个 MR 的意见。

### Phase 2: 检视准备

```
1. clone MR 代码（或 gh pr diff 拿变更）
2. 构建代码知识:
   - 文件树 + 变更范围（哪些文件 added/modified/deleted）
   - 依赖关系（改了 X 影响谁）
   - MR body / 关联 issue / commit history
   - CI 状态（gh pr checks）/ rebase 状态（behind main?）
   - 文件分类（API/Shared/Web/Docs/保护路径）
3. 复杂 PR 判定（文件数 > 20 或 +/- > 2000 或多 commit）:
   → 执行 Inbound 五问分析（见核心知识）
   → 结果写入检视 thread 作为 Phase 3-10 上下文
4. 输出: 检视上下文包（diff + 文件树 + 依赖图 + 五问结果[如有]）
```

### Phase 3: 代码检视（检视猫 A）

```
逐文件审查，对每个文件:
  1. 读完整 diff（不只看 +/- 行，看上下文）
  2. 按检查维度审查:
     - 正确性: 逻辑错误 / 边界条件 / null safety
     - 安全性: 注入 / 权限 / 敏感信息泄露
     - 性能: N+1 / 不必要循环 / 大对象
     - 可维护性: 命名 / 复杂度 / 重复
  3. 专项检视（由五问结果触发）:
     - 安全策略: commandPolicy 矩阵 / path traversal / symlink-safe / crypto
     - 接口契约: protocol adapter seam 完整性 / vendor-neutrality
     - UI/UX: 指派 @gemini 审美 review（AC-G28）
  4. 每条意见附: 文件路径 + 行号 + 级别 + 描述 + 建议
  5. 输出结构化意见集
```

意见格式:
```json
{
  "file": "src/foo.ts",
  "line": 42,
  "level": "P1",
  "category": "correctness",
  "description": "边界条件未处理：空数组时 reduce 抛异常",
  "evidence": "输入 [] → TypeError",
  "suggestion": "加空数组 guard: if (arr.length === 0) return default;",
  "reviewer": "小菊/glm-5.2🐾",
  "verifier": "宪宪/glm-4.6v🐾"
}
```

> **签名铁律**：每条 DiffNote body 末尾必须附检视猫 + 验证猫签名（`[检视猫/模型🐾]` + `[验证猫/模型🐾]`），区别人为提交。

### Phase 4: 意见验证（验证猫 B — 对抗验证）

```
验证猫 B 独立审查检视猫 A 的每条意见:
  对每条意见:
    1. 独立读对应代码行 + 上下文
    2. 判定:
       - CONFIRM: 问题真实存在 → 维持原级别
       - OVERTHROW: 误报 → 推翻（附理由）
       - DOWNGRADE: 问题存在但级别过高 → 降级（P1→P2 / P2→P3）
       - UPGRADE: 问题比说的更严重 → 升级
    3. 附验证证据（复现 / 反例 / 代码引用）
  复杂 PR 附加验证:
    4. AC 清单检查: 逐项验证 AC-G1/AC-G9/AC-G28 等门禁状态
    5. 跨家族 reviewer 识别: 识别作者 family → 确认验证猫 B 不同 family（AC-G9 gate）
    6. merge gate 状态: rebase 需求 / CI 状态 / brand dictionary 保护路径
  输出: 验证报告（每条意见的 verdict + 证据 + 门禁状态[如有]）
```

> **对抗验证 ≠ 走过场**：验证猫 B 的职责是**质疑**，不是同意。零推翻 = 验证没做。

### Phase 5: 辩论收敛

```
A ↔ B 辩论，最多 3 轮:
  Round 1: B 提交验证报告 → A 对每条 verdict 回应（同意/反驳）
  Round 2: 仍有分歧的条目 → 双方各给最终论证
  Round 3: 仍不一致 → 以检视猫 A 为准（检视者有最终裁量权）

输出: 最终意见集（含每条意见的最终级别 + 双方共识状态）
```

> **3 轮上限**：防止无限辩论。不一致时以检视猫 A 为准——A 是主检视者，承担审查责任。

### Phase 6: 提交意见

```bash
# 对每条最终意见提交行级 DiffNote
gh api repos/{OWNER}/{REPO}/pulls/{N}/comments \
  --method POST \
  -f body="{level}: {description}\n\n建议: {suggestion}" \
  -F line={line} \
  -F path="{file}" \
  -F start_line={start_line} \
  -F commit_id="{head_sha}"

# 提交 review summary + 设置 blocking
gh pr review {N} --request-changes \
  --body "## 检视报告\n\nP1: {count} | P2: {count} | P3: {count}\n\n详见行级意见。请处理后通知。"
```

> **blocking**：有 P1/P2 → `--request-changes` 阻塞合入。只有 P3 → `--comment` 不阻塞。

### Phase 7: 通道通知

```
→ cat_cafe_post_message(
    content: "## MR #{N} 检视完成\n\n**{MR title}**\n\n| 级别 | 数量 | 状态 |\n|------|------|------|\n| P1 | {n} | 🔴 阻塞 |\n| P2 | {n} | 🟡 阻塞 |\n| P3 | {n} | 🟢 建议 |\n\n@{MR作者} 请处理检视意见，完成后回复"已修改完成"。"
  )
```

### Phase 8: 等修改

```
监听 MR 作者回复"已修改完成"（群消息 / MR comment）:
  - 事件驱动: MR comment webhook → github-review-feedback connector
  - 轮询降级: cat_cafe_hold_ball({ wakeAfterMs: 300000, waitSourceRef: { kind: "mr-comment", value: "{MR_URL}", expectedSignal: "已修改完成" } })
```

### Phase 9: 审视修改

```
检视猫 A 逐条确认修改:
  1. gh pr diff {N} 拿最新 diff
  2. 对每条 P1/P2 意见:
     - 已正确修改 → 标记 resolved
     - 未修改 / 修改不正确 → 补充意见（回 Phase 6 补提交）
     - 引入新问题 → 新增意见
  3. 全部 resolved → 进 Phase 10
  4. 有未解决 → 回 Phase 6 + 通知作者
```

### Phase 10: 闭环

```bash
# resolve 所有 discussions
gh api repos/{OWNER}/{REPO}/pulls/{N}/comments --paginate \
  | jq '.[].id' \
  | xargs -I{} gh api repos/{OWNER}/{REPO}/pulls/comments/{}/resolve --method PUT

# 0 P1/P2 → approve
gh pr review {N} --approve --body "检视通过，所有意见已处理。"

# 关闭关联 issue（如有）
# 从 MR body 或 commit message 解析 "fixes #N" / "closes #N" → 提取 issue numbers
for ISSUE_NUM in {resolved_issue_numbers}; do
  gh issue close $ISSUE_NUM --repo {OWNER}/{REPO} \
    --comment "✅ 已通过 MR #{N} 修复并检视闭环。[检视猫: {catA}/{modelA}🐾] [验证猫: {catB}/{modelB}🐾]"
done

# 群通知完成
# → cat_cafe_post_message("✅ MR #{N} 检视闭环，已批准合入。关联 issue #{N} 已关闭。")
```

> **关联 issue 闭环**：MR body / commit message 中的 `fixes #N` / `closes #N` / `resolves #N` 在 MR 合入后需确认 issue 已关闭。GitHub Auto-close 仅在合入 default branch 时触发；如未自动关闭，手动 `gh issue close` + 附检视签名 comment。

## 收尾流程（Phase 6-10 操作清单）

> 检视讨论完 ≠ 结束。必须走完收尾流程，把意见落到 MR 上、阻塞合入、等修改、复审、闭环。
> 以下为协调猫执行的操作清单（检视猫 A 协助 Phase 9）。

### Step 1: 提交行级 DiffNote（Phase 6）

```bash
HEAD_SHA="$(gh pr view {N} --repo {owner}/{repo} --json headRefOid --jq '.headRefOid')"

# 对每条最终意见提交行级 comment
gh api repos/{owner}/{repo}/pulls/{N}/comments --method POST \
  -f body="**{level}**: {description}\n\n建议: {suggestion}\n\n---\n[检视猫: {catA}/{modelA}🐾] [验证猫: {catB}/{modelB}🐾]" \
  -F line={line} \
  -f path="{file}" \
  -F commit_id="$HEAD_SHA" \
  -f side="RIGHT"
```

### Step 2: 设置合入门禁（Phase 6）

```bash
# 有 P1/P2 → 阻塞合入
gh pr review {N} --repo {owner}/{repo} --request-changes --body "检视报告: {P1}×P1 + {P2}×P2 + {P3}×P3"

# GH 账号 self-review 限制 → 降级用 comment 落 logical verdict
gh pr comment {N} --repo {owner}/{repo} --body "⚠️ Logical Review Verdict: REQUEST_CHANGES"
```

### Step 3: 通知 MR 作者（Phase 7）

```
→ cat_cafe_post_message("## MR #{N} 检视完成\n\nP1: {n} | P2: {n} | P3: {n}\n\n@{作者} 请处理检视意见，完成后回复「已修改完成」。")
```

### Step 4: 等待修改（Phase 8）

```
事件驱动: MR comment webhook → github-review-feedback connector 自动唤醒
轮询降级: cat_cafe_hold_ball({ wakeAfterMs: 300000, waitSourceRef: { kind: "mr-comment", value: "{MR_URL}", expectedSignal: "已修改完成" } })
```

### Step 5: 复审修改（Phase 9 — 检视猫 A）

```bash
# 拿最新 diff
gh pr diff {N} --repo {owner}/{repo}

# 逐条确认 P1/P2 意见:
#   已修复 → 在 MR comment reply "✅ 已确认"
#   未修复 → reply "❌ 仍未解决" + 回 Step 1 补提交
#   新问题 → 新增意见 + 回 Step 1
```

### Step 6: 闭环审批（Phase 10）

```bash
# 0 P1/P2 → approve
gh pr review {N} --approve --body "✅ 检视闭环，所有意见已处理。"
# 或 logical approve: gh pr comment {N} --body "✅ Logical Approve"

# 关闭关联 issue（从 MR body / commit 解析 fixes #N / closes #N）
for ISSUE_NUM in {resolved_issues}; do
  gh issue close $ISSUE_NUM --repo {owner}/{repo} \
    --comment "✅ 已通过 MR #{N} 修复并检视闭环。[检视猫: {catA}/{modelA}🐾]"
done

# 群通知完成
→ cat_cafe_post_message("✅ MR #{N} 检视闭环，已批准合入。关联 issue 已关闭。")

# 检视 thread 归档
```

### 收尾完成标志

- [ ] 所有 P1/P2 意见已提交为行级 DiffNote（附猫签名）
- [ ] 合入门禁已设置（request-changes 或 logical verdict）
- [ ] MR 作者已通知
- [ ] 修改已复审（逐条 confirmed）
- [ ] 0 未解决 P1/P2
- [ ] approve 已落（gh 或 logical）
- [ ] 关联 issue 已关闭（如有）
- [ ] 群通知已发送
- [ ] 检视 thread 可归档

## Quick Reference

| 阶段 | 角色 | 关键动作 | 产出 |
|------|------|----------|------|
| 1 触发 | 协调猫 | 解析 MR + propose_thread | 检视 thread |
| 2 准备 | 检视猫 A | clone + diff + 知识构建 | 检视上下文包 |
| 3 检视 | 检视猫 A | 逐文件审查 | 三级意见集 |
| 4 验证 | 验证猫 B | 逐条对抗质疑 | 验证报告 |
| 5 辩论 | A + B | 最多 3 轮 | 最终意见集 |
| 6 提交 | 协调猫 | DiffNote + request-changes | MR 行级意见 |
| 7 通知 | 协调猫 | 群通知作者 | 通知消息 |
| 8 等修改 | 协调猫 | hold_ball 监听 | 修改完成信号 |
| 9 复审 | 检视猫 A | 逐条确认修改 | resolved/补充意见 |
| 10 闭环 | 协调猫 | resolve + approve + 通知 | 检视闭环 |

## Common Mistakes

| 错误 | 后果 | 修复 |
|------|------|------|
| 在原 thread 里做检视 | 过程混入触发 thread，审计困难 | **必须开新 thread**（Phase 1） |
| 检视猫 = 验证猫 | 对抗验证失效，走过场 | A ≠ B，跨 family 优先 |
| 检视猫 = MR 作者 | self-review 铁律违反 | 从 roster 选非作者猫 |
| 一只猫做完所有阶段 | 违反多猫传球原则 | A 做检视+复审，B 做验证，协调猫做通知 |
| Phase 4 零推翻 | 对抗验证没做 | B 的职责是质疑不是同意；零推翻 = 走过场 |
| 辩论超过 3 轮 | 无限循环 | 3 轮上限，不一致以 A 为准 |
| 只提交 review summary 不提交行级意见 | 作者不知道改哪行 | Phase 6 必须行级 DiffNote |
| P3 也阻塞合入 | 过度阻塞 | 只有 P1/P2 阻塞，P3 不阻塞 |
| Phase 9 不逐条确认就 approve | 修改不正确被放过 | 逐条确认 resolved 才进 Phase 10 |
| 不等"已修改完成"就主动复审 | 浪费资源 | Phase 8 等信号再进 Phase 9 |
| 用 `gh pr review --approve` 但所有猫共享 GH 账号 | GitHub 拒绝 self-approve | 用 `gh pr comment` 落 logical-approve（同 merge-gate 教训） |
| 复杂 PR 不做五问分析 | 检视无方向，遗漏架构/安全/门禁问题 | 文件数 > 20 或 +/- > 2000 时必须执行 Inbound 五问 |
| 不检查 rebase 状态 | behind main 的 PR merge 后丢失 commit | Phase 2 检查 `gh pr view --json headRefOid` + base branch sync 状态 |
| 不验证 AC 清单 | 门禁项遗漏，未达标就 approve | Phase 4 逐项检查 AC-G9/AC-G28 等 gate |
| 跨家族 review 不识别 family | 同 family 互审，AC-G9 失效 | Phase 4 识别作者 family，确认验证猫 B 不同 family |

## 和其他 skill 的区别

| Skill | 触发源 | 对象 | 产出 | 区别 |
|-------|--------|------|------|------|
| **github-mr-review（本 skill）** | 群内 @猫猫+MR链接 | 外部 MR | 行级 DiffNote + 阻塞合入 | 外部触发、双猫对抗、10 阶段闭环 |
| `quality-gate` → `request-review` → `merge-gate` | 内部开发完成 | 猫咖自己的 PR | 内部 review + merge | 内部自驱、单 reviewer、SOP 链 |
| `receive-review` | reviewer 反馈 | 自己的代码 | 修复确认 | 处理别人给的 review，不是自己做 review |
| `thread-orchestration` | 主动拆任务 | 多子任务 | 多 thread 编排 | 通用编排，不含检视/对抗验证协议 |
| `fresh-context-review` | author 自驱 | 自己的 PR diff | finding list | 单猫 finding generator，无对抗验证 |

## 通知通道

Phase 7/10 的群通知通过 `cat_cafe_post_message` 发送，由 `OutboundDeliveryHook` 自动投递到 thread 绑定的 IM 通道。

当前 active 通道：

| 通道 | 状态 | 说明 |
|------|------|------|
| **微信（weixin）** | ✅ active | iLink Bot，QR 登录已确认 |
| 飞书（feishu） | 未配置 | 需在 Hub UI 配置 APP_ID + APP_SECRET |
| Telegram | 未配置 | 需在 Hub UI 配置 BOT_TOKEN |
| 钉钉（dingtalk） | 未配置 | 需在 Hub UI 配置 APP_KEY + APP_SECRET |
| 企微 Bot（wecom-bot） | 未配置 | 需在 Hub UI 配置 BOT_ID + BOT_SECRET |
| 企微 Agent（wecom-agent） | 未配置 | 需在 Hub UI 配置 CORP_ID + AGENT_ID + SECRET |
| 小艺（xiaoyi） | 未配置 | 需在 Hub UI 配置 AK + SK + AGENT_ID |

> 通知默认走 thread 绑定的通道（当前 = 微信）。新增通道后在 Hub UI 绑定即可，无需改 skill。

## 测试验证记录

### 首次验证（2026-07-10）

- 测试 PR: https://github.com/rouroumaibing/mr-review-test/pull/1
- Phase 1-10 全流程跑通
- 8 条意见（2×P1 + 4×P2 + 2×P3），宪宪对抗验证 8/8 CONFIRM
- 16 条行级 DiffNote 提交 + 签名 reply 补齐
- 修复推送（commit b34a9c4d）→ Phase 9 复审 8/8 resolved → Phase 10 闭环
- **已知偏差**：首次测试未执行 `propose_thread` 开独立检视 thread，直接在触发 thread 中做检视。skill 设计正确（Phase 1 铁律要求开新 thread），此为执行偏差。后续验证需确认 `propose_thread` 正确创建检视 thread。

## 下一步

闭环后 → 检视 thread 可归档。如发现系统性问题 → `self-evolution` 沉淀方法论。
