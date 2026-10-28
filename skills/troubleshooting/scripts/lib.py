#!/usr/bin/env python3
"""Shared helpers for the troubleshooting skill (Python port of the original bash scripts).

Design notes
------------
* Configuration is read from ``config.yaml`` (next to this file) with PyYAML.
* All remote access goes through one bastion host (config.conn.public-ip /
  config.conn.private-ip).  This module never builds host names itself; every
  remote command is funnelled through run_on_bastion() / run_kubectl().
* The bastion's public SSH port lives in its own config field
  (``conn.public-port``, default 22) and only ever applies to the *public*
  path (``user@public-ip:port``).  The private LAN path stays on port 22 —
  bastion_target() is the single place that assembles the target string,
  so no other call site needs to know about the port.
* **Transport is Paramiko (pure-Python SSH), not the ``ssh`` binary.**  This removes the
  dependency on OpenSSH on the client and the Windows ``ControlMaster``/``ControlPath``
  problem (``getsockname failed: Not a socket``) entirely — Paramiko does not read
  ``~/.ssh/config``.  A single SSHClient is cached per target and reused for every
  exec within a process, which restores the connection-reuse benefit without any
  ControlMaster socket.
* Host-key policy mirrors ``StrictHostKeyChecking=accept-new``: an unknown host is
  accepted (added), but a *changed* key for an already-known host is rejected
  (``BadHostKeyException``) rather than silently trusted.
* The bastion -> node hop still runs ``ssh`` *on the bastion* (see execute_on_node),
  so the shared bastion key model is preserved and no ``ssh`` binary is needed locally.
  A direct-tcpip tunnel through the bastion transport (as used by testkit's SSHExecutor)
  was evaluated and rejected: it requires node credentials on the local machine, which
  contradicts the personal-key(local->bastion) / shared-key(bastion->nodes) distribution.
* Resilience borrowed from testkit: transports carry a keepalive (DEFAULT_KEEPALIVE) so
  idle NAT gateways do not drop connections mid-session; a transport-level exec failure
  reconnects once and retries (command timeouts are NOT retried — the command may have
  started remotely); _Result records wall-clock duration for evidence quality.
* The module is intentionally read-only: it never mutates the cluster or nodes,
  only a small per-user cache under ~/.cache.
* Time-related tunables (connect/probe/cache/request) live in config.conn and
  fall back to safe defaults, so callers do not hard-code magic numbers.
* Node-side reads/searches are bounded by a scan directory set
  (config.conn.scan_dirs, defaulting to the common on-host log locations).
  Entry scripts that must read outside the set use an explicit escape hatch
  (logs.py ``--any-path``) rather than silently widening the boundary here.
"""
from __future__ import annotations

import atexit
import os
import re
import socket
import sys
import threading
import time
import shlex
import subprocess
import logging

try:
    import yaml
except ImportError:
    sys.stderr.write(
        "status=ERROR msg=需要 PyYAML：请运行 `pip install pyyaml`（或 pip3 install pyyaml）\n"
    )
    sys.exit(1)

try:
    import paramiko
except ImportError:
    sys.stderr.write(
        "status=ERROR msg=需要 paramiko：请运行 `pip install paramiko`\n"
    )
    sys.exit(1)

# Paramiko logs internal errors (e.g. banner-read failures) with full tracebacks at
# ERROR level; that noise would drown our own status lines, so silence it here.
logging.getLogger("paramiko").setLevel(logging.CRITICAL)

# Windows/GBK 控制台：强制 UTF-8 输出，非 GBK 字符替换而不是抛 UnicodeEncodeError
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT_DIR, "config.yaml")

# Safe defaults for the timeouts that config.conn may override.
DEFAULT_CONNECT_TIMEOUT = 10   # ssh connect / banner / auth timeout (seconds)
DEFAULT_PROBE_TIMEOUT = 5      # private-IP reachability probe (seconds)
DEFAULT_CACHE_TTL = 300        # how long a resolved bastion host is cached (seconds)
DEFAULT_REQUEST_TIMEOUT = 10    # kubectl --request-timeout (seconds)
DEFAULT_KEEPALIVE = 30          # transport keepalive interval (seconds)
DEFAULT_SSH_PORT = 22           # default SSH port when conn.public-port is unset

