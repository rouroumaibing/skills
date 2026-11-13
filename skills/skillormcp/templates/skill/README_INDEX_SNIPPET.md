# 双 README 索引条目片段（复制后替换 `<...>`）

把下面两行分别加进 `README.md` 与 `README_EN.md` 的 skill 索引表。**两条的触发词列（第三列）必须逐词一致**（中英两份都要写中文触发词）。

## README.md（中文索引表，加一行）

```
| [`<skill-name>`](skills/<skill-name>/SKILL.md) | <一句话功能说明（中文）> | `<触发词1>` `<触发词2>` `<触发词3>` |
```

## README_EN.md（英文索引表，加一行，触发词列与中文保持一致）

```
| [`<skill-name>`](skills/<skill-name>/SKILL.md) | <one-line English description> | `<触发词1>` `<触发词2>` `<触发词3>` |
```

## 字段对照

- `<skill-name>`：与 SKILL.md frontmatter 的 `name` 完全一致（kebab-case）。
- 第二列：中文写功能说明；英文写 one-line 摘要。
- 第三列：触发词，用反引号包裹、空格分隔；中文两份都写同一组中文触发词（英文表的第三列也是中文触发词，不是英文）。

## 可选：审计规则（仅当仓库尚未写入时加一次）

在索引表之后加一句：

```
**审计规则**：新增或调整 `skills/` 与 `mcps/` 内容前，可调用 `@skillormcp`（或 `/skillormcp`）审计落点是否错位。该 skill 只报建议、不改文件。
```
