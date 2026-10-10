#!/usr/bin/env python3
"""logs.py — query cluster pod logs / node logs / events via the bastion.

Subcommands:
  pods [component] [-n NS]
  logs <component> [-n NS] [--pod NAME] [--since 30m] [--tail 500]
       [--grep PAT] [--previous] [--before N] [--after N] [--context N]
       [--max-matches N] [--container NAME | --all-containers]
  events [--warnings] [--for OBJECT] [--max N] [-n NS]
  describe <pod> [-n NS]
  describe-node <node>
  node-of <pod> [-n NS]
  hop-check <node>
  node-log <node> <file> [--any-path] [--tail N] [--grep PAT] [--before N]
                    [--after N] [--context N] [--max-matches N]
  node-journal <node> <unit> [--since 30m] [--grep PAT] [--before N]
                    [--after N] [--context N] [--max-matches N]
  node-find <node> [--name PAT] [--newer 10m|2h|1d] [--max N]
  node-exec <node> <command...> [--scenario node|network|pod]
  pause-pid <node> [--name POD] [--namespace NS]
  nsenter-net <node> <pid> <command...>

Discovery-first model (problem-driven two-stage troubleshooting):
* ``pods`` / ``events`` default to ``--all-namespaces``; ``logs`` resolves the
  component's pods across namespaces (fixes NO_PODS when business pods live
  outside the default namespace).  ``-n NS`` narrows explicitly.
* ``node-find`` searches the scan directory set (``conn.scan_dirs``, default:
  /var/log, /var/log/pods, /var/log/containers, /data/logs, /var/lib/kubelet)
  and lists regular files only (``-type f``), so symlinks cannot escape the set.
* ``node-log`` refuses paths outside that set unless ``--any-path`` is given —
  the one explicit escape hatch, which always echoes a warning (auditable,
  never silent).
* ``node-exec`` runs read-only diagnostic commands on a node, gated per
  scenario (node / network / pod) by a strict allow-list; nothing outside it
  can run.  Put ``--scenario`` before ``<node>``.
* ``pause-pid`` discovers the pause (sandbox) container's host PID, probing
  crictl (containerd) first and falling back to docker automatically.  Each
  ``execute_on_node`` call is a fresh shell, so the runtime→tool selection
  lives as a Python variable here — remote environment variables cannot carry
  state between calls, and shell composition would bypass the allow-list.
* ``nsenter-net`` enters a container/pause network namespace from the node and
  runs a network-allow-listed read-only command there.  The ``-t <pid> -n``
  shape is built by the script itself, so no mount/uts/ipc namespace flag can
  be injected.  Note: ``/etc/resolv.conf`` belongs to the mount namespace and
  is NOT switched — reproduce container DNS with an explicit
  ``dig @<clusterDNS>`` plus manually constructed search domains.

Grep flags mirror grep -B/-A/-C: matched lines are prefixed with their local
line number (``L<no>:``), context lines are indented, match groups are
separated by ``--``, and ``--max-matches`` caps how many hits are shown so a
broad --grep cannot flood the context window.

All remote access is routed through lib.run_kubectl / lib.execute_on_node, so the
Agent never builds host names or runs ssh/kubectl directly.
"""
import os
import re
import sys
import json
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib

OUTPUT_LIMIT = 300  # max log lines echoed back per command
DEFAULT_MAX_MATCHES = 20
DEFAULT_EVENT_LIMIT = 50
DEFAULT_FIND_LIMIT = 50
MAX_PODS_PER_READ = 20  # refuse to fan out log reads beyond this without narrowing

_CONTAINER_RE = re.compile(r"[a-z0-9]([a-z0-9.-]*[a-z0-9])?")
_GLOB_RE = re.compile(r"[A-Za-z0-9_.*?\[\]/-]+")
_NODE_NAME_RE = re.compile(r"[a-z0-9.-]+")
_NAMESPACE_RE = re.compile(r"[a-z0-9-]+")

