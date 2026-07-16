---
name: cat-cafe-github-mr-review
description: "GitHub MR 全链路代码检视流水线。10 阶段双猫对抗式审查，产出行级 DiffNote 并阻塞合入直到闭环。Invoke when 群里收到 @猫猫+MR链接 或需要检视外部 GitHub MR/PR。"
---

# GitHub MR Review — 10 阶段全链路代码检视

> **上一步**: 无（由群内 @猫猫+MR链接 触发）| **下一步**: 无（闭环后通知完成）

## 价值门禁

这不是"用 gh pr diff 看一眼"——是**双猫对抗式结构化代码审查**，产出可追溯的行级意见并阻塞合入直到闭环。
和猫咖内部 review 链的区别：内部链是 author 自驱（quality-gate → request-review → merge-gate）；
本 skill 是**外部触发**（群里 @猫猫+MR链接），检视对象是外部 MR，产出物是 MR 上的行级 DiffNote。

## 10 阶段速查

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

## 分工

| 角色 | 职责 | 约束 |
|------|------|------|
| **检视猫 A** | Phase 3 逐文件审查，产出三级意见；Phase 9 复审修改 | ≠ MR 作者 |
| **验证猫 B** | Phase 4 对抗验证，逐条质疑检视意见 | ≠ 检视猫 A，≠ MR 作者 |
| **协调猫** | Phase 1 触发+开 thread，Phase 7/10 通知，全程协调 | 可由 A 或 B 兼任，但不做检视/验证 |

> **no self-review 铁律**：检视猫 A ≠ 验证猫 B ≠ MR 作者。跨 family 优先。

## 详细文档

| 文档 | 内容 |
|------|------|
| [refs/core-knowledge.md](refs/core-knowledge.md) | 核心知识：角色分离、三级意见、Inbound 五问、复杂 PR 策略、复用关系 |
| [refs/phase-details.md](refs/phase-details.md) | 10 阶段详细执行规范（触发条件、步骤、命令、输出格式） |
| [refs/closing-flow.md](refs/closing-flow.md) | 收尾流程：Phase 6-10 操作清单 + 闭环标志 |
| [refs/reference.md](refs/reference.md) | 速查表 + 常见错误 + skill 区别 + 通知通道 + 测试记录 |