# Directories node log reads/searches may target when conn.scan_dirs is not set.
# Covers system logs plus the per-pod / per-container symlink farms Kubernetes
# maintains on the host, plus the most common business-log and kubelet data
# locations — chosen so discovery is not blind to where logs actually land.
DEFAULT_SCAN_DIRS = [
    "/var/log",
    "/var/log/pods",
    "/var/log/containers",
    "/data/logs",
]
# /var/lib/kubelet 刻意不在缺省集：其 pki/ 下是节点私钥，与「只读排查日志」
# 的边界不符；确需读取时由运维在 conn.scan_dirs 显式加入并自担风险。

_config_cache = None
_connection = None  # populated by load_connection(); used by the remote helpers
_clients: dict[str, "paramiko.SSHClient"] = {}  # per-target connection reuse


def die(reason: str):
    """Print a machine-readable ERROR status and exit."""
    sys.stderr.write(f"status=ERROR msg={reason}\n")
    sys.exit(1)


def load_config() -> dict:
    """Parse config.yaml once and cache the result."""
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    if not os.path.isfile(CONFIG_PATH):
        die(f"找不到配置文件 {CONFIG_PATH}")
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            _config_cache = yaml.safe_load(fh) or {}
    except yaml.YAMLError as exc:
        die(f"config.yaml 解析失败: {exc}")
    return _config_cache


def expand_path(path: str) -> str:
    """Expand ~ to $HOME and normalise Windows-style backslashes to slashes."""
    return os.path.expanduser(path).replace("\\", "/")


def scan_dirs(config: dict | None = None) -> list[str]:
    """Directories node log reads/searches may target (conn.scan_dirs, else DEFAULT_SCAN_DIRS)."""
    config = config or load_config()
    dirs = (config.get("conn", {}) or {}).get("scan_dirs") or []
    if isinstance(dirs, list) and dirs:
        return [str(d) for d in dirs]
    return list(DEFAULT_SCAN_DIRS)


def validate_log_path(path: str, config: dict | None = None) -> None:
    """Refuse anything outside scan_dirs(); die() on violation.

    Guards against path traversal (``..``) and the /var/logxxx prefix bypass.
    Callers that must read outside the set (problem-clue-driven) should use an
    explicit escape hatch at the entry-script level, not widen this set.
    """
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", path) or ".." in path:
        die(f"路径不合法: {path}")
    allowed = scan_dirs(config)
    ok = any(path == d.rstrip("/") or path.startswith(d.rstrip("/") + "/") for d in allowed)
    if not ok:
        die(f"{path} 不在扫描目录集内（仅允许 {' '.join(allowed)}）")


class Connection:
    """Resolved bastion connection plus the tunables read from config.conn."""

    def __init__(self, bastion_host, user, context, namespace,
                 public_ip, private_ip, connect_timeout, probe_timeout,
                 cache_ttl, request_timeout, public_port=None):
        self.bastion_host = bastion_host
        self.user = user
        self.context = context
        self.namespace = namespace
        self.public_ip = public_ip
        self.private_ip = private_ip
        self.public_port = public_port  # ssh port of the PUBLIC path only
        self.connect_timeout = connect_timeout
        self.probe_timeout = probe_timeout
        self.cache_ttl = cache_ttl
        self.request_timeout = request_timeout


