#!/usr/bin/env python3
"""<mcp-name> MCP 服务骨架。

设计要点(与 README 对应):
  1. 凭证只在服务端从环境变量/密钥管理读取,绝不作为 tool 参数。
  2. 读、写 tool 拆分;写 tool 走审批令牌(硬约束)。
  3. 目标范围走白名单配置。
  4. 每次调用写审计日志(参数脱敏)。
  5. 配置统一由 Settings 收口:一处加载、一处校验,缺项即启动失败。
  6. 内部函数通过入参接收 settings 等依赖(便于测试注入),不做模块级隐式读取;
     tool 函数用闭包捕获 settings,避免把配置暴露成 tool 参数。
"""
import hashlib
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from fastmcp import FastMCP

# ---------- 配置 ----------
DEFAULT_CONFIG_FILENAME = "config.yaml"


@dataclass(frozen=True)
class Settings:
    """统一配置对象:所有配置项在此收口,加载时一次校验完。

    原则:
      - 必填项缺失直接抛错,不静默用默认值(否则"配置没写"会变成"行为悄悄变了")。
      - 业务连接参数(base_url 等)走 extra,按需在 Settings 上加具名字段,
        不要散落到 tool 代码里硬编码。
    """

    allowed_targets: frozenset[str]
    approval_ttl_seconds: int
    credential_refs: dict[str, str]
    rate_limit_per_minute: int
    # 业务连接参数示例(按需启用):
    # base_url: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "Settings":
        """从配置文件加载并校验。必填项缺失即抛错。"""
        if not path.exists():
            raise RuntimeError(f"配置文件不存在: {path}")
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise RuntimeError(f"配置文件格式错误(应为映射): {path}")

        targets = raw.get("allowed_targets") or []
        if not targets:
            raise RuntimeError("allowed_targets 不能为空: 没有白名单等于所有目标都被拒")
        refs = raw.get("credential_refs") or {}
        if not isinstance(refs, dict) or not refs:
            raise RuntimeError("credential_refs 不能为空: 凭证引用名缺失")

        known = {"allowed_targets", "approval_ttl_seconds",
                 "credential_refs", "rate_limit_per_minute"}
        return cls(
            allowed_targets=frozenset(str(t) for t in targets),
            approval_ttl_seconds=int(raw.get("approval_ttl_seconds", 300)),
            credential_refs={str(k): str(v) for k, v in refs.items()},
            rate_limit_per_minute=int(raw.get("rate_limit_per_minute", 30)),
            extra={k: v for k, v in raw.items() if k not in known},
        )

    def secret_env_name(self, ref_key: str) -> str:
        """引用名 -> 环境变量名。缺引用即报错,不给默认环境变量名。"""
        name = self.credential_refs.get(ref_key)
        if not name:
            raise RuntimeError(f"缺少凭证引用: {ref_key}")
        return name

    def get(self, key: str, default: Any = None) -> Any:
        """读取业务扩展配置(extra)。"""
        return self.extra.get(key, default)

    def require(self, key: str) -> Any:
        """读取业务扩展配置,缺失即报错(不静默降级)。"""
        if key not in self.extra:
            raise RuntimeError(f"缺少配置项: {key}")
        return self.extra[key]


def load_settings(config_path: str | None = None) -> Settings:
    """配置路径来自参数或环境变量 MCP_CONFIG,缺省为当前工作目录下的 config.yaml。"""
    path = Path(config_path or os.environ.get("MCP_CONFIG", DEFAULT_CONFIG_FILENAME))
    return Settings.load(path)


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


# ---------- 凭证(只在服务端读取,永不作为 tool 参数) ----------
def read_secret(settings: Settings, ref_key: str) -> str:
    """按引用名从环境变量取凭证。缺失即报错。"""
    env_name = settings.secret_env_name(ref_key)
    value = os.environ.get(env_name, "")
    if not value:
        raise RuntimeError(f"缺少凭证引用: {ref_key} (环境变量 {env_name} 未设置)")
    return value


# ---------- 限流 ----------
def check_rate_limit(settings: Settings, bucket: dict[str, list[float]],
                     scope: str = "read") -> None:
    """按分钟窗口限流。读、写分别计量——写配额不能被读调用吃光。

    bucket 由调用方持有,便于测试注入与重置。
    """
    now = time.time()
    key = f"hits:{scope}"
    hits = [t for t in bucket.get(key, []) if now - t < 60]
    if len(hits) >= settings.rate_limit_per_minute:
        bucket[key] = hits
        raise PermissionError(
            f"超过{scope}限流上限({settings.rate_limit_per_minute} 次/分钟),请稍后重试。")
    hits.append(now)
    bucket[key] = hits


