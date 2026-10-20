#!/usr/bin/env python3
"""apply.py — 本 skill 唯一运行期写操作入口（修复执行 / remediation）。

三通道（所有远端访问仍全部经跳板机；Agent 不得直连 ssh/kubectl、不得自拼 IP）：
  kubectl <args...>         集群通道：跳板机上的 kubectl 写命令
  node --snapshot <cmd> <node> <command...>
                            节点通道：跳板机 -> 节点 SSH 跳转后的写命令
  db [--snapshot <SELECT>] <pod> <sql>
                            数据库通道：kubectl exec 进 DB pod 执行 SQL

守卫链（硬性顺序，任一不过即在触达环境前拒绝）：
  1. 白名单    命令在本地切成 token，与结构化声明表逐字段比对
               （verb -> kind -> name -> flag 集合），不做正则拼串整体匹配。
               T3 反模式为结构判断（--all / 控制面 ns / rm -r / DROP
               DATABASE 等），最先评估，任何授权不可覆盖。拒绝时报精确
               到字段的原因。config.remediation.write_whitelist 可按通道
               整体覆盖内置表（条目同为结构化声明，字段语义见同目录
               README.md 第 2 节；条目 verb 与实际命令严格比对）。
  2. 授权分层  T1 默认放行 / T2R 先备份后执行（remediation.shared_cluster=true
               时需 --confirm）/ T2U 需 --confirm 显式授权 / T3 一律拒绝。
  3. 备份前置  kubectl 通道自动 `kubectl get -o yaml` 存盘（目标不存在记
               absent，回滚=删除）；node/db 通道必须显式 --snapshot <只读
               命令/SELECT>，输出随备份存盘。备份失败 => 不执行。
  4. 执行      经 lib.run_kubectl / lib.execute_on_node（连接信息只来自
               config.conn）。
  5. 复验回滚  调用方用 scripts/verify.py 复验；FAIL 时
               `apply.py --confirm rollback <备份文件>`（kubectl 通道自动还原）。

用法注意：
  * 全局参数（--dry-run/--confirm/--confirm-by）必须放在子命令之前；
  * node 通道的 --snapshot 必须放在 <node> 之前（同 logs.py node-exec 的
    --scenario 约定，REMAINDER 会吞掉其后的 flag）；
  * kubectl 通道 `apply/replace -f` 的清单是**本机路径**（脚本读出后经 stdin
    传给跳板机上的 `kubectl apply -f -`）；跳板机侧路径不受支持；
  * annotate 的值若包含 " -n " 会被先解析成 namespace 参数，避免这种写法；
  * 本脚本从不处理密码/密钥；认证全部走 SSH 公钥免密（config 无密码字段）。

状态码：DRY_RUN / WRITE_APPLIED / WRITE_FAILED / NOT_WHITELISTED /
        TIER_T3_FORBIDDEN / NEEDS_AUTH_CONFIRM / BACKUP_FAILED /
        SNAPSHOT_REQUIRED / NEEDS_MANUAL_AUTH / ROLLBACK_APPLIED /
        ROLLBACK_UNSUPPORTED

退出码：0 成功（WRITE_APPLIED / ROLLBACK_APPLIED / DRY_RUN）
        1 拒绝或失败（白名单/授权/备份/执行失败）
        2 该通道不支持自动回滚（ROLLBACK_UNSUPPORTED）
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shlex
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib

CHANNELS = ("kubectl", "node", "db")
DEFAULT_BACKUP_DIR = "~/.cache/troubleshooting-backup"
SNAPSHOT_OUTPUT_LIMIT = 300   # node/db 快照最多存盘行数（超出截断并标注）
SLUG_MAX = 60
# 写命令/快照的远端执行读超时。lib 默认读超时是 connect_timeout+5（15s），
# 对读够用、对写不行：kubectl delete pod 优雅等待 30s 期间无输出，15s 读
# 超时会误报 WRITE_FAILED 并诱导回滚，而删除其实已在远端生效。
DEFAULT_WRITE_TIMEOUT = 180

_NAME_RE = re.compile(r"[a-z0-9]([a-z0-9.-]*[a-z0-9])?")   # k8s 对象名
_NODE_NAME_RE = re.compile(r"[a-z0-9.-]+")
_NAMESPACE_RE = re.compile(r"[a-z0-9-]+")
_PATH_RE = re.compile(r"[A-Za-z0-9_./-]+")
_TABLE_RE = re.compile(r"[A-Za-z0-9_.]+")
_UNIT_RE = re.compile(r"[A-Za-z0-9._@-]+")
_OWNER_RE = re.compile(r"[A-Za-z0-9._:-]+")
# config 自定义条目里 flag/参数值的通用安全字符（无 shell 元字符、无空格）
_SAFE_VAL_RE = re.compile(r"[A-Za-z0-9._/:@+-]+")


# ---------------------------------------------------------------------------
# 白名单匹配的两种失败：
#   _Reject  -> NOT_WHITELISTED（可经 config 登记放行）
#   _T3      -> TIER_T3_FORBIDDEN（结构判断命中反模式，任何登记不可覆盖）
# ---------------------------------------------------------------------------
class _Reject(Exception):
    pass


class _T3(Exception):
    pass


# ----------------------------- flag 解析 -----------------------------
# flag 值校验器（返回 bool）。表中值为 None = 布尔开关（不接受值）。
def _v_int(v: str) -> bool:
    return bool(re.fullmatch(r"[0-9]+", v))


def _v_image(v: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9._/:-]+", v))


def _v_patch_type(v: str) -> bool:
    return v in ("merge", "json", "strategic")


def _v_patch(v: str) -> bool:
    """patch 内容必须是合法 JSON/YAML 对象（防把任意字符串塞进 -p）。"""
    try:
        parsed = json.loads(v)
        return isinstance(parsed, (dict, list))
    except ValueError:
        pass
    try:
        return isinstance(yaml.safe_load(v), dict)
    except yaml.YAMLError:
        return False


def _v_kv(v: str) -> bool:
    """label 的 key=value（单 token）。"""
    key, sep, _val = v.partition("=")
    return bool(sep) and bool(re.fullmatch(r"[A-Za-z0-9./_-]+", key)) \
        and bool(re.fullmatch(r"[A-Za-z0-9./_:+@-]+", _val))


def _v_annotate(v: str) -> bool:
    """annotate 的 key=value，值可含空格（token 已拼接）。"""
    key, sep, val = v.partition("=")
    if not sep or not re.fullmatch(r"[A-Za-z0-9./_-]+", key):
        return False
    return val == "" or bool(re.fullmatch(r"[A-Za-z0-9./_:+@ -]+", val))


def _v_safe(v: str) -> bool:
    return bool(_SAFE_VAL_RE.fullmatch(v))


# config 自定义 flag 的取值模式：布尔开关（--flag）或 --flag=value 均可
_MAYBE = object()


def _v_maybe(v: str) -> bool:
    return bool(_SAFE_VAL_RE.fullmatch(v))


def _parse_flags(tokens: list[str], allowed: dict[str, object]) -> dict:
    """把 flag token 解析成 {flag: 值或 True}。

    支持 `--flag value` 与 `--flag=value` 两种写法（顺序无关）；
    未知 flag 或值不合法立即 _Reject，并说明是哪个字段、允许什么。
    checker 为 _MAYBE 时（config 自定义 flag）只接受 `--flag` 或
    `--flag=value`，不接受分离式 `--flag value`（会与资源名歧义）。
    """
    out: dict = {}
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if not t.startswith("-"):
            raise _Reject(f"多余参数: {t}")
        if "=" in t:
            flag, val = t.split("=", 1)
        else:
            flag, val = t, None
        if flag not in allowed:
            raise _Reject(f"flag {flag} 不在允许列表 {sorted(allowed)} 内")
        checker = allowed[flag]
        if checker is None:                      # 布尔开关
            if val is not None:
                raise _Reject(f"{flag} 是布尔开关，不接受值: {val}")
            out[flag] = True
            i += 1
        elif checker is _MAYBE:                  # config 自定义 flag
            if val is not None and not _v_maybe(val):
                raise _Reject(f"{flag} 的值不合法: {val}")
            out[flag] = val if val is not None else True
            i += 1
        else:
            if val is None:
                if i + 1 >= len(tokens):
                    raise _Reject(f"{flag} 缺少值")
                val = tokens[i + 1]
                i += 2
            else:
                i += 1
            if not checker(val):
                raise _Reject(f"{flag} 的值不合法: {val}")
            out[flag] = val
    return out


# ----------------------------- kubectl 白名单 -----------------------------
# 声明表：每行 = (接受的 kind 拼写, 规则名, 层级)。没有的 kind 就是不允许。
_DELETE_KINDS: list[tuple[set[str], str, str]] = [
    ({"pod", "pods"},                                  "delete-pod",              "T2R"),
    ({"job", "jobs"},                                  "delete-job",              "T2R"),
    ({"deployment", "deployments"},                    "delete-deployment",       "T2R"),
    ({"pvc", "pvcs", "persistentvolumeclaim", "persistentvolumeclaims"},
                                                       "delete-pvc-pv",           "T2U"),
    ({"pv", "pvs", "persistentvolume", "persistentvolumes"},
                                                       "delete-pvc-pv",           "T2U"),
    ({"secret", "secrets"},                            "delete-secret-configmap", "T2U"),
    ({"configmap", "configmaps"},                      "delete-secret-configmap", "T2U"),
    ({"role", "roles", "rolebinding", "rolebindings"}, "delete-rbac",             "T2U"),
    ({"clusterrole", "clusterroles", "clusterrolebinding", "clusterrolebindings"},
                                                       "delete-rbac",             "T2U"),
    ({"crd", "crds", "customresourcedefinition", "customresourcedefinitions"},
                                                       "delete-crd",              "T2U"),
    ({"namespace", "namespaces", "ns"},                "delete-namespace",        "T2U"),
]
_DELETE_FLAGS = {"--grace-period": _v_int, "--now": None}

_SCALE_KINDS: list[tuple[set[str], str, str]] = [
    ({"deployment", "deployments"},                    "scale", "T2R"),
    ({"statefulset", "statefulsets"},                  "scale", "T2R"),
    ({"replicaset", "replicasets"},                    "scale", "T2R"),
    ({"replicationcontroller", "replicationcontrollers"}, "scale", "T2R"),
]
_SCALE_FLAGS = {"--replicas": _v_int}   # 必填，见 _k_scale

_ROLLOUT_KINDS = {"deployment", "deployments", "statefulset", "statefulsets",
                  "daemonset", "daemonsets"}
_ROLLOUT_SUBS = {"restart": "rollout-restart", "undo": "rollout-undo",
                 "pause": "rollout-pause-resume", "resume": "rollout-pause-resume"}

_PATCH_KINDS = {"pod", "pods", "deployment", "deployments", "statefulset",
                "statefulsets", "daemonset", "daemonsets", "job", "jobs",
                "service", "services", "configmap", "configmaps",
                "secret", "secrets", "node", "nodes"}
_PATCH_FLAGS = {"--type": _v_patch_type, "-p": _v_patch, "--patch": _v_patch}

_LABEL_KINDS = _PATCH_KINDS
_CREATE_JOB_FLAGS = {"--image": _v_image}
# kubectl create job 仅支持 --image（--restart 属 create deployment）；白名单 flag 以服务端实际支持为准

_T3_CONTROL_PLANE_NS = {"kube-system", "kube-public", "kube-node-lease"}


def _extract_ns(tokens: list[str]) -> tuple[str, list[str]]:
    """提取并移除 -n / --namespace（可在任何位置）；返回 (ns, 剩余 tokens)。"""
    ns = ""
    rest: list[str] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in ("-n", "--namespace"):
            if i + 1 >= len(tokens):
                raise _Reject(f"{t} 缺少 namespace 值")
            ns = tokens[i + 1]
            i += 2
        elif t.startswith("--namespace="):
            ns = t.split("=", 1)[1]
            i += 1
        else:
            rest.append(t)
            i += 1
    if ns and not _NAMESPACE_RE.fullmatch(ns):
        raise _Reject(f"namespace 不合法: {ns}")
    return ns, rest


def _need_name(name: str) -> None:
    if not _NAME_RE.fullmatch(name):
        raise _Reject(f"资源名不合法: {name}")


def _kinds_help(table: list[tuple[set[str], str, str]]) -> str:
    seen: list[str] = []
    for kinds, _n, _t in table:
        seen.append(sorted(kinds)[0])
    return "/".join(seen)


def _k_create(tok: list[str], ns: str) -> dict:
    if len(tok) < 2 or tok[1] not in ("job", "jobs"):
        raise _Reject("create 仅支持 job（kubectl create job <name>）")
    if len(tok) < 3:
        raise _Reject("create job 缺少 job 名")
    names, flag_toks = _split_names_flags(tok[2:], _CREATE_JOB_FLAGS)
    if len(names) != 1:
        raise _Reject(f"create job 需要恰好 1 个 job 名，得到: "
                      f"{' '.join(names) if names else '（无）'}")
    _need_name(names[0])
    _parse_flags(flag_toks, _CREATE_JOB_FLAGS)
    return {"rule": "create-job", "tier": "T1", "kind": "job",
            "name": names[0], "ns": ns}


def _k_rollout(tok: list[str], ns: str) -> dict:
    if len(tok) < 2 or tok[1] not in _ROLLOUT_SUBS:
        raise _Reject(f"rollout 子命令仅支持 {sorted(_ROLLOUT_SUBS)}")
    if len(tok) < 4:
        raise _Reject("用法: kubectl rollout <sub> <kind> <name>")
    if tok[2] not in _ROLLOUT_KINDS:
        raise _Reject(f"rollout 不支持资源 {tok[2]}"
                      f"（允许: {sorted(_ROLLOUT_KINDS)}）")
    _need_name(tok[3])
    return {"rule": _ROLLOUT_SUBS[tok[1]], "tier": "T2R", "kind": tok[2],
            "name": tok[3], "ns": ns}


def _k_patch(tok: list[str], ns: str) -> dict:
    if len(tok) < 3 or tok[1] not in _PATCH_KINDS:
        raise _Reject(f"patch 资源仅允许 {sorted(_PATCH_KINDS)}")
    _need_name(tok[2])
    parsed = _parse_flags(tok[3:], _PATCH_FLAGS)
    if "-p" not in parsed and "--patch" not in parsed:
        raise _Reject("patch 必须带 -p <JSON/YAML> 或 --patch=<JSON/YAML>")
    # patch secret/configmap 可改写凭据/配置，与 delete secret 同按 T2U 对齐
    tier = "T2U" if tok[1] in ("secret", "secrets", "configmap", "configmaps") \
        else "T2R"
    return {"rule": "patch", "tier": tier, "kind": tok[1],
            "name": tok[2], "ns": ns}


def _k_label(tok: list[str], ns: str) -> dict:
    if len(tok) != 4 or tok[1] not in _LABEL_KINDS:
        raise _Reject("用法: kubectl label <kind> <name> key=value"
                      f"（kind 仅允许 {sorted(_LABEL_KINDS)}）")
    if not _v_kv(tok[3]):
        raise _Reject(f"label 参数须为 key=value 形式: {tok[3]}")
    return {"rule": "label", "tier": "T2R", "kind": tok[1],
            "name": tok[2], "ns": ns}


def _k_annotate(tok: list[str], ns: str) -> dict:
    if len(tok) < 4 or tok[1] not in _LABEL_KINDS:
        raise _Reject("用法: kubectl annotate <kind> <name> key=value"
                      f"（kind 仅允许 {sorted(_LABEL_KINDS)}）")
    if not _v_annotate(" ".join(tok[3:])):
        raise _Reject(f"annotate 参数须为 key=value 形式: {' '.join(tok[3:])}")
    return {"rule": "annotate", "tier": "T2R", "kind": tok[1],
            "name": tok[2], "ns": ns}


def _k_cordon(tok: list[str], ns: str) -> dict:
    if len(tok) != 2 or not _NODE_NAME_RE.fullmatch(tok[1]):
        raise _Reject("用法: kubectl cordon|uncordon <node>")
    return {"rule": "cordon", "tier": "T2R", "kind": "node",
            "name": tok[1], "ns": ""}


def _k_apply(tok: list[str], ns: str, verb: str) -> dict:
    """shape: apply|replace -f <本机清单路径>。清单内容执行时经 stdin 上跳板机。"""
    try:
        idx = tok.index("-f")
    except ValueError:
        raise _Reject(f"{verb} 必须带 -f <本机清单路径>") from None
    if idx + 1 >= len(tok):
        raise _Reject("-f 缺少清单路径")
    path = tok[idx + 1]
    extra = tok[1:idx] + tok[idx + 2:]
    if extra:
        raise _Reject(f"{verb} 只接受 -f <path>，多余参数: {' '.join(extra)}")
    if not _PATH_RE.fullmatch(path) or ".." in path:
        raise _Reject(f"-f 路径不合法: {path}")
    return {"rule": f"{verb}-file", "tier": "T2U", "kind": "", "name": "",
            "ns": ns, "manifest": path}


def _split_names_flags(tokens: list[str], allowed: dict) -> tuple[list[str], list[str]]:
    """把 token 流拆成 (资源名列表, flag token 列表)。

    flag 与 name 的相对顺序不限（kubectl 本身允许 flag 出现在任意位置）；
    带值的 flag（如 --grace-period 5）其 value 一并归入 flag 流。
    """
    names: list[str] = []
    flags: list[str] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.startswith("-") and t != "-":
            flags.append(t)
            if "=" not in t and t in allowed and allowed[t] is not None \
                    and i + 1 < len(tokens):
                flags.append(tokens[i + 1])
                i += 2
                continue
            i += 1
        else:
            names.append(t)
            i += 1
    return names, flags


def _match_table(verb: str, tok: list[str], ns: str,
                 table: list[tuple[set[str], str, str]],
                 flags: dict) -> dict:
    """shape: <verb> <kind> <name> [flags 顺序不限]（delete/scale 共用）。"""
    if len(tok) < 3:
        raise _Reject(f"用法: kubectl {verb} <kind> <name> "
                      f"[-n ns]；已登记 kind: {_kinds_help(table)}")
    kind_tok = tok[1]
    for kinds, rule, tier in table:
        if kind_tok in kinds:
            names, flag_toks = _split_names_flags(tok[2:], flags)
            if len(names) != 1:
                raise _Reject(f"{verb} 需要恰好 1 个资源名，得到: "
                              f"{' '.join(names) if names else '（无）'}")
            _need_name(names[0])
            parsed = _parse_flags(flag_toks, flags)
            if verb == "scale" and "--replicas" not in parsed:
                raise _Reject("scale 必须带 --replicas=<n>")
            return {"rule": rule, "tier": tier, "kind": kind_tok,
                    "name": names[0], "ns": ns}
    raise _Reject(f"{verb} 不支持资源 {kind_tok}"
                  f"（已登记 kind: {_kinds_help(table)}）")


def _k_delete(tok: list[str], ns: str) -> dict:
    return _match_table("delete", tok, ns, _DELETE_KINDS, _DELETE_FLAGS)


def _k_scale(tok: list[str], ns: str) -> dict:
    return _match_table("scale", tok, ns, _SCALE_KINDS, _SCALE_FLAGS)


_KUBECTL_VERBS = {
    "create": _k_create, "delete": _k_delete, "scale": _k_scale,
    "rollout": _k_rollout, "patch": _k_patch, "label": _k_label,
    "annotate": _k_annotate, "cordon": _k_cordon, "uncordon": _k_cordon,
    "apply": _k_apply, "replace": _k_apply,
}


def _t3_check(verb: str, info: dict) -> None:
    """匹配结果上的字段级 T3 反模式检查（对内置表与 config 覆盖都生效）。

    之前只按 token 位置判断（tokens[2]），`delete namespace --now
    kube-system` 这类 flag 插入即可绕过；改为匹配出 kind/name 后再判，
    与 flag 位置无关。
    """
    if verb == "delete" \
            and str(info.get("kind", "")).lower() in ("namespace", "namespaces", "ns") \
            and str(info.get("name", "")) in _T3_CONTROL_PLANE_NS:
        raise _T3(f"删除控制面 namespace {info['name']} 属 T3 全局禁止")


def match_kubectl(argv: list[str]) -> dict:
    """匹配 kubectl 写命令；返回 {rule, tier, kind, name, ns[, manifest]}。

    argv 含前导 "kubectl"。匹配顺序：T3 结构判断（--all，任意位置）->
    白名单（config 覆盖则用 config 条目，否则内置声明表）-> 匹配结果再过
    字段级 T3 检查（内置表与 config 覆盖都生效）。
    """
    tokens = argv[1:]
    if not tokens:
        raise _Reject("kubectl 后需要子命令")
    ns, tokens = _extract_ns(tokens)
    if not tokens:
        raise _Reject("kubectl 后需要子命令")
    verb = tokens[0]
    # T3 结构判断：先于任何白名单表（含 config 覆盖）评估
    if verb == "delete":
        if any(t == "--all" or t.startswith("--all=") for t in tokens[1:]):
            raise _T3("kubectl delete --all 批量删除属 T3 全局禁止")
    cfg = _cfg_entries("kubectl")
    if cfg is not None:
        info = _match_cfg_kubectl(cfg, tokens[1:], ns, verb)
        if info is None:
            raise _Reject("config.write_whitelist.kubectl 已整体替换内置表，"
                          "该命令不在其中")
        _t3_check(verb, info)
        return info
    handler = _KUBECTL_VERBS.get(verb)
    if handler is None:
        raise _Reject(f"动词 {verb} 未登记（允许: {sorted(_KUBECTL_VERBS)}）")
    if verb in ("apply", "replace"):
        info = handler(tokens, ns, verb)
    else:
        info = handler(tokens, ns)
    _t3_check(verb, info)
    return info


# ----------------------------- node 白名单 -----------------------------
# 每条命令的形状逐字声明（token 数与取值域），没有的形状就是不允许。
_SYSTEMCTL_SUBS = {"restart", "start", "stop", "reload", "enable", "disable"}
# rm 的绝对禁区（T3）：整目录级目标，任何登记不可覆盖
_RM_FORBIDDEN = {"/", "/bin", "/boot", "/dev", "/etc", "/home", "/lib",
                 "/lib64", "/opt", "/proc", "/root", "/run", "/sbin",
                 "/sys", "/tmp", "/usr", "/var"}


def _safe_path(p: str) -> bool:
    return bool(_PATH_RE.fullmatch(p)) and ".." not in p and not p.startswith("-")


def match_node(tokens: list[str]) -> dict:
    """匹配 node 写命令；返回 {rule, tier}。tokens = shlex 后的参数。"""
    if not tokens:
        raise _Reject("node 写命令为空")
    cmd = tokens[0]
    cfg = _cfg_entries("node")
    if cfg is not None:
        info = _match_cfg_node(cfg, tokens)
        if info is None:
            raise _Reject("config.write_whitelist.node 已整体替换内置表，"
                          "该命令不在其中")
        return info
    if cmd == "rm":
        if any(t in ("-r", "-rf", "-fr", "-R", "--recursive") for t in tokens):
            raise _T3("rm 递归删除属 T3 全局禁止")
        if len(tokens) == 2:
            p = tokens[1]
            if p in _RM_FORBIDDEN:
                raise _T3(f"rm {p} 属 T3 全局禁止")
            if _safe_path(p):
                return {"rule": "rm-file", "tier": "T2U"}
        raise _Reject("rm 仅允许单文件: rm <path>（不允许递归/多目标/glob）")
    if cmd == "systemctl" and len(tokens) == 3 \
            and tokens[1] in _SYSTEMCTL_SUBS and _UNIT_RE.fullmatch(tokens[2]):
        return {"rule": "systemctl-lifecycle", "tier": "T2R"}
    if cmd == "truncate" and len(tokens) == 4 \
            and tokens[1] == "-s" and tokens[2] == "0" and _safe_path(tokens[3]):
        return {"rule": "truncate-log", "tier": "T2U"}
    if cmd == "journalctl" and len(tokens) == 2 \
            and re.fullmatch(r"--vacuum-(time|size)=[A-Za-z0-9]+", tokens[1]):
        return {"rule": "journal-vacuum", "tier": "T2U"}
    if cmd == "chown" and len(tokens) == 3 \
            and _OWNER_RE.fullmatch(tokens[1]) and _safe_path(tokens[2]):
        return {"rule": "chown", "tier": "T2U"}
    if cmd == "chmod" and len(tokens) == 3 \
            and re.fullmatch(r"[0-7]{3,4}", tokens[1]) and _safe_path(tokens[2]):
        return {"rule": "chmod", "tier": "T2U"}
    raise _Reject(f"node 写命令未登记: {' '.join(tokens)}（允许: "
                  "systemctl restart|start|stop|reload|enable|disable <unit>, "
                  "rm <file>, truncate -s 0 <file>, "
                  "journalctl --vacuum-time|size=<n>, chown <owner> <file>, "
                  "chmod <mode> <file>）")


# ----------------------------- db 白名单 -----------------------------
_DB_WRITE_KEYWORDS = {  # 首个关键字 -> (规则名, 层级)
    "UPDATE":   ("sql-dml", "T2U"),
    "INSERT":   ("sql-dml", "T2U"),
    "REPLACE":  ("sql-dml", "T2U"),
    "DELETE":   ("sql-dml", "T2U"),
    "ALTER":    ("sql-ddl", "T2U"),
    "REINDEX":  ("sql-ddl", "T2U"),
    "VACUUM":   ("sql-ddl", "T2U"),
    "ANALYZE":  ("sql-ddl", "T2U"),
    "TRUNCATE": ("sql-truncate", "T2U"),
}
_DB_SNAPSHOT_KEYWORDS = {"SELECT", "SHOW", "DESCRIBE", "DESC"}
# EXPLAIN 被剔除：PostgreSQL 的 EXPLAIN ANALYZE 会真正执行目标语句，
# 等于借快照通道绕过分层授权。


def match_db(sql: str) -> dict:
    """匹配 db 写 SQL；返回 {rule, tier}。多语句（含 ;）一律拒绝。"""
    if ";" in sql:
        raise _Reject("仅允许单条 SQL（检测到 ;，拒绝多语句拼接）")
    words = sql.split()
    if not words:
        raise _Reject("SQL 为空")
    first = words[0].upper()
    cfg = _cfg_entries("db")
    if cfg is not None:
        info = _match_cfg_db(cfg, sql, words)
        if info is None:
            raise _Reject("config.write_whitelist.db 已整体替换内置表，"
                          "该 SQL 不在其中")
        return info
    if first == "DROP":
        second = words[1].upper() if len(words) > 1 else ""
        if second in ("DATABASE", "SCHEMA"):
            raise _T3(f"DROP {second} 属 T3 全局禁止")
        if second == "TABLE":
            return {"rule": "sql-drop-table", "tier": "T2U"}
        raise _Reject("DROP 仅允许 TABLE（DATABASE/SCHEMA 属 T3）")
    if first in ("GRANT", "REVOKE"):
        raise _T3(f"{first} 属 T3 全局禁止")
    if first in _DB_WRITE_KEYWORDS:
        rule, tier = _DB_WRITE_KEYWORDS[first]
        return {"rule": rule, "tier": tier}
    raise _Reject(f"SQL 首关键字 {first} 未登记"
                  f"（允许写: {sorted(_DB_WRITE_KEYWORDS)} 与 DROP TABLE）")


# ----------------------------- config 覆盖（结构化条目） -----------------------------
def _cfg_entries(channel: str) -> list[dict] | None:
    """解析 config.remediation.write_whitelist.<channel>。

    条目为结构化声明（不是正则）：
      kubectl: {verb, kind, tier[, sub][, flags: [--x, ...]]}
      node:    {cmd, tier[, subs: [..]]}   首 token=cmd，其余 token 须为安全 token
      db:      {keyword, tier}             首关键字匹配
    出现该通道条目即**整体替换**内置表。字段错误直接 die（配置错误要在
    部署期暴露，不是运行期静默忽略）。
    """
    raw = (remediation_cfg().get("write_whitelist") or {}).get(channel)
    if not raw:
        return None
    if not isinstance(raw, list) or not raw:
        lib.die(f"config write_whitelist.{channel} 须为非空列表")
    for e in raw:
        if not isinstance(e, dict):
            lib.die(f"config write_whitelist.{channel} 条目须为 {key: value} 映射")
        tier = str(e.get("tier") or "").upper()
        if tier not in ("T1", "T2R", "T2U"):
            lib.die(f"config write_whitelist.{channel} 层级不合法: "
                    f"{tier}（应为 T1/T2R/T2U）")
        if channel == "kubectl":
            if not e.get("verb") or not e.get("kind"):
                lib.die("config kubectl 条目须含 verb/kind/tier"
                        "（可选 sub、flags）")
            for f in (e.get("flags") or []):
                if not str(f).startswith("--"):
                    lib.die(f"config kubectl flags 须为 -- 开头: {f}")
        elif channel == "node":
            if not e.get("cmd"):
                lib.die("config node 条目须含 cmd/tier（可选 subs）")
        else:
            if not e.get("keyword"):
                lib.die("config db 条目须含 keyword/tier")
    return raw


def _match_cfg_kubectl(entries: list[dict], tok: list[str], ns: str,
                       verb: str) -> dict | None:
    """tok 已剥离 verb；条目 verb 须与实际动词一致才算命中
    （否则登记 {verb: delete} 会放行 patch/expose/exec 等一切同形状命令）。"""
    for e in entries:
        if str(e.get("verb", "")).lower() != verb.lower():
            continue
        shape_rest = [str(e["sub"])] if e.get("sub") else []
        accepted = {str(e["kind"]), str(e["kind"]) + "s"}
        base = len(shape_rest)
        if len(tok) < base + 2 or tok[:base] != shape_rest:
            continue
        kind, name, flag_toks = tok[base], tok[base + 1], tok[base + 2:]
        if kind not in accepted or not _NAME_RE.fullmatch(name):
            continue
        try:
            _parse_flags(flag_toks, {f: _MAYBE for f in (e.get("flags") or [])})
        except _Reject:
            continue
        label = "-".join(x for x in (str(e["verb"]), str(e.get("sub") or ""),
                                     str(e["kind"])) if x)
        return {"rule": f"cfg-{label}", "tier": str(e["tier"]).upper(),
                "kind": kind, "name": name, "ns": ns}
    return None


def _match_cfg_node(entries: list[dict], tokens: list[str]) -> dict | None:
    for e in entries:
        if tokens[0] != str(e["cmd"]):
            continue
        subs = [str(s) for s in (e.get("subs") or [])]
        rest = tokens[1:]
        if subs:
            if not rest or rest[0] not in subs:
                continue
            rest = rest[1:]
        if len(rest) > 8 or not all(_SAFE_VAL_RE.fullmatch(t) for t in rest):
            continue
        return {"rule": f"cfg-{e['cmd']}", "tier": str(e["tier"]).upper()}
    return None


def _match_cfg_db(entries: list[dict], sql: str, words: list[str]) -> dict | None:
    for e in entries:
        if words[0].upper() == str(e["keyword"]).upper() and len(words) > 1:
            return {"rule": f"cfg-{e['keyword']}".lower(),
                    "tier": str(e["tier"]).upper()}
    return None


# ----------------------------- config: remediation -----------------------------
def remediation_cfg() -> dict:
    cfg = lib.load_config().get("remediation") or {}
    return cfg if isinstance(cfg, dict) else {}


def backup_dir() -> str:
    """Local backup directory (0700) for 0600 backup files.

    注意：备份是资源原样导出，Secret 对象会明文落盘——目录/文件权限只挡同机
    其他用户，不改变其敏感属性，按凭据数据对待。
    """
    d = os.path.expanduser(str(remediation_cfg().get("backup_dir") or DEFAULT_BACKUP_DIR))
    os.makedirs(d, exist_ok=True)
    os.chmod(d, 0o700)  # makedirs mode is umask-masked; enforce explicitly
    return d


def shared_cluster() -> bool:
    return bool(remediation_cfg().get("shared_cluster"))


# ----------------------------- guards -----------------------------
def enforce_tier(tier: str, confirm: bool, confirm_by: str) -> None:
    """Authorization gate. Exits with an actionable status on refusal."""
    if tier == "T3":
        print("status=TIER_T3_FORBIDDEN msg=该命令属 T3 全局禁止，任何授权不可覆盖")
        sys.exit(1)
    need_confirm = tier == "T2U" or (tier == "T2R" and shared_cluster())
    if need_confirm and not confirm:
        why = ("T2U 破坏性·不可恢复" if tier == "T2U"
               else "T2R 但 remediation.shared_cluster=true（共享/近生产集群）")
        print(f"status=NEEDS_AUTH_CONFIRM msg=该写操作属 {why}，需用户显式授权")
        print("  请先向用户说明操作与影响，获同意后加 --confirm（--confirm-by <授权人> 留痕）重试")
        sys.exit(1)
    if confirm and tier in ("T2U", "T2R") and not confirm_by:
        print("warn: 建议随 --confirm 提供 --confirm-by <授权人>，写入备份头供审计")


def write_timeout() -> int:
    """远端写命令（含快照）的执行读超时；remediation.write_timeout 可覆盖。"""
    raw = remediation_cfg().get("write_timeout")
    try:
        v = int(raw or DEFAULT_WRITE_TIMEOUT)
    except (TypeError, ValueError):
        lib.die(f"remediation.write_timeout 必须是数字: {raw}")
    if v <= 0:
        lib.die(f"remediation.write_timeout 必须为正数: {v}")
    return v


def _write_fail_detail(res, tmo: int) -> str:
    """WRITE_FAILED 的可行动信息：rc=255 且无 stderr 时点破「可能已生效」。"""
    detail = (res.stderr or "").strip()[:300]
    if not detail and res.returncode == 255:
        detail = (f"rc=255 无 stderr（连接中断或远端命令超时 {tmo}s）——"
                  "命令可能已在远端开始执行，勿盲目重试，先核对当前状态")
    return detail


def check_snapshot(channel: str, snapshot: str, bind: tuple | None = None) -> None:
    """Validate the mandatory pre-write snapshot is read-only by construction.

    bind=(kind, target) 时快照必须触碰同一目标（文件写 -> ls/ls -l/cat 同路径，
    systemctl 写 -> status/is-active/is-enabled 同 unit），否则「强制先备份」
    只是走形式（--snapshot uptime 能给 rm /etc/... 挡箭）。
    """
    if not snapshot or not snapshot.strip():
        print(f"status=SNAPSHOT_REQUIRED msg={channel} 通道强制先备份："
              "请显式提供 --snapshot \"<只读命令/SELECT>\"（其输出将存入备份目录）")
        sys.exit(1)
    cmd = snapshot.strip()
    if channel == "db":
        first = cmd.split()[0].upper()
        if ";" in cmd or first not in _DB_SNAPSHOT_KEYWORDS:
            lib.die(f"--snapshot 只允许单条只读 SQL（{_DB_SNAPSHOT_KEYWORDS}）: {cmd}")
        if re.search(r"\bINTO\b|\bFOR\s+UPDATE\b|\bNEXTVAL\s*\(|\bSETVAL\s*\(",
                     cmd, re.IGNORECASE):
            lib.die(f"--snapshot 拒绝含写/锁副作用的 SQL"
                    f"（INTO / FOR UPDATE / nextval / setval）: {cmd}")
        return
    # node channel：token 逐形状比对；命中后校验快照目标与写目标绑定
    t = cmd.split()

    def _bound(target) -> None:
        if bind is not None and target != bind[1]:
            lib.die(f"--snapshot 的目标 ({target}) 与写命令目标 ({bind[1]}) 不一致，"
                    f"快照必须针对同一目标取证: {cmd}")

    if t[:1] == ["cat"] and len(t) == 2:
        lib.validate_log_path(t[1])
        _bound(t[1])
        return
    if t[:1] == ["uptime"] or t in (["ps", "aux"], ["ps", "-ef"]):
        _bound("(uptime/ps)")
        return
    if t[:1] == ["free"] and len(t) == 2 and t[1] in ("-h", "-m"):
        _bound("(free)")
        return
    if t[:1] == ["df"] and len(t) <= 3 and all(x in ("-h", "-i") for x in t[1:]):
        _bound("(df)")
        return
    if t[:1] == ["systemctl"] and len(t) == 3 \
            and t[1] in ("status", "is-active", "is-enabled") \
            and _UNIT_RE.fullmatch(t[2]):
        _bound(t[2])
        return
    if t[:2] == ["systemctl", "list-units"] and len(t) <= 4 \
            and all(x == "--type=service" or re.fullmatch(r"--state=[a-z]+", x)
                    for x in t[2:]):
        _bound("(list-units)")
        return
    if t[:1] == ["ls"] and len(t) == 2 and _safe_path(t[1]):
        _bound(t[1])
        return
    if t[:2] == ["ls", "-l"] and len(t) == 3 and _safe_path(t[2]):
        _bound(t[2])
        return
    if t[:2] == ["journalctl", "-u"] and len(t) >= 3 and _UNIT_RE.fullmatch(t[2]):
        tail = t[3:]
        if tail in ([], ["--no-pager"]) or (
                len(tail) in (2, 3) and tail[0] == "-n"
                and re.fullmatch(r"[0-9]+", tail[1])
                and tail[2:] in ([], ["--no-pager"])):
            _bound(t[2])
            return
    lib.die(f"--snapshot 只允许白名单内只读命令（systemctl status|is-active|"
            f"is-enabled/list-units, ls, df, free, ps, uptime, journalctl -u, "
            f"cat<scan_dirs>）: {cmd}")


def _node_write_target(tokens: list[str]) -> tuple | None:
    """node 写命令的具体目标 -> ("path", <file>) / ("unit", <unit>)；无目标为 None。"""
    cmd = tokens[0] if tokens else ""
    if cmd == "rm" and len(tokens) == 2:
        return ("path", tokens[1])
    if cmd == "truncate" and len(tokens) == 4:
        return ("path", tokens[3])
    if cmd in ("chown", "chmod") and len(tokens) == 3:
        return ("path", tokens[2])
    if cmd == "systemctl" and len(tokens) == 3:
        return ("unit", tokens[2])
    return None


# ----------------------------- backup -----------------------------
def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")[:SLUG_MAX] or "op"


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _header(channel: str, command: str, rule: str, tier: str, confirm_by: str,
            targets: list[dict], snapshot: str = "") -> list[str]:
    lines = [
        "# remediation-backup v1",
        f"# channel: {channel}",
        f"# command: {command}",
        f"# rule: {rule}",
        f"# tier: {tier}",
        f"# authorized_by: {confirm_by}",
        f"# timestamp: {_now_iso()}",
    ]
    if snapshot:
        lines.append(f"# snapshot: {snapshot}")
    for t in targets:
        absent = " absent=true" if t.get("absent") else ""
        lines.append(f"# target: kind={t['kind']} name={t['name']} ns={t.get('ns') or ''}{absent}")
    return lines


def _write_backup(path: str, header: list[str], body: str) -> None:
    # backups may contain exported cluster objects -> 0600, not umask default
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(header) + "\n")
        if body.strip():
            fh.write(body.rstrip() + "\n")
    os.chmod(path, 0o600)


def _targets_from_manifest(path: str) -> list[dict]:
    """Parse a local manifest file into backup targets (no connection needed)."""
    full = os.path.expanduser(path)
    if not os.path.isfile(full):
        lib.die(f"-f 文件不存在: {path}")
    try:
        with open(full, encoding="utf-8") as fh:
            docs = [d for d in yaml.safe_load_all(fh) if isinstance(d, dict)]
    except Exception as exc:  # noqa: BLE001 — any parse failure must stop the write
        lib.die(f"-f 文件解析失败（不执行，防盲目 apply）: {exc}")
    targets = []
    for d in docs:
        kind = (d.get("kind") or "").strip()
        meta = d.get("metadata") or {}
        name = (meta.get("name") or "").strip()
        if not kind or not name:
            lib.die("-f 文件含缺少 kind/metadata.name 的文档，拒绝执行")
        targets.append({"kind": kind, "name": name,
                        "ns": (meta.get("namespace") or "").strip()})
    if not targets:
        lib.die(f"-f 文件没有任何可识别文档: {path}")
    return targets


def backup_kubectl(command: str, rule: str, tier: str, confirm_by: str,
                   targets: list[dict]) -> str:
    """Export current state of *targets*; return the backup file path.

    A target that does not exist is recorded as absent (rollback = delete).
    Any export failure other than NotFound aborts the write (BACKUP_FAILED).
    """
    for t in targets:
        res = lib.run_kubectl("get", t["kind"], t["name"],
                              *(["-n", t["ns"]] if t["ns"] else []),
                              "-o", "yaml", check=False, timeout=write_timeout())
        if res.returncode != 0:
            if "NotFound" in (res.stderr or ""):
                t["absent"] = True
                continue
            print(f"status=BACKUP_FAILED msg=备份失败（不执行写操作）: "
                  f"{t['kind']}/{t['name']}: {(res.stderr or '').strip()[:200]}")
            sys.exit(1)
        t["absent"] = False
        t["yaml"] = (res.stdout or "").strip()
    header = _header("kubectl", command, rule, tier, confirm_by, targets)
    parts = []
    for t in targets:
        if t.get("absent"):
            parts.append(f"# absent: {t['kind']}/{t['name']} did not exist")
        else:
            parts.append("---\n" + t["yaml"])
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(backup_dir(), f"{ts}-kubectl-{_slug(command)}.yaml")
    _write_backup(path, header, "\n".join(parts))
    return path


def _cap_lines(text: str) -> str:
    lines = text.splitlines()
    if len(lines) > SNAPSHOT_OUTPUT_LIMIT:
        lines = lines[:SNAPSHOT_OUTPUT_LIMIT] + \
            [f"# ... truncated at {SNAPSHOT_OUTPUT_LIMIT} lines"]
    return "\n".join(lines)


def _db_exec_args(args, client_cmd: list[str]) -> list[str]:
    """kubectl exec argv prefix + remote command, routed through run_kubectl."""
    argv = ["exec", args.pod]
    if args.namespace:
        argv += ["-n", args.namespace]
    if args.container:
        argv += ["-c", args.container]
    argv += ["--"] + client_cmd
    return argv


def _db_client_cmd(args, sql: str) -> list[str]:
    """Assemble the remote client command for psql/mysql (no password flags —
    authentication relies on the pod-side trust/secret mount, never on config)."""
    if args.client == "psql":
        cmd = ["psql"]
        if args.user:
            cmd += ["-U", args.user]
        if args.database:
            cmd += ["-d", args.database]
        cmd += ["-c", sql]
    else:
        cmd = ["mysql"]
        if args.user:
            cmd += ["-u", args.user]
        if args.database:
            cmd += [args.database]
        cmd += ["-e", sql]
    return cmd


# ----------------------------- channels -----------------------------
def preflight_connection() -> None:
    """Fail fast with NEEDS_MANUAL_AUTH before any backup/write is attempted."""
    conn = lib.load_connection()
    target = lib.bastion_target(conn)
    if lib.run_ssh(target, "true", timeout=conn.connect_timeout,
                   check=False).returncode != 0:
        print(f"status=NEEDS_MANUAL_AUTH bastion={conn.bastion_host}")
        print("  请运行: python3 scripts/setup_ssh.py local （然后等待，勿自行重试）")
        sys.exit(1)


def cmd_kubectl(args) -> int:
    # argv always carries the leading "kubectl" so match_kubectl sees the
    # full command shape and the backup target comes from the same parse.
    argv = ["kubectl", *args.args]
    command = " ".join(argv)
    try:
        info = match_kubectl(argv)
    except _T3 as exc:
        print(f"status=TIER_T3_FORBIDDEN msg={exc}")
        return 1
    except _Reject as exc:
        print(f"status=NOT_WHITELISTED msg={exc}")
        print("  如确属合法修复命令，请运维在 config.remediation.write_whitelist.kubectl 登记")
        return 1
    tier = info["tier"]
    enforce_tier(tier, args.confirm, args.confirm_by)
    if info.get("manifest"):
        targets = _targets_from_manifest(info["manifest"])
    else:
        targets = [{"kind": info["kind"], "name": info["name"], "ns": info["ns"]}]
    if args.dry_run:
        _print_plan("kubectl", command, tier, info["rule"], targets,
                    snapshot="（自动 kubectl get -o yaml）")
        print("status=DRY_RUN msg=仅输出计划，未连接、未备份、未执行")
        return 0
    preflight_connection()
    backup_path = backup_kubectl(command, info["rule"], tier, args.confirm_by, targets)
    print(f"backup={backup_path}")
    exec_args = argv[1:]  # run_kubectl prepends "kubectl"
    stdin_data = None
    if exec_args[0] in ("apply", "replace") and "-f" in exec_args:
        # -f manifests are local by contract (_targets_from_manifest already
        # read them); kubectl runs on the bastion where local paths are not
        # visible, so the manifest content is piped via stdin.
        idx = exec_args.index("-f")
        with open(os.path.expanduser(exec_args[idx + 1]), encoding="utf-8") as fh:
            stdin_data = fh.read()
        exec_args = exec_args[:idx] + ["-f", "-"] + exec_args[idx + 2:]
    res = lib.run_kubectl(*exec_args, check=False, stdin_data=stdin_data,
                          timeout=write_timeout())
    if res.returncode != 0:
        print(f"status=WRITE_FAILED msg={_write_fail_detail(res, write_timeout())}")
        print(f"  回滚/取证备份: {backup_path}")
        return 1
    print(f"status=WRITE_APPLIED tier={tier} rule={info['rule']}")
    print("  下一步复验: python3 scripts/verify.py --file <checks.yaml>")
    print(f"  复验不通过回滚: python3 scripts/apply.py --confirm rollback {backup_path}")
    return 0


def cmd_node(args) -> int:
    command = " ".join(args.node_command)
    tokens = shlex.split(command)
    try:
        info = match_node(tokens)
    except _T3 as exc:
        print(f"status=TIER_T3_FORBIDDEN msg={exc}")
        return 1
    except _Reject as exc:
        print(f"status=NOT_WHITELISTED msg={exc}")
        print("  如确属合法修复命令，请运维在 config.remediation.write_whitelist.node 登记")
        return 1
    tier = info["tier"]
    enforce_tier(tier, args.confirm, args.confirm_by)
    check_snapshot("node", args.snapshot, bind=_node_write_target(tokens))
    if args.dry_run:
        _print_plan("node", command, tier, info["rule"],
                    [{"kind": "node", "name": args.node, "ns": ""}],
                    snapshot=args.snapshot)
        print("status=DRY_RUN msg=仅输出计划，未连接、未备份、未执行")
        return 0
    preflight_connection()
    node_ip = lib.resolve_node_ip(args.node)
    snap_res = lib.execute_on_node(node_ip, *shlex.split(args.snapshot),
                                   capture=True, check=False,
                                   timeout=write_timeout())
    if snap_res.returncode != 0:
        print(f"status=BACKUP_FAILED msg=快照命令失败（不执行写操作）: "
              f"{(snap_res.stderr or '').strip()[:200]}")
        sys.exit(1)
    targets = [{"kind": "node", "name": args.node, "ns": ""}]
    header = _header("node", command, info["rule"], tier, args.confirm_by, targets,
                     snapshot=args.snapshot)
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(backup_dir(), f"{ts}-node-{_slug(command)}.snap")
    _write_backup(path, header, _cap_lines(snap_res.stdout or ""))
    print(f"backup={path}")
    res = lib.execute_on_node(node_ip, *args.node_command, capture=True,
                              check=False, timeout=write_timeout())
    if res.returncode != 0:
        print(f"status=WRITE_FAILED msg={_write_fail_detail(res, write_timeout())}")
        print(f"  快照取证: {path}（node 通道无自动回滚，按快照手工恢复）")
        return 1
    print(f"status=WRITE_APPLIED tier={tier} rule={info['rule']}")
    print("  下一步复验: python3 scripts/verify.py --file <checks.yaml>")
    print(f"  node 通道无自动回滚；快照: {path}")
    return 0


def cmd_db(args) -> int:
    sql = args.sql.strip().rstrip(";").strip()
    try:
        info = match_db(sql)
    except _T3 as exc:
        print(f"status=TIER_T3_FORBIDDEN msg={exc}")
        return 1
    except _Reject as exc:
        print(f"status=NOT_WHITELISTED msg={exc}")
        print("  如确属合法修复 SQL，请运维在 config.remediation.write_whitelist.db 登记")
        return 1
    tier = info["tier"]
    enforce_tier(tier, args.confirm, args.confirm_by)
    check_snapshot("db", args.snapshot)
    write_cmd = _db_client_cmd(args, sql)
    snap_cmd = _db_client_cmd(args, args.snapshot)
    if args.dry_run:
        _print_plan("db", f"{args.client} -c/e {sql}", tier, info["rule"],
                    [{"kind": "pod", "name": args.pod, "ns": args.namespace}],
                    snapshot=args.snapshot)
        print("status=DRY_RUN msg=仅输出计划，未连接、未备份、未执行")
        return 0
    preflight_connection()
    snap_res = lib.run_kubectl(*_db_exec_args(args, snap_cmd), check=False,
                               timeout=write_timeout())
    if snap_res.returncode != 0:
        print(f"status=BACKUP_FAILED msg=快照 SQL 失败（不执行写操作）: "
              f"{(snap_res.stderr or '').strip()[:200]}")
        sys.exit(1)
    targets = [{"kind": "pod", "name": args.pod, "ns": args.namespace}]
    header = _header("db", f"{args.client} -c/e {sql}", info["rule"], tier,
                     args.confirm_by, targets, snapshot=args.snapshot)
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(backup_dir(), f"{ts}-db-{_slug(sql)}.snap")
    _write_backup(path, header, _cap_lines(snap_res.stdout or ""))
    print(f"backup={path}")
    res = lib.run_kubectl(*_db_exec_args(args, write_cmd), check=False,
                          timeout=write_timeout())
    if res.returncode != 0:
        print(f"status=WRITE_FAILED msg={_write_fail_detail(res, write_timeout())}")
        print(f"  快照取证: {path}（db 通道无自动回滚，按快照手工恢复）")
        return 1
    print(f"status=WRITE_APPLIED tier={tier} rule={info['rule']}")
    print("  下一步复验: python3 scripts/verify.py --file <checks.yaml>")
    print(f"  db 通道无自动回滚；快照: {path}")
    return 0


# ----------------------------- rollback -----------------------------
_HEADER_KV_RE = re.compile(r"^# ([a-z_]+): (.*)$", re.M)
_TARGET_RE = re.compile(r"^# target: kind=(\S+) name=(\S+) ns=(\S*)( absent=true)?$",
                        re.M)

# Server-managed fields that make an exported object non-reappliable verbatim.
_APPLY_STRIP_META = ("uid", "resourceVersion", "creationTimestamp", "generation",
                     "managedFields", "selfLink", "ownerReferences")
_APPLY_STRIP_LABELS = ("controller-uid", "batch.kubernetes.io/controller-uid",
                       "job-name", "batch.kubernetes.io/job-name")


def _sanitize_for_apply(text: str) -> str:
    """Make exported YAML re-appliable (rollback only; backups stay verbatim).

    Exported workload objects cannot be applied as-is: they carry uid/
    resourceVersion/managedFields/status and Job controller-uid labels, which
    the API server rejects on a new object. Strip those in-memory before
    piping to ``kubectl apply -f -``; the backup file on disk keeps the exact
    exported state for audit.
    """
    docs = [d for d in yaml.safe_load_all(text) if isinstance(d, dict)]
    for d in docs:
        meta = d.get("metadata") or {}
        for k in _APPLY_STRIP_META:
            meta.pop(k, None)
        ann = meta.get("annotations")
        if isinstance(ann, dict):
            ann.pop("kubectl.kubernetes.io/last-applied-configuration", None)
            if not ann:
                meta.pop("annotations", None)
        d.pop("status", None)
        d["metadata"] = meta
        if str(d.get("kind", "")).lower() == "job":
            spec = d.get("spec") or {}
            spec.pop("selector", None)  # controller regenerates it from the new uid
            d["spec"] = spec
            for holder in (meta, ((spec.get("template") or {}).get("metadata") or {})):
                labels = holder.get("labels")
                if isinstance(labels, dict):
                    for k in _APPLY_STRIP_LABELS:
                        labels.pop(k, None)
                    if not labels:
                        holder.pop("labels", None)
    return "\n---\n".join(yaml.safe_dump(d, sort_keys=False) for d in docs)


def cmd_rollback(args) -> int:
    full = os.path.realpath(os.path.expanduser(args.file))
    root = os.path.realpath(backup_dir())
    if not full.startswith(root + os.sep):
        print(f"status=ERROR msg=rollback 只接受备份目录内的文件: {args.file}")
        return 1
    if not os.path.isfile(full):
        print(f"status=ERROR msg=备份文件不存在: {args.file}")
        return 1
    with open(full, encoding="utf-8") as fh:
        text = fh.read()
    head = text.split("\n---", 1)[0]
    meta = dict(_HEADER_KV_RE.findall(head))
    if meta.get("channel") != "kubectl":
        print(f"status=ROLLBACK_UNSUPPORTED msg={meta.get('channel', '?')} 通道无自动回滚")
        print(f"  请按备份内的快照手工恢复，并把恢复过程记入报告「修复执行记录」: {args.file}")
        return 2
    targets = [{"kind": m.group(1), "name": m.group(2), "ns": m.group(3),
                "absent": bool(m.group(4))}
               for m in _TARGET_RE.finditer(head)]
    if not targets:
        print(f"status=ERROR msg=备份文件缺少 target 头，无法回滚: {args.file}")
        return 1
    # rollback is itself a write -> T2U authorization, and backup-first applies
    if not args.confirm:
        print("status=NEEDS_AUTH_CONFIRM msg=rollback 属 T2U（还原会覆盖当前态），"
              "需用户显式授权：加 --confirm（--confirm-by <授权人>）重试")
        return 1
    preflight_connection()
    # backup current state first (uniform backup-first for every write)
    pre_targets = [{"kind": t["kind"], "name": t["name"], "ns": t["ns"]}
                   for t in targets]
    pre_path = backup_kubectl(f"rollback {os.path.basename(args.file)}",
                              "rollback", "T2U", args.confirm_by, pre_targets)
    print(f"backup={pre_path} (回滚前的当前态)")
    body = text.split("\n---", 1)[1].strip() if "\n---" in text else ""
    for t in targets:
        ns_args = ["-n", t["ns"]] if t["ns"] else []
        if t["absent"]:
            # the object was created by the remediation -> remove it again
            res = lib.run_kubectl("delete", t["kind"], t["name"], *ns_args, check=False)
        else:
            # kubectl runs on the bastion; sanitize then pipe via stdin
            res = lib.run_kubectl("apply", "-f", "-", check=False,
                                  stdin_data=_sanitize_for_apply(body))
        if res.returncode != 0:
            print(f"status=WRITE_FAILED msg=回滚失败 {t['kind']}/{t['name']}: "
                  f"{(res.stderr or '').strip()[:200]}")
            return 1
    print(f"status=ROLLBACK_APPLIED source={args.file}")
    print("  请重新复验: python3 scripts/verify.py --file <checks.yaml>")
    print("  注意: 回滚为 apply 合并语义，写操作新增的 label/annotation/patch "
          "字段不会被撤销（备份保留了导出原文）；需完整还原时按备份内容手工 "
          "replace --force")
    return 0


# ----------------------------- plan / parser -----------------------------
def _print_plan(channel: str, command: str, tier: str, rule: str,
                targets: list[dict], snapshot: str) -> None:
    print(f"plan: channel={channel} tier={tier} rule={rule}")
    print(f"command: {command}")
    for t in targets:
        print(f"backup-target: {t['kind']}/{t['name']}"
              + (f" ns={t['ns']}" if t.get("ns") else ""))
    print(f"snapshot: {snapshot}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="apply.py",
        description="写操作统一入口（白名单 + 授权分层 + 强制先备份）。"
                    "全局参数（--dry-run/--confirm/--confirm-by）放在子命令之前。")
    parser.add_argument("--dry-run", action="store_true",
                        help="只校验白名单/层级并输出计划，不连接、不备份、不执行")
    parser.add_argument("--confirm", action="store_true",
                        help="T2U（或共享集群的 T2R）所需的显式授权标记")
    parser.add_argument("--confirm-by", default="",
                        help="授权人留痕（写入备份头，供审计）")
    sub = parser.add_subparsers(dest="channel", required=True)

    p = sub.add_parser("kubectl", help="kubectl 写命令（集群通道）")
    p.add_argument("args", nargs=argparse.REMAINDER, metavar="kubectl-args")

    p = sub.add_parser("node", help="节点写命令（SSH 通道，经跳板机；--snapshot 须在 <node> 前）")
    p.add_argument("--snapshot", default="",
                   help="强制：写前只读快照命令（systemctl status/ls/df/...）")
    p.add_argument("node")
    p.add_argument("node_command", nargs=argparse.REMAINDER, metavar="command")

    p = sub.add_parser("db", help="数据库写 SQL（DB 通道，经 kubectl exec 进 pod）")
    p.add_argument("pod")
    p.add_argument("sql", help="单条 SQL 语句（整体加引号）")
    p.add_argument("-n", "--namespace", default="")
    p.add_argument("-c", "--container", default="")
    p.add_argument("--client", default="psql", choices=["psql", "mysql"])
    p.add_argument("--user", default="")
    p.add_argument("--database", default="")
    p.add_argument("--snapshot", default="",
                   help="强制：写前只读 SELECT 快照，输出存入备份目录")

    p = sub.add_parser("rollback",
                       help="按备份文件回滚（kubectl 通道自动；node/db 提示手工）")
    p.add_argument("file", help="apply.py 产出的备份文件（须在 backup_dir 内）")
    return parser


def _locate_channel(raw: list[str]) -> tuple[int, str | None]:
    """定位通道子命令的位置（跳过全局 flag；--confirm-by 带一个值）。"""
    i = 0
    while i < len(raw):
        t = raw[i]
        if t in ("kubectl", "node", "db", "rollback"):
            return i, t
        if t == "--confirm-by":
            i += 2
            continue
        if t.startswith("-"):
            i += 1
            continue
        break  # 未知的裸 token，交回 argparse 报错
    return -1, None


def main() -> int:
    raw = sys.argv[1:]
    parser = build_parser()
    # argparse 的 REMAINDER 对以 "-" 开头的参数有解析缺陷（误当选项报
    # unrecognized），kubectl 通道在「kubectl」处手动切开，其余原样透传。
    idx, channel = _locate_channel(raw)
    if channel == "kubectl":
        args = parser.parse_args([*raw[:idx], "kubectl"])
        args.args = raw[idx + 1:]
        return cmd_kubectl(args)
    args = parser.parse_args(raw)
    if args.channel == "kubectl":
        if not args.args:
            lib.die("kubectl 通道需要子命令参数，如: apply.py kubectl delete pod x -n y")
        return cmd_kubectl(args)
    if args.channel == "node":
        if not args.node_command:
            lib.die("node 通道需要写命令，如: "
                    "apply.py node --snapshot 'systemctl status kubelet' n1 systemctl restart kubelet")
        if not _NODE_NAME_RE.fullmatch(args.node):
            lib.die(f"节点名不合法: {args.node}")
        return cmd_node(args)
    if args.channel == "db":
        if not _NAME_RE.fullmatch(args.pod):
            lib.die(f"pod 名不合法: {args.pod}")
        if args.namespace and not _NAMESPACE_RE.fullmatch(args.namespace):
            lib.die(f"namespace 不合法: {args.namespace}")
        if args.container and not _NAME_RE.fullmatch(args.container):
            lib.die(f"容器名不合法: {args.container}")
        if args.user and not re.fullmatch(r"[A-Za-z0-9_-]+", args.user):
            lib.die(f"--user 不合法: {args.user}")
        if args.database and not _TABLE_RE.fullmatch(args.database):
            lib.die(f"--database 不合法: {args.database}")
        return cmd_db(args)
    return cmd_rollback(args)


if __name__ == "__main__":
    sys.exit(main())
