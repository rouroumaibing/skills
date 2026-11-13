#!/usr/bin/env python3
"""<mcp-name> MCP 服务骨架。

设计要点(与 README 对应):
  1. 凭证只在服务端从环境变量/密钥管理读取,绝不作为 tool 参数。
  2. 读、写 tool 拆分;写 tool 走审批令牌(硬约束)。
  3. 目标范围走白名单配置。
  4. 每次调用写审计日志(参数脱敏)。
"""
import hashlib
import json
import logging
import os
import secrets
import time
from pathlib import Path

import yaml
from fastmcp import FastMCP

# ---------- 配置 ----------
CONFIG_PATH = Path(os.environ.get("MCP_CONFIG", "config.yaml"))
CFG = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) if CONFIG_PATH.exists() else {}
ALLOWED_TARGETS = set(CFG.get("allowed_targets", []))
APPROVAL_TTL = int(CFG.get("approval_ttl_seconds", 300))

# 凭证:只读环境变量,变量名来自配置;缺失则启动失败,不给默认值
def _secret(ref_key: str) -> str:
    env_name = CFG.get("credential_refs", {}).get(ref_key)
    value = os.environ.get(env_name or "")
    if not value:
        raise RuntimeError(f"缺少凭证引用: {ref_key}")
    return value

# ---------- 审计 ----------
logging.basicConfig(level=logging.INFO, format="%(message)s")
audit_log = logging.getLogger("audit")

def _redact(params: dict) -> dict:
    """参数摘要:只记录键和哈希前缀,避免把业务敏感值写进日志。"""
    return {k: hashlib.sha256(str(v).encode()).hexdigest()[:8] for k, v in params.items()}

def audit(tool: str, params: dict, status: str) -> None:
    audit_log.info(json.dumps({
        "ts": int(time.time()), "tool": tool,
        "params": _redact(params), "status": status,
    }, ensure_ascii=False))

# ---------- 审批令牌(写操作硬拦截) ----------
_pending: dict[str, dict] = {}

def _issue_token(tool: str, params: dict) -> str:
    token = secrets.token_urlsafe(16)
    _pending[token] = {"tool": tool, "params": params, "exp": time.time() + APPROVAL_TTL}
    return token

def _consume_token(token: str, tool: str, params: dict) -> bool:
    item = _pending.pop(token, None)
    return bool(item and item["tool"] == tool and item["params"] == params and item["exp"] > time.time())

def _check_target(target: str) -> None:
    if target not in ALLOWED_TARGETS:
        raise ValueError(f"目标不在白名单内: {target}。可用目标请查看 list_targets。")

# ---------- Tools ----------
mcp = FastMCP("<mcp-name>")

@mcp.tool()
def list_targets() -> dict:
    """列出当前允许操作的目标。无需参数,只读。"""
    audit("list_targets", {}, "ok")
    return {"targets": sorted(ALLOWED_TARGETS)}

@mcp.tool()
def get_<noun>(target: str, <key>: str) -> dict:
    """只读:查询 <noun> 的状态。查询其他资源请用对应的 get_ 工具。

    Args:
        target: 目标名称,必须是 list_targets 返回值之一。
        <key>: <业务键,说明格式与示例>。
    """
    _check_target(target)
    client_secret = _secret("<ref_key>")  # 仅服务端使用,不回传
    # TODO: 用 client_secret 调用目标系统,只返回 Agent 需要的字段,并做分页/截断
    result = {"target": target, "<key>": <key>, "status": "TODO"}
    audit("get_<noun>", {"target": target, "<key>": <key>}, "ok")
    return result

@mcp.tool()
def prepare_<change_noun>(target: str, <key>: str, new_value: str) -> dict:
    """写操作第 1 步:生成变更预览和确认令牌,不会执行任何修改。
    请把 summary 展示给用户确认后,再调用 apply_<change_noun>。
    """
    _check_target(target)
    params = {"target": target, "<key>": <key>, "new_value": new_value}
    token = _issue_token("apply_<change_noun>", params)
    audit("prepare_<change_noun>", params, "pending")
    return {
        "summary": f"将把 {target} 的 {<key>} 修改为 {new_value}",
        "approval_token": token,
        "expires_in_seconds": APPROVAL_TTL,
    }

@mcp.tool()
def apply_<change_noun>(target: str, <key>: str, new_value: str, approval_token: str) -> dict:
    """写操作第 2 步:凭用户确认后的令牌执行修改。令牌一次有效、限时、与参数绑定。"""
    params = {"target": target, "<key>": <key>, "new_value": new_value}
    _check_target(target)
    if not _consume_token(approval_token, "apply_<change_noun>", params):
        audit("apply_<change_noun>", params, "denied")
        raise PermissionError("确认令牌无效或已过期,请重新调用 prepare_<change_noun> 并让用户确认。")
    client_secret = _secret("<ref_key>")
    # TODO: 带超时、幂等键执行修改
    audit("apply_<change_noun>", params, "ok")
    return {"status": "applied"}

if __name__ == "__main__":
    # 本地调试用 stdio;远程部署改用 streamable-http 并开启认证
    mcp.run()

# 端到端试跑样例(填写后保留):
#   1) list_targets            -> 返回白名单
#   2) get_<noun>              -> 返回状态
#   3) prepare_<change_noun>   -> 返回 summary + approval_token
#   4) apply_<change_noun>     -> 凭 token 执行;重复使用 token 应被拒绝
