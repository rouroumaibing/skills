#!/usr/bin/env python3
"""check_contract.py —— R-GOAL-001 / R-GOAL-002 / R-REL-003 实装（合入必需检查）。

服务规则：
  - R-GOAL-001：契约 criteria/boundaries/scope 字段不含 redlines.forbidden_words。
  - R-GOAL-002：锁定后 contract_hash 须等于规范化内容 sha256
                （front matter 排除 contract_hash/locked_at/signatures 三字段 + 正文结构化表格）。
  - R-REL-003：高风险任务批准人(approver)与负责人(owner)为不同注册账号（比对 roles.yaml）。

用法：
  check_contract.py --contract <path.md> \
      [--check-words] [--check-hash] [--check-approver] [--check-locked [--state <path>]] [--self-test]
  check_contract.py --self-test
至少指定一个 --check-*；--self-test 覆盖全部并跑反例夹具。

三态拆分（D-01/D-02：把"未锁定 / 哈希有效 / 已锁定"拆成三个独立判定，避免把
"没有发现哈希错误"误当成"契约已锁定"）：
  - --check-words  ：格式有效维度 —— 规范字段不含禁用词。redlines.yaml 缺失时
                     降级为软审（打印 WARNING，退出 0；报告须注明"无判据来源，
                     人工核对"）。
  - --check-hash   ：哈希有效维度 —— 若 contract_hash 已填，须等于规范化内容 sha256；
                     空哈希跳过（不证明锁定，仅说明"尚未锁定"）。
  - --check-approver：高风险时两半条判定——
                       (a) owner ≠ approver：不依赖 roles.yaml，可直接判；
                       (b) owner/approver 为已注册账号：需项目自建 roles.yaml。
                     roles.yaml 缺失（或 accounts 为空）时 (b) 无法核验，
                     退出 42（fail-closed：不可核验不得当通过）；(a) 仍先判，
                     已构成违反则退出 1（违反优先于未接线）。
  - --check-locked ：状态维度 —— 读 .workflow/state.json 的 status（须 ∈
                     {locked,implementation,verification,release,closed}），且 contract_hash
                     非空且与规范化内容一致。进入实现/验证/放行前必须过此关；
                     state.json 缺失/损坏 = 无法证明已锁定 = 违反（exit 1，
                     fail-closed，不是 42——缺状态文件本身就是安全问题）。
"""
import os
import sys
import re
import json
import hashlib
import tempfile
import shutil

try:
    import yaml
except ImportError:
    yaml = None

GOVERNANCE_DIR = os.environ.get("GOVERNANCE_DIR",
                                os.path.dirname(os.path.abspath(__file__)).rstrip("/") + "/..")
REDLINES = os.path.join(GOVERNANCE_DIR, "redlines.yaml")
ROLES = os.path.join(GOVERNANCE_DIR, "roles.yaml")
VIOLATION = 1
OK = 0
_last = []


def load_yaml_safe(path):
    if yaml is None:
        print("check_contract: pyyaml required (pip install pyyaml).", file=sys.stderr)
        sys.exit(42)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except yaml.YAMLError as e:
        # 判据来源损坏：不能当"通过"也不能误读为"违规"（退出码 1 的语义是"审出违规"，
        # 崩溃被误读会污染审计结论），按未实装退出（fail-closed）
        print("check_contract: %s is corrupt (not valid YAML); treat as NOT-WIRED "
              "and fix the file.\n  %s" % (path, e), file=sys.stderr)
        sys.exit(42)


def load_redlines():
    path = os.path.join(os.environ.get("GOVERNANCE_DIR", GOVERNANCE_DIR), "redlines.yaml")
    if not os.path.exists(path):
        print("check_contract: WARNING no policy config at %s; forbidden-word check degraded "
              "(soft, report as manual review). Bootstrap: copy audit/templates/"
              "redlines.starter.yaml to <project>/governance/redlines.yaml and adjust "
              "per project." % path, file=sys.stderr)
        return {}
    data = load_yaml_safe(path) or {}
    if not (data.get("forbidden_words") or []):
        # 配置存在但无禁用词：--check-words 退化为"零规则可评估"，如实提示（软审，不阻断）
        print("check_contract: WARNING redlines.yaml at %s has no forbidden_words; "
              "forbidden-word check evaluated zero rules (report as manual review)."
              % path, file=sys.stderr)
    return data