# "namespace/pod" pairs, one per line — used to discover pods across namespaces.
_POD_PAIRS_JSONPATH = r'{range .items[*]}{.metadata.namespace}/{.metadata.name}{"\n"}{end}'

# Read-only diagnostic commands allowed on a node, gated per scenario.
# Values are full-match regexes over the argument string (empty = no args).
# Anything not listed here is refused before any SSH round-trip happens.
_SCENARIO_COMMANDS: dict[str, dict[str, str]] = {
    "node": {
        "df":       r"(-h|-T|-i|-h -T|-h -i|-k|-m|-P|)",
        "free":     r"(-h|-m|-g|-k|-t|-w|--si|)",
        "vmstat":   r"([1-9][0-9]?( [1-9][0-9]?)?|-w|-a|-S [mk]|)",
        "uptime":   r"()",
        "top":      r"(-bn1|)",
        "dmesg":    r"(-T|-l [a-z,]+|--level=[a-z,]+|)",
        "pidstat":  r"([1-9][0-9]?|-d [1-9][0-9]?|)",
        "ps":       r"(aux|-ef|aux --sort=-%cpu|aux --sort=-%mem|)",
    },
    "network": {
        "ss":         r"(-[a-z]+|)",
        "ip":         r"((addr|route|link|neigh)( show)?|-br (addr|link|route)|)",
        "ping":       r"((-c [1-5] )?(-w [1-9] )?[A-Za-z0-9._-]+|)",
        "dig":        r"(@[A-Za-z0-9._-]+ )?[A-Za-z0-9._-]+( (A|AAAA|CNAME|MX|NS|PTR|SOA|TXT))?|",
        "nslookup":   r"([A-Za-z0-9._-]+( [A-Za-z]+)?|)",
        "traceroute": r"([A-Za-z0-9._-]+|-[1-9][0-9]? [A-Za-z0-9._-]+|)",
        "iptables":   r"(-L|-S|-L -n|-S -n|-L -n -v|-S -n -v|-nvL|-L --line-numbers"
                      r"|-t (nat|filter|mangle) -L -n|)",
        "conntrack":  r"(-L|-L -n|)",
    },
    "pod": {
        "crictl": r"(ps( -a)?|pods|images|stats|inspect [a-f0-9]{8,64}"
                  r"|inspectp [a-f0-9]{8,64}"
                  r"|logs [a-f0-9]{8,64}( --tail [1-9][0-9]{0,3})?|)",
        # docker shim clusters (k8s <= 1.23): read-only pause-container PID discovery.
        # inspect is pinned to a single fixed format string -- arbitrary format
        # templates are refused, so nothing beyond State.Pid can be read.
        "docker": r"(ps( -a)?( --filter name=k8s_Pause)?"
                  r"|inspect [A-Za-z0-9_.-]+ --format '?\{\{\.State\.Pid\}\}'?|)",
    },
}


def _nonneg_int(value: str, flag: str) -> int:
    """Validate a non-negative integer CLI argument (die on garbage)."""
    if not re.fullmatch(r"[0-9]+", value):
        lib.die(f"{flag} 必须是非负整数")
    return int(value)


def _add_grep_flags(parser: argparse.ArgumentParser) -> None:
    """Attach the shared --grep/context flag set to a subparser."""
    parser.add_argument("--grep", default="")
    parser.add_argument("--before", default="0")
    parser.add_argument("--after", default="0")
    parser.add_argument("--context", default="0")
    parser.add_argument("--max-matches", default=str(DEFAULT_MAX_MATCHES))


def _grep_context_args(args) -> tuple:
    """Resolve (pattern, before, after, max_matches) with validation.

    --context overrides both --before and --after when non-zero.
    """
    before = _nonneg_int(args.before, "--before")
    after = _nonneg_int(args.after, "--after")
    context = _nonneg_int(args.context, "--context")
    if context:
        before = after = context
    max_matches = _nonneg_int(args.max_matches, "--max-matches")
    return args.grep, before, after, max_matches


