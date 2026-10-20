#!/usr/bin/env python3
"""resolve_version.py — map a reported component+version to a read-only git worktree.

Usage:
  resolve_version.py --list                       list components and local paths
  resolve_version.py <component|alias> <version>  resolve and create the worktree

The version is never guessed: when it is not found the script reports NOT_FOUND and
prints nearby tags, and the Agent must ask the user to confirm — it must not
silently substitute a different version.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib


def _make_readonly(root: str) -> None:
    """Best-effort 把 worktree 设为只读（兑现 docstring 的 read-only 承诺）。"""
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            for d in dirnames:
                os.chmod(os.path.join(dirpath, d), 0o555)
            for f in filenames:
                os.chmod(os.path.join(dirpath, f), 0o444)
        os.chmod(root, 0o555)
    except OSError as exc:
        print(f"  warn: worktree 设只读失败（可继续，勿在其中改动）: {exc}")


def main() -> None:
    args = sys.argv[1:]
    config = lib.load_config()
    codehub = config.get("codehub", {}) or {}

    if args and args[0] == "--list":
        for key, value in codehub.items():
            if isinstance(value, dict):
                path = value.get("path", "")
                aliases = ",".join(str(a) for a in (value.get("aliases") or []))
            else:
                path, aliases = value, ""
            print(f"{key}\tpath={path}\taliases={aliases}")
        return

    if len(args) < 2:
        lib.die("usage: resolve_version.py <component|alias> <version> | --list")
    component_input = args[0]
    version = args[1].lstrip("vV")

    matches = []
    for key, value in codehub.items():
        if key == component_input:
            matches.append(key)
        elif isinstance(value, dict) and component_input in (value.get("aliases") or []):
            matches.append(key)
    if not matches:
        print(f"status=UNKNOWN_COMPONENT input={component_input}")
        print(f"  known: {' '.join(codehub.keys())}")
        sys.exit(1)
    if len(matches) > 1:
        print(f"status=AMBIGUOUS_COMPONENT input={component_input} "
              f"candidates={' '.join(matches)}")
        sys.exit(1)

    component = matches[0]
    entry = codehub[component]
    repo_path = lib.expand_path(entry.get("path") if isinstance(entry, dict) else entry)
    worktree_cfg = config.get("worktree") or {}
    worktree = lib.expand_path(worktree_cfg.get("workdir", "")) if isinstance(worktree_cfg, dict) else ""
    if not worktree:
        lib.die("config.worktree.workdir 未配置")
    os.makedirs(worktree, exist_ok=True)

    git_dir = os.path.join(repo_path, ".git")
    if not (os.path.isdir(git_dir) or os.path.isfile(git_dir)):
        print(f"component={component} status=NO_LOCAL_REPO path={repo_path}")
        sys.exit(1)

    lib.git_fetch(repo_path)

    patterns = (worktree_cfg.get("tag_patterns") if isinstance(worktree_cfg, dict) else None) \
        or ["v{version}", "{version}"]
    tags, commits = [], []
    for pattern in patterns:
        tag = pattern.replace("{version}", version)
        if lib.git_rev_parse(repo_path, f"refs/tags/{tag}"):
            tags.append(tag)
            commits.append(lib.git_rev_list(repo_path, tag))
    if not tags:
        print(f"component={component} repo={repo_path} status=NOT_FOUND version={version}")
        nearby = lib.git_tags_nearby(repo_path, version)
        print(f"  nearby: {nearby}" if nearby else "  无相近 tag")
        sys.exit(1)
    if len(set(commits)) > 1:
        print(f"component={component} status=AMBIGUOUS_TAG "
              f"tags={' '.join(tags)} commits={' '.join(commits)}")
        sys.exit(1)

    tag, commit = tags[0], commits[0]
    safe_tag = tag.replace("/", "_")
    path = os.path.join(worktree, f"{component}-{safe_tag}")
    if not os.path.isdir(path):
        lib.git_add_worktree(repo_path, path, commit)
        _make_readonly(path)
    print(f"component={component} repo={repo_path} status=OK tag={tag} "
          f"commit={commit[:12]} path={path}")


if __name__ == "__main__":
    main()
