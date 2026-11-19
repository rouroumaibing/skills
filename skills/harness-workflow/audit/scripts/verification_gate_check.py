#!/usr/bin/env python3
r"""verification_gate_check.py —— R-VER-008 实装 + 验证体系实装探测（--survey）。

两种模式：

1) 默认模式（R-VER-008，验证戳覆盖率）：
   审什么：最近 lookback 个 commit 中，携带**有效验证戳**的比例不低于
   thresholds.verification_stamp.min_ratio。验证戳是 commit message 末尾的
   固定 trailer：
       Verified: <e2e|unit|property|differential|manual|none>
       Evidence: <路径或链接，可选>
   - `none` 是**合法取值**（"未验证可查询"是特性）；只有"没盖戳"才违规。
   - 白名单之外的 method 取值视为**无效戳**（按未盖戳计，防伪造）。
   - 大小写不敏感；`Evidence:` 行存在与否不影响判定。

   接线：--repo <git 仓库路径>。阈值来自 GOVERNANCE_DIR/redlines.yaml 的
   thresholds.verification_stamp（lookback: int>=1 默认 50；min_ratio:
   0.0~1.0）。缺该配置段 = 判据未接线 → 42（fail-closed，与其它门禁一致）。

   退出码：0 通过 / 1 覆盖率低于阈值 / 42 未接线（非 git 仓库、git 不可用、
   无 commit、阈值配置缺失或非法）。

2) --survey <repo 路径>（证据层探测，不做合规判定）：
   只读探测验证体系实装形态，输出 JSON（stdout），退出码恒 0（路径不存在
   → 42）。探测项与蓝图维度（references/verification-program.md §2）对应：
   - verify_asset：verify 资产候选链（verify/ 目录、verify.yaml、verify.md、
     agent 技能目录下 verify* 入口、scripts/verify*）；
   - ci_config：常见 CI 配置位置候选链 + 是否含 test/build/lint 关键词 step
     （关键词命中只作证据，不判断配置正确性）；
   - test_types：依赖特征（package.json / pyproject.toml / go.mod /
     Cargo.toml / requirements*.txt / pom.xml 文本扫描）+ 目录特征：
     e2e / property / differential(golden/snapshot/approval) 三类；
   - verification_stamp：最近 lookback 条 commit 的戳覆盖率采样。
   候选链是**通用形态枚举**（跨生态约定，非点名特定平台）；项目自有布局不在
   候选链内时报告 miss，不构成违规——报告层按"建议模式"引用
   verification-program.md §4 的资产规格出具修改建议。

已知盲区（报告须注明"survey 为形态探测，正确性需人核"）：
   - verify 资产存在 ≠ 内容达标（manifest 三字段齐全性由人核，见 §4.1）；
   - 依赖特征只在常见清单文件里扫，monorepo / 非常规布局会 miss；
   - CI step 关键词命中不等于门真的接线（实装以 probe / required check 为准）。

用法：
  verification_gate_check.py --repo <path> [--governance-dir <dir>] [--self-test]
  verification_gate_check.py --survey <path>
  verification_gate_check.py --self-test
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

try:
    import yaml
except ImportError:
    yaml = None

VIOLATION = 1
OK = 0
NOT_WIRED = 42
GOVERNANCE_DIR = os.environ.get("GOVERNANCE_DIR", "governance")

# R-VER-008：验证戳合法 method 白名单（none 合法；白名单外 = 无效戳）
STAMP_METHODS = ("e2e", "unit", "property", "differential", "manual", "none")
STAMP_RE = re.compile(r"^Verified:\s*([A-Za-z0-9_-]+)\s*$", re.IGNORECASE)
DEFAULT_LOOKBACK = 50

# --survey 候选链（通用形态枚举；项目自有布局 miss 不构成违规）
VERIFY_ASSET_CANDIDATES = [
    "verify",                      # 目录（内部含可执行说明即算命中）
    "verify.yaml", "verify.yml", "verify.md",
]
VERIFY_SCRIPT_PREFIX = ("verify",)  # scripts/verify* / .claude/skills/verify*
AGENT_SKILL_DIRS = (".claude/skills", ".agent/skills", "skills")
AGENT_ADAPTER_FILES = (
    "AGENTS.md", "CLAUDE.md",                 # 平台中立 / 生态 agent 指令文件
    ".claude/skills", ".cursor/rules", ".github/copilot-instructions.md",
)
GIT_HOOK_FILES = (".githooks/pre-commit", ".husky/pre-commit")
CI_CONFIG_CANDIDATES = [
    ".github/workflows", ".gitlab-ci.yml", "Jenkinsfile",
    ".circleci/config.yml", "azure-pipelines.yml", ".drone.yml", "cloudbuild.yaml",
]
CI_STEP_KEYWORDS = ("test", "build", "lint")
E2E_DEP_TOKENS = (
    "playwright", "cypress", "selenium", "puppeteer", "detox", "appium",
    "nightwatch", "testcafe", "webdriver",
)
E2E_DIR_TOKENS = ("e2e", "test-e2e", "end2end", "features")
PROPERTY_DEP_TOKENS = (
    "fast-check", "jsverify", "testcheck", "hypothesis", "quickcheck",
    "scalacheck", "propcheck", "proptest", "jqwik", "hypothesmith",
)
DIFFERENTIAL_TOKENS = (
    "approvaltests", "approval_tests", " approval ",
)
DIFFERENTIAL_DIR_TOKENS = ("golden", "snapshots", "__snapshots__", "approvals")
DEP_MANIFESTS = (
    "package.json", "pyproject.toml", "go.mod", "Cargo.toml",
    "requirements.txt", "requirements-dev.txt", "pom.xml", "Gemfile",
)


def _git(repo, *args):
    """调 git；返回 (stdout, rc)。git 不可用 / 调用失败 → rc=None。"""
    try:
        p = subprocess.run(
            ["git", "-C", repo] + list(args),
            capture_output=True, text=True, timeout=30,
        )
        return p.stdout, p.returncode
    except (OSError, subprocess.TimeoutExpired):
        return None, None


def _is_repo(repo):
    out, rc = _git(repo, "rev-parse", "--is-inside-work-tree")
    return rc == 0 and out is not None and out.strip() == "true"


def stamp_coverage(repo, lookback):
    """返回 (stamped, total, unstamped_samples, invalid_samples)。

    stamped: 带有效戳的 commit 数；total: 回看窗口内 commit 总数；
    unstamped_samples / invalid_samples: 未盖戳 / 无效 method 的样例（各≤3）。
    无 commit / git 失败 → (None, 0, [], [])。
    """
    out, rc = _git(repo, "log", "-%d" % max(1, lookback),
                   "--pretty=format:%H%x1f%B%x1e")
    if rc != 0 or out is None:
        return None, 0, [], []
    records = [r for r in out.split("\x1e") if r.strip()]
    if not records:
        return None, 0, [], []
    stamped, unstamped, invalid = 0, [], []
    for rec in records:
        parts = rec.split("\x1f", 1)
        body = parts[1] if len(parts) > 1 else ""
        m = None
        for line in body.splitlines():
            lm = STAMP_RE.match(line.strip())
            if lm:
                m = lm
                break
        if m is None:
            unstamped.append(rec.strip()[:40])
            continue
        if m.group(1).lower() in STAMP_METHODS:
            stamped += 1
        else:
            invalid.append("%s %r" % (parts[0][:12] if parts else "?", m.group(1)))
    return stamped, len(records), unstamped[:3], invalid[:3]


def load_stamp_threshold(governance_dir):
    """读 thresholds.verification_stamp（lookback/min_ratio）。

    返回 (lookback, min_ratio)；redlines 缺失 → (None, None)（未接线）；
    存在但非法 → sys.exit(42)。
    """
    path = os.path.join(governance_dir, "redlines.yaml")
    if not os.path.exists(path):
        return None, None
    if yaml is None:
        print("verification_gate_check: pyyaml required (pip install pyyaml).",
              file=sys.stderr)
        sys.exit(NOT_WIRED)
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        print("verification_gate_check: redlines.yaml corrupt: %s" % e,
              file=sys.stderr)
        sys.exit(NOT_WIRED)
    if not isinstance(data, dict):
        print("verification_gate_check: redlines.yaml not a mapping.",
              file=sys.stderr)
        sys.exit(NOT_WIRED)
    sec = (data.get("thresholds") or {}).get("verification_stamp")
    if sec is None:
        return None, None
    if not isinstance(sec, dict):
        print("verification_gate_check: thresholds.verification_stamp must be a "
              "mapping (got %s)." % type(sec).__name__, file=sys.stderr)
        sys.exit(NOT_WIRED)
    lookback = sec.get("lookback", DEFAULT_LOOKBACK)
    ratio = sec.get("min_ratio")
    if not isinstance(lookback, int) or isinstance(lookback, bool) or lookback < 1:
        print("verification_gate_check: thresholds.verification_stamp.lookback "
              "must be int >= 1 (got %r)." % lookback, file=sys.stderr)
        sys.exit(NOT_WIRED)
    if not isinstance(ratio, (int, float)) or isinstance(ratio, bool) \
            or not (0.0 <= ratio <= 1.0):
        print("verification_gate_check: thresholds.verification_stamp.min_ratio "
              "must be a number in [0.0, 1.0] (got %r)." % ratio, file=sys.stderr)
        sys.exit(NOT_WIRED)
    return lookback, float(ratio)


def check_stamps(repo, governance_dir):
    lookback, min_ratio = load_stamp_threshold(governance_dir)
    if min_ratio is None:
        print("NOT-WIRED: thresholds.verification_stamp not configured in "
              "redlines.yaml (R-VER-008 needs lookback/min_ratio to judge); "
              "fail-closed.", file=sys.stderr)
        return NOT_WIRED
    if not os.path.isdir(repo) or not _is_repo(repo):
        print("NOT-WIRED: --repo is not a git work tree: %s" % repo,
              file=sys.stderr)
        return NOT_WIRED
    stamped, total, unstamped, invalid = stamp_coverage(repo, lookback)
    if stamped is None:
        print("NOT-WIRED: no commits readable in window (git failed or empty "
              "history); fail-closed.", file=sys.stderr)
        return NOT_WIRED
    ratio = stamped / total if total else 0.0
    print("verification_stamp: %d/%d stamped (ratio %.2f, min_ratio %.2f, "
          "lookback %d)" % (stamped, total, ratio, min_ratio, lookback))
    for s in unstamped:
        print("  unstamped: %.40s" % s, file=sys.stderr)
    for s in invalid:
        print("  invalid method (counted as unstamped): %s" % s, file=sys.stderr)
    if ratio < min_ratio:
        print("verification_gate_check: stamp coverage below threshold -> "
              "VIOLATION (R-VER-008). Stamp every commit, including "
              "Verified: none (unverified-but-stamped is legal and queryable).",
              file=sys.stderr)
        return VIOLATION
    print("verification_gate_check: PASS (R-VER-008 stamp coverage).")
    return OK


# ---- --survey：证据层探测（不做合规判定）----

def _exists_any(root, names):
    return [n for n in names if os.path.exists(os.path.join(root, n))]


def _survey_verify_asset(root):
    hits = []
    for cand in VERIFY_ASSET_CANDIDATES:
        p = os.path.join(root, cand)
        if os.path.isdir(p) and os.listdir(p):
            hits.append(cand + "/")
        elif os.path.isfile(p):
            hits.append(cand)
    for base in ("scripts", *AGENT_SKILL_DIRS):
        d = os.path.join(root, base)
        if not os.path.isdir(d):
            continue
        for entry in sorted(os.listdir(d)):
            if entry.lower().startswith(VERIFY_SCRIPT_PREFIX):
                hits.append(base + "/" + entry)
    return hits


def _survey_ci(root):
    hits = _exists_any(root, CI_CONFIG_CANDIDATES)
    step_hits = {}
    for c in hits:
        p = os.path.join(root, c)
        texts = []
        if os.path.isdir(p):
            for fn in sorted(os.listdir(p)):
                if fn.endswith((".yml", ".yaml")):
                    fp = os.path.join(p, fn)
                    if os.path.isfile(fp):
                        with open(fp, encoding="utf-8", errors="replace") as f:
                            texts.append(f.read().lower())
        elif os.path.isfile(p):
            with open(p, encoding="utf-8", errors="replace") as f:
                texts.append(f.read().lower())
        joined = "\n".join(texts)
        step_hits[c] = [k for k in CI_STEP_KEYWORDS if k in joined] if joined else []
    return {"config_found": hits, "step_keywords": step_hits}


def _survey_test_types(root):
    dep_text = []
    for m in DEP_MANIFESTS:
        p = os.path.join(root, m)
        if os.path.isfile(p):
            with open(p, encoding="utf-8", errors="replace") as f:
                dep_text.append(f.read().lower())
    joined = "\n".join(dep_text)
    dirs = []
    for base in ("", "test", "tests", "src"):
        d = os.path.join(root, base) if base else root
        if not os.path.isdir(d):
            continue
        dirs += [entry for entry in sorted(os.listdir(d))
                 if os.path.isdir(os.path.join(d, entry))]
    dirs = [x.lower() for x in dirs]
    return {
        "e2e": {
            "dep": [t for t in E2E_DEP_TOKENS if t in joined],
            "dir": [t for t in E2E_DIR_TOKENS if t in dirs],
        },
        "property": {
            "dep": [t for t in PROPERTY_DEP_TOKENS if t in joined],
        },
        "differential": {
            "dep": [t for t in DIFFERENTIAL_TOKENS if t.strip() in joined],
            "dir": [t for t in DIFFERENTIAL_DIR_TOKENS if t in dirs],
        },
        "dep_manifests_read": [m for m in DEP_MANIFESTS
                               if os.path.isfile(os.path.join(root, m))],
    }


def _survey_agent_adapter(root):
    """--survey：agent 调用适配层形态探测（证据层，不做合规判定）。

    三件事：可调用入口（复用 verify 资产候选）、git/CI 生命周期绑定、
    生态薄适配器候选。全部走候选链，miss 是建议素材、不构成违规。
    """
    entry = _survey_verify_asset(root)
    ci = _survey_ci(root)
    ci_calls_verify = {}
    for c in ci["config_found"]:
        p = os.path.join(root, c)
        texts = []
        if os.path.isdir(p):
            for fn in sorted(os.listdir(p)):
                if fn.endswith((".yml", ".yaml")):
                    fp = os.path.join(p, fn)
                    if os.path.isfile(fp):
                        with open(fp, encoding="utf-8", errors="replace") as f:
                            texts.append(f.read().lower())
        elif os.path.isfile(p):
            with open(p, encoding="utf-8", errors="replace") as f:
                texts.append(f.read().lower())
        ci_calls_verify[c] = "verify" in "\n".join(texts)
    return {
        "callable_entry": entry,
        "git_lifecycle_hook": _exists_any(root, GIT_HOOK_FILES),
        "ci_lifecycle": ci["config_found"],
        "ci_invokes_verify": ci_calls_verify,
        "ecosystem_adapter": _exists_any(root, AGENT_ADAPTER_FILES),
    }


def survey(repo):
    if not os.path.isdir(repo):
        print("NOT-WIRED: survey path is not a directory: %s" % repo,
              file=sys.stderr)
        return NOT_WIRED
    result = {
        "schema_version": 1,
        "repo": os.path.abspath(repo),
        "is_git_repo": _is_repo(repo),
        "verify_asset": _survey_verify_asset(repo),
        "ci": _survey_ci(repo),
        "test_types": _survey_test_types(repo),
        "agent_adapter": _survey_agent_adapter(repo),
    }
    if result["is_git_repo"]:
        stamped, total, _, _ = stamp_coverage(repo, DEFAULT_LOOKBACK)
        result["verification_stamp"] = {
            "lookback": DEFAULT_LOOKBACK,
            "stamped": stamped,
            "total": total,
            "ratio": round(stamped / total, 4) if total and stamped is not None
                     else None,
        }
    else:
        result["verification_stamp"] = None
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print("NOTE: survey is shape probing only; correctness (manifest fields, CI "
          "wiring, gate reality) needs human/CI verification. Misses are "
          "suggestion material, not violations.", file=sys.stderr)
    return OK


def main(argv):
    if "--self-test" in argv:
        return self_test()
    repo = None
    survey_path = None
    governance_dir = os.environ.get("GOVERNANCE_DIR", GOVERNANCE_DIR)
    i = 0
    while i < len(argv):
        if argv[i] == "--repo":
            repo = argv[i + 1]; i += 2
        elif argv[i] == "--survey":
            survey_path = argv[i + 1]; i += 2
        elif argv[i] == "--governance-dir":
            governance_dir = argv[i + 1]; i += 2
        else:
            i += 1
    if survey_path:
        return survey(survey_path)
    if repo is None:
        print("verification_gate_check: --repo <path> or --survey <path> "
              "required (or --self-test).", file=sys.stderr)
        return NOT_WIRED
    return check_stamps(repo, governance_dir)


def _mk_repo(path, commits):
    """建夹具 git 仓库：commits = [(message, files_or_None), ...]。"""
    os.makedirs(path, exist_ok=True)
    _git(path, "init", "-q")
    for msg, files in commits:
        if files:
            for fn, content in files.items():
                fp = os.path.join(path, fn)
                os.makedirs(os.path.dirname(fp), exist_ok=True)
                with open(fp, "w", encoding="utf-8") as f:
                    f.write(content)
            _git(path, "add", "-A")
        env = dict(os.environ)
        env["GIT_AUTHOR_NAME"] = env["GIT_COMMITTER_NAME"] = "fixture"
        env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = "f@example.com"
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = "2026-01-01T00:00:00"
        subprocess.run(["git", "-C", path, "commit", "-q", "--allow-empty",
                        "-m", msg], env=env, check=True,
                       capture_output=True, text=True)
    return path


def self_test():
    if shutil.which("git") is None:
        print("SELF-TEST: git unavailable; cannot test stamp logic. Treat as "
              "NOT-WIRED.", file=sys.stderr)
        return NOT_WIRED
    if yaml is None:
        print("SELF-TEST: pyyaml required.", file=sys.stderr)
        return NOT_WIRED
    tmp = tempfile.mkdtemp(prefix="vgc-")
    old_gov = os.environ.get("GOVERNANCE_DIR")
    os.environ.pop("GOVERNANCE_DIR", None)
    try:
        def gov_with(cfg):
            gd = os.path.join(tmp, "gov_" + cfg["name"])
            os.makedirs(gd, exist_ok=True)
            with open(os.path.join(gd, "redlines.yaml"), "w", encoding="utf-8") as f:
                yaml.safe_dump(cfg["yaml"], f)
            return gd

        def run(repo, gd=None):
            args = ["--repo", repo]
            if gd:
                args += ["--governance-dir", gd]
            try:
                return main(args)
            except SystemExit as e:
                return e.code

        # 夹具仓库：4 commit，3 盖有效戳（1 个 none），1 未盖
        repo = _mk_repo(os.path.join(tmp, "r1"), [
            ("feat: a\n\nVerified: e2e\nEvidence: reports/e2e.log", {"a.txt": "1"}),
            ("fix: b\n\nVerified: none", {"b.txt": "2"}),
            ("chore: c\n\nVerified: Unit", None),
            ("feat: d (no stamp)", {"d.txt": "4"}),
        ])
        th_90 = gov_with({"name": "t90", "yaml": {
            "thresholds": {"verification_stamp": {"lookback": 10, "min_ratio": 0.9}}}})
        th_50 = gov_with({"name": "t50", "yaml": {
            "thresholds": {"verification_stamp": {"lookback": 10, "min_ratio": 0.5}}}})
        th_noop = gov_with({"name": "noop", "yaml": {"thresholds": {}}})

        # 场景1：覆盖率 0.75 < 0.9 -> 违规
        rc1 = run(repo, th_90)
        assert rc1 == VIOLATION, "expected VIOLATION (ratio 0.75 < 0.9), got %d" % rc1
        # 场景2：覆盖率 0.75 >= 0.5 -> 通过
        rc2 = run(repo, th_50)
        assert rc2 == OK, "expected PASS (0.75 >= 0.5), got %d" % rc2
        # 场景3：阈值段缺失 -> 42（未接线，fail-closed）
        rc3 = run(repo, th_noop)
        assert rc3 == NOT_WIRED, "expected 42 (no verification_stamp config), got %d" % rc3
        # 场景4：无 governance 目录 -> 42
        rc4 = run(repo, os.path.join(tmp, "gov_missing"))
        assert rc4 == NOT_WIRED, "expected 42 (no governance dir), got %d" % rc4

        # 夹具仓库2：全部盖戳（含 none 与大小写混合）
        repo2 = _mk_repo(os.path.join(tmp, "r2"), [
            ("a\n\nVerified: e2e", None),
            ("b\n\nverified: NONE", None),
            ("c\n\nVerified: property", None),
        ])
        rc5 = run(repo2, th_90)
        assert rc5 == OK, "expected PASS (all stamped incl none), got %d" % rc5

        # 夹具仓库3：白名单外 method = 无效戳（按未盖计）
        repo3 = _mk_repo(os.path.join(tmp, "r3"), [
            ("a\n\nVerified: vibes", None),
            ("b\n\nVerified: unit", None),
        ])
        rc6 = run(repo3, th_90)
        assert rc6 == VIOLATION, "expected VIOLATION (invalid method counted " \
                                 "as unstamped), got %d" % rc6

        # 场景7：min_ratio 非法（字符串）-> 42
        th_bad = gov_with({"name": "tbad", "yaml": {
            "thresholds": {"verification_stamp": {"lookback": 10, "min_ratio": "high"}}}})
        rc7 = run(repo, th_bad)
        assert rc7 == NOT_WIRED, "expected 42 (bad min_ratio type), got %d" % rc7

        # 场景8：lookback 非法（0）-> 42
        th_bad2 = gov_with({"name": "tbad2", "yaml": {
            "thresholds": {"verification_stamp": {"lookback": 0, "min_ratio": 0.5}}}})
        rc8 = run(repo, th_bad2)
        assert rc8 == NOT_WIRED, "expected 42 (lookback 0), got %d" % rc8

        # 场景9：非 git 目录 -> 42
        plain = os.path.join(tmp, "plain")
        os.makedirs(plain, exist_ok=True)
        rc9 = run(plain, th_50)
        assert rc9 == NOT_WIRED, "expected 42 (not a git repo), got %d" % rc9

        # 场景10：--survey 探测（verify 资产 / CI 关键词 / 依赖特征 / 戳采样）
        rsv = os.path.join(tmp, "r_survey")
        os.makedirs(os.path.join(rsv, "verify"), exist_ok=True)
        os.makedirs(os.path.join(rsv, ".github", "workflows"), exist_ok=True)
        os.makedirs(os.path.join(rsv, "e2e"), exist_ok=True)
        with open(os.path.join(rsv, ".github", "workflows", "ci.yml"),
                  "w", encoding="utf-8") as f:
            f.write("jobs:\n  build:\n  test:\n    run: npm test\n  lint:\n")
        with open(os.path.join(rsv, "package.json"), "w", encoding="utf-8") as f:
            f.write('{"devDependencies": {"fast-check": "^3.0", '
                    '"playwright": "^1.40"}}')
        with open(os.path.join(rsv, "verify", "run.sh"), "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\n")
        _mk_repo(rsv, [("init\n\nVerified: unit", {"verify/run.sh": "#!/bin/sh\n"})])
        import contextlib, io as _io
        out10 = _io.StringIO()
        with contextlib.redirect_stdout(out10):
            rc10 = main(["--survey", rsv])
        assert rc10 == OK, "expected survey OK, got %d" % rc10
        j = json.loads(out10.getvalue())
        assert "verify/" in j["verify_asset"], j["verify_asset"]
        assert j["ci"]["config_found"] == [".github/workflows"], j["ci"]
        assert set(j["ci"]["step_keywords"][".github/workflows"]) == \
            {"test", "build", "lint"}, j["ci"]["step_keywords"]
        assert "fast-check" in j["test_types"]["property"]["dep"], j["test_types"]
        assert "playwright" in j["test_types"]["e2e"]["dep"], j["test_types"]
        assert "e2e" in j["test_types"]["e2e"]["dir"], j["test_types"]
        assert j["verification_stamp"]["stamped"] == 1, j["verification_stamp"]

        # 场景12：--survey agent_adapter 段（git 钩子 + CI 调 verify + 生态薄适配）
        rad = os.path.join(tmp, "r_adapter")
        os.makedirs(os.path.join(rad, ".githooks"), exist_ok=True)
        os.makedirs(os.path.join(rad, ".claude", "skills", "verify"), exist_ok=True)
        os.makedirs(os.path.join(rad, ".github", "workflows"), exist_ok=True)
        with open(os.path.join(rad, ".githooks", "pre-commit"), "w",
                  encoding="utf-8") as f:
            f.write("#!/bin/sh\nbash scripts/verify-gate.sh check\n")
        with open(os.path.join(rad, "AGENTS.md"), "w", encoding="utf-8") as f:
            f.write("# repo instructions\nrun scripts/verify then mark-verified\n")
        with open(os.path.join(rad, ".claude", "skills", "verify", "SKILL.md"),
                  "w", encoding="utf-8") as f:
            f.write("---\nname: verify\n---\ncall scripts/verify\n")
        with open(os.path.join(rad, ".github", "workflows", "ci.yml"), "w",
                  encoding="utf-8") as f:
            f.write("jobs:\n  verify:\n    run: bash scripts/verify.sh\n")
        _mk_repo(rad, [("init\n\nVerified: unit", {"AGENTS.md": "x"})])
        out12 = _io.StringIO()
        with contextlib.redirect_stdout(out12):
            rc12 = main(["--survey", rad])
        assert rc12 == OK, "expected survey OK, got %d" % rc12
        j12 = json.loads(out12.getvalue())
        aa = j12["agent_adapter"]
        assert aa["git_lifecycle_hook"] == [".githooks/pre-commit"], aa
        assert "AGENTS.md" in aa["ecosystem_adapter"], aa
        assert ".claude/skills" in aa["ecosystem_adapter"], aa
        assert aa["ci_invokes_verify"][".github/workflows"] is True, aa
        assert j["agent_adapter"]["git_lifecycle_hook"] == [], j["agent_adapter"]

        # 场景11：--survey 路径不存在 -> 42
        rc11 = main(["--survey", os.path.join(tmp, "nope")])
        assert rc11 == NOT_WIRED, "expected 42 (missing survey path), got %d" % rc11

        print("SELF-TEST: verification_gate_check.py PASS (12 scenarios).")
        return OK
    finally:
        os.environ.pop("GOVERNANCE_DIR", None)
        if old_gov is not None:
            os.environ["GOVERNANCE_DIR"] = old_gov
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
