#!/usr/bin/env python3
"""<mcp-name> MCP 服务骨架。

设计要点(与 README 对应):
  1. 双传输: 环境变量 MCP_TRANSPORT=stdio|http 切换。stdio 本地单租户;
     http 远程多租户, 启动时校验绑定地址, 拒绝 0.0.0.0/:: 通配, 只绑内网/回环地址。
  2. 凭证收口: 所有凭证由 CredentialManager 在服务端获取——
     - 每个 ref 有自己独立的 env 回退链, 只在同凭证内回退, 绝不借用其它 ref;
     - 获取模式二选一: static(直接读 env) / oauth2_cc(client_credentials 换 token);
     - 支持成对凭证(client_id/client_secret 各自独立 env 链);
     - http 调用凭证只进请求头; 静态凭证与连接配置永不作为 tool 参数。
  3. HTTP 唯一出口 request_api: 统一超时与 verify_tls(默认开);
     401 时刷新凭证并重试一次, 仍 401 抛 PermissionError。
  4. 写护栏 write_policy 二选一(启动时校验, 二者只居其一):
     - gate: 按写类别拆分的 env 开关, 缺失/未开启即拒(PermissionError);
     - token: prepare(预览+一次性限时令牌)与 apply(校验消费)两步。
  5. Settings 统一加载并校验必填项, 不静默降级; 白名单管控目标;
     @audited 定义即应用, 敏感值哈希脱敏; 列表输出截断分页。
"""
import functools
import hashlib
import inspect
import ipaddress
import json
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml
from fastmcp import FastMCP

# ---------- 配置 ----------
DEFAULT_CONFIG_FILENAME = "config.yaml"
TRANSPORT_ENV = "MCP_TRANSPORT"
FORBIDDEN_BIND_HOSTS = {"0.0.0.0", "::", "*", ""}
GATE_TRUTHY = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class CredentialSpec:
    """单个凭证引用的声明: 获取模式 + 它自己的 env 回退链。

    - env_chain: 本 ref 的回退链; 401 刷新时在链内下移, 只在同 ref 内回退。
    - pair: 成对凭证(oauth2_cc 用), 每个成员各有独立 env 链。
    - header_name: 空 = 默认 Authorization: Bearer; 非空 = 自定义请求头(如 X-Api-Key)。
    """

    ref_key: str
    mode: str                                  # static | oauth2_cc
    env_chain: tuple[str, ...]
    pair: dict[str, tuple[str, ...]] = field(default_factory=dict)
    token_url: str = ""
    scopes: tuple[str, ...] = ()
    header_name: str = ""


def _parse_credential_refs(refs_raw: Any) -> dict[str, CredentialSpec]:
    """解析并校验 credential_refs; 缺什么直接抛错, 不静默降级。"""
    if not isinstance(refs_raw, dict) or not refs_raw:
        raise RuntimeError("credential_refs 不能为空: 凭证引用名缺失")
    specs: dict[str, CredentialSpec] = {}
    for key, spec in refs_raw.items():
        key = str(key)
        if not isinstance(spec, dict):
            raise RuntimeError(f"凭证 {key} 的声明必须是映射")
        mode = str(spec.get("mode", "static")).strip().lower()
        if mode not in {"static", "oauth2_cc"}:
            raise RuntimeError(f"凭证 {key} 的 mode 只支持 static | oauth2_cc, 收到: {mode}")
        env_chain = tuple(str(e).strip() for e in (spec.get("env") or []) if str(e).strip())
        pair = {str(k): tuple(str(e).strip() for e in (v or []) if str(e).strip())
                for k, v in (spec.get("pair") or {}).items()}
        token_url = str(spec.get("token_url", "")).strip()
        if mode == "static" and not env_chain:
            raise RuntimeError(f"凭证 {key} (static) 缺少 env 回退链")
        if mode == "oauth2_cc":
            if not token_url:
                raise RuntimeError(f"凭证 {key} (oauth2_cc) 缺少 token_url")
            if not pair.get("client_id") or not pair.get("client_secret"):
                raise RuntimeError(
                    f"凭证 {key} (oauth2_cc) 缺少成对凭证 pair: client_id/client_secret 各需独立 env 链")
        specs[key] = CredentialSpec(
            ref_key=key, mode=mode, env_chain=env_chain, pair=pair,
            token_url=token_url,
            scopes=tuple(str(s).strip() for s in (spec.get("scopes") or []) if str(s).strip()),
            header_name=str(spec.get("header_name", "")).strip(),
        )
    return specs