def load_connection() -> Connection:
    """Resolve the bastion host and return a Connection.

    Private IP is preferred (faster LAN); on failure we fall back to the public IP.
    The resolved host is cached for ``cache_ttl`` seconds under ~/.cache, but the
    cached value is only trusted when it equals one of the two IPs from config —
    so a tampered cache on a shared machine cannot redirect traffic to a fake
    bastion.  ``conn.public-port`` (optional, default 22) is resolved once here
    and later applied only to the public path by bastion_target().
    """
    global _connection
    config = load_config()
    conn_cfg = config.get("conn", {}) or {}
    public_ip = (conn_cfg.get("public-ip") or "").strip()
    private_ip = (conn_cfg.get("private-ip") or "").strip()
    if not public_ip:
        die("config.conn.public-ip 未配置")

    public_port = None
    raw_port = str(conn_cfg.get("public-port") or "").strip()
    if raw_port:
        if not raw_port.isdigit() or not (1 <= int(raw_port) <= 65535):
            die(f"config.conn.public-port 不合法: {raw_port}（应为 1-65535 的数字）")
        public_port = int(raw_port)

    user = (conn_cfg.get("user") or "root").strip()
    context = (conn_cfg.get("context") or "").strip()
    namespace = (conn_cfg.get("namespace") or "").strip()
    try:
        connect_timeout = int(conn_cfg.get("connect_timeout") or DEFAULT_CONNECT_TIMEOUT)
        probe_timeout = int(conn_cfg.get("probe_timeout") or DEFAULT_PROBE_TIMEOUT)
        cache_ttl = int(conn_cfg.get("cache_ttl") or DEFAULT_CACHE_TTL)
        request_timeout = int(conn_cfg.get("request_timeout") or DEFAULT_REQUEST_TIMEOUT)
    except (TypeError, ValueError) as exc:
        die(f"config.conn 的时间配置必须是数字: {exc}")

    bastion_host = None
    if private_ip:
        cache_dir = os.path.join(os.path.expanduser("~"), ".cache", "troubleshooting-bastion")
        os.makedirs(cache_dir, exist_ok=True)
        os.chmod(cache_dir, 0o700)
        cache_file = os.path.join(cache_dir, re.sub(r"[^A-Za-z0-9]", "_", private_ip) + ".cache")
        now = int(time.time())
        cached_ts = cached_ip = None
        if os.path.isfile(cache_file) and os.access(cache_file, os.R_OK):
            try:
                with open(cache_file) as fh:
                    parts = fh.read().split()
                if len(parts) >= 2:
                    cached_ts, cached_ip = parts[0], parts[1]
            except OSError as exc:
                sys.stderr.write(f"warn: 读取缓存失败，忽略并重新探测: {exc}\n")
                cached_ts = cached_ip = None
        if (cached_ts and cached_ip and re.fullmatch(r"[0-9]+", cached_ts)
                and (now - int(cached_ts)) < cache_ttl
                and cached_ip in (private_ip, public_ip)):
            bastion_host = cached_ip
        elif cached_ip and cached_ip not in (private_ip, public_ip):
            sys.stderr.write(
                f"warn: 缓存 IP({cached_ip}) 不在配置白名单，已忽略并重新探测\n")
        if bastion_host is None:
            if _ssh_succeeds(f"{user}@{private_ip}", timeout=probe_timeout):
                bastion_host = private_ip
            else:
                bastion_host = public_ip
            try:
                with open(cache_file, "w") as fh:
                    fh.write(f"{now} {bastion_host}\n")
            except OSError as exc:
                sys.stderr.write(f"warn: 写入缓存失败（不影响功能）: {exc}\n")
    else:
        bastion_host = public_ip

    _connection = Connection(bastion_host, user, context, namespace,
                             public_ip, private_ip, connect_timeout,
                             probe_timeout, cache_ttl, request_timeout,
                             public_port=public_port)
    return _connection


# ----------------------------- Paramiko transport -----------------------------
class _Result:
    """CompletedProcess-like shim so callers keep using .returncode/.stdout/.stderr."""

    def __init__(self, returncode: int, stdout: str = "", stderr: str = "",
                 duration: float = 0.0):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.duration = duration  # seconds; 0.0 for paths that never executed


class _AuthFailure(Exception):
    """Raised when passwordless authentication to a target fails."""


class _ConnFailure(Exception):
    """Raised when a target cannot be reached or its host key changed."""


def _load_kh_tolerant(hostkeys: "paramiko.hostkeys.HostKeys", path: str) -> None:
    """逐行容错加载 known_hosts。

    paramiko 原生 HostKeys.load 在首个不可解析行上抛异常中止，其后条目全部
    丢失——坏行之后的 host 会被当成「首次连接」自动信任，主机密钥校验被静默
    削弱（实测复现）。本函数逐行解析：坏行与 @cert-authority/@revoked 标记行
    （合法 OpenSSH 语法但 paramiko 无法表示）跳过并逐行告警，其余条目照常生效。
    """
    from paramiko.hostkeys import HostKeyEntry

    with open(path, encoding="utf-8", errors="replace") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("@"):
                sys.stderr.write(f"warn: {path}:{lineno} 为 @cert-authority/@revoked"
                                 " 标记行，paramiko 不支持 CA 行，已跳过"
                                 "（该行声明的 CA 信任不生效）\n")
                continue
            try:
                entry = HostKeyEntry.from_line(line, lineno)
            except Exception as exc:
                sys.stderr.write(f"warn: {path}:{lineno} 解析失败已跳过: {exc}\n")
                continue
            if entry is not None:
                # 与原生 load 同一落法；_entries 为 paramiko 私有属性，升级 paramiko 后需回归本场景
                hostkeys._entries.append(entry)


