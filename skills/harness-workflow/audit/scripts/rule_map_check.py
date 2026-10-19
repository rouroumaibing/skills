#!/usr/bin/env python3
"""rule_map_check.py —— R-PRC-006 实装（规则映射完整且可机检）。

审什么：消费项目自建的规则映射台账 `<governance>/rule-map.yaml`——
`required_rules`（合入必需的规则子集）逐条有映射、机制可执行、与登记表对齐。
校验项（每项独立报错，全部通过才 PASS）：
  1. 文件存在且为 YAML mapping（缺失/损坏 → 42 fail-closed）；
  2. `required_rules` 为非空列表，每条为 mapping；
  3. 规则 ID 存在于本包登记表（内置 25 条 ID 清单；治理建设要求 14 条已迁
     attack-cases.md「治理机制设计要点」，不是登记表规则，不可采纳 → 违反）；
  4. ID 无重复定义；
  5. 机制字段 `mechanism` ∈ {script, ci, manual}；script 机制必须有非空 `script`
     指针；**ci 机制必须有非空 `ci_step`**（v12——没有可核对步骤名的 ci 映射
     等于"已映射"三个字的空话）；
  6. 软规则不得进 required_rules（软规则靠抽查，进必需清单 = 假强制）；
  7. `ci_step`（可选）须为非空字符串；
  8. **script 指针可执行实检**（v10 查存在、v12 升格）：指针解析后必须
     (a) 落在仓库根内（拒绝 /bin/ls 类仓库外绝对路径与 `../` 穿越）——
     可执行机制必须随仓库走，不能指向环境巧合；
     (b) 是普通文件（不是目录）；
     (c) 带可执行位（chmod +x）——R-PRC-006 承诺的是"存在且可执行"。
     解析顺序：绝对路径原值；相对路径先仓库根（台账目录的上级）相对、
     再 cwd 相对；
  9. **与 redlines.yaml 的采纳边界一致**（--redlines，缺省读
     <GOVERNANCE_DIR>/redlines.yaml）：
     - 采纳清单本身的校验（v12——清单是权威采纳边界，拼写错误 = 悄悄缩小
       审计范围）：未知 ID → 违反；重复 ID → 违反；
     - 台账强制了 redlines.required_rules 未采纳的规则 → 违反（强制未采纳 =
       替项目立法）；
     - redlines 采纳的硬规则在台账中无映射 → 违反（采纳了却无强制映射）；
     - redlines 未采纳任何规则而台账非空 → 违反；
     - **redlines.yaml 缺失或未声明 required_rules 而台账非空 → 同样违反**
       （v12——"没有采纳声明"与"采纳清单为空"语义相同，不再只打警告）。
     台账非空而红线上不存在任何采纳声明，二者必有一个是错的。

登记表元数据（ID + 软硬等级）内置在本脚本 RULES 中；self-test 会解析
`references/rule-registry.md` 的规则行数与 RULES 逐条比对，防止两处漂移。

台账格式：
  required_rules:
    - id: R-GOAL-001
      mechanism: script          # script | ci | manual
      script: audit/scripts/check_contract.py   # mechanism=script 时必填，
                                                # 且须仓库内普通文件 + 可执行位
      ci_step: check-contract    # mechanism=ci 时必填；script/manual 可选
退出码：0 通过 / 1 违反 / 42 未接线（文件缺失/损坏）。

用法：
  rule_map_check.py [--rule-map <path>] [--registry <path>] [--redlines <path>]
      [--self-test]
  --rule-map 缺省读 <GOVERNANCE_DIR>/rule-map.yaml；
  --redlines 缺省读 <GOVERNANCE_DIR>/redlines.yaml（缺失/未声明 required_rules
  而台账非空 → 违反，见第 9 条）。
"""
import os
import sys
import io
import re
import tempfile
import shutil

try:
    import yaml
except ImportError:
    yaml = None

VIOLATION = 1
OK = 0
NOT_WIRED = 42
GOVERNANCE_DIR = os.environ.get("GOVERNANCE_DIR", "governance")