@dataclass(frozen=True)
class Settings:
    """统一配置对象: 所有配置项在此收口, 加载时一次校验完。

    原则:
      - 必填项缺失直接抛错, 不静默用默认值(否则"配置没写"会变成"行为悄悄变了")。
      - 业务连接参数(base_url / http_host / http_port 等)走 extra, 按需在 Settings
        上加具名字段, 不要散落到 tool 代码里硬编码。
      - 静态凭证与连接配置永不作为 tool 参数: tool 签名就是 Agent 可见的 schema。
    """

    allowed_targets: frozenset[str]
    credential_specs: dict[str, CredentialSpec]
    write_policy: str                          # gate | token
    approval_ttl_seconds: int
    rate_limit_per_minute: int
    request_timeout_seconds: int
    verify_tls: bool
    write_gate_env_prefix: str
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

        specs = _parse_credential_refs(raw.get("credential_refs"))

        policy = str(raw.get("write_policy", "")).strip().lower()
        if policy not in {"gate", "token"}:
            raise RuntimeError("write_policy 必填且只能是 gate | token(写护栏二选一)")

        verify_tls = raw.get("verify_tls", True)
        if not isinstance(verify_tls, bool):
            raise RuntimeError("verify_tls 必须是布尔值(默认 true; 确要关闭需在配置显式写出)")

        known = {"allowed_targets", "credential_refs", "write_policy",
                 "approval_ttl_seconds", "rate_limit_per_minute",
                 "request_timeout_seconds", "verify_tls", "write_gate_env_prefix"}
        return cls(
            allowed_targets=frozenset(str(t) for t in targets),
            credential_specs=specs,
            write_policy=policy,
            approval_ttl_seconds=int(raw.get("approval_ttl_seconds", 300)),
            rate_limit_per_minute=int(raw.get("rate_limit_per_minute", 30)),
            request_timeout_seconds=int(raw.get("request_timeout_seconds", 30)),
            verify_tls=verify_tls,
            write_gate_env_prefix=str(raw.get("write_gate_env_prefix", "MCP_WRITE_ALLOW_")),
            extra={k: v for k, v in raw.items() if k not in known},
        )

    def get(self, key: str, default: Any = None) -> Any:
        """读取业务扩展配置(extra)。"""
        return self.extra.get(key, default)

    def require(self, key: str) -> Any:
        """读取业务扩展配置, 缺失即报错(不静默降级)。"""
        if key not in self.extra:
            raise RuntimeError(f"缺少配置项: {key}")
        return self.extra[key]


def load_settings(config_path: str | None = None) -> Settings:
    """配置路径来自参数或环境变量 MCP_CONFIG, 缺省为当前工作目录下的 config.yaml。"""
    path = Path(config_path or os.environ.get("MCP_CONFIG", DEFAULT_CONFIG_FILENAME))
    return Settings.load(path)


# ---------- 审计 ----------
logging.basicConfig(level=logging.INFO, format="%(message)s")
audit_log = logging.getLogger("audit")

SENSITIVE_KEY_RE = re.compile(
    r"(?i)(password|passwd|pwd|secret|token|api[_-]?key|apikey|"
    r"private[_-]?key|credential|authorization|cookie)")


def _redact_params(params: dict) -> dict:
    """审计参数摘要: 敏感键的值只留哈希前缀, 超长值截断。"""
    out: dict[str, Any] = {}
    for k, v in params.items():
        key = str(k)
        s = str(v)
        if SENSITIVE_KEY_RE.search(key):
            out[key] = "sha256:" + hashlib.sha256(s.encode()).hexdigest()[:8]
        elif len(s) > 120:
            out[key] = s[:60] + f"...({len(s)} chars)"
        else:
            out[key] = v
    return out


