# Skills — 技能仓库

本仓库包含一组可复用的 Skill，覆盖代码检视、架构分析、面试备战和经验提炼等场景。
A collection of reusable Skills covering code review, architecture analysis, interview prep, and experience distillation.

## Skills / 技能索引

以下是本仓库包含的所有 skill，点击链接查看完整文档。
All skills in this repository. Click links for full documentation.

| Skill | 功能说明 | 触发词 |
|---|---|---|
| [`multi-agent-github-mr-review`](skills/multi-agent-github-mr-review/SKILL.md) | 双 agent 对抗式代码审查，产出行级 DiffNote 并阻塞合入直到闭环 | `@某agent+MR链接` `GitHub MR 检视` `PR review` |
| [`code-analyzer-like-openwiki`](skills/code-analyzer-like-openwiki/SKILL.md) | 分析代码库生成含 Mermaid 图表的开放式技术 Wiki，支持本地预览 | `分析代码库` `生成技术文档` `梳理架构` `画架构图` |
| [`project-to-interview-experiences`](skills/project-to-interview-experiences/SKILL.md) | 将项目源码转化为可口述、防砸盘、带量化数据的面试备战讲稿 | `项目转面试` `面试备战` `面试讲稿` `面试经验` |
| [`superpowers-project-experiences`](skills/superpowers-project-experiences/SKILL.md) | 从已交付计划提炼可复用工程知识，建立模块索引，生成重生成提示词 | `总结经验` `归档` `建立索引` `更新索引` |
| [`jd-hunting`](skills/jd-hunting/SKILL.md) | 求职全流程辅助：简历解析→岗位推荐→招聘网站检索→适配度评分→定制简历与面试问答 | `找工作` `求职` `投递简历` `简历定制` `面试准备` |
| [`10x-learn`](skills/10x-learn/SKILL.md) | AI 十倍速深度学习闭环：五视角 STORM 调研 + 费曼/测试效应，按 入门→资源→吃透→回顾 五阶段输出提示词与一页速查表 | `系统学习` `吃透某领域` `快速入门` `10xLearn` `深度调研` |
| [`mini-code`](skills/mini-code/SKILL.md) | 安全的代码精简技能：先分类冗余、守住"行为不变"底线，拒绝无测试兜底的批量精简与错误合并测试；主动识别数据源扇入冗余与不必要的抽象（YAGNI）；覆盖测试代码精简，并对测试框架形态重构充当安全护栏（不主动发起）；尤适用于 AI 生成代码的去重与去过度设计 | `精简代码` `去除冗余代码` `简化这段代码` `合并重复逻辑` `提取公共模块` `识别过度设计` `不必要的 class` `YAGNI` `合并同源读取` `测试代码精简` `clean up this code` `remove dead code` `consolidate duplicated I/O` `detect unnecessary abstraction` `slim test code` |
| [`software-story-design`](skills/software-story-design/SKILL.md) | 软件设计文档撰写：设计目的、设计逻辑、概念空间建模、核心数据结构、接口定义与一致性校验；覆盖新建模块、功能扩展、性能优化、重构方案与复杂系统逻辑视图建模（线程模型、状态机等） | `设计模块` `增加功能` `写设计文档` `@software-story-design` |

## 开发规范 / Development Guide

### 标准化规范

1. **Frontmatter**：`description` 为"中文核心触发词 + 英文功能摘要"双语组合
2. **术语保留**：正文中文叙述，类名/API/配置 Key/专有名词保留英文原文（如 `ThreadPoolExecutor`、`JWT`、`epoll`）
3. **环境中立**：路径用相对路径（如 `./src`）或占位符（如 `<root_dir>`），无个人敏感信息

### 新增 Skill

1. 创建目录 `skills/<skill-name>/`
2. 编写 `SKILL.md`（遵循上述 3 条规范）
3. 更新本文件 Skills 索引表格
