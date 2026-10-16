#!/usr/bin/env python3
"""risk_floor_check.py —— R-RISK-001 实装（合入必需检查）。

服务规则 R-RISK-001：工程从 diff 按 redlines.yaml 的 risk_patterns 推算风险下限；
实际等级高于契约声明时触发升级 E-17。

推算口径（保守）：diff 命中任一 risk_pattern（受控路径 path_glob 或受控 SQL 模式 pattern）
=> 推算下限 = high（auth/payment/migration/secrets/不可逆DDL 一律按最高风险对待）。
命中后若 contract_risk_level 低于 high（即 low 或 medium），视为"声明低于推算下限"，
退出非零触发 E-17。

用法：
  risk_floor_check.py --changes <changes.json> --diff <diff.txt> --contract-risk-level <low|medium|high> [--repo <path>]
  risk_floor_check.py --self-test

changes.json / diff.txt 来自本次 PR 相对 base 的变更（CI 产出）。
"""
import os
import sys
import json
import re
import tempfile
import shutil
import fnmatch

try:
    import yaml
except ImportError:
    yaml = None

GOVERNANCE_DIR = os.environ.get("GOVERNANCE_DIR",
                                os.path.dirname(os.path.abspath(__file__)).rstrip("/") + "/..")
REDLINES = os.path.join(GOVERNANCE_DIR, "redlines.yaml")
VIOLATION = 1
OK = 0
ORDER = {"low": 0, "medium": 1, "high": 2}
_last_hits = []


def load_redlines():
    if yaml is None:
        print("risk_floor_check: pyyaml required (pip install pyyaml).", file=sys.stderr)
        sys.exit(42)
    path = os.path.join(os.environ.get("GOVERNANCE_DIR", GOVERNANCE_DIR), "redlines.yaml")
    if not os.path.exists(path):
        print("risk_floor_check: redlines.yaml not found: " + path, file=sys.stderr)
        sys.exit(42)
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def hits(redlines, changes, diff_text):
    out = []
    patterns = redlines.get("risk_patterns", []) or []
    changed_paths = []
    for ch in changes:
        p = ch.get("path") or ch.get("old_path")
        if p:
            changed_paths.append(p.replace(os.sep, "/"))
    for pat in patterns:
        if "path_glob" in pat:
            g = pat["path_glob"]
            for p in changed_paths:
                if fnmatch.fnmatch(p, g) or ("/" + p).endswith(g.replace("**", "")) or fnmatch.fnmatch(p, "**/" + g.lstrip("**/")):
                    out.append(("path_glob", g, p))
        elif "pattern" in pat:
            rx = re.compile(pat["pattern"], re.IGNORECASE)
            # 只在 diff 文本里扫描（内容级模式），避免文件名误伤
            if rx.search(diff_text or ""):
                out.append(("pattern", pat["pattern"], "diff"))
    return out


def main(argv):
    changes_path = diff_path = contract_level = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--changes":
            changes_path = argv[i + 1]; i += 2; continue
        elif a == "--diff":
            diff_path = argv[i + 1]; i += 2; continue
        elif a == "--contract-risk-level":
            contract_level = argv[i + 1]; i += 2; continue
        elif a == "--self-test":
            return self_test()
        i += 1
    if None in (changes_path, diff_path, contract_level):
        print("risk_floor_check: --changes/--diff/--contract-risk-level required.", file=sys.stderr)
        return 42
    if contract_level not in ORDER:
        print("risk_floor_check: bad --contract-risk-level %r" % contract_level, file=sys.stderr)
        return 42

    redlines = load_redlines()
    with open(changes_path, encoding="utf-8") as f:
        changes = json.load(f)
    with open(diff_path, encoding="utf-8", errors="replace") as f:
        diff_text = f.read()

    found = hits(redlines, changes, diff_text)
    _last_hits.clear()
    _last_hits.extend(found)

    if not found:
        print("risk_floor_check: PASS (no risk_pattern hit; floor=low).")
        return OK

    floor = "high"  # 命中受控模式一律按最高风险推算
    printed_floor = floor
    if ORDER[floor] > ORDER[contract_level]:
        for kind, spec, where in found:
            print("RISK-FLOOR[R-RISK-001] hit %s=%s at %s => floor=%s > declared=%s"
                  % (kind, spec, where, printed_floor, contract_level), file=sys.stderr)
        print("risk_floor_check: VIOLATION (declared %s below inferred floor %s) -> escalate E-17."
              % (contract_level, printed_floor), file=sys.stderr)
        return VIOLATION
    print("risk_floor_check: PASS (declared %s >= floor %s)." % (contract_level, printed_floor))
    return OK


def self_test():
    tmp = tempfile.mkdtemp(prefix="rfc-test-")
    try:
        redlines = {"risk_patterns": [
            {"path_glob": "**/auth/**"},
            {"pattern": "GRANT|REVOKE|ALTER TABLE"},
        ]}
        with open(os.path.join(tmp, "redlines.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(redlines, f, allow_unicode=True)

        # 场景1：命中 auth 路径、契约声明 low -> 触发
        ch = os.path.join(tmp, "c.json")
        with open(ch, "w", encoding="utf-8") as f:
            json.dump([{"path": "src/auth/login.py", "status": "A"}], f)
        d = os.path.join(tmp, "d.txt")
        open(d, "w", encoding="utf-8").close()
        os.environ["GOVERNANCE_DIR"] = tmp
        rc1 = main(["--changes", ch, "--diff", d, "--contract-risk-level", "low"])
        assert rc1 == VIOLATION, "expected VIOLATION, got %d" % rc1
        assert any(k == "path_glob" for k, _, _ in _last_hits), _last_hits

        # 场景2：命中 DDL 模式、契约 medium -> 触发
        ch2 = os.path.join(tmp, "c2.json")
        with open(ch2, "w", encoding="utf-8") as f:
            json.dump([{"path": "src/db/seed.py", "status": "M"}], f)
        d2 = os.path.join(tmp, "d2.txt")
        with open(d2, "w", encoding="utf-8") as f:
            f.write("+ALTER TABLE users ADD COLUMN x int;\n")
        rc2 = main(["--changes", ch2, "--diff", d2, "--contract-risk-level", "medium"])
        assert rc2 == VIOLATION, "expected VIOLATION (DDL), got %d" % rc2

        # 场景3：无命中、契约 low -> PASS
        ch3 = os.path.join(tmp, "c3.json")
        with open(ch3, "w", encoding="utf-8") as f:
            json.dump([{"path": "src/util/format.py", "status": "A"}], f)
        d3 = os.path.join(tmp, "d3.txt")
        open(d3, "w", encoding="utf-8").close()
        rc3 = main(["--changes", ch3, "--diff", d3, "--contract-risk-level", "low"])
        assert rc3 == OK, "expected PASS, got %d" % rc3

        # 场景4：命中但契约 high -> PASS（声明已是最高的）
        rc4 = main(["--changes", ch, "--diff", d, "--contract-risk-level", "high"])
        assert rc4 == OK, "expected PASS (declared high == floor), got %d" % rc4

        print("SELF-TEST: risk_floor_check.py PASS (4 scenarios).")
        return OK
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ.pop("GOVERNANCE_DIR", None)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
