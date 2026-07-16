# Phase Details — 10 阶段执行规范

> 本文件是 `github-mr-review/SKILL.md` 的详细阶段执行规范。

## Phase 1: 监听触发

### 触发条件

群消息满足以下模式之一：
- `@猫猫 {GitHub MR/PR URL}`
- `@猫猫 帮我检视 {URL}`
- `@猫猫 review {URL}`

### 解析步骤

```
1. 从消息提取 GitHub URL → 解析:
   - repo: {owner}/{repo}
   - type: pull (MR) / issues
   - number: {N}
2. 验证 URL 有效:
   gh pr view {N} --repo {owner}/{repo} --json title,body,author,headRefName,baseRefName,additions,deletions,files,state
3. 检查 state === "open"（已 closed/merged 的 MR 不检视）
4. 评估规模:
   - files ≤ 3 且 additions+deletions ≤ 50 → 小 MR，可单轮检视
   - files > 10 或 additions+deletions > 500 → 大 MR，考虑分批检视
```

### 开 Thread

```
→ cat_cafe_propose_thread(
    title: "[MR-Review] {MR title} #{N}",
    reason: "外部 MR 检视任务，需独立 thread 追踪全链路",
    preferredCats: [检视猫A, 验证猫B],
    projectPath: "<cat-cafe 工作区路径>",
    reportingMode: "final-only"
  )
```

**选猫规则**（从 roster 动态匹配，不 hardcode）：
- 检视猫 A：跨 family 优先，代码审查能力强的猫
- 验证猫 B：≠ A，≠ MR 作者，跨 family 优先
- 协调猫：可由 A 或 B 兼任

**thread 创建后首条消息**（必须含主 Thread header）：
```
## 主 Thread
ID: {trigger_thread_id}
标题: {trigger_thread_title}

## 检视任务
- MR: {owner}/{repo}#{N}
- 标题: {MR title}
- 作者: {MR author}
- 规模: {files} files, +{additions}/-{deletions}

## 分工
- 检视猫 A: @{catA} — Phase 2/3/9
- 验证猫 B: @{catB} — Phase 4
- 协调猫: @{coordinator} — Phase 1/6/7/8/10

@{catA} 请开始 Phase 2 检视准备
```

---

## Phase 2: 检视准备

### 步骤

```
1. 获取 MR 变更:
   gh pr diff {N} --repo {owner}/{repo} > /tmp/mr-review/{N}/diff.patch
   gh pr view {N} --json files --jq '.files[] | {path: .path, status: .status, additions: .additions, deletions: .deletions}'

2. clone MR 代码（如需完整上下文）:
   git clone --depth=1 --branch={headRefName} {repo_url} /tmp/mr-review/{N}/repo
   # 或对大 repo 只 clone 变更文件 + 依赖

3. 构建代码知识:
   a. 文件树: 变更文件列表 + status (added/modified/deleted/renamed)
   b. 变更范围: 每个文件的 +/- 行数、变更区域
   c. 依赖关系: 改了 X 影响谁（grep import/require 引用）
   d. 上下文: MR body / 关联 issue / commit messages / base branch

4. 输出检视上下文包:
   {
     "mr": { "repo": "...", "number": N, "title": "...", "author": "..." },
     "files": [{ "path": "...", "status": "modified", "additions": 10, "deletions": 5 }],
     "diff": "full diff text",
     "dependencies": { "src/foo.ts": ["src/bar.ts", "src/baz.ts"] },
     "context": { "body": "...", "commits": [...], "issue": "..." }
   }
```

---

## Phase 3: 代码检视（检视猫 A）

### 检查维度

| 维度 | 检查项 |
|------|--------|
| **正确性** | 逻辑错误 / 边界条件 / null safety / 类型安全 / 状态管理 |
| **安全性** | 注入（SQL/XSS/命令）/ 权限绕过 / 敏感信息泄露 / SSRF / 路径遍历 |
| **性能** | N+1 查询 / 不必要循环 / 大对象拷贝 / 内存泄漏 / 阻塞操作 |
| **可维护性** | 命名 / 圈复杂度 / 重复代码 / 函数长度 / 注释缺失 |
| **一致性** | 与 codebase 风格一致 / 错误处理模式一致 / API 契约一致 |

### 逐文件审查流程

```
FOR each file in 检视上下文包.files:
  1. 读完整 file diff（不只看 +/- 行，读上下文）
  2. 如需更多上下文 → 读完整文件（clone 的 repo 或 gh api contents）
  3. 按检查维度逐项审查
  4. 对每个发现:
     - 判定级别 (P1/P2/P3)
     - 附行号 + 证据 + 建议
  5. 记录意见到意见集
END FOR
```

### 意见集格式

