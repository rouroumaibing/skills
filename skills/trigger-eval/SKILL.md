---
name: trigger-eval
description: "触发词：触发评测、skill 触发测试、trigger eval、description 命中率、误触发、漏触发、skill 冲突检测。用独立子 agent 当评审，对一批本地 skill 做触发判定评测（给定查询，判断模型会不会调用正确的 skill），出命中率/FP/FN 与混淆矩阵；当官方评测 harness 依赖 claude -p 登录、未登录会静默全判 False 时，用本法零登录重跑，评审模型与宿主同源更对齐。"
license: MIT
metadata:
  agent_created: true
  version: "1"
---

# trigger-eval — skill 触发评测（子 agent 代理法）

## 何时用

- 你改了一批 skill 的 `description`，想验证"该触发时触发、不该触发时不触发"。
- 仓库里多个 skill 挤在一起，担心互相抢触发（混淆）。
- **skill-creator 的官方 harness 跑不起来时**：`scripts/run_eval.py` 依赖 `claude -p`，需要 Claude Code CLI 已登录。本机若未登录，它会静默返回 `authentication_failed` 却 `exit 0`，harness 把每条查询都判成"未触发"——反例反而"通过"，是**假红伪装成真红**的陷阱。此时改用本 skill 的子 agent 代理法。

## 方法（子 agent 代理法）

核心：触发决策本质是"给定 `available_skills`（name+description）列表 + 一条查询，模型选哪个 skill"。用一个**独立子 agent** 当评审，喂它完整的候选列表 + 查询，让它只从候选里选一个该调用的 skill（或 NONE）。评审用的是与宿主（host）同源的模型、同一套 `available_skills` 机制，比 `claude -p` 更贴近真实环境，且零登录。

### 步骤

1. **准备评测集**：每 skill 一份 `evals/trigger-eval.json`，格式 `[{"query":"...","should_trigger":true/false}, ...]`，各 10 正 10 反共 20 条。反例必须是**近邻难例**（从其他 skill 的领地取材），而不是明显无关的"写斐波那契"——后者测不出东西。
2. **抽候选列表**：取并集 = `已安装(<用户级 skills 安装目录>) ∪ 仓库定义(repo/skills)`，按 name 去重，每个取 `name`+`description`。用 **YAML 解析** frontmatter（很多 description 是无引号单行，正则 `^description:"(.*)"$` 会漏；个别文件的 frontmatter 本身可能不是合法 YAML，须逐文件容错跳过）。
3. **生成评审提示**：每份提示 = 任务说明 + 完整候选列表 + 该 skill 的 20 条查询；要求输出严格 JSON `[{"idx":1,"choice":"<skill名或NONE>","reason":"..."}]`，且只能从候选列表选。
4. **并行派发子 agent**：N 份提示 → N 个 `general-purpose` 子 agent 并行（N = 被测 skill 数），各读自己的提示、只回 JSON。
5. **打分**：对每 skill，`should_trigger=True` 且 `choice==skill` ⇒ 命中；`should_trigger=False` 且 `choice != skill` ⇒ 命中。统计 acc / 正例 / 反例 / FP / FN，并建正向混淆矩阵（本该触发的 skill → 被谁截走）。

### 候选 YAML 解析片段

```python
import yaml, glob, os, json
USER_SKILLS_DIR = os.environ['USER_SKILLS_DIR']  # 宿主的用户级 skills 安装目录（如 ~/.<host>/skills），经环境变量注入
def parse(path):
    t = open(path, encoding='utf-8').read()
    if not t.startswith('---'): return None, ''
    try:
        d = yaml.safe_load(t.split('---', 2)[1]) or {}
    except Exception:
        return None, ''  # 非法 frontmatter 的文件跳过，不让单个坏文件炸掉全量扫描
    return str(d.get('name')).strip() if d.get('name') else None, str(d.get('description','')).strip()
installed, repo = {}, {}
for p in glob.glob(os.path.join(USER_SKILLS_DIR, '*', 'SKILL.md')):
    n,de = parse(p)
    if n and not n.startswith('.'): installed[n]=de
for p in glob.glob('skills/*/SKILL.md'):
    n,de = parse(p)
    if n: repo[n]=de
union = dict(installed); union.update(repo)  # 仓库版优先：被测对象以仓库为准
cands = [{'name':k,'description':v} for k,v in sorted(union.items()) if v]
```

### 评审提示关键约束（写进子 agent 提示）

- "这是你唯一可依据的触发信息——不要调用列表以外的 skill，也不要用内部知识假设别的 skill。"
- "必须只从 N 个名称 或 NONE 中选一个。NONE = 你会直接用内置工具自己处理、不加载任何 skill。"（N = 候选数，按实际值代入）
- "明显属于某 skill 领地时，即使也能用工具直接做，也应选那个 skill。"
- "严格只输出 JSON 数组，不要 markdown 围栏，不要额外文字。"

## 重要边界（务必先讲给用户）

1. **单判非方差平均**：官方 harness 每条查跑 3 次取触发率；本法是 1 个独立评审 ×1 遍。`100%` 读作"单个评审零混淆"，不是生产零失手。要更严就把每条跑 3 次。
2. **评测集由模型生成的偏乐观**：正例措辞清晰、反例取自他者领地，会让 100% 偏乐观；但评审只看到 description 看不到标签，决策步独立。可补"更难反例"（语义近邻）压边界。
3. **未安装的 skill 的 100% 是设计侧结论**：它装上前根本不会被触发，与描述质量无关。先确认安装面，评测才有生产意义。
4. **竞争面要选对**：测"实际会不会触发对"用真实已安装集；测"内部自相残杀"用仓库集；最严苛用并集。默认并集（已安装 ∪ 仓库）。
5. **同条件对比才有效**：验证"改 description 有没有回退"时，新旧两轮必须用同一查询集、同一评审约束，只换被测 description；否则差异读不出因果。

## 真 gate（跑完必须自查）

- 候选列表非空、每个 skill 有 description（无描述的会被漏掉，竞争面失真）。
- 每份判定 JSON 长度 == 20，且 choice 都在候选名 ∪ {NONE} 内（有非法值说明子 agent 没遵守格式）。
- acc / FP / FN 与混淆矩阵同屏复核。
