#!/usr/bin/env python3
"""SSH 免密配置与分发工具 (setup_ssh.py)。

本脚本是 troubleshooting skill 里**唯一允许写操作**的配置工具（其余 scripts/ 均为只读排查脚本），
由运维人员在自己的终端手动运行，用于一次性把两类免密配好：

  1) 本机  -> 跳板机        子命令 ``local``
  2) 跳板机 -> 集群节点      子命令 ``bastion``

**传输用 Paramiko（纯 Python SSH），不依赖本机 OpenSSH。** 因此 Windows 上
``ControlMaster auto + ControlPath ~/.ssh/cm/%C`` 报 ``getsockname failed: Not a socket``
的问题从根上消失——Paramiko 不读 ``~/.ssh/config``，也无需 ControlMaster socket。
密钥用 ``cryptography`` 生成（OpenSSH 格式，Paramiko 与 ssh 均可读），文件传输用 SFTP，
首密用 ``getpass``（仅交互终端）。节点跳转仍在**跳板机**上跑 ``ssh``（保留共享密钥模型）。

两类密钥、两套分发策略（核心：防止"各人各生成一套、互相覆盖机器上的配置"）：
  * **本机密钥**（个人）：每人在自己机器上各自生成 ``~/.ssh/id_ed25519``，公钥以「追加」方式
    放进跳板机 ``authorized_keys``；可逐人吊销（删一行），互不影响。
  * **跳板机密钥**（全组共享）：整组共用**一把**，只存放在跳板机上；同事通过共用这台跳板机
    天然继承对节点的免密，**不各自生成**。只有新建「并行跳板机」访问同一批节点时，才用
    ``export-bundle`` / ``import-bundle`` 把这把密钥对拷过去，且安装时幂等（已存在则拒绝覆盖），
    从机制上避免重复生成导致各节点 ``authorized_keys`` 分裂。

设计原则：
  * **幂等**：重复执行不重复追加、不覆盖既有配置（"重复配置"安全）。
  * **只追加不覆写**：``authorized_keys`` 永不整体重写；用 ``grep -qxF`` 按整行去重后再追加。
  * **密钥只生成不覆盖**：已存在则跳过；私钥权限 600，绝不外发 / 提交 git。
  * **异常不静默**：失败给出明确 status 码与下一步动作，不裸 ``pass``。
"""
from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import os
import re
import shlex
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib  # noqa: E402  (local module next to this script; also ensures paramiko is present)
import paramiko  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from lib import load_connection, die  # noqa: E402

HOME = Path.home()
SSH_DIR = HOME / ".ssh"
PRIVATE_KEY = SSH_DIR / "id_ed25519"
PUBLIC_KEY = SSH_DIR / "id_ed25519.pub"


# ------------------------------- 基础输出 -------------------------------
def info(message: str) -> None:
    """Print a human-readable progress line."""
    print(message)


def warn(message: str) -> None:
    """Print a non-fatal warning to stderr (never silently swallowed)."""
    sys.stderr.write(f"warn: {message}\n")


def _close(client) -> None:
    """Close a Paramiko client, logging (not swallowing) any error."""
    try:
        client.close()
    except (paramiko.SSHException, OSError) as exc:
        sys.stderr.write(f"warn: 关闭连接失败: {exc}\n")


def _exec(client, command: str, *, stdin_text=None, timeout: int = 30):
    """Run *command* over an open Paramiko client; return (returncode, stdout, stderr)."""
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    if stdin_text is not None:
        stdin.write(stdin_text)
        stdin.flush()
        stdin.close()
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    returncode = stdout.channel.recv_exit_status()
    return returncode, out, err


# ------------------------------- 本机密钥 -------------------------------
def ensure_ssh_dir() -> None:
    """Create ~/.ssh with strict 0700 permissions (a fresh machine may lack it)."""
    SSH_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(SSH_DIR, 0o700)


def _key_comment() -> str:
    """A stable comment for a new key: user@host."""
    try:
        login = os.getlogin()
    except OSError:
        login = os.environ.get("USER") or "user"
    return f"{login}@{socket.gethostname()}"


