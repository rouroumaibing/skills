# 各语言工具与陷阱参考表（运行时按需加载）

> 本文件由 `SKILL.md` 的「可选：静态检查工具辅助」一节引用，作为**运行时按需加载**的参考，**不是技能执行的必需文件**。技能的核心判断逻辑全部在 `SKILL.md`，本表只补充「每语言该跑哪个工具」与「该警惕哪些语言特异性伪冗余陷阱」。
>
> 定位提醒：以下工具仅用于**提高候选发现效率**，输出必须过 `SKILL.md`「第二步」的五项 checklist，**禁止**直接把工具标记的 duplicate code 当结论。

## Go

| 检查目的 | 推荐工具 |
|---|---|
| 综合 linter | `golangci-lint`（聚合 staticcheck/govet/errcheck） |
| 死代码 | `staticcheck`（`U1000`）、`deadcode`（反射/interface 实现会漏检） |
| 重复代码 | `dupl`（不理解 error wrap 语义差异） |
| 圈复杂度 | `gocyclo` |
| error 规范 | `errcheck`、`wrapcheck` |
| 覆盖率 | `go test ./... -cover` |

**特有陷阱**：`error` wrap 链（`errors.Is/As`）；goroutine/channel 所有权语义；接口隐式实现（合并函数可能打断某 interface 的隐式满足）。

## Python

| 检查目的 | 推荐工具 |
|---|---|
| 综合 linter | `ruff`（主力）、`pylint`（可选叠加） |
| 死代码 | `vulture`（动态语言天然不完备，需人工复核） |
| 重复代码 | `pylint --enable=duplicate-code`、`jscpd` |
| 圈复杂度 | `radon cc`、`xenon` |
| 类型检查 | `mypy` / `pyright`（类型不一致常暴露伪冗余） |
| 覆盖率 | `pytest --cov --cov-report=term-missing` |

**特有陷阱**：`None` vs 空字符串 vs 空集合的边界差异；装饰器注册（Flask 路由、Celery task）让 grep 找不到调用点；`async def` 与同步函数的隐式差异。

## TypeScript

| 检查目的 | 推荐工具 |
|---|---|
| 综合 linter | `eslint`（配 `@typescript-eslint`） |
| 死代码 | `knip`（推荐）、`ts-prune` |
| 重复代码 | `jscpd` |
| 圈复杂度 | `eslint-plugin-complexity` |
| 类型语义差异 | `tsc --noEmit`（严格模式） |
| 覆盖率 | `jest --coverage` / `vitest --coverage` |

**特有陷阱**：`null` vs `undefined` vs 可选属性 `?:` 的语义差异；Promise 链 vs async/await 混用；联合类型收窄逻辑不同。

## React（构建于 TS/JS 之上）

| 检查目的 | 推荐工具 |
|---|---|
| Hooks 规则 | `eslint-plugin-react-hooks`（精简自定义 hook 最易踩依赖数组的坑） |
| 组件重复 | `jscpd` 针对 `.tsx`（JSX 相似 ≠ 该合并） |
| 未用 props | `eslint-plugin-react` 的 `no-unused-prop-types` |
| 状态管理冗余 | 无标准工具，人工识别多个 store/context 是否读同一数据源 |

**特有陷阱**：受控 vs 非受控组件；`useEffect` 依赖数组不同导致重渲染时机差异；自定义 hook 表面同名但内部依赖不同 context。

## Vue3

| 检查目的 | 推荐工具 |
|---|---|
| 综合 linter | `eslint-plugin-vue`（配 `vue3-recommended`） |
| composable 重复 | `jscpd` 针对 `.vue` / `composables/*.ts` |
| 死代码 | `knip`（支持 SFC） |
| 模板复杂度 | 人工 review 为主（v-if/v-for 嵌套过深是过度设计信号） |

**特有陷阱**：`ref` vs `reactive` 语义差异；`watch` 的 `immediate`/`deep` 选项不同；composable 间共享 vs 各自持有响应式状态（合并可能意外引入跨组件状态共享）。

## Rust

| 检查目的 | 推荐工具 |
|---|---|
| 综合 linter | `clippy` |
| 死代码 | 编译器 `#[warn(dead_code)]`（trait 实现/pub 导出需注意跨 crate 引用） |
| 重复代码 | `jscpd` 或人工 review |
| 所有权/生命周期 | **编译器本身**（Rust 独有优势） |
| 覆盖率 | `cargo tarpaulin` |

**特有陷阱**：`Result<T, E>` 中 `E` 类型不同（合并需引入统一错误枚举，属结构性改动，要走「提案」流程）；trait 泛型约束不同；所有权语义差异（`&self` vs `&mut self` vs 消费 `self`）——即使逻辑相似也**不能**简单合并。

## 跨语言

`jscpd` 是目前唯一能横跨 Go/Python/TS/Vue/Rust 统一跑的重复检测工具，适合多语言混合仓库作为候选发现的第一层，各语言专属 linter 作为第二层补充。