def _apply_filters(lines, grep: str):
    """Plain grep: keep matching lines only (no context)."""
    if not grep:
        return lines
    pattern = re.compile(grep)
    return [line for line in lines if pattern.search(line)]


def _grep_with_context(lines, pattern: str, before: int, after: int,
                       max_matches: int):
    """grep -B/-A/-C style extraction.

    Matched lines carry an ``L<lineno>:`` prefix (lineno is the line's position
    in the fetched window), context lines are indented by five spaces, groups
    are separated by ``--``. With before == after == 0 this degrades to plain
    filtering. Returns [] when nothing matches.
    """
    regex = re.compile(pattern)
    hits = [i for i, line in enumerate(lines) if regex.search(line)]
    if not hits:
        return []
    truncated = len(hits) > max_matches
    if truncated:
        hits = hits[:max_matches]
    if before == 0 and after == 0:
        rendered = [lines[i] for i in hits]
        if truncated:
            rendered.append(
                f"(--max-matches {max_matches} reached; "
                "narrow --since or refine --grep)")
        return rendered
    hit_set = set(hits)
    keep = set()
    for i in hits:
        for j in range(max(0, i - before), min(len(lines), i + after + 1)):
            keep.add(j)
    ordered = sorted(keep)
    groups = []
    current = [ordered[0]]
    for idx in ordered[1:]:
        if idx > current[-1] + 1:
            groups.append(current)
            current = []
        current.append(idx)
    groups.append(current)
    rendered = []
    for group_no, group in enumerate(groups):
        if group_no:
            rendered.append("--")
        for i in group:
            prefix = f"L{i + 1}:" if i in hit_set else " " * 5
            rendered.append(prefix + lines[i])
    if truncated:
        rendered.append(
            f"(--max-matches {max_matches} reached; "
            "narrow --since or refine --grep)")
    return rendered


def _render_grep(lines, args):
    """Render grep output honouring --before/--after/--context/--max-matches."""
    pattern, before, after, max_matches = _grep_context_args(args)
    if pattern and (before or after):
        return _grep_with_context(lines, pattern, before, after, max_matches)
    return _apply_filters(lines, pattern)


def _tail(lines, n: int):
    return lines[-n:] if n > 0 else lines


def _pods_matching(selector: str, namespace: str) -> list[tuple[str, str]]:
    """Return (namespace, pod) pairs for *selector*.

    ``namespace == ""`` scans every namespace (the discovery default); otherwise
    the listing is narrowed to the given namespace.  The listing itself always
    goes out with ``namespace=""`` so a configured conn.namespace cannot hide
    pods from discovery.
    """
    scope = ["-n", namespace] if namespace else ["-A"]
    result = lib.run_kubectl("get", "pods", *scope, "-l", selector,
                             "-o", "jsonpath=" + _POD_PAIRS_JSONPATH,
                             namespace="", capture=True)
    pairs = []
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if "/" in line:
            ns, _, name = line.partition("/")
            if ns and name:
                pairs.append((ns, name))
    return pairs


def _resolve_pod(pod: str, namespace: str) -> tuple[str, str]:
    """Return (namespace, pod) for *pod*, searching all namespaces when none given."""
    if namespace:
        return namespace, pod
    result = lib.run_kubectl("get", "pods", "-A",
                             "-o", "jsonpath=" + _POD_PAIRS_JSONPATH,
                             namespace="", capture=True)
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if "/" in line:
            ns, _, name = line.partition("/")
            if name == pod:
                return ns, name
    lib.die(f"找不到 pod {pod}（已扫描全部 namespace；可用 -n 指定）")


def _remote_lines(result) -> list[str]:
    """Return stripped non-empty stdout lines of a check=False remote run."""
    if result is None or (result.returncode or 0) != 0:
        return []
    return [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]