def read_public_key(path: Path = PUBLIC_KEY) -> str:
    """Read and sanity-check a public key line."""
    try:
        line = Path(path).read_text(encoding="utf-8").strip()
    except OSError as exc:
        die(f"读取公钥失败 {path}: {exc}")
    if not re.match(r"^(ssh-ed25519|ssh-rsa|ecdsa-sha2-\S+)\s+\S+", line):
        die(f"公钥格式异常: {path}")
    return line


def ensure_local_key() -> str:
    """Ensure a local ed25519 keypair exists; generate only if missing, never overwrite.

    Returns the public key line. Refuses to regenerate when a stale public key exists
    without its private half (that would silently invalidate an already-distributed key).
    Generation uses ``cryptography`` (pure Python), producing standard OpenSSH-format
    keys readable by both Paramiko and the ssh tooling.
    """
    ensure_ssh_dir()
    has_private = PRIVATE_KEY.exists()
    has_public = PUBLIC_KEY.exists()
    if has_private and has_public:
        os.chmod(PRIVATE_KEY, 0o600)
        info(f"[key] 本机密钥已存在，跳过生成（绝不覆盖）: {PRIVATE_KEY}")
        return read_public_key()
    if has_public and not has_private:
        die(f"发现公钥 {PUBLIC_KEY} 但缺少私钥 {PRIVATE_KEY}；"
            f"若该公钥已分发给跳板机，重新生成会导致失配。请恢复私钥或手动处理。")
    info(f"[key] 生成本机密钥（ed25519，仅此一次）: {PRIVATE_KEY}")
    key = Ed25519PrivateKey.generate()
    private_bytes = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.OpenSSH,
        serialization.NoEncryption(),
    )
    public_body = key.public_key().public_bytes(
        serialization.Encoding.OpenSSH,
        serialization.PublicFormat.OpenSSH,
    ).decode().strip()
    public_line = f"{public_body} {_key_comment()}"
    PRIVATE_KEY.write_bytes(private_bytes)
    os.chmod(PRIVATE_KEY, 0o600)
    PUBLIC_KEY.write_text(public_line + "\n", encoding="utf-8")
    os.chmod(PUBLIC_KEY, 0o644)
    return public_line


def key_fingerprint(path: Path = PUBLIC_KEY) -> str:
    """Return the SHA256 fingerprint (ssh-keygen -lf style), or '' if unavailable."""
    if not Path(path).exists():
        return ""
    parts = read_public_key(path).split()
    if len(parts) < 2:
        return ""
    digest = hashlib.sha256(base64.b64decode(parts[1])).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


# ------------------------------- 连接 / 远端幂等追加 -------------------------------
def _connect(target: str, *, timeout: int = 15, allow_interactive: bool = True):
    """Return a connected Paramiko client to *target*, or None on failure.

    Tries key/agent auth first; on auth failure and an interactive TTY, prompts once
    for a password (first-time setup). A non-interactive caller never hangs.
    """
    client = lib.make_ssh_client()
    try:
        lib.connect_client(client, target, timeout=timeout)
        return client
    except paramiko.AuthenticationException:
        _close(client)
        if not (allow_interactive and sys.stdin.isatty()):
            return None
        info(f"       首次配置：请输入 {target} 的登录密码（仅这一次，之后免密）")
        client = lib.make_ssh_client()
        try:
            password = getpass.getpass(f"密码 {target}: ")
            lib.connect_client(client, target, password=password, timeout=timeout)
            return client
        except paramiko.AuthenticationException:
            warn(f"密码认证失败: {target}")
            _close(client)
            return None
        except (paramiko.BadHostKeyException, paramiko.SSHException, socket.timeout, OSError) as exc:
            warn(f"连接失败 {target}: {exc}")
            _close(client)
            return None
    except paramiko.BadHostKeyException as exc:
        warn(f"主机密钥不匹配(疑被篡改) {target}: {exc}")
        _close(client)
        return None
    except (paramiko.SSHException, socket.timeout, OSError) as exc:
        warn(f"连接失败 {target}: {exc}")
        _close(client)
        return None


