#!/usr/bin/env python3
"""connect.py — verify bastion SSH reachability and that kubectl works on it.

Prints machine-readable statuses:
  BASTION_OK / NEEDS_MANUAL_AUTH / KUBECTL_OK / KUBECTL_FAIL

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
        print(f"status=KUBECTL_FAIL namespace={conn.namespace or 'default'} "
              f"context={conn.context or 'default'}")
        sys.exit(1)


if __name__ == "__main__":
    main()
