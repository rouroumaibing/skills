# 收尾流程 — Phase 6-10 操作清单

> 检视讨论完 ≠ 结束。必须走完收尾流程，把意见落到 MR 上、阻塞合入、等修改、复审、闭环。
> 以下为协调 agent 执行的操作清单（检视 agent A 协助 Phase 9）。

## Step 1: 提交行级 DiffNote（Phase 6）

```bash
HEAD_SHA="$(gh pr view {N} --repo {owner}/{repo} --json headRefOid --jq '.headRefOid')"

# 对每条最终意见提交行级 comment
gh api repos/{owner}/{repo}/pulls/{N}/comments --method POST \
  -f body="**{level}**: {description}\n\n建议: {suggestion}\n\n---\n[检视 agent: {agentA}/{modelA}] [验证 agent: {agentB}/{modelB}]" \
  -F line={line} \
  -f path="{file}" \
  -F commit_id="$HEAD_SHA" \
  -f side="RIGHT"
```

## Step 2: 设置合入门禁（Phase 6）

```bash
# 有 P1/P2 → 阻塞合入
gh pr review {N} --repo {owner}/{repo} --request-changes --body "检视报告: {P1}×P1 + {P2}×P2 + {P3}×P3"

# GH 账号 self-review 限制 → 降级用 comment 落 logical verdict
gh pr comment {N} --repo {owner}/{repo} --body "⚠️ Logical Review Verdict: REQUEST_CHANGES"
```

## Step 3: 通知 MR 作者（Phase 7）

```
→ multi_agent_post_message("## MR #{N} 检视完成\n\nP1: {n} | P2: {n} | P3: {n}\n\n@{作者} 请处理检视意见，完成后回复「已修改完成」。")
```

## Step 4: 等待修改（Phase 8）

```
事件驱动: MR comment webhook → github-review-feedback connector 自动唤醒
轮询降级: multi_agent_hold_ball({ wakeAfterMs: 300000, waitSourceRef: { kind: "mr-comment", value: "{MR_URL}", expectedSignal: "已修改完成" } })
```

## Step 5: 复审修改（Phase 9 — 检视 agent A）

```bash
# 拿最新 diff
gh pr diff {N} --repo {owner}/{repo}

# 逐条确认 P1/P2 意见:
#   已修复 → 在 MR comment reply "✅ 已确认"
#   未修复 → reply "❌ 仍未解决" + 回 Step 1 补提交
#   新问题 → 新增意见 + 回 Step 1
```

## Step 6: 闭环审批（Phase 10）

```bash
# 0 P1/P2 → approve
gh pr review {N} --approve --body "✅ 检视闭环，所有意见已处理。"
# 或 logical approve: gh pr comment {N} --body "✅ Logical Approve"

# 关闭关联 issue（从 MR body / commit 解析 fixes #N / closes #N）
for ISSUE_NUM in {resolved_issues}; do
  gh issue close $ISSUE_NUM --repo {owner}/{repo} \
    --comment "✅ 已通过 MR #{N} 修复并检视闭环。[检视 agent: {agentA}/{modelA}]"
done

# 群通知完成
→ multi_agent_post_message("✅ MR #{N} 检视闭环，已批准合入。关联 issue 已关闭。")

# 检视 thread 归档
```

## 收尾完成标志

- [ ] 所有 P1/P2 意见已提交为行级 DiffNote（附 agent 签名）
- [ ] 合入门禁已设置（request-changes 或 logical verdict）
- [ ] MR 作者已通知
- [ ] 修改已复审（逐条 confirmed）
- [ ] 0 未解决 P1/P2
- [ ] approve 已落（gh 或 logical）
- [ ] 关联 issue 已关闭（如有）
- [ ] 群通知已发送
- [ ] 检视 thread 可归档