def build_append_script(public_key_b64: str) -> str:
    """Remote shell that idempotently appends a base64-encoded pubkey to authorized_keys.

    The key travels base64-encoded (pure ASCII) so quoting/whitespace in the key can never
    break the remote command. ``grep -qxF`` matches the whole line, so re-runs never duplicate.
    """
    return (
        "set -e; "
        "mkdir -p ~/.ssh && chmod 700 ~/.ssh; "
        "touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys; "
        f"K=$(printf %s {public_key_b64} | base64 -d); "
        'if grep -qxF "$K" ~/.ssh/authorized_keys; then echo ALREADY; '
        'else printf \'%s\\n\' "$K" >> ~/.ssh/authorized_keys; echo APPENDED; fi'
    )


def append_public_key(target: str, public_key: str, *, timeout: int = 15,
                      allow_interactive: bool = True):
    """Idempotently trust *public_key* on *target* (user@host) over Paramiko.

    Returns 'ALREADY' / 'APPENDED' on success, or None on failure.
    """
    script = build_append_script(base64.b64encode(public_key.encode()).decode())
    client = _connect(target, timeout=timeout, allow_interactive=allow_interactive)
    if client is None:
        return None
    try:
        returncode, out, err = _exec(client, script, timeout=timeout + 10)
    except (paramiko.SSHException, socket.timeout, OSError) as exc:
        warn(f"远端执行失败 {target}: {exc}")
        return None
    finally:
        _close(client)
    status = out.strip()
    if returncode == 0 and status in ("ALREADY", "APPENDED"):
        return status
    if err.strip():
        sys.stderr.write(f"warn: {target}: {err.strip()}\n")
    return None


def verify_passwordless(target: str, *, timeout: int = 15) -> bool:
    """True when *target* accepts a passwordless login (key/agent auth only)."""
    client = lib.make_ssh_client()
    try:
        lib.connect_client(client, target, timeout=timeout)
        returncode, _, _ = _exec(client, "true", timeout=timeout)
        return returncode == 0
    except (paramiko.AuthenticationException, paramiko.BadHostKeyException,
            paramiko.SSHException, socket.timeout, OSError):
        return False
    finally:
        _close(client)


# ------------------------------- 跳板机->节点 bootstrap -------------------------------
_BASTION_TEMPLATE = r'''set -u
U="__USER__"
if [ ! -f "$HOME/.ssh/id_ed25519" ]; then
  mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
  ssh-keygen -t ed25519 -N "" -f "$HOME/.ssh/id_ed25519" >/dev/null 2>&1
  echo "[bastion] 已生成跳板机密钥 ~/.ssh/id_ed25519（整组共用这一把）"
else
  echo "[bastion] 跳板机密钥已存在，跳过生成（不覆盖）"
fi
chmod 600 "$HOME/.ssh/id_ed25519" 2>/dev/null || true
PUB=$(cat "$HOME/.ssh/id_ed25519.pub")
B=$(printf %s "$PUB" | base64 | tr -d '\n')
if [ "$#" -gt 0 ]; then
  NODES="$*"
else
  NODES=$(kubectl get nodes -o jsonpath='{.items[*].status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null || true)
fi
if [ -z "$NODES" ]; then
  echo "[bastion] status=NO_NODES 未发现节点；可用 --nodes <ip...> 指定"
  exit 0
fi
APPEND="set -e; mkdir -p ~/.ssh && chmod 700 ~/.ssh; touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys; K=\$(printf %s $B | base64 -d); if grep -qxF \"\$K\" ~/.ssh/authorized_keys; then echo ALREADY; else printf '%s\\n' \"\$K\" >> ~/.ssh/authorized_keys; echo APPENDED; fi"
for ip in $NODES; do
  if OUT=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8 ${U}@$ip "$APPEND" 2>/dev/null); then
    echo "[bastion] $ip -> $OUT"
  else
    echo "[bastion] status=HOP_FAIL $ip 未免密；请在跳板机手动执行: ssh-copy-id -i ~/.ssh/id_ed25519.pub ${U}@$ip"
  fi
done
'''


def bastion_bootstrap_script(user: str) -> str:
    """Render the bash bootstrap that runs ON the bastion to configure bastion->nodes."""
    return _BASTION_TEMPLATE.replace("__USER__", user)