def make_ssh_client() -> "paramiko.SSHClient":
    """Return a configured-but-unconnected SSHClient.

    Trust (read) comes from the user's default known_hosts, loaded LINE BY
    LINE: paramiko's native load aborts at the first unparseable line and
    silently loses every entry AFTER it, so a later host would be re-trusted
    on first connect (key-mismatch detection silently disabled — verified
    empirically). Unparseable lines and @cert-authority/@revoked markers
    (valid OpenSSH syntax paramiko cannot represent) are skipped with a
    per-line warning; all other entries load normally. AutoAdd saves go to a
    skill-local known_hosts file, so the user's real file is NEVER rewritten
    (paramiko's HostKeys.save would drop comments/blank lines and cannot
    re-emit @cert-authority lines). Parse failures degrade to a warning,
    never a crash.
    """
    client = paramiko.SSHClient()
    try:
        # 逐行容错加载（原生 load 首坏行即中止，其后条目全丢）；用户文件只读信任
        _load_kh_tolerant(client._system_host_keys,
                          os.path.expanduser("~/.ssh/known_hosts"))
    except FileNotFoundError:
        pass
    except Exception as exc:  # InvalidHostKey(Exception) 不属 SSHException；降级为告警不崩溃
        sys.stderr.write(f"warn: 读取系统 known_hosts 失败: {exc}\n")
    kh_dir = os.path.join(os.path.expanduser("~/.cache"), "troubleshooting")
    skill_hosts = os.path.join(kh_dir, "known_hosts")
    try:
        os.makedirs(kh_dir, exist_ok=True)
        os.chmod(kh_dir, 0o700)
        if not os.path.isfile(skill_hosts):
            fd = os.open(skill_hosts, os.O_WRONLY | os.O_CREAT, 0o600)
            os.close(fd)
        # 新密钥只落本文件：不绑定落盘文件名，AutoAdd 会重写用户真实 known_hosts
        client._host_keys_filename = skill_hosts
        _load_kh_tolerant(client.get_host_keys(), skill_hosts)
    except Exception as exc:
        sys.stderr.write(f"warn: 读取/初始化 {skill_hosts} 失败: {exc}\n")
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    return client


def connect_client(client: "paramiko.SSHClient", target: str, *,
                   password=None, timeout: int | None = None) -> None:
    """Connect *client* to *target* (``user@host`` or ``user@host:port``).

    The port may ride inside *target* (``user@host:port``, IPv4 only) — the
    public bastion path carries ``conn.public-port`` that way; every other
    target connects on ``DEFAULT_SSH_PORT`` (22).

    With ``password=None`` only key/agent auth is attempted (the read-only path).
    Exceptions from Paramiko propagate so callers can map them to statuses.
    """
    timeout = timeout or DEFAULT_CONNECT_TIMEOUT
    user, _, hostport = target.partition("@")
    host, port = hostport, None
    if hostport.count(":") == 1:
        h, _, p = hostport.rpartition(":")
        if p.isdigit():
            host, port = h, int(p)
    kwargs = {
        "hostname": host,
        "port": port or DEFAULT_SSH_PORT,
        "username": user or "root",
        "timeout": timeout,
        "banner_timeout": timeout + 10,
        "auth_timeout": timeout + 10,
    }
    identity = os.path.expanduser("~/.ssh/id_ed25519")
    if os.path.isfile(identity):
        kwargs["key_filename"] = identity
    if password is not None:
        kwargs["password"] = password
        kwargs["look_for_keys"] = False
        kwargs["allow_agent"] = False
    else:
        kwargs["allow_agent"] = True
        kwargs["look_for_keys"] = True
    client.connect(**kwargs)


def _close_client(target: str) -> None:
    """Drop and close a cached client for *target* (errors are logged, never fatal)."""
    client = _clients.pop(target, None)
    if client is not None:
        try:
            client.close()
        except (paramiko.SSHException, OSError) as exc:
            sys.stderr.write(f"warn: 关闭到 {target} 的连接失败: {exc}\n")


