#!/usr/bin/env python3
"""connect.py — verify bastion SSH reachability and that kubectl works on it.

Prints machine-readable statuses:
  BASTION_OK / NEEDS_MANUAL_AUTH / KUBECTL_OK / KUBECTL_UNAVAILABLE
(KUBECTL_UNAVAILABLE is a warning, not a failure: node/host channels still work.)

Usage:
  connect.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib


def main() -> None:
    conn = lib.load_connection()
    target = lib.bastion_target(conn)

    if lib.run_ssh(target, "true", timeout=conn.connect_timeout,
                   capture=True, check=False).returncode == 0:
        print(f"status=BASTION_OK bastion={conn.bastion_host}")
    else:
        print(f"status=NEEDS_MANUAL_AUTH bastion={conn.bastion_host}")
        print(f"  请运行: python3 scripts/setup_ssh.py local   "
              f"（配置本机到 {conn.user}@{conn.bastion_host} 的免密）")
        sys.exit(2)

    result = lib.run_kubectl("get", "pods", f"--request-timeout={conn.request_timeout}s",
                             "-o", "name", check=False)
    if result.returncode == 0:
        print(f"status=KUBECTL_OK namespace={conn.namespace or 'default'} "
              f"context={conn.context or 'default'}")
        if result.stdout.strip():
            print(result.stdout.rstrip())
    else:
        # kubectl is optional for the node/host channels: they resolve IPs via
        # direct IP / conn.node_ips / direct-tcpip tunnel. Only kubectl-backed
        # subcommands (pods/logs/events/describe/resolve-by-name) are affected,
        # so report a warning instead of failing the whole connection check.
        detail = ((result.stderr or "").strip())[:200]
        print(f"status=KUBECTL_UNAVAILABLE namespace={conn.namespace or 'default'} "
              f"context={conn.context or 'default'}")
        print("  kubectl 不可用/未安装；pod 类命令与按节点名解析不可用，"
              "但节点通道可传 IP 或用 conn.node_ips 映射、host 通道走直连隧道")
        if detail:
            print(f"  远端提示: {detail}")


if __name__ == "__main__":
    main()