def _pause_pid(node_ip: str, name: str, namespace: str) -> tuple[str, str]:
    """Discover the pause (sandbox) container's host PID, auto-detecting runtime.

    Probes crictl (containerd / CRI-O) first; on failure falls back to docker
    (dockershim, k8s <= 1.23).  Returns (pid, runtime).  The runtime→tool
    selection is a Python variable here: remote env vars cannot survive across
    execute_on_node calls (fresh shell each time), and compound shell commands
    would bypass the allow-list.
    """
    crictl_args = ["crictl", "pods", "-q"]
    if namespace:
        crictl_args += ["--namespace", namespace]
    if name:
        crictl_args += ["--name", name]
    ids = _remote_lines(lib.execute_on_node(node_ip, *crictl_args,
                                            capture=True, check=False))
    if ids:
        runtime = "containerd"
    else:
        docker_args = ["docker", "ps", "-a", "--filter", "name=k8s_Pause",
                       "--format", "{{.ID}}"]
        if name:
            docker_args += ["--filter", f"name={name}"]
        ids = _remote_lines(lib.execute_on_node(node_ip, *docker_args,
                                                capture=True, check=False))
        runtime = "docker"

    if not ids:
        lib.die(f"status=NO_PAUSE_CONTAINER name={name or 'any'} "
                f"namespace={namespace or 'any'}：节点上未发现 pause/sandbox 容器，"
                "确认目标 pod 是否已调度到该节点")
    if len(ids) > 1:
        print(f"status=AMBIGUOUS_PAUSE matches={len(ids)} runtime={runtime}")
        for sid in ids:
            print(f"  {sid}")
        print("  用 --name <pod> / --namespace <ns> 收窄后再试")
        sys.exit(1)
    sid = ids[0]

    if runtime == "containerd":
        r = lib.execute_on_node(node_ip, "crictl", "inspectp", "-o", "json", sid,
                                capture=True, check=False)
        try:
            data = json.loads((r.stdout or "") if r else "")
            pid = data["info"]["pid"]
            # crictl's info map values are JSON-encoded strings — unwrap once more.
            pid = json.loads(pid) if isinstance(pid, str) else pid
            pid = str(pid)
        except (ValueError, KeyError, TypeError):
            lib.die(f"status=PARSE_FAIL runtime=containerd sandbox={sid}："
                    "无法从 crictl inspectp 输出解析 info.pid")
        if not pid.isdigit():
            lib.die(f"status=PARSE_FAIL runtime=containerd sandbox={sid}："
                    f"info.pid 不是数字: {pid}")
    else:
        r = lib.execute_on_node(node_ip, "docker", "inspect", sid,
                                "--format", "{{.State.Pid}}",
                                capture=True, check=False)
        pid = (r.stdout or "").strip() if r else ""
        if not pid.isdigit():
            lib.die(f"status=PARSE_FAIL runtime=docker container={sid}："
                    f"docker inspect 输出不是数字: {pid}")
    return pid, runtime


def _validate_node_exec(args) -> None:
    """Gate node-exec behind the per-scenario read-only allow-list (pre-network)."""
    scenario = args.scenario
    if scenario not in _SCENARIO_COMMANDS:
        lib.die(f"--scenario 只支持: {' '.join(sorted(_SCENARIO_COMMANDS))}")
    allowed = _SCENARIO_COMMANDS[scenario]
    command = args.node_command[0] if args.node_command else ""
    if command not in allowed:
        lib.die(f"场景 {scenario} 不允许执行 {command or '(空)'}；"
                f"可用命令: {' '.join(sorted(allowed))}")
    rest = " ".join(args.node_command[1:])
    if not re.fullmatch(allowed[command], rest):
        lib.die(f"参数不被允许（场景 {scenario}）: {command} {rest}")