# ---- 登记表元数据（与 references/rule-registry.md 同步维护；self-test 防漂移）----
# v10：治理建设要求 14 条（R-VER-003/004、R-REL-001/004/005/006、
# R-SEC-001/002/005/006、R-PRC-001/002/004/005）已迁 references/attack-cases.md
# 「治理机制设计要点」，不再是登记表规则、不可采纳进 required_rules。
RULES = {
    "R-GOAL-001": "硬", "R-GOAL-002": "硬", "R-GOAL-003": "软", "R-GOAL-004": "软",
    "R-DES-001": "硬", "R-DES-002": "软", "R-DES-003": "软",
    "R-IMP-001": "硬", "R-IMP-002": "硬", "R-IMP-003": "硬", "R-IMP-004": "硬",
    "R-IMP-005": "硬",
    "R-VER-001": "硬", "R-VER-002": "硬", "R-VER-005": "硬", "R-VER-006": "硬",
    "R-VER-007": "硬",
    "R-REL-002": "硬", "R-REL-003": "硬",
    "R-SEC-003": "硬", "R-SEC-004": "硬",
    "R-PRC-003": "软", "R-PRC-006": "硬", "R-PRC-007": "硬",
    "R-RISK-001": "硬",
}
VALID_MECHANISMS = ("script", "ci", "manual")


def _script_pointer_violations(pointer, map_path, tag, rid):
    """v12：'可执行' 的最低证据 = 解析后落在仓库根内、是普通文件、带可执行位。

    解析顺序：绝对路径原值；相对路径先仓库根（台账目录的上级）相对、
    再 cwd 相对。返回违反描述列表（空列表 = 通过）。
    """
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(map_path)))
    cands = []
    if os.path.isabs(pointer):
        cands.append(pointer)
    else:
        cands.append(os.path.abspath(os.path.join(repo_root, pointer)))
        cands.append(os.path.abspath(pointer))
    resolved = next((c for c in cands if os.path.exists(c)), None)
    if resolved is None:
        return ["%s (%s): script pointer %r does not exist on disk (checked "
                "repo-root-relative and cwd-relative)" % (tag, rid, pointer)]
    root_real = os.path.realpath(repo_root)
    real = os.path.realpath(resolved)
    if real != root_real and not real.startswith(root_real + os.sep):
        return ["%s (%s): script pointer %r resolves outside the repository (%s); "
                "executable mechanisms must live inside the repo (no /bin/ls, "
                "no ../ traversal)" % (tag, rid, pointer, real)]
    if not os.path.isfile(resolved):
        return ["%s (%s): script pointer %r is not a regular file (directory?)"
                % (tag, rid, pointer)]
    if not os.access(resolved, os.X_OK):
        return ["%s (%s): script pointer %r exists but is not executable "
                "(chmod +x); R-PRC-006 promises 'exists and is executable'"
                % (tag, rid, pointer)]
    return []


def _ids_from_registry(path):
    """从 rule-registry.md 抽规则 ID（行首 | R-XXX-000），供 --registry 覆盖内置清单。"""
    ids = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^\| (R-[A-Z]+-\d+) \|", line)
            if m:
                ids[m.group(1)] = ""
    return ids