def close_all_clients() -> None:
    """Close every cached client (call at the end of a long-running process)."""
    for target in list(_clients):
        _close_client(target)


atexit.register(close_all_clients)


def get_client(target: str, *, timeout: int | None = None) -> "paramiko.SSHClient":
    """Return a connected SSHClient for *target*, reusing a live cached one.

    Raises _AuthFailure (passwordless denied) or _ConnFailure (unreachable / host-key
    changed) so run_ssh can translate them into actionable statuses.
    """
    timeout = timeout or DEFAULT_CONNECT_TIMEOUT
    cached = _clients.get(target)
    if cached is not None:
        transport = cached.get_transport()
        if transport is not None and transport.is_active():
            return cached
        _close_client(target)

    client = make_ssh_client()
    try:
        connect_client(client, target, timeout=timeout)
    except paramiko.AuthenticationException:
        _safe_close(client)
        raise _AuthFailure("passwordless auth rejected")
    except paramiko.BadHostKeyException as exc:
        _safe_close(client)
        raise _ConnFailure(f"主机密钥不匹配(疑被篡改): {exc}")
    except (paramiko.SSHException, socket.timeout, OSError) as exc:
        _safe_close(client)
        raise _ConnFailure(str(exc))
    # Keep the transport alive across long diagnosis sessions: idle NAT
    # gateways would otherwise drop the connection between commands.
    transport = client.get_transport()
    if transport is not None:
        transport.set_keepalive(DEFAULT_KEEPALIVE)
    _clients[target] = client
    return client


def _safe_close(client: "paramiko.SSHClient") -> None:
    """Close a client, logging (not swallowing) any error."""
    try:
        client.close()
    except (paramiko.SSHException, OSError) as exc:
        sys.stderr.write(f"warn: 关闭连接失败: {exc}\n")


def _exec_once(client: "paramiko.SSHClient", target: str, remote_command: str,
               timeout: int, capture: bool, stdin_data: str | None = None) -> _Result:
    """Run one exec_command round-trip, recording the wall-clock duration."""
    started = time.monotonic()
    _stdin, stdout, stderr = client.exec_command(remote_command, timeout=timeout + 5)
    if stdin_data:
        _stdin.write(stdin_data)
        _stdin.flush()
        _stdin.channel.shutdown_write()  # signal EOF so remote readers unblock
    # stdout/stderr 必须并发排空：先读完一路再读另一路时，任一路缓冲打满会
    # 阻塞远端进程 -> 两路都等不到 EOF（stderr 很大时 classic 死锁）
    out_box: dict = {}
    t_out = threading.Thread(
        target=lambda: out_box.setdefault(
            "v", stdout.read().decode("utf-8", "replace")),
        daemon=True)
    t_out.start()
    err = stderr.read().decode("utf-8", "replace")
    t_out.join()
    out = out_box.get("v", "")
    returncode = stdout.channel.recv_exit_status()
    result = _Result(returncode, out if capture else "", err if capture else "")
    result.duration = time.monotonic() - started
    return result


def run_ssh(target: str, remote_command: str, *, timeout: int = DEFAULT_CONNECT_TIMEOUT,
            capture: bool = True, check: bool = True,
            stdin_data: str | None = None) -> _Result:
    """Run *remote_command* on *target* (user@host) over Paramiko.

    Returns a CompletedProcess-like _Result. On auth failure it maps to
    NEEDS_MANUAL_AUTH guidance (die when check=True, else returncode=255).
    A transport-level failure (SSHException/OSError — connection dropped
    between calls, NAT idle timeout) retries once after reconnecting;
    a command *timeout* is never retried, because the command may already
    have started running on the remote side. ``stdin_data`` (if given) is
    written to the remote process's stdin and EOF-signalled — used for
    ``kubectl apply -f -`` style manifest piping.
    """
    try:
        client = get_client(target, timeout=timeout)
    except _AuthFailure:
        sys.stderr.write(f"warn: 无法免密登录 {target}\n")
        if check:
            die(f"无法免密登录 {target}（NEEDS_MANUAL_AUTH）；"
                "请先在终端完成免密配置（setup_ssh.py local）")
        return _Result(255)
    except _ConnFailure as exc:
        sys.stderr.write(f"warn: 连接失败 {target}: {exc}\n")
        if check:
            die(f"连接失败 {target}: {exc}")
        return _Result(255)

    attempts = 0
    while True:
        attempts += 1
        try:
            return _exec_once(client, target, remote_command, timeout, capture,
                              stdin_data=stdin_data)
        except (socket.timeout, TimeoutError):
            _close_client(target)
            sys.stderr.write(f"warn: 远端命令超时 {target}\n")
            if check:
                die(f"远端命令超时 {target}")
            return _Result(255)
        except (paramiko.SSHException, OSError) as exc:
            _close_client(target)
            if attempts >= 2:
                sys.stderr.write(f"warn: 远端命令失败 {target}: {exc}\n")
                if check:
                    die(f"远端命令失败 {target}")
                return _Result(255)
            sys.stderr.write(f"warn: 到 {target} 的连接中断，重连一次后重试: {exc}\n")
            try:
                client = get_client(target, timeout=timeout)
            except (_AuthFailure, _ConnFailure) as exc2:
                sys.stderr.write(f"warn: 重连失败 {target}: {exc2}\n")
                if check:
                    die(f"远端命令失败 {target}: 重连失败")
                return _Result(255)


