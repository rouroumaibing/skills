#!/usr/bin/env python3
"""evidence_pack_check.py —— R-REL-002 实装（合入必需检查）。

服务规则 R-REL-002：证据包包含追溯矩阵、门禁报告、独立审查报告；
偏离清单由工程从 diff 与契约推导，不采用 AI 自报草稿作为必需项。

校验（见 schemas.md §3）：
  - manifest.items[].type 覆盖 required_present 全部类型；
  - 每个 item 的 sha256 与实际文件一致（防 AI 改了证据还谎称未改）；
  - contract_sha256 与契约 contract_hash 一致（证明证据对应被锁定的那一份）；
  - 每个 item.corresponds_to 的判据 ID 在契约 scope/criteria/boundaries 中存在；
  - --check-bindings（D-09/D-12）：manifest 须绑定 commit_sha / base_sha /
    runner_identity（仅接受 ci: / platform: 前缀，拒绝 cli: 或自填身份）/
    governance_commit / contract_hash，并可用 --expected-commit 绑定 CI 传入的 PR HEAD。

用法：
  evidence_pack_check.py --manifest <manifest.json> --contract <contract.md> [--reports-dir <dir>]
                          [--check-bindings] [--expected-commit <sha>] [--self-test]
  evidence_pack_check.py --self-test
"""
import os
import sys
import json
import re
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
VIOLATION = 1
OK = 0
_last = []


def load_yaml_safe(path):
    if yaml is None:
        print("evidence_pack_check: pyyaml required (pip install pyyaml).", file=sys.stderr)
        sys.exit(42)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def parse_contract_ids(contract_path):
    if not contract_path or not os.path.exists(contract_path):
        return set()
    text = open(contract_path, encoding="utf-8").read()
    fm = None
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            fm = yaml.safe_load(text[3:end].strip()) or {}
    else:
        # 兼容纯 YAML 契约（无 front matter 包装）
        try:
            fm = yaml.safe_load(text) or {}
        except yaml.YAMLError:
            fm = None
    ids = set()
    if fm:
        for key in ("scope", "criteria", "boundaries", "non_goals", "forbidden_items"):
            for e in (fm.get(key) or []):
                if isinstance(e, dict) and e.get("id"):
                    ids.add(e["id"])
    return ids


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(65536), b""):
            h.update(blk)
    return h.hexdigest()


def binding_violations(manifest, contract_hash):
    """D-09/D-12：证据包绑定校验。
    - commit_sha / base_sha 必填（绑定本次提交与合入基线，防旧证据复用）；
    - runner_identity 必须为 ci: / platform: 前缀（平台/CI 身份；拒绝 cli: 或自填）；
    - governance_commit 若与 GOVERNANCE_SHA（CI 传入）不一致即拒（治理目录版本绑定）；
    - contract_hash 与契约一致（放行基线绑定，契约变更后证据作废）。"""
    v = []
    commit = (manifest.get("commit_sha") or "").strip()
    base = (manifest.get("base_sha") or "").strip()
    runner = (manifest.get("runner_identity") or "").strip()
    if not commit:
        v.append("binding-missing-commit_sha")
    elif not re.fullmatch(r"[0-9a-f]{7,40}", commit):
        v.append("binding-bad-commit_sha:%s" % commit)
    if not base:
        v.append("binding-missing-base_sha")
    elif not re.fullmatch(r"[0-9a-f]{7,40}", base):
        v.append("binding-bad-base_sha:%s" % base)
    if not runner:
        v.append("binding-missing-runner_identity")
    elif not (runner.startswith("ci:") or runner.startswith("platform:")):
        v.append("binding-untrusted-runner:%s" % runner)
    gexp = os.environ.get("GOVERNANCE_SHA")
    gc = (manifest.get("governance_commit") or "").strip()
    if gexp and gc and gc != gexp:
        v.append("binding-governance-mismatch:%s" % gc)
    mch = (manifest.get("contract_hash") or "").strip()
    if contract_hash and mch and mch != contract_hash:
        v.append("binding-contract-hash-mismatch")
    return v


