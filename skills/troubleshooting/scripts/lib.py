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
  so the shared bastion key model is preserved and no ``ssh`` binary is needed locally;
  node credentials never live on the local machine. For non-cluster machines, the
  separate host channel (execute_on_host) does open a direct-tcpip tunnel using creds
  from local config.
* Transports carry a keepalive (DEFAULT_KEEPALIVE) so idle NAT gateways do not drop
  connections mid-session; a transport-level exec failure reconnects once and retries
  (command timeouts are NOT retried — the command may have started remotely);
  _Result records wall-clock duration for evidence quality.
* The module is intentionally read-only: it never mutates the cluster or nodes,
  only a small per-user cache under ~/.cache.
* Time-related tunables (connect/request) live in config.conn and fall back
  to safe defaults, so callers do not hard-code magic numbers.
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
                 public_ip, private_ip, connect_timeout, request_timeout,
                 public_port=None,
                 node_ips=None, host_credentials=None, sshpassword=None):
        self.bastion_host = bastion_host
        self.user = user
        self.context = context
        self.namespace = namespace
        self.public_ip = public_ip
        self.private_ip = private_ip
        self.public_port = public_port  # ssh port of the PUBLIC path only
        self.connect_timeout = connect_timeout
        self.request_timeout = request_timeout
        # node name -> IP static map: lets node channels work without kubectl.
        self.node_ips = node_ips or {}
        # host -> {user, password, port}: per-host credentials for direct-tcpip tunnels.
        self.host_credentials = host_credentials or {}
        # Shared password list for non-cluster hosts (tried in order).
        self.sshpassword = list(sshpassword or [])


def load_connection() -> Connection:
    """Resolve the bastion host and return a Connection.

    Public IP wins whenever it is configured: setting a public address means the
    private LAN is NOT directly reachable from this machine, so the public path
    is the intended one.  Only when no public IP is set do we use the private IP
    (pure intranet setups may either omit public-ip or set it to the same value).
    At least one of the two must be configured.  ``conn.public-port`` (optional,
    default 22) applies only to the public path via bastion_target().

    ``conn.node_ips`` (name -> IP) and ``conn.host_credentials`` (host -> creds)
    are parsed here once and carried on the Connection for the kubectl-free node
    path and the direct-tcpip host tunnel respectively.
    """
    global _connection
    config = load_config()
    conn_cfg = config.get("conn", {}) or {}
    public_ip = (conn_cfg.get("public-ip") or "").strip()
    private_ip = (conn_cfg.get("private-ip") or "").strip()
    if not public_ip and not private_ip:
        die("config.conn.public-ip / private-ip 至少配置一个")

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
        request_timeout = int(conn_cfg.get("request_timeout") or DEFAULT_REQUEST_TIMEOUT)
    except (TypeError, ValueError) as exc:
        die(f"config.conn 的时间配置必须是数字: {exc}")

    # Public-first: configured public IP is the intended path (see docstring).
    bastion_host = public_ip or private_ip

    node_ips = {}
    raw_node_ips = conn_cfg.get("node_ips") or {}
    if isinstance(raw_node_ips, dict):
        for name, ip in raw_node_ips.items():
            name, ip = str(name).strip(), str(ip).strip()
            if re.fullmatch(r"[A-Za-z0-9._-]+", name) and re.fullmatch(r"[0-9A-Fa-f.:]+", ip):
                node_ips[name] = ip
            else:
                sys.stderr.write(f"warn: conn.node_ips 忽略非法条目 {name} -> {ip}\n")

    # Shared passwords for the host channel: a list tried in order.  A single
    # string is accepted as a one-element list for config convenience.  Values
    # are kept opaque (never logged); non-scalar entries are ignored with a warning.
    sshpassword = []
    raw_pw = conn_cfg.get("sshpassword") or []
    if isinstance(raw_pw, str):
        raw_pw = [raw_pw]
    if isinstance(raw_pw, list):
        for pw in raw_pw:
            if isinstance(pw, (str, int, float)) and str(pw) != "":
                sshpassword.append(str(pw))
            else:
                sys.stderr.write("warn: conn.sshpassword 忽略非法条目\n")
    else:
        die("config.conn.sshpassword 必须是密码列表")

    host_credentials = {}
    raw_creds = conn_cfg.get("host_credentials") or {}
    if isinstance(raw_creds, dict):
        for host, cred in raw_creds.items():
            host = str(host).strip()
            if not re.fullmatch(r"[A-Za-z0-9._-]+", host):
                sys.stderr.write(f"warn: conn.host_credentials 忽略非法 host {host}\n")
                continue
            if not isinstance(cred, dict):
                die(f"config.conn.host_credentials.{host} 必须是 map（user/password/port）")
            raw_hport = str(cred.get("port") or DEFAULT_SSH_PORT).strip()
            if not raw_hport.isdigit() or not (1 <= int(raw_hport) <= 65535):
                die(f"config.conn.host_credentials.{host}.port 不合法: {raw_hport}")
            host_credentials[host] = {
                "user": str(cred.get("user") or user).strip(),
                "password": cred.get("password"),
                "port": int(raw_hport),
            }

    _connection = Connection(bastion_host, user, context, namespace,
                             public_ip, private_ip, connect_timeout,
                             request_timeout, public_port=public_port,
                             node_ips=node_ips,
                             host_credentials=host_credentials,
                             sshpassword=sshpassword)
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
    # stdout/stderr 必须并发排空：串行读取会在缓冲打满时与远端互等成死锁。
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