# ------------------------------- 子命令 -------------------------------
def cmd_local(args) -> None:
    """Configure passwordless SSH from this machine to the bastion (idempotent)."""
    conn = load_connection()
    ensure_ssh_dir()

    public_key = ensure_local_key()
    fingerprint = key_fingerprint()
    if fingerprint:
        info(f"[key] 指纹: {fingerprint}")

    target = lib.bastion_target(conn, conn.public_ip)
    info(f"[push] 追加本机公钥到跳板机 {target}（paramiko，幂等，只追加不覆盖）…")
    status = append_public_key(target, public_key)
    if status == "ALREADY":
        info(f"[push] 跳板机已信任本机公钥（重复配置，无需改动）: {target}")
    elif status == "APPENDED":
        info(f"[push] 已把本机公钥追加到跳板机 authorized_keys: {target}")
    else:
        warn(f"status=NEEDS_MANUAL_AUTH 无法自动追加公钥到 {target}"
             "（非交互终端 / 密码错误 / 网络不通）。")
        warn("       请在交互终端重跑本命令并输入一次跳板机密码。")

    if verify_passwordless(target):
        info(f"[verify] status=LOCAL_OK 免密登录跳板机成功: {target}")
    else:
        warn(f"[verify] status=NEEDS_MANUAL_AUTH 仍无法免密登录 {target}；请在交互终端重跑 local。")


def cmd_bastion(args) -> None:
    """Configure passwordless SSH from the bastion to the cluster nodes (idempotent)."""
    conn = load_connection()
    target = lib.bastion_target(conn)

    if not verify_passwordless(target):
        warn(f"status=NEEDS_MANUAL_AUTH 本机到跳板机 {target} 未免密；请先运行 `setup_ssh.py local`。")
        return

    for node in args.nodes:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", node):
            die(f"节点标识不合法: {node}")

    script = bastion_bootstrap_script(conn.user)
    command = "bash -s" + "".join(" " + shlex.quote(node) for node in args.nodes)
    info(f"[bastion] 在跳板机 {target} 上配置到节点的免密"
         "（paramiko 触发；节点跳转仍用跳板机 ssh；幂等，只追加不覆盖）…")
    client = lib.make_ssh_client()
    try:
        lib.connect_client(client, target, timeout=conn.connect_timeout)
        returncode, out, err = _exec(client, command, stdin_text=script,
                                     timeout=conn.connect_timeout + 120)
    except (paramiko.SSHException, socket.timeout, OSError) as exc:
        die(f"连接跳板机失败 {target}: {exc}")
    finally:
        _close(client)
    for line in out.splitlines():
        print(line)
    if returncode != 0 and err.strip():
        warn(f"[bastion] 远端返回非零: {err.strip()}")


def cmd_status(args) -> None:
    """Report the current passwordless setup state (local key, bastion, nodes)."""
    conn = load_connection()
    ensure_ssh_dir()
    rows = []

    if PRIVATE_KEY.exists() and PUBLIC_KEY.exists():
        rows.append(("本机密钥", "OK", f"{PRIVATE_KEY} {key_fingerprint()}"))
    else:
        rows.append(("本机密钥", "MISSING", "运行 setup_ssh.py local 生成"))

    target = lib.bastion_target(conn, conn.public_ip)
    reachable = verify_passwordless(target)
    rows.append(("本机 -> 跳板机 免密", "OK" if reachable else "FAIL", target))

    if reachable and PUBLIC_KEY.exists():
        public_key_b64 = base64.b64encode(read_public_key().encode()).decode()
        check = (f'grep -qxF "$(printf %s {public_key_b64} | base64 -d)" '
                 "~/.ssh/authorized_keys")
        client = lib.make_ssh_client()
        try:
            lib.connect_client(client, target)
            returncode, _, _ = _exec(client, check)
            trusted = returncode == 0
        except (paramiko.SSHException, socket.timeout, OSError):
            trusted = False
        finally:
            _close(client)
        rows.append(("跳板机已信任本机公钥", "OK" if trusted else "NO", "authorized_keys"))

    for node in args.nodes:
        result = lib.execute_on_node(node, "true", check=False)
        ok = result is not None and result.returncode == 0
        rows.append((f"跳板机 -> 节点 {node}", "HOP_OK" if ok else "HOP_FAIL",
                     "" if ok else "在跳板机 ssh-copy-id 或运行 bastion 子命令"))

    width = max(len(name) for name, _, _ in rows)
    print(f"{'检查项'.ljust(width)}  状态      说明")
    print("-" * (width + 30))
    for name, status, note in rows:
        print(f"{name.ljust(width)}  {status.ljust(9)} {note}")