def main(argv):
    manifest_path = contract_path = reports_dir = None
    check_bindings = False
    expected_commit = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--manifest":
            manifest_path = argv[i + 1]; i += 2; continue
        elif a == "--contract":
            contract_path = argv[i + 1]; i += 2; continue
        elif a == "--reports-dir":
            reports_dir = argv[i + 1]; i += 2; continue
        elif a == "--check-bindings":
            check_bindings = True; i += 1; continue
        elif a == "--expected-commit":
            expected_commit = argv[i + 1]; i += 2; continue
        elif a == "--self-test":
            return self_test()
        i += 1
    if manifest_path is None or contract_path is None:
        print("evidence_pack_check: --manifest and --contract required.", file=sys.stderr)
        return 42

    manifest = json.load(open(manifest_path, encoding="utf-8"))
    if reports_dir is None:
        reports_dir = os.path.dirname(os.path.abspath(manifest_path))
    contract = load_yaml_safe(contract_path) or {}
    contract_hash = (contract.get("contract_hash") or "").strip()
    contract_ids = parse_contract_ids(contract_path)

    violations = []
    items = manifest.get("items", []) or []
    present_types = {it.get("type") for it in items}
    for req in (manifest.get("required_present") or []):
        if req not in present_types:
            violations.append("missing-required-item:%s" % req)

    for it in items:
        rel = it.get("path")
        fpath = rel if os.path.isabs(rel) else os.path.join(reports_dir, rel)
        if not os.path.exists(fpath):
            violations.append("item-missing-file:%s" % rel); continue
        actual = sha256_file(fpath)
        if it.get("sha256") and it.get("sha256") != actual:
            violations.append("item-sha256-mismatch:%s" % rel)
        for cid in (it.get("corresponds_to") or []):
            if isinstance(cid, str) and cid not in ("*",) and cid not in contract_ids:
                violations.append("corresponds_to-unknown:%s in %s" % (cid, rel))

    ms = manifest.get("contract_sha256")
    if contract_hash and ms and ms != contract_hash:
        violations.append("contract-sha256-mismatch")

    if check_bindings:
        violations.extend(binding_violations(manifest, contract_hash))
        if expected_commit:
            mc = (manifest.get("commit_sha") or "").strip()
            if mc != expected_commit:
                violations.append("binding-commit-mismatch:%s!=%s" % (mc, expected_commit))

    _last.clear()
    _last.extend(violations)
    if violations:
        for m in violations:
            print("VIOLATION[R-REL-002] " + m, file=sys.stderr)
        print("evidence_pack_check: %d violation(s)." % len(violations), file=sys.stderr)
        return VIOLATION
    print("evidence_pack_check: PASS.")
    return OK