def _ssh_succeeds(target: str, *, timeout: int) -> bool:
    result = run_ssh(target, "true", timeout=timeout, capture=True, check=False)
    return result is not None and result.returncode == 0


def bastion_target(conn: "Connection", host: str | None = None) -> str:
    """Assemble ``user@host[:port]`` for reaching the bastion.

    *host* defaults to the resolved bastion host (private IP first, public IP
    on fallback).  ``conn.public-port`` is appended only when the target is the
    bastion's *public* address — the private LAN path never uses it, so the
    internal network keeps connecting on port 22.
    """
    host = host or conn.bastion_host
    target = f"{conn.user}@{host}"
    if conn.public_port and host == conn.public_ip:
        target += f":{conn.public_port}"
    return target


def run_on_bastion(*command, timeout: int | None = None, capture: bool = True, check: bool = True,
                   stdin_data: str | None = None):
    """Run *command* on the bastion host (config.conn). Arguments are shell-quoted."""
    if _connection is None:
        die("load_connection() 未调用")
    timeout = timeout or _connection.connect_timeout
    remote = shlex.join(command)
    return run_ssh(bastion_target(_connection), remote,
                   timeout=timeout, capture=capture, check=check,
                   stdin_data=stdin_data)


def run_kubectl(*args, namespace: str | None = None, timeout: int | None = None,
                capture: bool = True, check: bool = True, stdin_data: str | None = None):
    """Run kubectl on the bastion, injecting --context when configured.

    ``namespace`` semantics:
      * ``None``  -> use ``conn.namespace`` when set (legacy default behaviour);
      * ``""``   -> never pass ``-n`` (used by ``--all-namespaces`` discovery);
      * other    -> pass ``-n <value>`` for this call (explicit narrowing).
    """
    cmd = ["kubectl"]
    if _connection.context:
        cmd += ["--context", _connection.context]
    ns = _connection.namespace if namespace is None else namespace
    if ns:
        cmd += ["-n", ns]
    cmd += list(args)
    return run_on_bastion(*cmd, timeout=timeout, capture=capture, check=check,
                          stdin_data=stdin_data)


_IP_TOKEN_RE = re.compile(r"[0-9a-fA-F.:]+")


def resolve_node_ip(name: str) -> str:
    """Return ONE InternalIP of a node (never built from user input).

    jsonpath 对双栈节点会返回空格分隔的多个地址；不拆分直接拼进 hop 命令
    会变成 ``ssh root@10.0.0.5 fd00::5 '...'``（远端执行 fd00::5 df -h）。
    优先取 IPv4，否则取第一个合法 token。
    """
    if not re.fullmatch(r"[a-z0-9.-]+", name):
        die(f"节点名不合法: {name}")
    result = run_kubectl("get", "node", name,
                         "-o", r'jsonpath={.status.addresses[?(@.type=="InternalIP")].address}',
                         capture=True, check=False)
    ip = (result.stdout or "").strip() if result else ""
    if not ip:
        die(f"集群中没有节点 {name}")
    tokens = [t for t in ip.split() if _IP_TOKEN_RE.fullmatch(t)]
    if not tokens:
        die(f"节点 {name} 的 InternalIP 不合法: {ip}")
    v4 = [t for t in tokens if ":" not in t]
    return (v4 or tokens)[0]