def _resolve_bastion_target(explicit_host):
    """Use --host if given, else the bastion from config.yaml."""
    if explicit_host:
        return explicit_host
    conn = load_connection()
    return lib.bastion_target(conn, conn.public_ip)


def cmd_export_bundle(args) -> None:
    """Export the bastion keypair to a tar for standing up a NEW parallel bastion.

    This is the ONLY case where a private key is deliberately copied: the new bastion must be
    trusted by the same nodes WITHOUT regenerating (regenerating would split node authorized_keys).
    The bundle contains a private key — transfer it over an existing trusted channel, never by
    email and never into git.
    """
    target = _resolve_bastion_target(args.host)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    remote_tar = f"/tmp/ts-bastion-key-{stamp}.tar.gz"

    make = ("set -e; "
            'if [ ! -f ~/.ssh/id_ed25519 ]; then echo "status=NO_KEY 跳板机还没有密钥，'
            '请先运行 bastion 子命令"; exit 3; fi; '
            f"tar -czf {remote_tar} -C ~/.ssh id_ed25519 id_ed25519.pub && sha256sum {remote_tar}")

    client = lib.make_ssh_client()
    try:
        lib.connect_client(client, target)
        returncode, out, err = _exec(client, make, timeout=60)
        if returncode != 0:
            die(f"导出失败（{target}）: {(err or out).strip()}")
        sftp = client.open_sftp()
        sftp.get(remote_tar, args.out)
        sftp.close()
        _exec(client, f"rm -f {remote_tar}", timeout=15)
    except (paramiko.SSHException, socket.timeout, OSError) as exc:
        die(f"导出失败（{target}）: {exc}")
    finally:
        _close(client)

    restore = args.out + ".RESTORE.md"
    Path(restore).write_text(
        "# 跳板机密钥对（含私钥，敏感）\n\n"
        f"- 来源跳板机: {target}\n"
        f"- 打包时间: {stamp}\n"
        "- 内容: id_ed25519 + id_ed25519.pub\n\n"
        "## 用途\n"
        "仅在**新建并行跳板机**、需访问**同一批节点**时使用，避免重新生成密钥导致节点 "
        "authorized_keys 分裂。\n\n"
        "## 安装（幂等，已存在则拒绝覆盖）\n"
        f"```\npython3 setup_ssh.py import-bundle {Path(args.out).name} --host root@<新跳板机IP>\n```\n\n"
        "## 纪律\n"
        "- 只通过既有可信通道传输；禁止邮件 / 聊天工具 / 提交 git。\n"
        "- 用后即焚；怀疑泄露就在所有节点删掉对应公钥行。\n",
        encoding="utf-8")
    info(f"[export] status=EXPORTED 已导出跳板机密钥对: {args.out}")
    info(f"[export] 恢复说明: {restore}")
    warn("[export] 包含私钥！请安全传输，禁止邮件/提交 git；仅供新建并行跳板机使用。")