def load_roles():
    """返回 (roles_dict, roles_present)。roles_present=False 表示文件缺失（无法核验注册身份）。"""
    path = os.path.join(os.environ.get("GOVERNANCE_DIR", GOVERNANCE_DIR), "roles.yaml")
    return load_yaml_safe(path), os.path.exists(path)


def parse_contract(path):
    text = open(path, encoding="utf-8").read()
    if not text.startswith("---"):
        raise ValueError("contract must start with YAML front matter")
    end = text.find("\n---", 3)
    if end == -1:
        raise ValueError("unterminated front matter")
    fm = yaml.safe_load(text[3:end].strip())
    body = text[end + 4:]
    return fm, body


def canonical_front_matter(fm):
    fm = {k: v for k, v in fm.items() if k not in ("contract_hash", "locked_at", "signatures")}
    return json.dumps(fm, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def canonical_tables(body):
    """提取正文所有 Markdown 表格，按文档顺序规范化行（列内去空白，行内 | 分隔）。"""
    lines = body.splitlines()
    out = []
    in_table = False
    for ln in lines:
        s = ln.strip()
        if s.startswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            # 跳过分隔行（全为 --- / :--）
            if all(re.fullmatch(r":?-+:?", c) for c in cells):
                in_table = True
                continue
            out.append("|" + "|".join(cells) + "|")
            in_table = True
        else:
            in_table = False
    return "\n".join(out)


def contract_hash(fm, body):
    blob = canonical_front_matter(fm) + "\n" + canonical_tables(body)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def check_words(fm, redlines):
    words = redlines.get("forbidden_words", []) or []
    if not words:
        return []
    targets = []
    for s in (fm.get("scope") or []):
        targets.append(s.get("desc", ""))
    for c in (fm.get("criteria") or []):
        targets.append(c.get("desc", ""))
    for b in (fm.get("boundaries") or []):
        targets.append(b.get("scenario", ""))
        targets.append(b.get("expected", ""))
    bad = []
    for t in targets:
        for w in words:
            if w and w in t:
                bad.append((w, t))
    return bad


def check_hash(fm, body):
    stored = (fm.get("contract_hash") or "").strip()
    if not stored:
        return None  # 未锁定：跳过（R-GOAL-002 仅约束锁定后）
    computed = contract_hash(fm, body)
    return None if stored == computed else (stored, computed)


def check_approver(fm, roles, roles_present):
    """R-REL-003 两半条拆分。返回 None=通过，否则 (code, *detail)：
      - ("same-account", owner)          owner==approver（不依赖 roles.yaml，违反）
      - ("unregistered", owner, approver) 账号未注册（roles.yaml 可用，违反）
      - ("roles-missing",)               roles.yaml 缺失/无账号，"已注册"半条无法核验
                                        -> 未接线（main 退出 42，fail-closed）
    """
    if (fm.get("risk_level") or "").lower() != "high":
        return None
    owner = fm.get("owner")
    approver = fm.get("approver")
    if owner == approver:
        return ("same-account", owner)
    accounts = [a.get("id") for a in (roles or {}).get("accounts", []) or []]
    if not roles_present or not accounts:
        return ("roles-missing",)
    if owner not in accounts or approver not in accounts:
        return ("unregistered", owner, approver)
    return None


def load_state(state_path):
    if not state_path or not os.path.exists(state_path):
        return None
    try:
        return json.loads(open(state_path, encoding="utf-8").read())
    except Exception:
        return {"_corrupt": True}


# 视为"已锁定"的状态（draft/changed 视为未锁定，不得进入实现/验证/放行）
LOCKED_STATUSES = ("locked", "implementation", "verification", "release", "closed")


def check_locked(fm, body, state):
    """D-01/D-02：状态维度判定。返回 None=通过，否则 (code, *detail)。

    拆分三态，杜绝"--check-hash 没报错"被误读为"契约已锁定"：
      - state 缺失/损坏        -> no-state      （无法证明锁定）
      - status 不在 LOCKED 集   -> not-locked    （契约仍是草案）
      - contract_hash 为空      -> no-hash       （锁定的契约必须有哈希）
      - 哈希不符                -> hash-mismatch （锁定后被篡改）
      - state.contract_id 不符  -> contract-mismatch
    """
    if state is None or state.get("_corrupt"):
        return ("no-state",)
    status = (state.get("status") or "").lower()
    if status not in LOCKED_STATUSES:
        return ("not-locked", status)
    stored = (fm.get("contract_hash") or "").strip()
    if not stored:
        return ("no-hash",)
    computed = contract_hash(fm, body)
    if stored != computed:
        return ("hash-mismatch", stored, computed)
    scid = state.get("contract_id")
    fcid = fm.get("contract_id")
    if scid and fcid and scid != fcid:
        return ("contract-mismatch", scid, fcid)
    return None


def main(argv):
    contract_path = None
    do_words = do_hash = do_approver = do_locked = False
    state_path = os.environ.get(
        "STATE_FILE",
        os.path.join(os.environ.get("WORKFLOW_DIR", ".workflow"), "state.json"))
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--contract":
            contract_path = argv[i + 1]; i += 2; continue
        elif a == "--check-words":
            do_words = True; i += 1; continue
        elif a == "--check-hash":
            do_hash = True; i += 1; continue
        elif a == "--check-approver":
            do_approver = True; i += 1; continue
        elif a == "--check-locked":
            do_locked = True; i += 1; continue
        elif a == "--state":
            state_path = argv[i + 1]; i += 2; continue
        elif a == "--self-test":
            return self_test()
        i += 1
    if contract_path is None:
        print("check_contract: --contract <path.md> required.", file=sys.stderr)
        return 42
    if not (do_words or do_hash or do_approver or do_locked):
        print("check_contract: specify at least one --check-*.", file=sys.stderr)
        return 42

    fm, body = parse_contract(contract_path)
    redlines = load_redlines()
    roles, roles_present = load_roles()

    violations = []
    not_wired = []
    if do_words:
        for w, t in check_words(fm, redlines):
            violations.append("R-GOAL-001 forbidden word %r in %r" % (w, t))
    if do_hash:
        r = check_hash(fm, body)
        if r is not None:
            violations.append("R-GOAL-002 hash mismatch stored=%s computed=%s" % r)
    if do_approver:
        r = check_approver(fm, roles, roles_present)
        if r is not None:
            if r[0] == "roles-missing":
                # fail-closed：高风险"已注册"半条无法核验时不得报通过
                not_wired.append("R-REL-003 high-risk approver registration unverifiable "
                                 "(roles.yaml missing or has no accounts)")
            else:
                violations.append("R-REL-003 high-risk approver==owner or unregistered: %s" % (r,))
    if do_locked:
        r = check_locked(fm, body, load_state(state_path))
        if r is not None:
            code = r[0]
            detail = ", ".join(str(x) for x in r[1:]) if len(r) > 1 else ""
            violations.append("R-GOAL-002 contract not locked/intact: %s %s" % (code, detail))

    _last.clear()
    _last.extend(violations)
    if violations:
        for m in violations:
            print("VIOLATION " + m, file=sys.stderr)
        print("check_contract: %d violation(s)." % len(violations), file=sys.stderr)
        return VIOLATION
    if not_wired:
        for m in not_wired:
            print("NOT-WIRED " + m, file=sys.stderr)
        print("check_contract: NOT WIRED (exit 42, fail-closed; wire before enabling "
              "as required check).", file=sys.stderr)
        return 42
    print("check_contract: PASS.")
    return OK


def self_test():
    tmp = tempfile.mkdtemp(prefix="cc-test-")
    try:
        redlines = {"forbidden_words": ["适当", "尽量", "等等", "大概", "合理", "关键", "重要", "必要时"]}
        roles = {"accounts": [{"id": "alice"}, {"id": "bob"}]}
        yaml.safe_dump(redlines, open(os.path.join(tmp, "redlines.yaml"), "w"), allow_unicode=True)
        yaml.safe_dump(roles, open(os.path.join(tmp, "roles.yaml"), "w"), allow_unicode=True)
        os.environ["GOVERNANCE_DIR"] = tmp

        def write_fm(fm_dict, body):
            fm_txt = yaml.safe_dump(fm_dict, allow_unicode=True)
            fn = "c%d.md" % write_fm.counter
            write_fm.counter += 1
            p = os.path.join(tmp, fn)
            open(p, "w", encoding="utf-8").write("---\n" + fm_txt + "---\n" + body)
            return p
        write_fm.counter = 0

        good_fm = {
            "contract_id": "C-1", "schema_version": 1, "version": "v1", "contract_hash": "",
            "owner": "alice", "approver": "bob", "risk_level": "low",
            "scope": [{"id": "S-1", "path": "src/x", "desc": "处理上传请求"}],
            "criteria": [{"id": "A-1", "desc": "成功率不低于 99%"}],
            "boundaries": [{"id": "B-1", "scenario": "网络超时", "expected": "返回错误码"}],
        }
        # 场景1：合法 -> PASS
        p1 = write_fm(good_fm, "| id | path | desc |\n|---|---|---|\n| S-1 | src/x | 处理上传请求 |\n")
        rc1 = main(["--contract", p1, "--check-words", "--check-hash", "--check-approver"])
        assert rc1 == OK, "expected PASS, got %d / %s" % (rc1, _last)

        # 场景2：含禁用词 -> FAIL
        bad = dict(good_fm)
        bad["criteria"] = [{"id": "A-1", "desc": "成功率适当提升"}]
        p2 = write_fm(bad, "")
        rc2 = main(["--contract", p2, "--check-words"])
        assert rc2 == VIOLATION and any("R-GOAL-001" in m for m in _last), _last

        # 场景3：hash 不符 -> FAIL（先用正确 hash 写，再篡改正文）
        correct = dict(good_fm)
        correct["contract_hash"] = contract_hash(correct, "| id | path | desc |\n|---|---|---|\n| S-1 | src/x | 处理上传请求 |\n")
        p3 = write_fm(correct, "| id | path | desc |\n|---|---|---|\n| S-1 | src/x | 处理上传请求 |\n")
        rc3a = main(["--contract", p3, "--check-hash"])
        assert rc3a == OK, "correct hash should pass"
        # 篡改正文表格内容（不改 front matter）
        open(p3, "w", encoding="utf-8").write(
            "---\n" + yaml.safe_dump(correct, allow_unicode=True) +
            "---\n| id | path | desc |\n|---|---|---|\n| S-1 | src/x | 处理上传请求（被改） |\n")
        rc3b = main(["--contract", p3, "--check-hash"])
        assert rc3b == VIOLATION, "tampered body should fail hash"

        # 场景4：高风险 approver==owner -> FAIL
        hi = dict(good_fm)
        hi["risk_level"] = "high"
        hi["approver"] = "alice"
        p4 = write_fm(hi, "")
        rc4 = main(["--contract", p4, "--check-approver"])
        assert rc4 == VIOLATION, "high-risk same approver should fail"

        # 场景5：未锁定时 hash 跳过 -> PASS
        rc5 = main(["--contract", p1, "--check-hash"])
        assert rc5 == OK, "unlocked should skip hash"

        # ---- 场景6~10：--check-locked 三态拆分（D-01/D-02） ----
        os.environ["WORKFLOW_DIR"] = tmp  # 默认 state 路径 = tmp/state.json
        locked_body = "| id | path | desc |\n|---|---|---|\n| S-1 | src/x | 处理上传请求 |\n"
        locked_fm = dict(good_fm)
        locked_fm["contract_hash"] = contract_hash(locked_fm, locked_body)
        p6 = write_fm(locked_fm, locked_body)

        def write_state(d):
            open(os.path.join(tmp, "state.json"), "w", encoding="utf-8").write(
                json.dumps(d, ensure_ascii=False))

        # 场景6：已锁定 + 有效哈希 -> PASS
        write_state({"schema_version": 1, "contract_id": "C-1", "status": "locked", "current_phase": "锁定"})
        rc6 = main(["--contract", p6, "--check-locked"])
        assert rc6 == OK, "locked+valid hash should pass, got %d / %s" % (rc6, _last)

        # 场景7：draft 状态 -> FAIL（not-locked）
        write_state({"schema_version": 1, "contract_id": "C-1", "status": "draft"})
        rc7 = main(["--contract", p6, "--check-locked"])
        assert rc7 == VIOLATION and any("not-locked" in m for m in _last), _last

        # 场景8：state 缺失 -> FAIL（no-state）
        os.remove(os.path.join(tmp, "state.json"))
        rc8 = main(["--contract", p6, "--check-locked"])
        assert rc8 == VIOLATION and any("no-state" in m for m in _last), _last

        # 场景9：锁定但哈希为空 -> FAIL（no-hash）
        nohash = dict(good_fm); nohash["contract_hash"] = ""
        p9 = write_fm(nohash, locked_body)
        write_state({"schema_version": 1, "contract_id": "C-1", "status": "locked"})
        rc9 = main(["--contract", p9, "--check-locked"])
        assert rc9 == VIOLATION and any("no-hash" in m for m in _last), _last

        # 场景10：锁定 + 哈希篡改（p3 的 front matter 哈希正确但正文被改）-> FAIL（hash-mismatch）
        write_state({"schema_version": 1, "contract_id": "C-1", "status": "locked"})
        rc10 = main(["--contract", p3, "--check-locked"])
        assert rc10 == VIOLATION and any("hash-mismatch" in m for m in _last), _last

        # ---- 场景11~12：roles.yaml 缺失/无账号时"已注册"半条 fail-closed ----
        os.remove(os.path.join(tmp, "roles.yaml"))  # 场景11：roles.yaml 缺失
        hi2 = dict(good_fm)
        hi2["risk_level"] = "high"
        p11 = write_fm(hi2, "")
        rc11 = main(["--contract", p11, "--check-approver"])
        assert rc11 == 42, "expected 42 (roles missing, registration unverifiable), got %d" % rc11

        yaml.safe_dump({"accounts": []},
                       open(os.path.join(tmp, "roles.yaml"), "w"))  # 场景12：accounts 为空
        rc12 = main(["--contract", p11, "--check-approver"])
        assert rc12 == 42, "expected 42 (empty accounts), got %d" % rc12

        # 同契约 owner==approver 时违反优先于未接线（roles.yaml 仍缺失影响不大）
        yaml.safe_dump({"accounts": []},
                       open(os.path.join(tmp, "roles.yaml"), "w"))
        hi3 = dict(hi2)
        hi3["approver"] = hi3["owner"]
        p12b = write_fm(hi3, "")
        rc12b = main(["--contract", p12b, "--check-approver"])
        assert rc12b == VIOLATION, "expected VIOLATION (same-account beats not-wired), got %d" % rc12b

        # 场景13：roles.yaml 损坏（非法 YAML）→ 判据来源损坏，退出 42（fail-closed）；
        # 崩溃不得被误读为"违规"（退出码 1 的语义是审出违规）
        with open(os.path.join(tmp, "roles.yaml"), "w") as f:
            f.write("accounts: [broken\n  - :: {{{yaml")
        try:
            rc13 = main(["--contract", p11, "--check-approver"])
        except SystemExit as e:
            rc13 = e.code
        assert rc13 == 42, "expected 42 (corrupt roles.yaml = not wired), got %d" % rc13

        print("SELF-TEST: check_contract.py PASS (13 scenarios).")
        return OK
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ.pop("GOVERNANCE_DIR", None)
        os.environ.pop("WORKFLOW_DIR", None)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