def execute_on_node(node_ip: str, *command, timeout: int | None = None,
                    capture: bool = True, check: bool = True):
    """Run *command* on a cluster node by hopping through the bastion.

    The hop still runs ``ssh`` *on the bastion*, so it uses the shared bastion key and
    needs no ``ssh`` binary on the client. Relies on the bastion already having
    passwordless SSH to the node.
    """
    if _connection is None:
        die("load_connection() 未调用")
    if not re.fullmatch(r"[A-Za-z0-9._:-]+", node_ip) or node_ip.startswith("-"):
        die(f"节点 IP 不合法: {node_ip}")
    timeout = timeout or _connection.connect_timeout
    inner = shlex.join(command)
    # user@host 整体 quote：未校验/未 quote 的 "IP" 会把 hop 命令拆散，
    # 构造的「IP」（如 1.2.3.4; id #）甚至能在跳板机上注入第二段命令

    # 防回退：ConnectTimeout 勿改用 write_timeout——节点不可达时要干等 180s 才报错
    hop = ("ssh -o BatchMode=yes -o ConnectTimeout=%d "
           "-o StrictHostKeyChecking=accept-new %s %s"
           % (_connection.connect_timeout, shlex.quote(f"{_connection.user}@{node_ip}"),
              shlex.quote(inner)))
    return run_on_bastion("bash", "-c", hop, timeout=timeout, capture=capture, check=check)


def selector_override(component: str) -> str | None:
    """Configured pod-label selector override for a component, else None.

    Platform-agnostic on purpose: the skill carries NO built-in knowledge of
    any distribution's component names or label conventions.  Callers fall
    back to generic label-key candidates and a name-substring match (see
    logs.py); per-component quirks, if any, belong in config (codehub
    [component].selector), not in code.
    """
    entry = (load_config().get("codehub", {}) or {}).get(component)
    if isinstance(entry, dict) and entry.get("selector"):
        return entry["selector"]
    return None


# ----------------------------- git helpers (local) -----------------------------
def git(*args, repo: str, capture: bool = True, check: bool = True,
        timeout: int | None = None, no_prompt: bool = False):
    """Run a local git command against *repo* and return the CompletedProcess.

    no_prompt=True 禁用凭据交互（GIT_TERMINAL_PROMPT=0 / GIT_ASKPASS=echo）：
    被 Agent 调用时卡在凭据提示会挂死整个会话，宁可失败。timeout 到点按
    rc=124 处理（fetch 类调用以 warn 兜底，不 fail 整个流程）。
    """
    env = None
    if no_prompt:
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "echo"}
    try:
        result = subprocess.run(["git", "-C", repo, *args], capture_output=capture,
                                text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        sys.stderr.write(f"warn: git {' '.join(args[:2])} 超时（>{timeout}s）\n")
        result = subprocess.CompletedProcess(args, returncode=124, stdout="", stderr="")
    if check and result.returncode != 0:
        err = (result.stderr or "").strip()
        die(f"git 命令失败: {' '.join(args)} -> {err}")
    return result


def git_fetch(repo: str) -> None:
    """Fetch tags; warn (do not fail) if the remote is unreachable."""
    result = git("fetch", "--tags", "--quiet", repo=repo, check=False,
                 timeout=120, no_prompt=True)
    if result.returncode != 0:
        sys.stderr.write("warn: fetch 失败，使用本地已有 tag\n")


def git_rev_parse(repo: str, ref: str) -> bool:
    """True when *ref* resolves to an object in *repo*."""
    return git("rev-parse", "-q", "--verify", ref, repo=repo, check=False).returncode == 0


def git_rev_list(repo: str, ref: str) -> str:
    """Return the commit hash a ref points to."""
    return git("rev-list", "-n1", ref, repo=repo, capture=True, check=False).stdout.strip()


def git_tags_nearby(repo: str, substring: str) -> str:
    """Up to 5 most-recent tags containing *substring* (space-joined)."""
    result = git("tag", "--sort=-creatordate", repo=repo, capture=True, check=False)
    matches = [line for line in (result.stdout or "").splitlines() if substring in line][:5]
    return " ".join(matches)


def git_add_worktree(repo: str, path: str, commit: str) -> None:
    """Create a detached read-only worktree at *path* for *commit*."""
    git("worktree", "add", "--detach", path, commit, repo=repo)