```json
{
  "reviewer": "catA",
  "mr": "{owner}/{repo}#{N}",
  "timestamp": "2026-07-10T10:00:00Z",
  "opinions": [
    {
      "id": "O1",
      "file": "src/foo.ts",
      "line": 42,
      "start_line": 40,
      "level": "P1",
      "category": "correctness",
      "description": "边界条件未处理：空数组时 reduce 抛异常",
      "evidence": "输入 [] → TypeError: Reduce of empty array with no initial value",
      "suggestion": "加空数组 guard: if (arr.length === 0) return default;"
    }
  ]
}
```

---

## Phase 4: 意见验证（验证猫 B — 对抗验证）

### 验证协议

验证猫 B **独立**审查检视猫 A 的每条意见。B 的职责是**质疑**，不是同意。

```
FOR each opinion in 检视猫A.意见集:
  1. 独立读对应代码行 + 上下文（不依赖 A 的描述，自己看代码）
  2. 判定:
     - CONFIRM: 问题真实存在 → 维持原级别
     - OVERTHROW: 误报 / 不适用 → 推翻（附反证）
     - DOWNGRADE: 问题存在但级别过高 → 降级（附理由）
     - UPGRADE: 问题比说的更严重 → 升级（附证据）
  3. 附验证证据:
     - CONFIRM: 复现步骤 / 代码引用
     - OVERTHROW: 反例 / 上下文证明不适用
     - DOWNGRADE: 说明为什么实际影响小于声称
     - UPGRADE: 说明为什么实际影响大于声称
END FOR
```

### 验证报告格式

```json
{
  "verifier": "catB",
  "timestamp": "2026-07-10T10:15:00Z",
  "verdicts": [
    {
      "opinion_id": "O1",
      "verdict": "CONFIRM",
      "evidence": "确认: arr=[] 时 line 42 确实抛 TypeError",
      "final_level": "P1"
    },
    {
      "opinion_id": "O2",
      "verdict": "OVERTHROW",
      "evidence": "推翻: 该函数已有上层 guard 在 line 20 拦截空数组",
      "final_level": null
    }
  ]
}
```

### 质量门禁

- 零推翻 + 零降级 + 零升级 = **验证没做**（走过场）
- B 必须至少对 1 条意见提出质疑（即使最终 CONFIRM，也要独立验证后确认）

---

## Phase 5: 辩论收敛

### 辩论协议

```
Round 1:
  B 提交验证报告 → A 对每条 verdict 回应:
    - 同意 B 的判定 → 该条定稿
    - 反驳 B 的判定 → 附论证，进 Round 2

Round 2 (仅分歧条目):
  A 和 B 各给最终论证 →
    - 达成共识 → 定稿
    - 仍分歧 → 进 Round 3

Round 3 (终局):
  仍不一致的条目 → 以检视猫 A 为准（A 有最终裁量权）
  → 记录分歧原因（供后续 self-evolution 分析）
```

### 最终意见集

```json
{
  "final_opinions": [
    {
      "id": "O1",
      "file": "src/foo.ts",
      "line": 42,
      "level": "P1",
      "description": "...",
      "suggestion": "...",
      "consensus": "unanimous",
      "debate_rounds": 1
    },
    {
      "id": "O3",
      "file": "src/bar.ts",
      "line": 18,
      "level": "P2",
      "description": "...",
      "suggestion": "...",
      "consensus": "A_final",
      "debate_rounds": 3,
      "dissent": "B 认为 P3，A 坚持 P2"
    }
  ]
}
```

---

## Phase 6: 提交意见

### 行级 DiffNote 提交

```bash
HEAD_SHA="$(gh pr view {N} --repo {owner}/{repo} --json headRefOid --jq '.headRefOid')"

FOR each opinion in 最终意见集:
  gh api repos/{owner}/{repo}/pulls/{N}/comments \
    --method POST \
    -f body="**{level}**: {description}

建议: {suggestion}

---
检视猫: {catA} | 验证猫: {catB} | 共识: {consensus}" \
    -F line={opinion.line} \
    -F start_line={opinion.start_line} \
    -F path="{opinion.file}" \
    -F commit_id="$HEAD_SHA"
END FOR
```

### Review Summary + Blocking

```bash
P1_COUNT=...  # 统计 P1 数量
P2_COUNT=...  # 统计 P2 数量
P3_COUNT=...  # 统计 P3 数量

if [ "$P1_COUNT" -gt 0 ] || [ "$P2_COUNT" -gt 0 ]; then
  # 有 P1/P2 → 阻塞合入
  gh pr review {N} --repo {owner}/{repo} --request-changes \
    --body "## 检视报告

| 级别 | 数量 | 状态 |
|------|------|------|
| P1 (Blocking) | $P1_COUNT | 🔴 必须修改 |
| P2 (Should Fix) | $P2_COUNT | 🟡 必须修改 |
| P3 (Suggestion) | $P3_COUNT | 🟢 可选 |

检视猫: @{catA} | 验证猫: @{catB}
请处理 P1/P2 意见后回复"已修改完成"。"
else
  # 只有 P3 → 不阻塞
  gh pr review {N} --repo {owner}/{repo} --comment \
    --body "## 检视报告

| 级别 | 数量 | 状态 |
|------|------|------|
| P3 (Suggestion) | $P3_COUNT | 🟢 可选 |

无阻塞意见，可合入。建议处理 P3 意见。"
fi
```