# ---------- 审批令牌(写操作硬拦截) ----------
def issue_token(settings: Settings, pending: dict[str, dict],
                tool: str, params: dict) -> str:
    """签发一次性限时令牌。pending 由调用方持有,便于测试隔离。"""
    token = secrets.token_urlsafe(16)
    pending[token] = {"tool": tool, "params": params,
                      "exp": time.time() + settings.approval_ttl_seconds}
    return token


def consume_token(settings: Settings, pending: dict[str, dict],
                  token: str, tool: str, params: dict) -> bool:
    """校验并消费令牌:一次有效、限时、与参数绑定。"""
    item = pending.pop(token, None)
    return bool(item and item["tool"] == tool and item["params"] == params
                and item["exp"] > time.time())


# ---------- 白名单 ----------
def check_target(settings: Settings, target: str) -> None:
    if target not in settings.allowed_targets:
        raise ValueError(f"目标不在白名单内: {target}。可用目标请查看 list_targets。")


# ---------- MCP 组装 ----------
def build_mcp(settings: Settings) -> FastMCP:
    """按配置组装 MCP 服务。

    settings 通过闭包注入各 tool,而不是作为 tool 参数暴露给 Agent;
    测试可直接调用 build_mcp(自定义 Settings) 注入替身配置。
    """
    mcp = FastMCP("<mcp-name>")
    pending: dict[str, dict] = {}              # 待确认令牌
    rate_bucket: dict[str, list[float]] = {"hits": []}   # 限流窗口

    @mcp.tool()
    def list_targets() -> dict:
        """列出当前允许操作的目标。无需参数,只读。"""
        audit("list_targets", {}, "ok")
        return {"targets": sorted(settings.allowed_targets)}

    @mcp.tool()
    def get_<noun>(target: str, <key>: str) -> dict:
        """只读:查询 <noun> 的状态。查询其他资源请用对应的 get_ 工具。

        Args:
            target: 目标名称,必须是 list_targets 返回值之一。
            <key>: <业务键,说明格式与示例>。
        """
        check_target(settings, target)
        check_rate_limit(settings, rate_bucket, "read")
        client_secret = read_secret(settings, "<ref_key>")  # 仅服务端使用,不回传
        # TODO: 用 client_secret 调用目标系统,只返回 Agent 需要的字段,并做分页/截断
        result = {"target": target, "<key>": <key>, "status": "TODO"}
        audit("get_<noun>", {"target": target, "<key>": <key>}, "ok")
        return result

    @mcp.tool()
    def prepare_<change_noun>(target: str, <key>: str, new_value: str) -> dict:
        """写操作第 1 步:生成变更预览和确认令牌,不会执行任何修改。
        请把 summary 展示给用户确认后,再调用 apply_<change_noun>。
        """
        check_target(settings, target)
        check_rate_limit(settings, rate_bucket, "write")
        params = {"target": target, "<key>": <key>, "new_value": new_value}
        token = issue_token(settings, pending, "apply_<change_noun>", params)
        audit("prepare_<change_noun>", params, "pending")
        return {
            "summary": f"将把 {target} 的 {<key>} 修改为 {new_value}",
            "approval_token": token,
            "expires_in_seconds": settings.approval_ttl_seconds,
        }

    @mcp.tool()
    def apply_<change_noun>(target: str, <key>: str, new_value: str,
                            approval_token: str) -> dict:
        """写操作第 2 步:凭用户确认后的令牌执行修改。令牌一次有效、限时、与参数绑定。"""
        params = {"target": target, "<key>": <key>, "new_value": new_value}
        check_target(settings, target)
        if not consume_token(settings, pending, approval_token,
                             "apply_<change_noun>", params):
            audit("apply_<change_noun>", params, "denied")
            raise PermissionError("确认令牌无效或已过期,请重新调用 prepare_<change_noun> 并让用户确认。")
        check_rate_limit(settings, rate_bucket, "write")
        client_secret = read_secret(settings, "<ref_key>")
        # TODO: 带超时、幂等键执行修改
        audit("apply_<change_noun>", params, "ok")
        return {"status": "applied"}

    return mcp


if __name__ == "__main__":
    # 配置先加载并校验,缺项直接失败,不带着半截配置启动
    mcp = build_mcp(load_settings())
    # 本地调试用 stdio;远程部署改用 streamable-http 并开启认证
    mcp.run()

# 端到端试跑样例(填写后保留):
#   1) list_targets            -> 返回白名单
#   2) get_<noun>              -> 返回状态
#   3) prepare_<change_noun>   -> 返回 summary + approval_token
#   4) apply_<change_noun>     -> 凭 token 执行;重复使用 token 应被拒绝
