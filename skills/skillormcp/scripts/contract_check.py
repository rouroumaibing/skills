#!/usr/bin/env python3
"""MCP 契约回归检查: 用 MCP 客户端 tools/list 出快照, 与基线 diff 判破坏。

MCP tool 是运行时发现的机器级契约(见 SKILL.md「类比不成立的 3 处」第 ① 条):
schema 一变就可能破坏依赖它的技能与提示词, 所以契约变化必须像 API 变化一样
有版本纪律。本脚本就是那把尺:

  snapshot  连真实 MCP 服务(必须走实际传输, 不许 import 服务端模块做静态内省),
            完成 initialize + tools/list 握手, 把每个 tool 的
            名/schema/是否写(is_write)/破坏性(destructive)/服务端版本 落成快照文件。
  check     把当前快照与基线快照 diff, 按破坏语义分类:
            破坏(BREAKING, 须阻断提交或升 major + --allow-breaking 显式放行):
              - 删 tool / 改名
              - 删必填参数 / 删可选参数 / 新增必填参数(对旧调用方是收窄)
              - 类型收窄(any->具体类型、多类型->单类型)
              - 枚举收窄(自由文本->枚举、枚举值减少)
              - is_write(读写标注)翻转 / destructive 从 False 变 True
            非破坏(PASS, 放行): 新增 tool、新增可选参数、类型放宽、枚举扩充、
              destructive 从 True 变 False、description 变化(只报告)。

只报告、不改文件、不调用任何写 tool(全程只发 initialize/initialized/tools/list)。
退出码: 0 = 无破坏(或破坏已 --allow-breaking 放行); 1 = 存在破坏, 待处置(可挂 CI 阻断);
        2 = 用法/传输错误。

用法:
    # 产出/刷新基线(stdio: 起子进程; http: 打 Streamable HTTP 端点)
    python3 contract_check.py snapshot --command "python server.py" -o contract-baseline.json
    python3 contract_check.py snapshot --url http://127.0.0.1:8080/mcp -o contract-baseline.json

    # 契约回归(提交前/CI)
    python3 contract_check.py check --baseline contract-baseline.json --command "python server.py"
    python3 contract_check.py check --baseline contract-baseline.json --url http://... [--allow-breaking]

    # 内置断言(不依赖网络与子进程)
    python3 contract_check.py --selftest
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import select
import shlex
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

PROTOCOL_VERSION = "2024-11-05"
CLIENT_INFO = {"name": "skillormcp-contract-check", "version": "1"}
SNAPSHOT_VERSION = 1
IO_TIMEOUT_SECONDS = 30.0


# ----------------------------------------------------------------------------
# 传输层: 快照必须覆盖实际传输 —— stdio(spawn 子进程) 与 http(Streamable HTTP)
# ----------------------------------------------------------------------------
class TransportError(RuntimeError):
    pass


class StdioTransport:
    """newline-delimited JSON-RPC over child stdin/stdout(MCP stdio 传输)。"""

    def __init__(self, command: str):
        argv = shlex.split(command)
        if not argv:
            raise TransportError("--command 为空, 无法启动 stdio MCP 服务")
        self.proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1)

    def _send(self, payload: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def _recv_until_id(self, want_id: int) -> dict:
        assert self.proc.stdout is not None
        fd = self.proc.stdout.fileno()
        while True:
            ready, _, _ = select.select([fd], [], [], IO_TIMEOUT_SECONDS)
            if not ready:
                self.close()
                raise TransportError("等待 MCP 响应超时(stdio)")
            line = self.proc.stdout.readline()
            if not line:
                self.close()
                raise TransportError("MCP 子进程已退出, 未收到响应(stdio)")
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue          # 非 JSON 行(日志噪声)跳过
            if msg.get("id") == want_id and ("result" in msg or "error" in msg):
                return msg

    def request(self, payload: dict) -> dict:
        self._send(payload)
        resp = self._recv_until_id(payload["id"])
        if "error" in resp:
            raise TransportError(f"MCP 返回错误: {resp['error']}")
        return resp["result"]

    def notify(self, payload: dict) -> None:
        self._send(payload)

    def close(self) -> None:
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
        except OSError:
            pass


class HttpTransport:
    """Streamable HTTP 传输: POST JSON-RPC, 兼容 JSON 与 SSE(event-stream) 响应。"""

    def __init__(self, url: str):
        self.url = url
        self.session_id: Optional[str] = None
        self._next_id = 1

    def _post(self, payload: dict) -> Tuple[dict, Any]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.url, data=data, headers=headers,
                                      method="POST")
        try:
            resp = urllib.request.urlopen(req, timeout=IO_TIMEOUT_SECONDS)
        except urllib.error.HTTPError as exc:
            raise TransportError(f"http {exc.code}: {exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise TransportError(f"http 连接失败: {exc.reason}") from exc
        self.session_id = resp.headers.get("Mcp-Session-Id") or self.session_id
        ctype = resp.headers.get("Content-Type", "")
        body = resp.read().decode("utf-8", errors="replace")
        resp.close()
        if "text/event-stream" in ctype:
            result = self._parse_sse(body)
        else:
            try:
                result = json.loads(body)
            except ValueError as exc:
                raise TransportError(f"http 响应不是 JSON: {body[:120]}") from exc
        if "error" in result:
            raise TransportError(f"MCP 返回错误: {result['error']}")
        return result, resp

    @staticmethod
    def _parse_sse(body: str) -> dict:
        """SSE 事件块中取第一个带 result/error 的 data: 行。"""
        for block in body.split("\n\n"):
            for ln in block.splitlines():
                if ln.startswith("data:"):
                    try:
                        msg = json.loads(ln[5:].strip())
                    except ValueError:
                        continue
                    if "result" in msg or "error" in msg:
                        return msg
        raise TransportError(f"SSE 响应中未找到 JSON-RPC 结果: {body[:120]}")

    def request(self, payload: dict) -> dict:
        result, _ = self._post(payload)
        return result["result"]

    def notify(self, payload: dict) -> None:
        self._post(payload)

    def close(self) -> None:
        pass


def open_transport(args: argparse.Namespace) -> Any:
    if getattr(args, "command", None):
        return StdioTransport(args.command)
    if getattr(args, "url", None):
        return HttpTransport(args.url)
    raise TransportError("需要 --command(stdio) 或 --url(http) 之一")


# ----------------------------------------------------------------------------
# 快照
# ----------------------------------------------------------------------------
def handshake(tr: Any) -> Dict[str, Any]:
    """initialize -> initialized -> tools/list, 全程只读, 不调用任何业务 tool。"""
    result = tr.request({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": PROTOCOL_VERSION,
                   "capabilities": {}, "clientInfo": CLIENT_INFO},
    })
    tr.notify({"jsonrpc": "2.0", "method": "notifications/initialized"})
    tools = tr.request({"jsonrpc": "2.0", "id": 2,
                        "method": "tools/list", "params": {}})
    server_info = result.get("serverInfo") or {}
    return {"server_info": server_info, "tools": tools.get("tools", [])}


def _tool_signature(tool: dict) -> Dict[str, Any]:
    """把单个 tool 压成契约快照条目: 名/schema/是否写/破坏性/描述哈希。

    is_write 的判定口径(保守): annotations.readOnlyHint 不为 True 即视为写侧。
    未标注的 tool 一律按写处理 —— 宁可误报破坏, 不让"读写语义悄悄变了"溜过去。
    description 不进原文只进哈希: 描述文案变化不判破坏, 但 hash 变化会提示复核。
    """
    ann = tool.get("annotations") or {}
    schema = tool.get("inputSchema") or {}
    desc = tool.get("description") or ""
    return {
        "is_write": ann.get("readOnlyHint") is not True,
        "destructive": bool(ann.get("destructiveHint")),
        "description_hash": hashlib.sha256(desc.encode("utf-8")).hexdigest()[:12],
        "schema": {
            "type": schema.get("type"),
            "required": sorted(schema.get("required") or []),
            "properties": schema.get("properties") or {},
        },
    }


def make_snapshot(server: Dict[str, Any], transport_kind: str) -> Dict[str, Any]:
    tools = {str(t.get("name")): _tool_signature(t) for t in server["tools"]}
    return {
        "snapshot_version": SNAPSHOT_VERSION,
        "transport": transport_kind,
        "server_info": {
            "name": server["server_info"].get("name", ""),
            "version": str(server["server_info"].get("version", "")),
        },
        "tools": tools,
    }


# ----------------------------------------------------------------------------
# diff 与破坏判定
# ----------------------------------------------------------------------------
def _type_set(t: Any) -> Optional[set]:
    """schema.type 归一为集合; None(None/缺失) 表示未约束(最宽)。"""
    if t is None:
        return None
    if isinstance(t, str):
        return {t}
    if isinstance(t, list):
        return {x for x in t if isinstance(x, str)} or None
    return None


def _param_breaking(name: str, base: dict, cur: dict) -> List[Tuple[str, str]]:
    """单参数级别的破坏判定, 返回 [(类别, 细节)]。空列表 = 无破坏。"""
    out: List[Tuple[str, str]] = []
    bt, ct = _type_set(base.get("type")), _type_set(cur.get("type"))
    if ct is not None and (bt is None or not bt <= ct):
        out.append(("类型收窄", f"参数 {name}: type "
                    f"{sorted(bt) if bt else '(未约束)'} -> {sorted(ct)}"))
    be, ce = base.get("enum"), cur.get("enum")
    if ce is not None:
        if be is None:
            out.append(("枚举收窄", f"参数 {name}: 自由文本 -> 枚举 {sorted(ce)}"))
        elif not set(ce) <= set(be):
            pass                       # 枚举扩充 = 放宽
        elif set(ce) != set(be):
            out.append(("枚举收窄", f"参数 {name}: 枚举值减少 "
                        f"{sorted(set(be) - set(ce))}"))
    return out


def diff_snapshots(base: Dict[str, Any], cur: Dict[str, Any]
                   ) -> Tuple[List[dict], List[dict], List[str]]:
    """返回 (breaking, nonbreaking, info)。

    breaking    : 判破坏的变更, 须阻断或升 major + allow-breaking 放行
    nonbreaking : 新增/放宽, 放行但报告(让人知道契约长大了)
    info        : 非契约信息(版本号/描述 hash), 仅提示
    """
    breaking: List[dict] = []
    nonbreaking: List[dict] = []
    info: List[str] = []
    btools, ctools = base.get("tools", {}), cur.get("tools", {})

    for name, b in sorted(btools.items()):
        c = ctools.get(name)
        if c is None:
            breaking.append({"tool": name, "category": "删tool",
                             "detail": "基线存在、当前缺失(含改名)"})
            continue
        if b["is_write"] != c["is_write"]:
            breaking.append({"tool": name, "category": "读写标注变化",
                             "detail": f"is_write: {b['is_write']} -> {c['is_write']}"
                                       " (readOnlyHint 翻转, 读写护栏语义变了)"})
        if c["destructive"] and not b["destructive"]:
            breaking.append({"tool": name, "category": "破坏性标注变化",
                             "detail": "destructiveHint: False -> True"})
        elif b["destructive"] and not c["destructive"]:
            nonbreaking.append({"tool": name, "category": "破坏性标注放宽",
                                "detail": "destructiveHint: True -> False"})

        breq = set(b["schema"]["required"])
        creq = set(c["schema"]["required"])
        for p in sorted(breq - creq):
            breaking.append({"tool": name, "category": "删必填",
                             "detail": f"必填参数 {p} 被删除"})
        for p in sorted(creq - breq):
            breaking.append({"tool": name, "category": "新增必填",
                             "detail": f"新增必填参数 {p} (旧调用方立即失效, 属收窄)"})

        bprops = b["schema"]["properties"]
        cprops = c["schema"]["properties"]
        for p in sorted(set(bprops) - set(cprops)):
            if p not in creq and p in breq:
                continue               # 已按删必填报过
            breaking.append({"tool": name, "category": "删参数",
                             "detail": f"可选参数 {p} 被删除 (旧调用方传该参数即失败)"})
        for p in sorted(set(cprops) - set(bprops)):
            nonbreaking.append({"tool": name, "category": "新增可选参数",
                                "detail": p})
        for p in sorted(set(bprops) & set(cprops)):
            for cat, detail in _param_breaking(p, bprops[p], cprops[p]):
                breaking.append({"tool": name, "category": cat, "detail": detail})
            bt, ct = _type_set(bprops[p].get("type")), _type_set(cprops[p].get("type"))
            if bt is not None and (ct is None or ct > bt):
                nonbreaking.append({"tool": name, "category": "类型放宽",
                                    "detail": f"参数 {p}: type 放宽"})
            be, ce = bprops[p].get("enum"), cprops[p].get("enum")
            if be is not None and ce is not None and set(ce) > set(be):
                nonbreaking.append({"tool": name, "category": "枚举扩充",
                                    "detail": f"参数 {p}: 新增枚举值 {sorted(set(ce)-set(be))}"})

        if b["description_hash"] != c["description_hash"]:
            info.append(f"{name}: description 变化(不判破坏; 复核方法是否被塞进描述)")

    for name in sorted(set(ctools) - set(btools)):
        nonbreaking.append({"tool": name, "category": "新增tool", "detail": "放行"})

    bver = (base.get("server_info") or {}).get("version")
    cver = (cur.get("server_info") or {}).get("version")
    if bver != cver:
        info.append(f"服务端版本: {bver!r} -> {cver!r}")
    return breaking, nonbreaking, info


# ----------------------------------------------------------------------------
# 自检(纯 diff 语义断言, 不依赖网络/子进程)
# ----------------------------------------------------------------------------
def _selftest_asserts() -> None:
    def snap(tools=None, version="0.1.0"):
        tools = tools if tools is not None else {
            "get_item": {"is_write": False, "destructive": False,
                         "description_hash": "aaa",
                         "schema": {"type": "object",
                                    "required": ["id"],
                                    "properties": {"id": {"type": "string"}}}},
        }
        return {"snapshot_version": 1, "transport": "stdio",
                "server_info": {"name": "x", "version": version}, "tools": tools}

    def copy_tools(base, **over):
        tools = json.loads(json.dumps(base["tools"]))
        for k, v in over.items():
            if k in tools:
                if v is None:
                    del tools[k]
                else:
                    tools[k].update(v)
        return tools

    b = snap()
    brk, ok, _ = diff_snapshots(b, snap())
    assert not brk, f"相同快照误报破坏: {brk}"

    def must_break(mod, category):
        cur = snap(copy_tools(b, **mod))
        brk, _, _ = diff_snapshots(b, cur)
        cats = {x["category"] for x in brk}
        assert category in cats, f"未判破坏({category}): {brk} | got {cats}"

    def must_pass(mod, category):
        cur = snap(copy_tools(b, **mod))
        brk, okc, _ = diff_snapshots(b, cur)
        assert not brk, f"误判破坏: {brk}"
        assert any(x["category"] == category for x in okc), f"未报告放宽({category}): {okc}"

    # 破坏面
    must_break({"get_item": None}, "删tool")
    must_break({"get_item": {"schema": {"type": "object", "required": [],
                                       "properties": {"id": {"type": "string"}}}}},
               "删必填")
    must_break({"get_item": {"schema": {"type": "object", "required": ["id", "env"],
                                       "properties": {"id": {"type": "string"},
                                                      "env": {"type": "string"}}}}},
               "新增必填")
    # 删可选参数: 基线带 tag, 当前删掉
    b_del = snap({"get_item": {"is_write": False, "destructive": False,
                               "description_hash": "aaa",
                               "schema": {"type": "object", "required": ["id"],
                                          "properties": {"id": {"type": "string"},
                                                         "tag": {"type": "string"}}}}})
    cur_del = snap({"get_item": {"is_write": False, "destructive": False,
                                 "description_hash": "aaa",
                                 "schema": {"type": "object", "required": ["id"],
                                            "properties": {"id": {"type": "string"}}}}})
    brk_d, _, _ = diff_snapshots(b_del, cur_del)
    assert any(x["category"] == "删参数" for x in brk_d), f"未判破坏(删参数): {brk_d}"
    must_break({"get_item": {"schema": {"type": "object", "required": ["id"],
                                       "properties": {"id": {"enum": ["a", "b"]}}}}},
               "枚举收窄")
    must_break({"get_item": {"schema": {"type": "object", "required": ["id"],
                                       "properties": {"id": {"type": "integer"}}}}},
               "类型收窄")
    must_break({"get_item": {"is_write": True}}, "读写标注变化")
    must_break({"get_item": {"destructive": True}}, "破坏性标注变化")

    # 放行面
    must_pass({"create_item": {"is_write": True, "destructive": True,
                               "description_hash": "b",
                               "schema": {"type": "object", "required": ["x"],
                                          "properties": {"x": {"type": "string"}}}}},
              "新增tool")
    must_pass({"get_item": {"schema": {"type": "object", "required": ["id"],
                                       "properties": {"id": {"type": "string"},
                                                      "limit": {"type": "integer"}}}}},
              "新增可选参数")
    must_pass({"get_item": {"schema": {"type": "object", "required": ["id"],
                                       "properties": {"id": {"type": ["string", "integer"]}}}}},
              "类型放宽")
    # 枚举扩充: 基线本身要有 enum, 增值才是放宽; 无 enum -> 有 enum 是收窄(破坏面已覆盖)
    b_enum = snap({"get_item": {"is_write": False, "destructive": False,
                                "description_hash": "aaa",
                                "schema": {"type": "object", "required": ["id"],
                                           "properties": {"id": {"enum": ["a", "b"]}}}}})
    cur_enum = snap({"get_item": {"is_write": False, "destructive": False,
                                  "description_hash": "aaa",
                                  "schema": {"type": "object", "required": ["id"],
                                             "properties": {"id": {"enum": ["a", "b", "c"]}}}}})
    brk_e, okc_e, _ = diff_snapshots(b_enum, cur_enum)
    assert not brk_e, f"枚举扩充误判破坏: {brk_e}"
    assert any(x["category"] == "枚举扩充" for x in okc_e), okc_e

    # destructive 放宽 + description 只进 info
    cur = snap(copy_tools(b, get_item={"destructive": False,
                                       "description_hash": "zzz"}))
    b2 = snap(copy_tools(b, get_item={"destructive": True}))
    brk, okc, info = diff_snapshots(b2, cur)
    assert not brk, f"destructive 放宽误判破坏: {brk}"
    assert any(x["category"] == "破坏性标注放宽" for x in okc)
    assert any("description 变化" in s for s in info), info

    # 未标注 readOnlyHint 的 tool 一律视为写侧(保守)
    sig = _tool_signature({"name": "t", "inputSchema": {"type": "object", "required": []},
                           "annotations": {}, "description": ""})
    assert sig["is_write"] is True, sig
    sig2 = _tool_signature({"name": "t", "annotations": {"readOnlyHint": True}})
    assert sig2["is_write"] is False

    print("OK: selftest 通过(删tool/删必填/新增必填/删参数/类型与枚举收窄/"
          "读写与破坏性标注翻转判破坏; 新增/放宽放行; description 只进 info; "
          "未标注 tool 保守视为写)。")


def selftest() -> int:
    try:
        _selftest_asserts()
    except AssertionError as exc:
        print(f"FAIL: {exc}")
        return 1
    return 0


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def _load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="mode")

    for name in ("snapshot", "check"):
        p = sub.add_parser(name)
        p.add_argument("--command", help='stdio 传输: 启动 MCP 的命令, 如 "python server.py"')
        p.add_argument("--url", help="http 传输: MCP Streamable HTTP 端点")
        if name == "snapshot":
            p.add_argument("-o", "--output", required=True, help="快照输出文件")
        else:
            p.add_argument("--baseline", required=True, help="基线快照文件")
            p.add_argument("--allow-breaking", action="store_true",
                           help="显式放行破坏(须升 major 版本并在提交说明记录)")
            p.add_argument("--json", action="store_true", help="机器可读输出")

    parser.add_argument("--selftest", action="store_true", help="运行内置断言")

    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    if not getattr(args, "mode", None):
        parser.error("需要 snapshot / check 子命令或 --selftest")

    if args.mode == "snapshot" and not (args.command or args.url):
        parser.error("snapshot 需要 --command(stdio) 或 --url(http)")
    if args.mode == "check" and not (args.command or args.url):
        parser.error("check 需要 --command(stdio) 或 --url(http)")

    tr = None
    try:
        tr = open_transport(args)
        server = handshake(tr)
        transport_kind = "stdio" if args.command else "http"
        snapshot = make_snapshot(server, transport_kind)
    except TransportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        if tr is not None:
            tr.close()

    if args.mode == "snapshot":
        out = json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(out + "\n")
        n = len(snapshot["tools"])
        print(f"✓ 快照已写入 {args.output}: {n} 个 tool, "
              f"服务端 {snapshot['server_info']['name']} "
              f"v{snapshot['server_info']['version']} ({transport_kind})")
        return 0

    # check
    try:
        base = _load_json(args.baseline)
    except (OSError, ValueError) as exc:
        print(f"error: 基线读取失败: {exc}", file=sys.stderr)
        return 2
    breaking, nonbreaking, info = diff_snapshots(base, snapshot)

    if args.json:
        print(json.dumps({"breaking": breaking, "nonbreaking": nonbreaking,
                          "info": info}, ensure_ascii=False, indent=2))
    else:
        if breaking:
            print(f"发现 {len(breaking)} 处破坏性契约变更(BREAKING):")
            for x in breaking:
                print(f"  [破坏|{x['category']}] {x['tool']}: {x['detail']}")
        if nonbreaking:
            print(f"非破坏变更 {len(nonbreaking)} 处(放行, 仅报告):")
            for x in nonbreaking:
                print(f"  [放行|{x['category']}] {x['tool']}: {x['detail']}")
        for s in info:
            print(f"  [info] {s}")
        if not breaking and not nonbreaking and not info:
            print("✓ 契约与基线一致。")

    if breaking and not args.allow_breaking:
        print("处置: 阻断本次提交, 或升 major 版本后用 --allow-breaking 显式放行, "
              "并把破坏项写进提交说明。", file=sys.stderr)
        return 1
    if breaking and args.allow_breaking:
        print("已放行破坏性变更: 确认已升 major 版本, 且破坏项已写进提交说明。",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