def audit(tool: str, params: dict, status: str, detail: str = "") -> None:
    entry = {"ts": int(time.time()), "tool": tool,
             "params": _redact_params(params), "status": status}
    if detail:
        entry["detail"] = detail
    audit_log.info(json.dumps(entry, ensure_ascii=False))


def audited(tool_name: str | None = None):
    """审计装饰器: 定义即应用(直接叠在 tool 函数上)。

    成功/被拒(PermissionError)/异常三条路径都留痕; 审计参数先经 _redact_params 脱敏。
    """
    def deco(fn):
        name = tool_name or fn.__name__

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                bound = inspect.signature(fn).bind(*args, **kwargs)
                params = dict(bound.arguments)
            except TypeError:
                params = {"args": str(args), "kwargs": str(kwargs)}
            try:
                result = fn(*args, **kwargs)
            except PermissionError as exc:
                audit(name, params, "denied", str(exc)[:160])
                raise
            except Exception as exc:
                audit(name, params, "error", type(exc).__name__)
                raise
            audit(name, params, "ok")
            return result

        return wrapper
    return deco


# ---------- 白名单 / 限流 / 写护栏 ----------
def check_target(settings: Settings, target: str) -> None:
    if target not in settings.allowed_targets:
        raise ValueError(f"目标不在白名单内: {target}。可用目标请查看 list_targets。")


def check_rate_limit(settings: Settings, bucket: dict[str, list[float]],
                     scope: str = "read") -> None:
    """按分钟窗口限流。读、写分别计量——写配额不能被读调用吃光。

    bucket 由调用方持有, 便于测试注入与重置。
    """
    now = time.time()
    key = f"hits:{scope}"
    hits = [t for t in bucket.get(key, []) if now - t < 60]
    if len(hits) >= settings.rate_limit_per_minute:
        bucket[key] = hits
        raise PermissionError(
            f"超过{scope}限流上限({settings.rate_limit_per_minute} 次/分钟), 请稍后重试。")
    hits.append(now)
    bucket[key] = hits


def check_write_gate(settings: Settings, category: str) -> None:
    """gate 写护栏: 按写类别拆分的 env 开关, 缺失/未开启即拒(PermissionError)。

    写类别是服务端定义的常量, 不作为 tool 参数——否则 Agent 可以挑一个开着的类别绕过。
    """
    env_name = f"{settings.write_gate_env_prefix}{str(category).upper()}"
    value = str(os.environ.get(env_name, "")).strip().lower()
    if value not in GATE_TRUTHY:
        raise PermissionError(
            f"写类别未授权: {category}(环境变量 {env_name} 未开启), 写操作被拒绝。")


# ---------- 审批令牌(token 写护栏) ----------
def issue_token(settings: Settings, pending: dict[str, dict],
                tool: str, params: dict) -> str:
    """签发一次性限时令牌。pending 由调用方持有, 便于测试隔离。"""
    token = secrets.token_urlsafe(16)
    pending[token] = {"tool": tool, "params": params,
                      "exp": time.time() + settings.approval_ttl_seconds}
    return token


def consume_token(settings: Settings, pending: dict[str, dict],
                  approval_token: str, tool: str, params: dict) -> bool:
    """校验并消费令牌: 一次有效、限时、与参数绑定。"""
    item = pending.pop(approval_token, None)
    return bool(item and item["tool"] == tool and item["params"] == params
                and item["exp"] > time.time())