def _validate_nsenter_net(args) -> None:
    """Gate nsenter-net: pure-digit pid + network allow-list inner command.

    A composite gate: the ``nsenter -t <pid> -n`` shape is constructed by the
    tool itself, so the agent can only supply the pid and the inner command —
    no mount/uts/ipc namespace flag can ever be injected, and the inner command
    stays inside the network scenario's read-only allow-list.
    """
    if not re.fullmatch(r"[0-9]{1,10}", args.pid):
        lib.die(f"pid 必须是宿主机进程号（纯数字，来自 crictl inspectp 的 info.pid）: {args.pid}")
    allowed = _SCENARIO_COMMANDS["network"]
    command = args.net_command[0] if args.net_command else ""
    if command not in allowed:
        lib.die(f"nsenter-net 内部命令仅限 network 场景白名单: {command or '(空)'}；"
                f"可用: {' '.join(sorted(allowed))}")
    rest = " ".join(args.net_command[1:])
    if not re.fullmatch(allowed[command], rest):
        lib.die(f"参数不被允许: {command} {rest}")


def _validate_args(args) -> None:
    """Validate every subcommand's arguments *before* any network access.

    Keeping validation ahead of load_connection() means bad input never costs
    an SSH round-trip and a connection failure can never mask a local error.
    """
    if getattr(args, "namespace", "") and not _NAMESPACE_RE.fullmatch(args.namespace):
        lib.die(f"namespace 不合法: {args.namespace}")

    if args.command == "logs":
        if not re.fullmatch(r"[0-9]+[smhd]", args.since):
            lib.die("--since 格式应为 30m / 2h / 1d")
        _nonneg_int(args.tail, "--tail")
        if args.container and args.all_containers:
            lib.die("-c/--container 与 --all-containers 互斥，二选一")
        if args.container and not _CONTAINER_RE.fullmatch(args.container):
            lib.die(f"容器名不合法: {args.container}")
        if args.previous and args.all_containers:
            lib.die("--previous 不能与 --all-containers 同用（需指定 -c）")
        if args.pod and not _CONTAINER_RE.fullmatch(args.pod):
            lib.die(f"pod 名不合法: {args.pod}")
        _grep_context_args(args)

    elif args.command == "events":
        _nonneg_int(args.max, "--max")
        if args.for_object and not _NODE_NAME_RE.fullmatch(args.for_object):
            lib.die(f"--for 对象名不合法: {args.for_object}")

    elif args.command in ("describe", "node-of"):
        if not _NODE_NAME_RE.fullmatch(args.pod):
            lib.die("pod 名不合法")

    elif args.command == "describe-node":
        if not _NODE_NAME_RE.fullmatch(args.node):
            lib.die("节点名不合法")

    elif args.command == "node-log":
        _nonneg_int(args.tail, "--tail")
        if not args.any_path:
            lib.validate_log_path(args.file)
        _grep_context_args(args)

    elif args.command == "node-journal":
        if not re.fullmatch(r"[A-Za-z0-9_.@-]+", args.unit):
            lib.die("unit 名不合法")
        if not re.fullmatch(r"[0-9]+[smhd]", args.since):
            lib.die("--since 格式应为 30m / 2h / 1d")
        _grep_context_args(args)

    elif args.command == "node-find":
        _nonneg_int(args.max, "--max")
        if args.name and not _GLOB_RE.fullmatch(args.name):
            lib.die(f"--name 只允许文件名通配字符（*.log 等）: {args.name}")
        if args.newer and not re.fullmatch(r"[0-9]+[mhd]", args.newer):
            lib.die("--newer 格式应为 10m / 2h / 1d")

    elif args.command == "node-exec":
        _validate_node_exec(args)

    elif args.command == "nsenter-net":
        _validate_nsenter_net(args)

    elif args.command == "pause-pid":
        if args.name and not _CONTAINER_RE.fullmatch(args.name):
            lib.die(f"pod 名不合法: {args.name}")