def _adopted_ids_from_redlines(path):
    """读 redlines.yaml 的 required_rules 采纳清单；返回 (found, ids_list)。

    found=False：文件存在但未声明 required_rules（缺失文件由调用方处理）。
    条目兼容字符串与 {id: ...} mapping 两种形态；**保留顺序与重复**（v12：
    重复 ID 是采纳边界的缺陷，不能在读取时静默去重）。
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        print("rule_map_check: redlines.yaml is corrupt; treat as NOT-WIRED.\n  %s"
              % e, file=sys.stderr)
        sys.exit(NOT_WIRED)
    rr = data.get("required_rules") if isinstance(data, dict) else None
    if rr is None:
        return False, []
    if not isinstance(rr, list):
        print("rule_map_check: redlines.yaml 'required_rules' must be a list "
              "(got %s); treat as NOT-WIRED." % type(rr).__name__, file=sys.stderr)
        sys.exit(NOT_WIRED)
    ids = []
    for ent in rr:
        if isinstance(ent, str) and ent.strip():
            ids.append(ent.strip())
        elif isinstance(ent, dict) and isinstance(ent.get("id"), str):
            ids.append(ent["id"].strip())
    return True, ids


def main(argv):
    if "--self-test" in argv:
        return self_test()
    gov_dir = os.environ.get("GOVERNANCE_DIR", GOVERNANCE_DIR)
    map_path = redlines_path = reg_path = None
    i = 0
    while i < len(argv):
        if argv[i] == "--rule-map":
            map_path = argv[i + 1]
        elif argv[i] == "--registry":
            reg_path = argv[i + 1]
        elif argv[i] == "--redlines":
            redlines_path = argv[i + 1]
        i += 1
    if map_path is None:
        map_path = os.path.join(gov_dir, "rule-map.yaml")
    if redlines_path is None:
        redlines_path = os.path.join(gov_dir, "redlines.yaml")

    if yaml is None:
        print("rule_map_check: pyyaml required (pip install pyyaml).", file=sys.stderr)
        return NOT_WIRED
    if not os.path.exists(map_path):
        print("rule_map_check: rule-map.yaml not found: %s — rule mapping NOT built; "
              "treat as NOT-WIRED (fail-closed). Bootstrap: list which of the 25 "
              "registry rules are merge-required and how each is enforced "
              "(script / ci / manual)." % map_path, file=sys.stderr)
        return NOT_WIRED
    try:
        with open(map_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        print("rule_map_check: rule-map.yaml is corrupt; treat as NOT-WIRED.\n  %s"
              % e, file=sys.stderr)
        return NOT_WIRED
    if not isinstance(data, dict) or not isinstance(data.get("required_rules"), list) \
            or not data["required_rules"]:
        print("rule_map_check: rule-map.yaml must be a mapping with a non-empty "
              "'required_rules' list; treat as NOT-WIRED.", file=sys.stderr)
        return NOT_WIRED

    rules = RULES
    if reg_path is not None:
        ids = _ids_from_registry(reg_path)
        if ids:
            rules = ids  # 仅 ID 对齐用；等级仍取内置

    violations = []
    seen = set()
    for idx, ent in enumerate(data["required_rules"], 1):
        tag = "required_rules[%d]" % idx
        if not isinstance(ent, dict):
            violations.append("%s is not a mapping" % tag)
            continue
        rid = ent.get("id")
        if rid not in rules:
            violations.append("%s: unknown rule id %r (not in registry)" % (tag, rid))
            continue
        if rid in seen:
            violations.append("%s: duplicate definition of %s" % (tag, rid))
        seen.add(rid)
        mech = ent.get("mechanism")
        if mech not in VALID_MECHANISMS:
            violations.append("%s (%s): mechanism must be one of %s (got %r)"
                              % (tag, rid, "|".join(VALID_MECHANISMS), mech))
        if mech == "script":
            ptr = ent.get("script")
            if not (isinstance(ptr, str) and ptr.strip()):
                violations.append("%s (%s): mechanism=script requires a non-empty "
                                  "'script' pointer" % (tag, rid))
            else:
                violations.extend(_script_pointer_violations(ptr, map_path, tag, rid))
        if mech == "ci" and not (isinstance(ent.get("ci_step"), str)
                                 and ent["ci_step"].strip()):
            violations.append("%s (%s): mechanism=ci requires a non-empty "
                              "'ci_step' (a mapped gate must be checkable)"
                              % (tag, rid))
        if rules.get(rid) == "软":
            violations.append("%s (%s): soft rule must not be in required_rules "
                              "(soft rules are spot-checked, faking enforcement)"
                              % (tag, rid))
        if "ci_step" in ent and not (isinstance(ent["ci_step"], str)
                                     and ent["ci_step"].strip()):
            violations.append("%s (%s): 'ci_step' must be a non-empty string"
                              % (tag, rid))

    # ---- 采纳边界一致性（v10；v12 扩充）----
    redlines_found = os.path.exists(redlines_path)
    declared = False
    adopted_list = []
    if redlines_found:
        declared, adopted_list = _adopted_ids_from_redlines(redlines_path)
    if declared:
        # v12：采纳清单本身是权威边界，先校验其质量
        for a in adopted_list:
            if a not in rules:
                violations.append(
                    "redlines.yaml adopts unknown rule id %r (not in registry; "
                    "a typo silently shrinks the audit scope)" % a)
        counts = {}
        for a in adopted_list:
            counts[a] = counts.get(a, 0) + 1
        for a in sorted(k for k, v in counts.items() if v > 1):
            violations.append(
                "redlines.yaml adopts %r %d time(s) (duplicate adoption entry)"
                % (a, counts[a]))
        adopted = set(adopted_list)
        if not adopted and seen:
            violations.append(
                "rule-map enforces %d rule(s) but redlines.yaml 'required_rules' "
                "adopts none — enforcing unadopted rules is legislation, adopt "
                "them in redlines.yaml first" % len(seen))
        for rid in sorted(seen - adopted):
            violations.append(
                "rule-map enforces %r but it is not adopted in redlines.yaml "
                "'required_rules' (enforcing unadopted rules)" % rid)
        for rid in sorted(a for a in adopted
                          if a in rules and rules.get(a) != "软" and a not in seen):
            violations.append(
                "redlines.yaml adopts hard rule %r but rule-map has no mapping "
                "for it (adopted without enforcement mapping)" % rid)
    elif seen:
        # v12：缺 redlines.yaml 或未声明 required_rules，语义同"采纳清单为空"——
        # 非空台账 = 强制无人采纳的规则 = 替项目立法，不再只打警告。
        violations.append(
            "rule-map enforces %d rule(s) but no adoption declaration found "
            "(redlines.yaml missing at %s or 'required_rules' undeclared) — "
            "enforcing unadopted rules is legislation; declare required_rules "
            "in redlines.yaml or remove the rule-map" % (len(seen), redlines_path))

    if violations:
        for m in violations:
            print("VIOLATION " + m, file=sys.stderr)
        print("rule_map_check: %d violation(s) -> VIOLATION (R-PRC-006)."
              % len(violations), file=sys.stderr)
        return VIOLATION
    print("rule_map_check: PASS (%d required rule(s), all mapped, pointers "
          "executable, registry-aligned, adoption-boundary consistent)."
          % len(data["required_rules"]))
    return OK


def self_test():
    tmp = tempfile.mkdtemp(prefix="rule-map-")
    old_gov = os.environ.get("GOVERNANCE_DIR")
    try:
        # 防漂移：内置 RULES 与仓库内 rule-registry.md 逐条比对
        reg = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           os.pardir, "references", "rule-registry.md")
        reg = os.path.normpath(reg)
        reg_ids = _ids_from_registry(reg)
        assert reg_ids, "registry parse failed: %s" % reg
        assert set(reg_ids) == set(RULES), (
            "registry/rules drift: only-in-registry=%s only-in-script=%s"
            % (sorted(set(reg_ids) - set(RULES)), sorted(set(RULES) - set(reg_ids))))

        # 台账夹具与真实布局同构：<repo>/governance/rule-map.yaml（repo 根 = 台账上级）。
        wk = os.path.join(tmp, "repo")           # 仓库根
        mapdir = os.path.join(wk, "gov")
        os.makedirs(mapdir, exist_ok=True)
        for rel in ("audit/scripts/check_contract.py",
                    "audit/scripts/static_risk_check.py"):
            p = os.path.join(wk, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write("#!/usr/bin/env python3\n")
            os.chmod(p, 0o755)  # v12：可执行位实检要求夹具真实可执行
        # 仓库外的诱饵脚本（穿越/外部指针应被拒绝）
        outside = os.path.join(tmp, "outside")
        os.makedirs(outside, exist_ok=True)
        with open(os.path.join(outside, "evil.sh"), "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\n")
        os.chmod(os.path.join(outside, "evil.sh"), 0o755)

        def wr(name, data):
            p = os.path.join(mapdir, name)
            with open(p, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f)
            return p

        def wrl(name, ids):
            p = os.path.join(mapdir, name)
            with open(p, "w", encoding="utf-8") as f:
                yaml.safe_dump({"required_rules": ids}, f)
            return p

        def run(p, *extra):
            try:
                return main(["--rule-map", p] + list(extra))
            except SystemExit as e:
                return e.code

        good = {"required_rules": [
            {"id": "R-GOAL-001", "mechanism": "script",
             "script": "audit/scripts/check_contract.py", "ci_step": "check-contract"},
            {"id": "R-IMP-004", "mechanism": "script",
             "script": "audit/scripts/static_risk_check.py"},
            {"id": "R-VER-006", "mechanism": "ci", "ci_step": "type-check"},
            {"id": "R-SEC-004", "mechanism": "manual"},
        ]}
        ADOPT_GOOD = ["R-GOAL-001", "R-IMP-004", "R-VER-006", "R-SEC-004"]
        rl_ok = wrl("redlines-ok.yaml", ADOPT_GOOD)

        # 场景1：台账缺失 -> 42
        rc1 = run(os.path.join(tmp, "nope.yaml"))
        assert rc1 == NOT_WIRED, "expected 42 (no rule-map), got %d" % rc1

        # 场景2：合法台账 + 一致采纳清单 -> 0
        rc2 = run(wr("good.yaml", good), "--redlines", rl_ok)
        assert rc2 == OK, "expected PASS, got %d" % rc2

        # 场景3：未知规则 ID -> 1（采纳清单同带未知 ID，双层命中）
        bad = {"required_rules": [{"id": "R-XXX-999", "mechanism": "manual"}]}
        rc3 = run(wr("unknown.yaml", bad), "--redlines",
                  wrl("rl-unk.yaml", ["R-XXX-999"]))
        assert rc3 == VIOLATION, "expected VIOLATION (unknown id), got %d" % rc3

        # 场景3b：迁出的治理设计要点编号（非登记表规则）不可采纳 -> 1
        gov_id = {"required_rules": [{"id": "R-SEC-001", "mechanism": "manual"}]}
        rc3b = run(wr("gov-id.yaml", gov_id), "--redlines",
                   wrl("rl-govid.yaml", ["R-SEC-001"]))
        assert rc3b == VIOLATION, \
            "expected VIOLATION (migrated design-point id), got %d" % rc3b

        # 场景4：重复定义 -> 1
        dup = {"required_rules": [
            {"id": "R-IMP-004", "mechanism": "manual"},
            {"id": "R-IMP-004", "mechanism": "ci", "ci_step": "s"},
        ]}
        rc4 = run(wr("dup.yaml", dup), "--redlines",
                  wrl("rl-imp.yaml", ["R-IMP-004"]))
        assert rc4 == VIOLATION, "expected VIOLATION (duplicate), got %d" % rc4

        # 场景5：script 机制缺 script 指针 -> 1
        nos = {"required_rules": [{"id": "R-IMP-004", "mechanism": "script"}]}
        rc5 = run(wr("noscript.yaml", nos), "--redlines",
                  wrl("rl-imp2.yaml", ["R-IMP-004"]))
        assert rc5 == VIOLATION, "expected VIOLATION (script w/o pointer), got %d" % rc5

        # 场景5b：script 指针指向不存在的文件 -> 1
        ghost = {"required_rules": [{"id": "R-GOAL-001", "mechanism": "script",
                                     "script": "audit/scripts/no_such_script.py"}]}
        rc5b = run(wr("ghost.yaml", ghost), "--redlines",
                   wrl("rl-goal.yaml", ["R-GOAL-001"]))
        assert rc5b == VIOLATION, \
            "expected VIOLATION (nonexistent script pointer), got %d" % rc5b

        # 场景5c：指针存在（绝对路径、仓库内、可执行）-> 0
        real = {"required_rules": [{"id": "R-GOAL-001", "mechanism": "script",
                                    "script": os.path.join(
                                        wk, "audit/scripts/check_contract.py")}]}
        rc5c = run(wr("real.yaml", real), "--redlines",
                   wrl("rl-goal2.yaml", ["R-GOAL-001"]))
        assert rc5c == OK, "expected PASS (absolute in-repo exec pointer), got %d" % rc5c

        # 场景5d：指针指向目录 -> 1（v12：不是普通文件）
        mdir = {"required_rules": [{"id": "R-IMP-004", "mechanism": "script",
                                    "script": "audit"}]}
        rc5d = run(wr("mdir.yaml", mdir), "--redlines",
                   wrl("rl-imp3.yaml", ["R-IMP-004"]))
        assert rc5d == VIOLATION, \
            "expected VIOLATION (pointer to directory), got %d" % rc5d

        # 场景5e：仓库外绝对路径（/bin/ls）-> 1（v12：可执行机制必须随仓库走）
        mext = {"required_rules": [{"id": "R-IMP-004", "mechanism": "script",
                                    "script": "/bin/ls"}]}
        rc5e = run(wr("mext.yaml", mext), "--redlines",
                   wrl("rl-imp4.yaml", ["R-IMP-004"]))
        assert rc5e == VIOLATION, \
            "expected VIOLATION (outside-repo pointer), got %d" % rc5e

        # 场景5f：../ 路径穿越出仓库 -> 1（v12）
        mtrav = {"required_rules": [{"id": "R-IMP-004", "mechanism": "script",
                                     "script": "../../outside/evil.sh"}]}
        rc5f = run(wr("mtrav.yaml", mtrav), "--redlines",
                   wrl("rl-imp5.yaml", ["R-IMP-004"]))
        assert rc5f == VIOLATION, \
            "expected VIOLATION (traversal pointer), got %d" % rc5f

        # 场景5g：仓库内但无可执行位 -> 1（v12：R-PRC-006 承诺"可执行"）
        p_nox = os.path.join(wk, "audit/scripts/noexec.py")
        with open(p_nox, "w", encoding="utf-8") as f:
            f.write("#!/usr/bin/env python3\n")
        mnox = {"required_rules": [{"id": "R-IMP-004", "mechanism": "script",
                                    "script": "audit/scripts/noexec.py"}]}
        rc5g = run(wr("mnox.yaml", mnox), "--redlines",
                   wrl("rl-imp6.yaml", ["R-IMP-004"]))
        assert rc5g == VIOLATION, \
            "expected VIOLATION (missing exec bit), got %d" % rc5g

        # 场景6：软规则进 required_rules -> 1
        soft = {"required_rules": [{"id": "R-GOAL-003", "mechanism": "manual"}]}
        rc6 = run(wr("soft.yaml", soft), "--redlines",
                  wrl("rl-soft1.yaml", ["R-GOAL-003"]))
        assert rc6 == VIOLATION, "expected VIOLATION (soft rule in required), got %d" % rc6

        # 场景7：非法机制 -> 1
        mech = {"required_rules": [{"id": "R-IMP-004", "mechanism": "magic"}]}
        rc7 = run(wr("mech.yaml", mech), "--redlines",
                  wrl("rl-imp7.yaml", ["R-IMP-004"]))
        assert rc7 == VIOLATION, "expected VIOLATION (bad mechanism), got %d" % rc7

        # 场景8：空 required_rules -> 42
        rc8 = run(wr("empty.yaml", {"required_rules": []}))
        assert rc8 == NOT_WIRED, "expected 42 (empty required_rules), got %d" % rc8

        # 场景9：文件损坏 -> 42
        p9 = os.path.join(mapdir, "corrupt.yaml")
        with open(p9, "w", encoding="utf-8") as f:
            f.write("required_rules: [broken\n  - :: {{{")
        rc9 = run(p9)
        assert rc9 == NOT_WIRED, "expected 42 (corrupt map), got %d" % rc9

        # 场景10：ci_step 空串 -> 1
        cs = {"required_rules": [{"id": "R-VER-006", "mechanism": "ci", "ci_step": ""}]}
        rc10 = run(wr("cistep.yaml", cs), "--redlines",
                   wrl("rl-ver.yaml", ["R-VER-006"]))
        assert rc10 == VIOLATION, "expected VIOLATION (empty ci_step), got %d" % rc10

        # 场景10b：mechanism=ci 但完全没有 ci_step -> 1（v12：已映射但无可核对步骤）
        nostep = {"required_rules": [{"id": "R-VER-006", "mechanism": "ci"}]}
        rc10b = run(wr("nostep.yaml", nostep), "--redlines",
                    wrl("rl-ver2.yaml", ["R-VER-006"]))
        assert rc10b == VIOLATION, \
            "expected VIOLATION (ci without ci_step), got %d" % rc10b

        # ---- v10/v12：采纳边界一致性（--redlines）----
        # 场景11：采纳清单含未知 ID -> 1（v12：拼写错误会悄悄缩小审计范围）
        rl_unknown = wrl("rl-unknown.yaml", ["R-GOAL-001", "R-XXX-123"])
        rc11 = run(wr("map-ok2.yaml", good), "--redlines", rl_unknown)
        assert rc11 == VIOLATION, \
            "expected VIOLATION (unknown adoption id), got %d" % rc11

        # 场景12：采纳清单重复 ID -> 1（v12）
        rl_dup = wrl("rl-dup.yaml", ["R-GOAL-001", "R-GOAL-001"])
        rc12 = run(wr("map-ok3.yaml", good), "--redlines", rl_dup)
        assert rc12 == VIOLATION, \
            "expected VIOLATION (duplicate adoption id), got %d" % rc12

        # 场景13：采纳了硬规则但台账无映射 -> 1
        rl_more = wrl("rl-more.yaml", ADOPT_GOOD + ["R-RISK-001"])
        rc13 = run(wr("map-ok4.yaml", good), "--redlines", rl_more)
        assert rc13 == VIOLATION, \
            "expected VIOLATION (adopted hard rule unmapped), got %d" % rc13

        # 场景14：采纳清单为空而台账非空 -> 1
        rl_empty = wrl("rl-empty.yaml", [])
        rc14 = run(wr("map-ok5.yaml", good), "--redlines", rl_empty)
        assert rc14 == VIOLATION, \
            "expected VIOLATION (enforcing with zero adoption), got %d" % rc14

        # 场景15：redlines 损坏 -> 42
        p15 = os.path.join(tmp, "redlines-broken.yaml")
        with open(p15, "w", encoding="utf-8") as f:
            f.write("required_rules: [broken\n  - :: {{{")
        rc15 = run(wr("map-ok6.yaml", good), "--redlines", p15)
        assert rc15 == NOT_WIRED, "expected 42 (corrupt redlines), got %d" % rc15

        # 场景16：采纳软规则不要求台账映射（软规则永不进必需清单）-> 0
        rl_soft = wrl("rl-soft2.yaml", ADOPT_GOOD + ["R-GOAL-003"])
        rc16 = run(wr("map-ok7.yaml", good), "--redlines", rl_soft)
        assert rc16 == OK, \
            "expected PASS (adopted soft rule needs no mapping), got %d" % rc16

        # 场景17：无 redlines.yaml 且台账非空 -> 1（v12：与空采纳同语义，
        # 不再只打警告——"没有采纳声明"时非空台账 = 替项目立法）
        rc17 = run(wr("map-no_rl.yaml", good))
        assert rc17 == VIOLATION, \
            "expected VIOLATION (non-empty map without redlines), got %d" % rc17

        # 场景18：redlines.yaml 存在但未声明 required_rules 且台账非空 -> 1（v12）
        p18 = wrl("rl-nodecl.yaml", [])
        with open(p18, "w", encoding="utf-8") as f:
            f.write("thresholds:\n  coverage: 80\n")  # 合法 YAML，无 required_rules
        rc18 = run(wr("map-nodecl.yaml", good), "--redlines", p18)
        assert rc18 == VIOLATION, \
            "expected VIOLATION (map without adoption declaration), got %d" % rc18

        print("SELF-TEST: rule_map_check.py PASS (24 scenarios + registry drift check).")
        return OK
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        if old_gov is None:
            os.environ.pop("GOVERNANCE_DIR", None)
        else:
            os.environ["GOVERNANCE_DIR"] = old_gov


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