# ---------- HTTP 唯一出口 ----------
def request_api(settings: Settings, creds: "CredentialManager", method: str, path: str,
                *, credential_ref: str | None = None, json_body=None, form_body=None,
                params=None, headers=None) -> dict:
    """所有对目标系统(含 IdP token 端点)的 HTTP 调用必须走这里。

    - 统一超时(settings.request_timeout_seconds)与 verify_tls(默认开)。
    - 凭证只进请求头: 默认 Authorization: Bearer <value>;
      声明了 header_name 的 ref 用自定义请求头。凭证值不进日志、不进返回值。
    - 带 credential_ref 的调用遇 401: 刷新凭证(static 链内下移 / oauth 重新换取)
      并重试一次; 仍 401 抛 PermissionError。
    """
    if json_body is not None and form_body is not None:
        raise ValueError("json_body 与 form_body 二选一")
    path = str(path)
    url = path if path.startswith(("http://", "https://")) \
        else str(settings.require("base_url")).rstrip("/") + path
    base_headers = dict(headers or {})

    def _once() -> httpx.Response:
        hdrs = dict(base_headers)
        if credential_ref:
            _, value = creds.acquire(credential_ref)
            spec = settings.credential_specs[credential_ref]
            if spec.header_name:
                hdrs[spec.header_name] = value
            else:
                hdrs.setdefault("Authorization", f"Bearer {value}")
        with httpx.Client(verify=settings.verify_tls,
                          timeout=settings.request_timeout_seconds) as client:
            return client.request(method.upper(), url, headers=hdrs,
                                  params=params, json=json_body, data=form_body)

    resp = _once()
    if resp.status_code == 401 and credential_ref:
        creds.acquire(credential_ref, force_refresh=True)   # 401 -> 刷新凭证
        resp = _once()                                      # 重试一次
        if resp.status_code == 401:
            raise PermissionError(
                f"目标系统认证失败(401), 凭证已刷新并重试一次仍未通过: {url}")
    try:
        data = resp.json()
    except ValueError:
        data = None
    return {"status_code": resp.status_code, "data": data}


# ---------- 凭证(只在服务端读取, 永不作为 tool 参数) ----------
class CredentialManager:
    """凭证收口: 每个 ref 独立回退链, 只在同 ref 内回退, 绝不借用其它 ref。

    - static: 按 ref 自己的 env 链取值; 401 刷新时把当前 env 标记为已用、链内下移;
      链用尽即报错, 不去其它 ref 的 env 里找。
    - oauth2_cc: 用成对凭证(client_id/client_secret 各自独立 env 链)向 token_url
      换 access_token, 缓存到到期前 30 秒; 401 刷新时强制重新换取。
    """

    def __init__(self, settings: Settings, env: dict | None = None):
        self.settings = settings
        self._env = env if env is not None else os.environ
        self._used_env: dict[str, str] = {}        # ref -> 当前命中的 env 名
        self._excluded: dict[str, set] = {}        # ref -> 已被 401 刷新淘汰的 env
        self._token_cache: dict[str, dict] = {}    # ref -> {token, exp}

    def _read_chain(self, chain, label: str, excluded=()) -> tuple[str, str]:
        for env in chain:
            if env in excluded:
                continue
            value = str(self._env.get(env, "") or "")
            if value:
                return env, value
        raise RuntimeError(f"凭证 {label} 的 env 回退链全部不可用: {list(chain)}")

    def acquire(self, ref_key: str, force_refresh: bool = False) -> tuple[str, str]:
        """取得凭证值。返回 (来源标识, 值); 来源只用于审计, 不回传给 Agent。"""
        spec = self.settings.credential_specs.get(ref_key)
        if spec is None:
            raise RuntimeError(f"未定义的凭证引用: {ref_key}")
        if spec.mode == "static":
            excluded = self._excluded.setdefault(ref_key, set())
            if force_refresh and self._used_env.get(ref_key):
                excluded.add(self._used_env[ref_key])       # 同 ref 内回退: 淘汰当前 env
            env, value = self._read_chain(spec.env_chain, ref_key, excluded)
            self._used_env[ref_key] = env
            return env, value
        # oauth2_cc: 缓存未到期直接复用; force_refresh 强制重新换取
        cache = self._token_cache.get(ref_key)
        if cache and not force_refresh and cache["exp"] > time.time() + 30:
            return "token-endpoint(cached)", cache["token"]
        _, client_id = self._read_chain(spec.pair["client_id"], f"{ref_key}.client_id")
        _, pair_secret = self._read_chain(
            spec.pair["client_secret"], f"{ref_key}.client_secret")
        form = {"grant_type": "client_credentials",
                "client_id": client_id, "client_secret": pair_secret}
        if spec.scopes:
            form["scope"] = " ".join(spec.scopes)
        resp = request_api(self.settings, self, "POST", spec.token_url, form_body=form)
        data = resp["data"] or {}
        token = str(data.get("access_token", ""))
        if resp["status_code"] != 200 or not token:
            raise RuntimeError(
                f"凭证 {ref_key} 换取 token 失败(HTTP {resp['status_code']})")
        expires_in = int(data.get("expires_in", 300) or 300)
        self._token_cache[ref_key] = {"token": token, "exp": time.time() + expires_in}
        return "token-endpoint", token


