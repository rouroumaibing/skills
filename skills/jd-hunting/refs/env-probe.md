# L0 环境探针 — jd-hunting

> 本文件是 `SKILL.md` 的环境探针参考。

用户触发 skill 后、第一步之前，主对话直接执行（不调子代理）。探针结果写入 `outputs/00-index.md` 头部。

## 探针清单

| 探针项 | 检测方式（macOS/Linux） | Windows PowerShell 等价命令 | 00-index.md 字段 |
| :--- | :--- | :--- | :--- |
| docx skill 可用性 | LS `~/.trae-cn/skills/docx/` | 同左（LS 工具屏蔽平台差异） | `docx_skill: yes/no` |
| pdf skill 可用性 | LS `~/.trae-cn/skills/pdf/` | 同左 | `pdf_skill: yes/no` |
| pandoc 可用性 | `pandoc --version` | `pandoc --version`（若已加入 PATH） | `pandoc: yes/no` |
| python-docx 可用性 | `python3 -c "import docx"` | `python -c "import docx"` | `python_docx: yes/no` |

**跨平台实施约定**：探针指令在主对话执行时，根据当前 OS 选择等价命令。不写死 `python3` 或 `curl`。

## 00-index.md 探针结果写入规范

探针完成后，创建 `outputs/00-index.md`，头部写入：

```markdown
# 求职辅助进度看板

## 环境探针（{今日日期}）
- docx_skill: yes
- pdf_skill: yes
- pandoc: no
- python_docx: yes

## 当前阶段
第一步：简历解析（进行中）

## 产出索引
（每步完成后追加）
```