def cmd_import_bundle(args) -> None:
    """Install a bastion keypair bundle onto a bastion; idempotent, never overwrites."""
    target = args.host
    stamp = time.strftime("%Y%m%d-%H%M%S")
    remote_tar = f"/tmp/ts-bastion-key-import-{stamp}.tar.gz"

    install = (
        "set -e; mkdir -p ~/.ssh && chmod 700 ~/.ssh; "
        f'D=$(mktemp -d); tar -xzf {remote_tar} -C "$D"; '
        'if [ -f ~/.ssh/id_ed25519 ]; then '
        'echo "status=EXISTS 目标跳板机已有密钥，已保留（不覆盖）。若确需替换请手动处理。"; '
        f'rm -rf "$D" {remote_tar}; exit 0; fi; '
        'mv "$D/id_ed25519" ~/.ssh/id_ed25519; '
        'mv "$D/id_ed25519.pub" ~/.ssh/id_ed25519.pub; '
        "chmod 600 ~/.ssh/id_ed25519; chmod 644 ~/.ssh/id_ed25519.pub; "
        f'rm -rf "$D" {remote_tar}; '
        'echo "status=INSTALLED 已安装跳板机密钥对（未重新生成，各节点 authorized_keys 继续有效）"'
    )

    client = lib.make_ssh_client()
    try:
        lib.connect_client(client, target)
        sftp = client.open_sftp()
        sftp.put(args.file, remote_tar)
        sftp.close()
        returncode, out, err = _exec(client, install, timeout=60)
    except (paramiko.SSHException, socket.timeout, OSError) as exc:
        die(f"导入失败（{target}）: {exc}")
    finally:
        _close(client)
    for line in out.splitlines():
        print(line)
    if returncode != 0 and err.strip():
        warn(f"[import] 远端返回非零: {err.strip()}")


# ------------------------------- 入口 -------------------------------
def build_parser() -> argparse.ArgumentParser:
    """Build the CLI: local / bastion / status / export-bundle / import-bundle."""
    parser = argparse.ArgumentParser(
        prog="setup_ssh.py",
        description="SSH 免密配置与分发工具（Paramiko，幂等；本 skill 唯一允许写操作的脚本）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "两类密钥 / 两套分发:\n"
            "  local   : 每人在本机生成个人密钥，公钥追加到跳板机（可逐人吊销）\n"
            "  bastion : 全组共用一把跳板机密钥，追加到各节点（同事共用跳板机天然继承）\n"
            "  export-bundle / import-bundle : 仅新建并行跳板机时拷贝这把共享密钥对（禁私发）\n"))
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("local", help="配置 本机 -> 跳板机 免密（幂等）",
                   description="生成个人密钥（若无）并把公钥幂等追加到跳板机；传输用 Paramiko。")

    p_bastion = sub.add_parser("bastion", help="配置 跳板机 -> 节点 免密（在跳板机上执行，幂等）",
                               description="确保跳板机共用密钥存在，并把其公钥幂等追加到各节点。")
    p_bastion.add_argument("--nodes", nargs="*", default=[],
                           help="指定节点 IP/主机名；缺省用跳板机 kubectl 自动发现")

    p_status = sub.add_parser("status", help="体检当前免密状态",
                              description="检查本机密钥、到跳板机免密、以及（可选）到节点免密。")
    p_status.add_argument("--nodes", nargs="*", default=[], help="要检查免密的节点 IP/主机名")

    p_export = sub.add_parser("export-bundle", help="导出跳板机密钥对（给新并行跳板机）",
                              description="经 SFTP 打包跳板机 id_ed25519(.pub) 到 tar；含私钥，仅限可信通道传输。")
    p_export.add_argument("--host", help="跳板机 user@ip；缺省取 config.yaml 的跳板机")
    p_export.add_argument("--out", default=f"bastion-key-{time.strftime('%Y%m%d-%H%M%S')}.tar.gz",
                          help="导出文件路径")

    p_import = sub.add_parser("import-bundle", help="在跳板机上安装密钥对（幂等，不覆盖）",
                              description="把 export-bundle 的 tar 经 SFTP 安装到目标跳板机；已存在密钥则拒绝覆盖。")
    p_import.add_argument("file", help="export-bundle 生成的 tar 文件")
    p_import.add_argument("--host", required=True, help="目标跳板机 user@ip")

    return parser


_DISPATCH = {
    "local": cmd_local,
    "bastion": cmd_bastion,
    "status": cmd_status,
    "export-bundle": cmd_export_bundle,
    "import-bundle": cmd_import_bundle,
}


def main() -> None:
    """Entry point: parse args and dispatch to the selected subcommand."""
    args = build_parser().parse_args()
    info("== setup_ssh.py：本 skill 唯一允许写操作的配置工具，请由运维人员手动运行 ==")
    _DISPATCH[args.command](args)


if __name__ == "__main__":
    main()