def bastion_target(conn: "Connection", host: str | None = None) -> str:
    """Assemble ``user@host[:port]`` for reaching the bastion.

    *host* defaults to the resolved bastion host (public IP wins when set).
    ``conn.public-port`` is appended only when the target equals the configured
    public address — the private LAN path never uses it, so the internal network
    keeps connecting on port 22.
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
    """Return the IP of a node, WITHOUT requiring kubectl when avoidable.

    Resolution order (kubectl-free paths first so a missing/unavailable kubectl
    never blocks node channels):
      1. *name* is already an IP literal  -> return it as-is;
      2. ``conn.node_ips`` static map      -> return the configured IP;
      3. kubectl ``get node ...InternalIP`` -> dynamic lookup (last resort).

    jsonpath 对双栈节点会返回空格分隔的多个地址；不拆分直接拼进 hop 命令
    会变成 ``ssh root@10.0.0.5 fd00::5 '...'``（远端执行 fd00::5 df -h）。
    优先取 IPv4，否则取第一个合法 token。
    """
    if not re.fullmatch(r"[A-Za-z0-9._:-]+", name):
        die(f"节点标识不合法: {name}")
    # 1. Already an IP: no kubectl needed.
    if re.fullmatch(r"[0-9A-Fa-f.:]+", name):
        return name
    # 2. Static map from config (explicitly for kubectl-less environments).
    mapping = (_connection.node_ips if _connection is not None else None) or {}
    if name in mapping:
        return mapping[name]
    # 3. Dynamic lookup via kubectl.
    result = run_kubectl("get", "node", name,
                         "-o", r'jsonpath={.status.addresses[?(@.type=="InternalIP")].address}',
                         capture=True, check=False)
    ip = (result.stdout or "").strip() if result else ""
    if not ip:
        die(f"集群中没有节点 {name}（kubectl 不可用或未配 node_ips 映射时，"
            f"请直接传节点 IP 或在 config.conn.node_ips 登记）")
    tokens = [t for t in ip.split() if _IP_TOKEN_RE.fullmatch(t)]
    if not tokens:
        die(f"节点 {name} 的 InternalIP 不合法: {ip}")
    v4 = [t for t in tokens if ":" not in t]
    return (v4 or tokens)[0]


def _tunnel_client(key: str, host: str, cred: dict, timeout: int) -> "paramiko.SSHClient":
    """Return a client connected to *host* through a direct-tcpip tunnel on the bastion.

    The bastion only relays a TCP socket; the SSH session (including password
    auth and host-key verification) is established end-to-end from this machine.
    That is what lets us reach arbitrary non-cluster machines with a password,
    without needing an ``ssh`` binary or sshpass on the bastion.

    ``cred['passwords']`` is a list tried in order; each attempt opens a fresh
    direct-tcpip channel because a rejected auth socket cannot be reused.  On
    total failure only the count is reported — passwords are never logged.
    """
    cached = _clients.get(key)
    if cached is not None:
        transport = cached.get_transport()
        if transport is not None and transport.is_active():
            return cached
        _close_client(key)

    jump = get_client(bastion_target(_connection), timeout=timeout)
    transport = jump.get_transport()
    if transport is None or not transport.is_active():
        raise _ConnFailure("跳板机 transport 不可用，无法开隧道")
    port = cred["port"]
    passwords = cred.get("passwords") or []
    # No password configured: fall back to key/agent auth (single attempt).
    attempts = list(passwords) if passwords else [None]
    last_auth_fail = False
    for password in attempts:
        try:
            sock = transport.open_channel("direct-tcpip", (host, port), ("127.0.0.1", 0))
        except Exception as exc:  # paramiko raises several channel errors here
            raise _ConnFailure(f"开 direct-tcpip 隧道失败 {host}:{port}: {exc}")

        client = make_ssh_client()
        kwargs = {
            "hostname": host, "port": port, "username": cred["user"],
            "sock": sock, "timeout": timeout,
            "banner_timeout": timeout + 10, "auth_timeout": timeout + 10,
        }
        if password is not None and password != "":
            kwargs.update(password=password, allow_agent=False, look_for_keys=False)
        else:
            kwargs.update(allow_agent=True, look_for_keys=True)
        try:
            client.connect(**kwargs)
        except paramiko.AuthenticationException:
            _safe_close(client)
            last_auth_fail = True
            continue  # try the next password
        except paramiko.BadHostKeyException as exc:
            _safe_close(client)
            raise _ConnFailure(f"目标机 {host} 主机密钥不匹配(疑被篡改): {exc}")
        except (paramiko.SSHException, socket.timeout, OSError) as exc:
            _safe_close(client)
            raise _ConnFailure(f"隧道连接失败 {host}:{port}: {exc}")
        tr = client.get_transport()
        if tr is not None:
            tr.set_keepalive(DEFAULT_KEEPALIVE)
        _clients[key] = client
        return client
    if last_auth_fail:
        raise _AuthFailure(f"目标机 {host} 认证被拒（已尝试 {len(attempts)} 个密码）")
    raise _ConnFailure(f"目标机 {host} 无可用密码")


def _host_credential(host: str) -> dict:
    """Resolve credentials for a non-cluster host.

    Per-host ``conn.host_credentials[host]`` wins (backward compatible, supports
    distinct user/port/password); otherwise the shared ``conn.sshpassword`` list
    is used with ``conn.user`` and port 22.  Returns a dict with keys
    ``user``, ``port``, ``passwords`` (list, possibly empty for key auth).
    """
    if _connection is None:
        die("load_connection() 未调用")
    override = (_connection.host_credentials or {}).get(host)
    if override is not None:
        pw = override.get("password")
        return {
            "user": override["user"],
            "port": override["port"],
            "passwords": [pw] if pw else [],
        }
    return {
        "user": _connection.user,
        "port": DEFAULT_SSH_PORT,
        "passwords": list(_connection.sshpassword or []),
    }


def execute_on_host(host: str, *command, timeout: int | None = None,
                    capture: bool = True, check: bool = True):
    """Run *command* on an arbitrary host through a direct-tcpip tunnel on the bastion.

    Credentials come from ``config.conn.host_credentials[host]`` (per-host
    override) or the shared ``config.conn.sshpassword`` list.  Passwords are
    never printed: failures map to generic statuses.  Host is strictly
    validated and shell-quoted so no command injection is possible.
    """
    if _connection is None:
        die("load_connection() 未调用")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", host) or host.startswith("-"):
        die(f"host 不合法: {host}")
    cred = _host_credential(host)
    timeout = timeout or _connection.connect_timeout
    key = f"tunnel:{cred['user']}@{host}:{cred['port']}"
    try:
        client = _tunnel_client(key, host, cred, timeout)
    except _AuthFailure as exc:
        sys.stderr.write(f"warn: {exc}\n")
        if check:
            die(f"HOST_AUTH_FAIL host={host}：{exc}")
        return _Result(255)
    except _ConnFailure as exc:
        sys.stderr.write(f"warn: 隧道失败 {host}: {exc}\n")
        if check:
            die(f"HOST_CONN_FAIL host={host}: {exc}")
        return _Result(255)
    return run_ssh_via(client, key, command, timeout=timeout,
                       capture=capture, check=check)


def run_ssh_via(client: "paramiko.SSHClient", key: str, command,
                *, timeout: int, capture: bool, check: bool) -> _Result:
    """Execute *command* on an already-connected cached client (tunnel path)."""
    remote = shlex.join(command)
    try:
        return _exec_once(client, key, remote, timeout, capture)
    except (socket.timeout, TimeoutError):
        _close_client(key)
        sys.stderr.write(f"warn: 远端命令超时 {key}\n")
        if check:
            die(f"远端命令超时 {key}")
        return _Result(255)
    except (paramiko.SSHException, OSError) as exc:
        _close_client(key)
        sys.stderr.write(f"warn: 远端命令失败 {key}: {exc}\n")
        if check:
            die(f"远端命令失败 {key}: {exc}")
        return _Result(255)


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