# ---------- 列表分页 ----------
def paginate(items: list, page: int = 1, page_size: int = 20,
             max_page_size: int = 100) -> dict:
    """列表输出统一截断分页, 避免大结果撑爆上下文。"""
    total = len(items)
    page = max(1, int(page))
    page_size = min(max(1, int(page_size)), max_page_size)
    start = (page - 1) * page_size
    chunk = items[start:start + page_size]
    has_more = start + page_size < total
    return {"items": chunk, "total": total, "page": page, "page_size": page_size,
            "has_more": has_more, "next_page": page + 1 if has_more else None}


# ---------- 传输 ----------
def validate_http_bind(settings: Settings) -> None:
    """http(远程多租户)绑定地址校验: 绝不允许绑 0.0.0.0/:: 通配地址。"""
    host = str(settings.require("http_host")).strip()
    if host.lower() in FORBIDDEN_BIND_HOSTS:
        raise RuntimeError(
            f"http 传输禁止绑定通配地址({host!r}), 必须绑定内网地址或 localhost")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        pass  # 内网主机名(DNS)由部署侧保证可达性与私网属性, 见 README「部署与连通」
    else:
        if not (ip.is_private or ip.is_loopback):
            raise RuntimeError(f"http 绑定地址必须是内网/回环地址, 收到: {host}")
    port = int(settings.require("http_port"))
    if not 1 <= port <= 65535:
        raise RuntimeError(f"http_port 超出范围: {port}")