def self_test():
    tmp = tempfile.mkdtemp(prefix="epc-test-")
    try:
        reports = os.path.join(tmp, "reports"); os.makedirs(reports)
        g = os.path.join(tmp, "governance"); os.makedirs(g)
        redlines = {"forbidden_words": []}
        yaml.safe_dump(redlines, open(os.path.join(g, "redlines.yaml"), "w"), allow_unicode=True)
        os.environ["GOVERNANCE_DIR"] = g

        # 契约
        contract = {"contract_id": "C-1", "contract_hash": "abc",
                    "scope": [{"id": "S-1", "path": "src/x"}],
                    "criteria": [{"id": "A-1", "desc": "x"}],
                    "boundaries": [{"id": "B-1", "scenario": "y", "expected": "z"}]}
        cmd = os.path.join(tmp, "C.md")
        yaml.safe_dump(contract, open(cmd, "w"), allow_unicode=True)

        def make_manifest(items, required, csha, bindings=None):
            m = {"schema_version": 1, "contract_id": "C-1", "contract_sha256": csha,
                 "items": items, "required_present": required, "ai_drafts": []}
            m.update(bindings or {})
            p = os.path.join(tmp, "manifest.json")
            json.dump(m, open(p, "w"), ensure_ascii=False)
            return p

        good_bindings = {"commit_sha": "abc1234", "base_sha": "def5678",
                         "runner_identity": "ci:governance-gate",
                         "governance_commit": "tree:test", "contract_hash": "abc"}

        # 真实文件
        gate = os.path.join(reports, "gate.json"); open(gate, "w").write('{"ok":true}')
        trace = os.path.join(reports, "trace.csv"); open(trace, "w").write("req_id,design_id\nS-1,D-1\n")
        verifier = os.path.join(reports, "verifier.md"); open(verifier, "w").write("verified")

        good_items = [
            {"type": "gate_report", "path": "reports/gate.json", "corresponds_to": ["A-1"],
             "sha256": sha256_file(gate)},
            {"type": "trace_matrix", "path": "reports/trace.csv", "corresponds_to": ["S-1"],
             "sha256": sha256_file(trace)},
            {"type": "review_report", "path": "reports/verifier.md", "corresponds_to": ["*"],
             "sha256": sha256_file(verifier)},
        ]
        # 场景1：完整且一致 -> PASS
        mp = make_manifest(good_items, ["gate_report", "trace_matrix", "review_report"], "abc")
        rc1 = main(["--manifest", mp, "--contract", cmd, "--reports-dir", tmp])
        assert rc1 == OK, "expected PASS, got %d / %s" % (rc1, _last)

        # 场景2：缺 required 类型 -> FAIL
        mp2 = make_manifest(good_items[:2], ["gate_report", "trace_matrix", "review_report"], "abc")
        rc2 = main(["--manifest", mp2, "--contract", cmd, "--reports-dir", tmp])
        assert rc2 == VIOLATION and any("missing-required-item" in m for m in _last), _last

        # 场景3：sha256 篡改 -> FAIL
        bad = [dict(good_items[0]), dict(good_items[1]), dict(good_items[2])]
        bad[0]["sha256"] = "deadbeef"
        mp3 = make_manifest(bad, ["gate_report", "trace_matrix", "review_report"], "abc")
        rc3 = main(["--manifest", mp3, "--contract", cmd, "--reports-dir", tmp])
        assert rc3 == VIOLATION and any("item-sha256-mismatch" in m for m in _last), _last

        # 场景4：corresponds_to 未知判据 -> FAIL
        bad4 = [dict(good_items[0]), dict(good_items[1]), dict(good_items[2])]
        bad4[0]["corresponds_to"] = ["A-999"]
        mp4 = make_manifest(bad4, ["gate_report", "trace_matrix", "review_report"], "abc")
        rc4 = main(["--manifest", mp4, "--contract", cmd, "--reports-dir", tmp])
        assert rc4 == VIOLATION and any("corresponds_to-unknown" in m for m in _last), _last

        # 场景5：contract_sha256 不匹配 -> FAIL
        mp5 = make_manifest(good_items, ["gate_report", "trace_matrix", "review_report"], "WRONG")
        rc5 = main(["--manifest", mp5, "--contract", cmd, "--reports-dir", tmp])
        assert rc5 == VIOLATION and any("contract-sha256-mismatch" in m for m in _last), _last

        # 场景6：--check-bindings 全合法 -> PASS
        mp6 = make_manifest(good_items, ["gate_report", "trace_matrix", "review_report"],
                            "abc", good_bindings)
        rc6 = main(["--manifest", mp6, "--contract", cmd, "--reports-dir", tmp,
                    "--check-bindings"])
        assert rc6 == OK, "expected PASS, got %d / %s" % (rc6, _last)

        # 场景7：缺 commit_sha -> FAIL（证据未绑定提交，不可判定与当前变更相关）
        b7 = dict(good_bindings); b7.pop("commit_sha")
        mp7 = make_manifest(good_items, ["gate_report", "trace_matrix", "review_report"],
                            "abc", b7)
        rc7 = main(["--manifest", mp7, "--contract", cmd, "--reports-dir", tmp,
                    "--check-bindings"])
        assert rc7 == VIOLATION and any("binding-missing-commit_sha" in m for m in _last), _last

        # 场景8：runner_identity=cli:someone -> FAIL（D-12：自填/CLI 身份不可信）
        b8 = dict(good_bindings); b8["runner_identity"] = "cli:someone"
        mp8 = make_manifest(good_items, ["gate_report", "trace_matrix", "review_report"],
                            "abc", b8)
        rc8 = main(["--manifest", mp8, "--contract", cmd, "--reports-dir", tmp,
                    "--check-bindings"])
        assert rc8 == VIOLATION and any("binding-untrusted-runner" in m for m in _last), _last

        # 场景9：--expected-commit 不匹配 -> FAIL（绑定 CI 传入的 PR HEAD）
        mp9 = make_manifest(good_items, ["gate_report", "trace_matrix", "review_report"],
                            "abc", good_bindings)
        rc9 = main(["--manifest", mp9, "--contract", cmd, "--reports-dir", tmp,
                    "--check-bindings", "--expected-commit", "zzz9999"])
        assert rc9 == VIOLATION and any("binding-commit-mismatch" in m for m in _last), _last

        print("SELF-TEST: evidence_pack_check.py PASS (9 scenarios).")
        return OK
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ.pop("GOVERNANCE_DIR", None)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