def build_parser() -> argparse.ArgumentParser:
    """Register every subparser; handlers are cmd_* functions (see _DISPATCH)."""
    parser = argparse.ArgumentParser(prog="logs.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("pods")
    p.add_argument("component", nargs="?")
    p.add_argument("-n", "--namespace", default="")

    p = sub.add_parser("logs")
    p.add_argument("component")
    p.add_argument("-n", "--namespace", default="")
    p.add_argument("--pod", default="")
    p.add_argument("--since", default="30m")
    p.add_argument("--tail", default="500")
    _add_grep_flags(p)
    p.add_argument("--previous", action="store_true")
    p.add_argument("-c", "--container", default="")
    p.add_argument("--all-containers", action="store_true")

    p = sub.add_parser("events")
    p.add_argument("--warnings", action="store_true")
    p.add_argument("--for", dest="for_object", default="")
    p.add_argument("--max", default=str(DEFAULT_EVENT_LIMIT))
    p.add_argument("-n", "--namespace", default="")

    p = sub.add_parser("describe")
    p.add_argument("pod")
    p.add_argument("-n", "--namespace", default="")
    p = sub.add_parser("describe-node")
    p.add_argument("node")
    p = sub.add_parser("node-of")
    p.add_argument("pod")
    p.add_argument("-n", "--namespace", default="")
    p = sub.add_parser("hop-check")
    p.add_argument("node")

    p = sub.add_parser("node-log")
    p.add_argument("node")
    p.add_argument("file")
    p.add_argument("--any-path", action="store_true")
    p.add_argument("--tail", default="500")
    _add_grep_flags(p)

    p = sub.add_parser("node-journal")
    p.add_argument("node")
    p.add_argument("unit")
    p.add_argument("--since", default="30m")
    _add_grep_flags(p)

    p = sub.add_parser("node-find")
    p.add_argument("node")
    p.add_argument("--name", default="")
    p.add_argument("--newer", default="")
    p.add_argument("--max", default=str(DEFAULT_FIND_LIMIT))

    # NOTE: the positional must NOT be named "command" — that collides with the
    # subparsers' dest="command" and would shadow the subcommand name.
    p = sub.add_parser("node-exec")
    p.add_argument("--scenario", default="node", choices=sorted(_SCENARIO_COMMANDS))
    p.add_argument("node")
    p.add_argument("node_command", nargs=argparse.REMAINDER, metavar="command")

    p = sub.add_parser("nsenter-net")
    p.add_argument("node")
    p.add_argument("pid")
    p.add_argument("net_command", nargs=argparse.REMAINDER, metavar="command")

    p = sub.add_parser("pause-pid")
    p.add_argument("node")
    p.add_argument("--name", default="")
    p.add_argument("--namespace", default="")

    return parser


def main() -> None:
    """Parse, validate (pre-network), connect, then dispatch to a cmd_* handler."""
    args = build_parser().parse_args()
    _validate_args(args)   # local checks first: no wasted SSH round-trip
    lib.load_connection()  # network only after every argument is known-good
    _DISPATCH[args.command](args)


def cmd_pods(args) -> None:
    scope = ["-n", args.namespace] if args.namespace else ["-A"]
    if args.component:
        result = lib.run_kubectl("get", "pods", *scope, "-l",
                                 lib.selector_for(args.component), "-o", "wide",
                                 namespace="", capture=True)
    else:
        result = lib.run_kubectl("get", "pods", *scope, "-o", "wide",
                                 namespace="", capture=True)
    print(result.stdout.rstrip())

def cmd_logs(args) -> None:
    selector = lib.selector_for(args.component)
    pairs = _pods_matching(selector, args.namespace)
    if args.pod:
        pairs = [(ns, pod) for ns, pod in pairs if pod == args.pod]
    if not pairs:
        print(f"status=NO_PODS component={args.component} selector={selector} "
              f"namespace={args.namespace or 'ALL'}")
        print("  已默认扫描全部 namespace；可用 -n <ns> 收窄，或检查 codehub 的 selector 配置")
        sys.exit(1)
    if len(pairs) > MAX_PODS_PER_READ:
        print(f"status=TOO_MANY_PODS component={args.component} "
              f"selector={selector} count={len(pairs)}")
        print(f"  匹配 pod 超过 {MAX_PODS_PER_READ} 个；"
              "用 --pod <name> 指定单个 pod，或 -n <ns> 收窄后再读日志")
        sys.exit(1)
    extra = []
    if args.container:
        extra += ["-c", args.container]
    if args.all_containers:
        extra += ["--all-containers=true"]
    previous = ["--previous"] if args.previous else []
    for ns, pod in pairs:
        print(f"=== {ns}/{pod} (since={args.since} tail={args.tail} "
              f"grep={args.grep or 'none'}) ===")
        out = lib.run_kubectl("logs", "-n", ns, pod, f"--since={args.since}",
                              f"--tail={args.tail}", *extra, *previous,
                              namespace="", capture=True)
        lines = _render_grep((out.stdout or "").splitlines(), args)
        print("\n".join(_tail(lines, OUTPUT_LIMIT)))

def cmd_events(args) -> None:
    selectors = []
    if args.warnings:
        selectors.append("type=Warning")
    if args.for_object:
        selectors.append(f"involvedObject.name={args.for_object}")
    scope = ["-n", args.namespace] if args.namespace else ["-A"]
    cmd = ["get", "events", *scope, "--sort-by=.lastTimestamp"]
    if selectors:
        cmd += ["--field-selector", ",".join(selectors)]
    result = lib.run_kubectl(*cmd, namespace="", capture=True)
    print("\n".join(_tail((result.stdout or "").splitlines(), int(args.max))))


def cmd_describe(args) -> None:
    ns, pod = _resolve_pod(args.pod, args.namespace)
    result = lib.run_kubectl("describe", "pod", "-n", ns, pod,
                             namespace="", capture=True)
    print(result.stdout.rstrip())

def cmd_describe_node(args) -> None:
    result = lib.run_kubectl("describe", "node", args.node, capture=True)
    print(result.stdout.rstrip())


def cmd_node_of(args) -> None:
    ns, pod = _resolve_pod(args.pod, args.namespace)
    result = lib.run_kubectl("get", "pod", "-n", ns, pod,
                             "-o", "jsonpath=node={.spec.nodeName} hostIP={.status.hostIP}",
                             namespace="", capture=True)
    print((result.stdout or "").rstrip())


def cmd_hop_check(args) -> None:
    conn = lib.load_connection()
    node_ip = lib.resolve_node_ip(args.node)
    result = lib.execute_on_node(node_ip, "true", capture=True, check=False)
    if result is not None and result.returncode == 0:
        print(f"node={args.node} ip={node_ip} status=HOP_OK")
    else:
        print(f"node={args.node} ip={node_ip} status=HOP_FAIL")
        print(f"  请确认跳板机能免密跳到该节点（ssh {conn.user}@{node_ip}）")
        sys.exit(1)

def cmd_node_log(args) -> None:
    node_ip = lib.resolve_node_ip(args.node)
    if args.any_path:
        sys.stderr.write(
            f"warn: --any-path 已启用，跳过扫描目录集校验"
            f"（路径由问题线索驱动，请确认可信）: {args.file}\n")
        real = args.file
    else:
        # Resolve the real path on the node (catches symlink escapes), then
        # re-validate it against the scan-dir set before reading.
        result = lib.execute_on_node(node_ip, "readlink", "-f", args.file,
                                     capture=True, check=False)
        real = (result.stdout or "").strip() if result else ""
        if not real:
            lib.die(f"无法解析节点路径 {args.file}")
        lib.validate_log_path(real)
    print(f"=== node {args.node}({node_ip}):{real} "
          f"(tail={args.tail} grep={args.grep or 'none'}) ===")
    out = lib.execute_on_node(node_ip, "tail", "-n", args.tail, real, capture=True)
    lines = _render_grep((out.stdout or "").splitlines(), args)
    print("\n".join(_tail(lines, OUTPUT_LIMIT)))

def cmd_node_journal(args) -> None:
    node_ip = lib.resolve_node_ip(args.node)
    print(f"=== node {args.node}({node_ip}) journal {args.unit} "
          f"(since={args.since} grep={args.grep or 'none'}) ===")
    out = lib.execute_on_node(node_ip, "journalctl", "-u", args.unit, "--no-pager",
                              f"--since=-{args.since}", capture=True)
    lines = _render_grep((out.stdout or "").splitlines(), args)
    print("\n".join(_tail(lines, OUTPUT_LIMIT)))


def cmd_node_find(args) -> None:
    mtime_expr = []
    if args.newer:
        amount, unit = int(args.newer[:-1]), args.newer[-1]
        if unit == "m":
            mtime_expr = ["-mmin", f"-{amount}"]
        elif unit == "h":
            mtime_expr = ["-mmin", f"-{amount * 60}"]
        else:
            mtime_expr = ["-mtime", f"-{amount}"]
    roots = lib.scan_dirs()
    find_args = ["find", *roots, "-type", "f"]
    if args.name:
        find_args += ["-name", args.name]
    if mtime_expr:
        find_args += mtime_expr
    # Literal \t / \n escapes: find's -printf interprets them itself.
    find_args += ["-printf", "%p\\t%s\\t%TY-%Tm-%Td %TH:%TM\\n"]
    node_ip = lib.resolve_node_ip(args.node)
    result = lib.execute_on_node(node_ip, *find_args, capture=True)
    rows = []
    for line in (result.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            rows.append(parts)
    print(f"=== node {args.node}({node_ip}) find roots={' '.join(roots)} "
          f"name={args.name or '*'} newer={args.newer or 'any'} "
          f"(max={args.max}) ===")
    if not rows:
        print("(无匹配文件)")
    for path, size, mtime in rows[:int(args.max)]:
        print(f"{path}\t{size} bytes\t{mtime}")
    if len(rows) > int(args.max):
        print(f"(共 {len(rows)} 个文件，已按 --max 截断)")

def cmd_node_exec(args) -> None:
    node_ip = lib.resolve_node_ip(args.node)
    print(f"=== node {args.node}({node_ip}) exec(scenario={args.scenario}): "
          f"{' '.join(args.node_command)} ===")
    out = lib.execute_on_node(node_ip, *args.node_command, capture=True)
    print("\n".join(_tail((out.stdout or "").splitlines(), OUTPUT_LIMIT)))
    stderr = (out.stderr or "").strip()
    if stderr:
        sys.stderr.write(stderr + "\n")


def cmd_nsenter_net(args) -> None:
    node_ip = lib.resolve_node_ip(args.node)
    print(f"=== node {args.node}({node_ip}) nsenter -t {args.pid} -n "
          f"{' '.join(args.net_command)} ===")
    print("note: 已进入容器网络命名空间；/etc/resolv.conf 属 mount ns 不会切换，"
          "复现容器 DNS 请显式 dig @<clusterDNS> 并手动拼接搜索域")
    out = lib.execute_on_node(node_ip, "nsenter", "-t", args.pid, "-n",
                              *args.net_command, capture=True)
    print("\n".join(_tail((out.stdout or "").splitlines(), OUTPUT_LIMIT)))
    stderr = (out.stderr or "").strip()
    if stderr:
        sys.stderr.write(stderr + "\n")

def cmd_pause_pid(args) -> None:
    node_ip = lib.resolve_node_ip(args.node)
    pid, runtime = _pause_pid(node_ip, args.name, args.namespace)
    print(f"node={args.node} ip={node_ip} runtime={runtime} pid={pid}")
    print("  下一步: logs.py nsenter-net "
          f"{args.node} {pid} <network 白名单命令>")


# Subcommand -> handler. Kept in sync with build_parser(): adding a subcommand
# means a parser registration + a cmd_* function + one entry here.
_DISPATCH = {
    "pods": cmd_pods,
    "logs": cmd_logs,
    "events": cmd_events,
    "describe": cmd_describe,
    "describe-node": cmd_describe_node,
    "node-of": cmd_node_of,
    "hop-check": cmd_hop_check,
    "node-log": cmd_node_log,
    "node-journal": cmd_node_journal,
    "node-find": cmd_node_find,
    "node-exec": cmd_node_exec,
    "nsenter-net": cmd_nsenter_net,
    "pause-pid": cmd_pause_pid,
}


if __name__ == "__main__":
    main()
