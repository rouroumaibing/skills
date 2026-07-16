# Reference — GitHub MR Review

> 本文件是 `cat-cafe-github-mr-review/SKILL.md` 的速查表、常见错误、skill 区别和通知通道参考。

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
| **cat-cafe-github-mr-review（本 skill）** | 群内 @猫猫+MR链接 | 外部 MR | 行级 DiffNote + 阻塞合入 | 外部触发、双猫对抗、10 阶段闭环 |
| `quality-gate` → `request-review` → `merge-gate` | 内部开发完成 | 猫咖自己的 PR | 内部 review + merge | 内部自驱、单 reviewer、SOP 链 |
| `receive-review` | reviewer 反馈 | 自己的代码 | 修复确认 | 处理别人给的 review，不是自己做 review |
| `thread-orchestration` | 主动拆任务 | 多子任务 | 多 thread 编排 | 通用编排，不含检视/对抗验证协议 |
| `fresh-context-review` | author 自驱 | 自己的 PR diff | finding list | 单猫 finding generator，无对抗验证 |

## 通知通道

Phase 7/10 的群通知通过 `cat_cafe_post_message` 发送，由 `OutboundDeliveryHook` 自动投递到 thread 绑定的 IM 通道。通知默认走 thread 绑定的通道，新增通道后在 Hub UI 绑定即可，无需改 skill。