> **GH 账号 self-approve 陷阱**：所有猫共享一个 GitHub 账号时，`--request-changes` 和 `--approve` 可能被 GitHub 拒绝（同 author）。降级用 `gh pr comment` 落 logical verdict（同 merge-gate 教训）。

---

## Phase 7: 通道通知

```
→ cat_cafe_post_message(
    content: "## MR #{N} 检视完成

**{MR title}**
仓库: {owner}/{repo}
链接: {MR_URL}

| 级别 | 数量 | 状态 |
|------|------|------|
| P1 | $P1_COUNT | 🔴 阻塞 |
| P2 | $P2_COUNT | 🟡 阻塞 |
| P3 | $P3_COUNT | 🟢 建议 |

@{MR作者} 请处理检视意见，完成后回复"已修改完成"。"
  )
```

---

## Phase 8: 等修改

### 事件驱动（优先）

```
MR 作者在 MR comment 回复"已修改完成" →
  github-review-feedback connector 自动唤醒 → 进 Phase 9
```

### 轮询降级

```
→ cat_cafe_hold_ball(
    wakeAfterMs: 300000,  // 5 分钟检查一次
    waitSourceRef: {
      kind: "mr-comment",
      value: "{MR_URL}",
      expectedSignal: "已修改完成"
    }
  )
```

唤醒后检查：
```bash
# 检查 MR 是否有新 commit
LATEST_SHA="$(gh pr view {N} --repo {owner}/{repo} --json headRefOid --jq '.headRefOid')"
if [ "$LATEST_SHA" != "$REVIEWED_SHA" ]; then
  # 有新提交 → 进 Phase 9 复审
else
  # 无新提交 → 继续 hold_ball
fi
```

---

## Phase 9: 审视修改（检视猫 A）

```
1. 获取最新 diff:
   gh pr diff {N} --repo {owner}/{repo}
   LATEST_SHA="$(gh pr view {N} --json headRefOid --jq '.headRefOid')"

2. 逐条确认原意见:
   FOR each opinion in 最终意见集 where level in [P1, P2]:
     a. 读对应文件最新代码
     b. 判定:
        - RESOLVED: 已正确修改 → 标记 resolved
        - UNRESOLVED: 未修改 / 修改不正确 → 保留意见
        - NEW_ISSUE: 修改引入新问题 → 新增意见
     c. 在 MR comment 回复验证结果:
        gh api repos/{owner}/{repo}/pulls/comments/{comment_id}/replies \
          --method POST \
          -f body="✅ 已确认修改正确"  # 或 ❌ 仍未解决
   END FOR

3. 判定:
   - 全部 RESOLVED 且无 NEW_ISSUE → 进 Phase 10
   - 有 UNRESOLVED 或 NEW_ISSUE → 回 Phase 6 补提交 + 通知作者
```

---

## Phase 10: 闭环

### Resolve Discussions

```bash
# 获取所有 review threads
THREADS=$(gh api repos/{owner}/{repo}/pulls/{N}/comments --paginate \
  --jq '.[] | select(.user.login == "{bot_account}") | .id')

# resolve 每个 thread
for thread_id in $THREADS; do
  gh api graphql -f query='
    mutation {
      resolveReviewThread(input: {threadID: "'$thread_id'"}) {
        thread { isResolved }
      }
    }'
done
```

### 门禁审批

```bash
# 确认 0 P1/P2
REMAINING_P1P2=...  # 扫描未 resolved 的 P1/P2 意见

if [ "$REMAINING_P1P2" -eq 0 ]; then
  # approve（注意 GH 账号陷阱，可能需要 gh pr comment 落 logical-approve）
  gh pr review {N} --repo {owner}/{repo} --approve \
    --body "✅ 检视闭环

所有 P1/P2 意见已处理并确认。
检视猫: @{catA} | 验证猫: @{catB}
检视轮次: {rounds}"
else
  echo "❌ 仍有 $REMAINING_P1P2 条未解决意见，不能 approve"
  exit 1
fi
```

### 群通知完成

```
→ cat_cafe_post_message(
    content: "✅ MR #{N} 检视闭环

**{MR title}**
仓库: {owner}/{repo}
链接: {MR_URL}

所有检视意见已处理，已批准合入。
检视猫: @{catA} | 验证猫: @{catB} | 检视轮次: {rounds}"
  )
```

### Thread 归档

检视 thread 标记完成，可归档。如发现系统性问题（同类错误反复出现在不同 MR）→ `self-evolution` 沉淀方法论。
