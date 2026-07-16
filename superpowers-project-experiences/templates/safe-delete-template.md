# .RecycleBin 安全删除机制

> 以下模板供 Step 1 安全归档文件时参考。

## 工作流程

### safeDelete(filePath)

1. 确认 .RecycleBin/ 存在（不存在则创建）
2. 生成目标路径: .RecycleBin/{ISO时间戳}-{原文件名}
3. mv filePath → .RecycleBin/{时间戳}-{文件名}
4. 记录日志: "[safe-delete] {filePath} → .RecycleBin/{时间戳}-{文件名}"

### restore(recycleBinPath)

1. 从 .RecycleBin/{时间戳}-{文件名} 中解析原文件名
2. mv 回原路径（如原路径已存在同名文件，提示冲突，不覆盖）

## .RecycleBin 目录结构

```
.RecycleBin/
├── 2026-07-12T12-30-00-000Z-deck-restructure.md
├── 2026-07-12T12-30-00-001Z-data-migration.md
└── ...
```

时间戳前缀确保同文件多次归档不冲突。ISO 格式中 `:` 替换为 `-` 以避免路径问题。

## 命令拦截规则

| 命令模式 | 拦截动作 | 提示 |
|----------|----------|------|
| `rm` / `del` / `rmdir` / `unlink` / `remove` | 重定向到 safeDelete | "已将文件移至 .RecycleBin/，未执行删除" |
| `rm -rf .RecycleBin` / 清空 .RecycleBin 的任何命令 | **拦截，不执行** | "⚠️ .RecycleBin 清空只能由 co-creator 手动执行。Agent 无权清空回收站。" |
| `rm .RecycleBin/*` / `find .RecycleBin -delete` 等 | **拦截，不执行** | 同上 |

> **铁律**：任何 Agent（包括本 skill）都无权清空 `.RecycleBin/`。只有 co-creator 能手动清空。
