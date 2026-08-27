# 伪冗余陷阱示例（跨语言）

> 本文件由 `SKILL.md` 的「伪冗余陷阱示例（跨语言）」节按需引用，**不是运行时必读**。判断伪冗余的核心仍是 SKILL.md「第二步」的五项 checklist；此处只提供两个高频语言特异形态的完整代码与合并风险，帮助理解"为什么外观相似 ≠ 语义相同"。

## TypeScript：空值语义不同

```ts
// 看似重复，实为伪冗余
function getDisplayName(user: User): string {
  return user.name.trim().toLowerCase(); // 假设 name 必存在
}

function getSafeDisplayName(user: User | null): string {
  return user?.name?.trim().toLowerCase() ?? "匿名用户"; // 全链路空值兜底
}
```

合并风险：要么给前者加上它不需要的兜底（可能掩盖上游本该暴露的 bug），要么让后者失去兜底（页面上显示出 `undefined`）。

## Go：error 处理语义不同

```go
// 看似重复，实为伪冗余
func ReadConfig(path string) ([]byte, error) {
    data, err := os.ReadFile(path)
    if err != nil {
        return nil, fmt.Errorf("read config: %w", err) // wrap，调用方依赖 errors.Is
    }
    return data, nil
}

func ReadCache(path string) ([]byte, error) {
    data, err := os.ReadFile(path)
    if err != nil {
        return []byte{}, nil // 缓存缺失是正常路径，有意吞掉错误
    }
    return data, nil
}
```

合并风险：统一 error 处理会打断 `errors.Is` 链，或让配置缺失这种真正的故障被当成"缓存未命中"静默吞掉。

## 与五项 checklist 的对应

| 示例 | 触发的 checklist 项 | 差异点 |
|---|---|---|
| `getDisplayName` vs `getSafeDisplayName` | 空值处理 | 必填字段 vs 全链路 `??` 兜底 |
| `ReadConfig` vs `ReadCache` | 异常/错误类型 | `%w` wrap（供 `errors.Is`）vs 有意吞错（正常路径） |

任一不同 → 判伪冗余，标记差异点交用户决定是否统一。