# ---------- MCP 组装 ----------
def build_mcp(settings: Settings) -> FastMCP:
    """按配置组装 MCP 服务。

    - settings / creds 通过闭包注入各 tool, 绝不作为 tool 参数暴露给 Agent;
    - 写护栏按 write_policy 二选一装配, 只注册所选策略的 tool;
    - 测试可直接调用 build_mcp(自定义 Settings) 注入替身配置。
    """
    mcp = FastMCP("<mcp-name>")
    creds = CredentialManager(settings)
    pending: dict[str, dict] = {}                        # token 护栏: 待确认令牌
    rate_bucket: dict[str, list[float]] = {"hits:read": [], "hits:write": []}

    @mcp.tool()
    @audited()
    def list_targets() -> dict:
        """列出当前允许操作的目标。无需参数, 只读。"""
        return {"targets": sorted(settings.allowed_targets)}

    @mcp.tool()
    @audited()
    def get_<noun>(target: str, <key>: str) -> dict:
        """只读: 查询 <noun> 的状态。查询其他资源请用对应的 get_ 工具。

        Args:
            target: 目标名称, 必须是 list_targets 返回值之一。
            <key>: <业务键, 说明格式与示例>。
        """
        check_target(settings, target)
        check_rate_limit(settings, rate_bucket, "read")
        resp = request_api(settings, creds, "GET", f"/api/v1/<noun>s/{<key>}",
                           credential_ref="<ref_key>")
        if resp["status_code"] != 200:
            raise RuntimeError(f"上游返回 HTTP {resp['status_code']}")
        data = resp["data"] or {}
        # TODO: 只返回 Agent 需要的字段, 不要透传上游整包
        return {"target": target, "<key>": <key>, "status": data.get("status", "unknown")}

    @mcp.tool()
    @audited()
    def list_<noun>s(target: str, page: int = 1, page_size: int = 20) -> dict:
        """只读: 分页列出 <noun>。大结果自动截断, 翻页用返回的 next_page。

        Args:
            target: 目标名称, 必须是 list_targets 返回值之一。
            page: 页码, 从 1 开始。
            page_size: 每页条数, 上限 100。
        """
        check_target(settings, target)
        check_rate_limit(settings, rate_bucket, "read")
        resp = request_api(settings, creds, "GET", "/api/v1/<noun>s",
                           params={"target": target}, credential_ref="<ref_key>")
        if resp["status_code"] != 200:
            raise RuntimeError(f"上游返回 HTTP {resp['status_code']}")
        return paginate(resp["data"] or [], page=page, page_size=page_size)

    if settings.write_policy == "token":
        @mcp.tool()
        @audited()
        def prepare_<change_noun>(target: str, <key>: str, new_value: str) -> dict:
            """写操作第 1 步: 生成变更预览和一次性限时令牌, 不会执行任何修改。
            请把 summary 展示给用户确认后, 再调用 apply_<change_noun>。
            """
            check_target(settings, target)
            check_rate_limit(settings, rate_bucket, "write")
            params = {"target": target, "<key>": <key>, "new_value": new_value}
            token = issue_token(settings, pending, "apply_<change_noun>", params)
            return {
                "summary": f"将把 {target} 的 {<key>} 修改为 {new_value}",
                "approval_token": token,
                "expires_in_seconds": settings.approval_ttl_seconds,
            }

        @mcp.tool()
        @audited()
        def apply_<change_noun>(target: str, <key>: str, new_value: str,
                                approval_token: str) -> dict:
            """写操作第 2 步: 凭用户确认后的令牌执行修改。令牌一次有效、限时、与参数绑定。"""
            params = {"target": target, "<key>": <key>, "new_value": new_value}
            check_target(settings, target)
            if not consume_token(settings, pending, approval_token,
                                 "apply_<change_noun>", params):
                raise PermissionError(
                    "确认令牌无效或已过期, 请重新调用 prepare_<change_noun> 并让用户确认。")
            check_rate_limit(settings, rate_bucket, "write")
            resp = request_api(settings, creds, "PUT", f"/api/v1/<noun>s/{<key>}",
                               json_body={"new_value": new_value},
                               credential_ref="<ref_key>")
            if resp["status_code"] not in (200, 201, 204):
                raise RuntimeError(f"上游返回 HTTP {resp['status_code']}, 修改未确认生效")
            return {"status": "applied", "status_code": resp["status_code"]}
    else:  # gate
        @mcp.tool()
        @audited()
        def apply_<change_noun>(target: str, <key>: str, new_value: str) -> dict:
            """写操作(类别 <change_category>): 由服务端 env 开关管控, 未开启即拒绝。
            写类别是服务端常量, 不接受 Agent 指定。
            """
            check_target(settings, target)
            check_write_gate(settings, "<change_category>")
            check_rate_limit(settings, rate_bucket, "write")
            resp = request_api(settings, creds, "PUT", f"/api/v1/<noun>s/{<key>}",
                               json_body={"new_value": new_value},
                               credential_ref="<ref_key>")
            if resp["status_code"] not in (200, 201, 204):
                raise RuntimeError(f"上游返回 HTTP {resp['status_code']}, 修改未确认生效")
            return {"status": "applied", "status_code": resp["status_code"]}

    return mcp


if __name__ == "__main__":
    # 配置先加载并校验, 缺项直接失败, 不带着半截配置启动
    transport = os.environ.get(TRANSPORT_ENV, "stdio").strip().lower()
    settings = load_settings()
    mcp = build_mcp(settings)
    if transport == "stdio":
        mcp.run()                       # 本地单租户
    elif transport == "http":
        validate_http_bind(settings)    # 远程多租户: 绑定内网地址, 拒绝通配
        mcp.run(transport="http", host=str(settings.require("http_host")),
                port=int(settings.require("http_port")))
    else:
        raise RuntimeError(f"{TRANSPORT_ENV} 仅支持 stdio | http, 收到: {transport}")

# 端到端试跑样例(填写后保留):
#   stdio:  MCP_CONFIG=<config> python server.py
#   http:   MCP_TRANSPORT=http MCP_CONFIG=<config> python server.py   (config 里 http_host/http_port)
#   1) list_targets            -> 返回白名单
#   2) get_<noun>              -> 经 request_api 走凭证请求头; 401 时自动刷新重试一次
#   3) list_<noun>s            -> 分页返回(has_more/next_page)
#   4) 写操作(token 策略): prepare_<change_noun> 返回预览+令牌 -> apply_<change_noun> 凭令牌执行;
#      写操作(gate 策略): 环境变量 <MCP_WRITE_ALLOW_前缀><写类别大写> 未开启时直接被拒
