#!/usr/bin/env python3
"""skillormcp -- 落点审计扫描器（advisory，只报不改）。

扫描给定目录（skill / mcp / skills 全仓）中的 "MCP 形" 信号，判断：
  (a) 某个 skill 是否疑似在干 MCP 的活（持证 / 连内网 / 有状态连接 / 万能 tool / 对外写操作）；
  (b) 某个 mcp 是否命中 MCP 反模式（暴露万能 tool、把凭证作为 tool 参数、写 tool 无审批令牌）。

设计边界（与 SKILL.md 一致）：
  - 只报信号、不裁决发布范围：内网/私有地址仅作事实信号提示，是否适合公开仓库由人判断。
  - 凭证检测分级：字面凭证值 = 红线；环境变量引用 / 从安全源读取 = 合规提示；提及 = 提示。
  - 报告对字面凭证值脱敏（只显 key=***），不把密钥原样打印；且仅在判定为字面值行上遮罩。
  - 反向 MCP 审计（仅 Python，AST 精判）：识别 @*.tool 装饰函数的凭证类参数名（无视类型标注）。
      * Python：优先 AST 精确解析；若文件语法错误 AST 失败，**回退正则粗扫**并明确提示
        「该文件无法 AST 解析，已回退正则（可能漏检跨行签名）」，绝不静默通过。
      * 非 Python（TypeScript/JavaScript 等）不做反向 MCP 审计（AST 仅支持 Python；团队只用
        Python，忽略其它语言）。普通规则（写操作/内网/万能tool）仍对所有文件生效。
  - 凭证参数名分强弱两级：
      * 强（几乎确定是凭证，报红线 MCP反模式）：password/secret/api_key/private_key/
        access_token/client_secret/bearer ……
      * 弱（歧义，降级为提示 MCP可疑参数）：裸 token、credentials（分页 token 等合法用途）。
      * 白名单（明确不报）：page_token、next_token、cursor、csrf_token。
  - 只豁免扫描器【自身包路径】（自审干净），被审计 skill 自带的 templates/ 正常纳入审计，
    不存在「按目录名全局豁免」的绕过口。
  - 判定口径与 SKILL.md 一致: MCP = Agent SDK 的能力面, 技能 = 用法面; 命中硬缺口
    (凭证/连通性/强制性)无逃生舱, 但低频场景可走成本维度换部署形态(需人工确认)。

用法:
    python audit_placement.py <path>          # 扫描单个目录或 skills/ 全仓
    python audit_placement.py --self-test     # 内置全部断言

退出码: 0 = 无高危(红线)信号; 1 = 发现错位/反模式(供人工复核, 非阻断); 2 = 路径不存在
"""
import argparse
import ast
import os
import re
import sys
import tempfile
import shutil

# ----------------------------------------------------------------------------
# 扫描后缀（覆盖真实放凭证的文件）
# ----------------------------------------------------------------------------
SCAN_SUFFIXES = (
    ".md", ".py", ".sh", ".yaml", ".yml", ".txt", ".json", ".js", ".ts",
    ".env", ".toml", ".ini", ".cfg", ".conf", ".properties",
)
# 无后缀也要扫的常见文件
SCAN_NOEXT_NAMES = {"Dockerfile", "Makefile", "Jenkinsfile", "LICENSE", "AUTHORS"}

# 跳过目录（按路径组件，跨平台）：版本库/依赖/缓存。
# 注意：不再含 "templates" —— 被审计 skill 的 templates/ 必须正常审计，
# 自审干净只靠「豁免自身包路径」实现，不存在全局目录名绕过口。
SKIP_DIR_PARTS = {".git", "node_modules", "__pycache__"}

# 扫描器自身包路径（其 SKILL.md/scripts/templates 不纳入审计，保证自审干净）
SCANNER_PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ----------------------------------------------------------------------------
# 凭证：结构化检测（分级）
# ----------------------------------------------------------------------------
# 已知前缀（确定字面值，命中即红线）
CRED_PREFIX = [
    r"AKIA[0-9A-Z]{16,}",
    r"sk-[A-Za-z0-9_-]{10,}",          # 注意 < / { 占位不匹配（不在字符类内）
    r"ghp_[A-Za-z0-9]{20,}",
    r"xox[baprs]-[A-Za-z0-9-]{10,}",
    r"Bearer\s+[A-Za-z0-9._~-]{10,}",
    r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
    r"-----BEGIN (?:RSA |EC |OPENSSH |PGP |DSA )?PRIVATE KEY-----",
    # Authorization: Basic/Bearer（兼容 "Authorization": 字典形式与裸 Authorization: 形式）
    r'(?:"?Authorization"?)\s*:\s*"?\s*(?:Basic|Bearer)\s+[A-Za-z0-9._~+/=+-]{6,}',
]
# 连接串内嵌凭证（user:pass@host）
CRED_CONNSTR = re.compile(r"[a-zA-Z][\w+.\-]*://[^\s:@/]+:[^\s:@/]+@")
# 赋值式 key（用于区分字面值 / 引用 / 类型标注 / 函数调用）
CRED_KEY = (r"(?:password|passwd|pwd|passphrase|secret|client[_-]?secret|"
            r"api[_-]?key|apikey|private[_-]?key|access[_-]?token|"
            r"refresh[_-]?token|token|auth[_-]?token)")
CRED_ASSIGN = re.compile(CRED_KEY + r"\s*[:=]\s*(\S+)", re.IGNORECASE)
# 合规引用前缀（环境变量 / 占位 / 从安全源读取）
CRED_REF_RE = re.compile(r"^(os\.environ|getenv|\$\{|<|\>)", re.IGNORECASE)
# 环境变量名形态：全大写 + 至少一个下划线分隔（SERVICE_API_TOKEN / API_KEY）。
# 这是「引用名」而非「凭证值」——config 里常见 credential_refs: {key: ENV_NAME}。
# 保守取舍：要求下划线，否则 PASSWORD123 / HUNTER2 这类大写口令会被误放行。
# 已知边界：含下划线的全大写口令（PASSWORD_X）仍会被放行，需人工复核。
CRED_ENVNAME_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")
# 占位符形态：<ENV_VAR_NAME> / <your-token> 这类尖括号包裹的模板值。
# 与 CRED_REF_RE 的 ^< 只覆盖「以 < 开头」，这里补齐「整串被 <...> 包裹」。
CRED_PLACEHOLDER_RE = re.compile(r"^<[^<>]+>$")
CRED_SAFE_CALL_RE = re.compile(r"^(secrets\.|_secret|vault|os\.environ|getenv)", re.IGNORECASE)
CRED_TYPE_RE = re.compile(r"^(str|int|bool|float|Optional|List|Dict|Union|Any|None)\b")
CRED_MENTION_WORD = re.compile(r"\b(凭证|credential|密钥|私钥|口令|密码)\b")

# 反向 MCP 审计：@*.tool 装饰函数的凭证类参数名（无视类型标注）
# 拆为强/弱两级 + 白名单：
#  - 强（几乎确定是凭证，报红线 MCP反模式）
#  - 弱（歧义：裸 token / credentials，降级为提示 MCP可疑参数）
#  - 白名单（分页/防伪造等合法用途，明确不报）
CRED_PARAM_STRONG_RE = re.compile(
    r"^(?:password|passwd|pwd|passphrase|secret|secret[_-]?key|client[_-]?secret|"
    r"credential|api[_-]?key|apikey|private[_-]?key|access[_-]?token|"
    r"refresh[_-]?token|auth[_-]?token|bearer)$", re.IGNORECASE)
CRED_PARAM_WEAK_RE = re.compile(r"^(?:token|credentials)$", re.IGNORECASE)
PARAM_WHITELIST = {"page_token", "next_token", "cursor", "csrf_token",
                   "device_token", "push_token"}

# mcp 上下文识别（仅 Python 信号；非 Python 文件不做反向审计）
MCP_CONTEXT_RE = re.compile(
    r"FastMCP|mcp\.run|@mcp\.tool|@server\.tool|from fastmcp|server\.tool")

# 非 Python 源文件后缀：反向参数审计仅支持 Python AST，这些语言的 MCP 文件跳过该审计，
# 但需在报告「人工确认项」里提示「反向参数审计未执行，请人工复核」（与 SKILL.md 报告格式一致）。
# 不含 .md/.json/.yaml 等文档/配置：它们不是 tool 函数签名，反向审计本就不适用，避免误报噪声。
CODE_EXTS = {".ts", ".tsx", ".js", ".jsx", ".go", ".java", ".rb", ".php",
             ".cs", ".rs", ".c", ".cpp", ".h", ".hpp", ".kt", ".swift", ".scala", ".m"}


def classify_param(name):
    """把 tool 函数参数名分类为 'strong' / 'weak' / None（白名单或无关）。"""
    n = name.lower().strip()
    if n in PARAM_WHITELIST:
        return None
    if CRED_PARAM_STRONG_RE.match(n):
        return "strong"
    if CRED_PARAM_WEAK_RE.match(n):
        return "weak"
    return None


def scan_cred(line):
    """返回该行命中的凭证类别集合。分级：字面值=红线；引用/提及=提示。"""
    cats = set()
    # 1) 已知前缀（确定字面值）
    for pat in CRED_PREFIX:
        if re.search(pat, line, re.IGNORECASE):
            cats.add("凭证字面值")
            return cats
    # 2) 连接串内嵌凭证（确定字面值）
    if CRED_CONNSTR.search(line):
        cats.add("凭证字面值")
        return cats
    # 3) 赋值结构分级
    m = CRED_ASSIGN.search(line)
    if m:
        val = m.group(1)
        # 先剥掉引号再判定：引号本身不改变「这是不是凭证值」的判断
        bare = val.strip()
        if len(bare) >= 2 and bare[0] in "\"'" and bare[-1] == bare[0]:
            bare = bare[1:-1]
        if CRED_REF_RE.match(val):
            cats.add("凭证提及")          # 环境变量/占位引用，合规
        elif CRED_PLACEHOLDER_RE.match(bare):
            cats.add("凭证提及")          # <ENV_VAR_NAME> 模板占位，合规
        elif CRED_ENVNAME_RE.match(bare):
            cats.add("凭证提及")          # SERVICE_API_TOKEN 这类环境变量名，合规
        elif re.match(r"^[\"']", val):
            cats.add("凭证字面值")        # 引号包裹值（含开引号未闭合）
        elif CRED_TYPE_RE.match(val):
            pass                          # 类型标注（: str），不报
        elif CRED_SAFE_CALL_RE.match(val):
            pass                          # 从安全源读取（secrets./_secret/vault），合规
        elif re.match(r"^[A-Za-z_][\w.]*\(", val):
            cats.add("凭证提及")          # 未知函数调用，保守提示而非红线
        else:
            cats.add("凭证字面值")        # 无引号字面量（如 password=abc123）
    # 4) 中文提及词
    if CRED_MENTION_WORD.search(line):
        cats.add("凭证提及")
    return cats


# 内网/连通性（红线：执行位置受限 / 连通性缺口 → 建议 MCP）
# 私有 IP 必须是完整四段且落在私有范围，避免误中版本号（10.15.2 / 3.10.4.5）。
NETWORK = [
    r"\bssh\b", r"\bscp\b", r"\bkubectl\b", r"\bkubeconfig\b",
    r"\b192\.168\.\d{1,3}\.\d{1,3}\b",
    r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b",
    r"\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b",
    r"\bjump\s*host\b", r"\bjumphost\b", r"\b跳板机\b", r"\b跳板\b",
    r"\b(vpn|专线)\b",
]

# 有状态/常驻（红线）
STATEFUL = [
    r"token\s*刷新", r"刷新\s*token", r"长连接",
    r"\bwebsocket\b", r"订阅", r"\bsubscription\b", r"常驻", r"持久化连接",
]

# 万能 tool（红线）—— eval 排除方法调用（model.eval()）
UNIVERSAL = [
    r"\bexecute_sql\b", r"\brun_shell\b", r"\bos\.system\b",
    r"(?<!\.)eval\s*\(", r"\bexec\s*\(",
]

# 写操作（红线：对外写 / 不可逆 → 需硬护栏）
WRITE_OP = [
    r"requests\.(post|put|patch|delete)\b",
    r"httpx\.(post|put|patch|delete)\b",
    r"curl\s+[^\n]*-X\s+(?:POST|PUT|PATCH|DELETE)\b",
    r"\bDELETE\s+FROM\b", r"\bINSERT\s+INTO\b", r"\bUPDATE\s+\w+\s+SET\b",
    r"\bDROP\s+TABLE\b", r"\bTRUNCATE\s+(?:TABLE\s+)?\w+", r"\bALTER\s+TABLE\b",
    r"terraform\s+apply\b", r"git\s+push\b",
    r"\bdocker\s+(?:rm|rmi|stop|exec)\b",
    r"shutil\.rmtree\b", r"os\.remove\b", r"os\.unlink\b", r"\.rmtree\b",
    r"\bkubectl\s+(?:apply|delete|create|patch|scale|rollout)\b",
    r"\brm\s+-rf\b",
]

# 规则表：(类别, 严重度, 正则列表)。凭证由 scan_cred 单独处理；MCP 反模式由 AST + 正则 + scan_mcp_antipattern 处理。
RULES = [
    ("内网/连通性", "红线", NETWORK),
    ("有状态/常驻", "红线", STATEFUL),
    ("万能tool", "红线", UNIVERSAL),
    ("写操作", "红线", WRITE_OP),
]

# 反向 MCP 审计：凭证作为 tool 参数（内联 keyword 形式，非类型/非引用/非安全调用才报）。
# 注意：tool 函数【签名】参数名由 AST / 正则处理（无视类型标注），这里只处理内联调用写法。
MCP_ANTIPATTERN_KEY = re.compile(
    r"\b(password|secret|api[_-]?key|access[_-]?token|private[_-]?key|"
    r"client[_-]?secret)\s*[:=]\s*(\S+)", re.IGNORECASE)


def scan_mcp_antipattern(line):
    """仅 mcp 上下文调用。凭证作显式参数（非类型/非引用/非安全调用）才报。"""
    m = MCP_ANTIPATTERN_KEY.search(line)
    if not m:
        return False
    val = m.group(2)
    if CRED_TYPE_RE.match(val):
        return False                       # 类型标注 password: str
    bare = val.strip()
    if len(bare) >= 2 and bare[0] in "\"'" and bare[-1] == bare[0]:
        bare = bare[1:-1]
    if CRED_REF_RE.match(val) or CRED_SAFE_CALL_RE.match(val):
        return False                       # 环境变量/占位/安全源读取，合规
    if CRED_PLACEHOLDER_RE.match(bare) or CRED_ENVNAME_RE.match(bare):
        return False                       # 占位符 / 环境变量名形态，合规
    if re.match(r"^[\"']", val):
        return False                       # 引号值由 scan_cred 处理
    return True


# tool 装饰器判定：必须是「.tool」成员调用(mcp.tool / server.tool)或裸 @tool。
# 不许用 "tool" in src 宽匹配 —— functools.wraps 里的 "tools" 会把 @functools.wraps
# 装饰的 wrapper 误判成 tool(端到端物化冒烟实测命中)。
TOOL_DECORATOR_RE = re.compile(r"(?:\A|\.)tool(?:\s*\(|\s*\Z)", re.IGNORECASE)


def _is_tool_decorator(dec_src: str) -> bool:
    return bool(TOOL_DECORATOR_RE.search(dec_src.strip()))


def scan_mcp_tool_params(text):
    """AST 解析 mcp 上下文里 @*.tool 装饰函数的参数名（无视类型标注）。

    参数名属强凭证类 -> 报 MCP反模式 红线；弱类 -> MCP可疑参数 提示。
    返回 (findings, ok)：
      findings: {行号: {"strong": set, "weak": set}}
      ok:       True=AST 成功；False=语法错误，调用方应回退正则扫描。
    非 tool 装饰函数不处理(装饰器按 TOOL_DECORATOR_RE 判定, 排除 functools.wraps 等)。
    """
    findings = {}
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return findings, False
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        is_tool = False
        for dec in node.decorator_list:
            try:
                src = ast.unparse(dec)
            except Exception:
                src = ""
            if _is_tool_decorator(src):
                is_tool = True
                break
        if not is_tool:
            continue
        for arg in list(node.args.args) + list(node.args.kwonlyargs):
            if arg.arg in ("self", "cls", "ctx", "context"):
                continue
            kind = classify_param(arg.arg)
            if kind:
                bucket = findings.setdefault(node.lineno, {"strong": set(), "weak": set()})
                bucket[kind].add(arg.arg)
    return findings, True


# 正则回退：仅用于 Python 语法错误（AST 失败）场景。
# 提取函数签名里的参数名，再按 classify_param 分级。不要求闭合 ')'（写到一半的签名也抓），
# 无法跨行签名，仅作降级兜底。
FUNC_SIG_RE = re.compile(
    r"\b(?:async\s+)?def\s+[A-Za-z_]\w*\s*\(([^()]*)", re.IGNORECASE)


def _extract_param_names(arglist):
    """从参数列表串里提取参数标识符，跳过嵌套括号（默认值/解构）。"""
    names = []
    depth = 0
    cur = ""
    for ch in arglist:
        if ch in "([{":
            depth += 1
            continue
        if ch in ")]}":
            depth -= 1
            continue
        if depth > 0:
            continue
        if ch == ",":
            names.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        names.append(cur)
    out = []
    for n in names:
        n = n.strip()
        n = re.split(r"[:?=]", n, maxsplit=1)[0].strip()
        m = re.match(r"[A-Za-z_]\w*", n)
        if m:
            out.append(m.group(0))
    return out


def scan_mcp_tool_params_regex(text):
    """正则提取 Python 函数签名参数名并分级（AST 失败时的回退）。"""
    findings = {}
    for m in FUNC_SIG_RE.finditer(text):
        arglist = m.group(1)
        if not arglist:
            continue
        ln = text[:m.start()].count("\n") + 1
        for nm in _extract_param_names(arglist):
            kind = classify_param(nm)
            if kind:
                bucket = findings.setdefault(ln, {"strong": set(), "weak": set()})
                bucket[kind].add(nm)
    return findings


def scan_tool_annotations(text):
    """AST 检查 @*.tool 装饰函数是否声明了 annotations(读写语义), 返回 [(行号, 函数名)]。

    未标注的 tool 不判红线(不是错位, 是缺标注), 由调用方生成提示:
    契约回归(contract_check.py)会把未标注的 tool 按写侧处理(保守)。
    语法错误时返回空列表(与反向参数审计一致, 由调用方提示 AST 降级)。
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            try:
                src = ast.unparse(dec)
            except Exception:
                continue
            if not _is_tool_decorator(src):
                continue
            has_annotations = False
            if isinstance(dec, ast.Call):
                for kw in dec.keywords:
                    if kw.arg == "annotations":
                        has_annotations = True
                        break
            if not has_annotations:
                out.append((node.lineno, node.name))
            break                        # 只看第一个 tool 装饰器
    return out


def flatten_argv(line):
    """把 ['terraform','apply'] 这类 argv 列表拼成 'terraform apply'，便于写操作匹配。

    仅当括号内含 ≥2 个引号字符串时才拼接，避免误伤普通 Python 列表。
    """
    def repl(m):
        inner = m.group(1)
        toks = re.findall(r'"([^"]*)"', inner)
        if len(toks) >= 2:
            return " " + " ".join(toks) + " "
        return m.group(0)
    return re.sub(r"\[([^\]]*)\]", repl, line)


def mask_line(line, literal=False):
    """对字面凭证值脱敏：只显示 key=***，不回显密钥原文。

    literal=True 时才对 password=/secret=/... 赋值做脱敏（仅字面值行）；
    连接串密码 / Authorization / 已知前缀 无论是否字面值都遮（无歧义）。
    """
    m = line
    # 连接串密码
    m = re.sub(r"([a-zA-Z][\w+.\-]*://[^\s:@/]+:)[^\s:@/]+(@)", r"\1***\2", m)
    # 赋值式脱敏（仅字面值行）
    if literal:
        m = re.sub(
            r"((?:password|passwd|pwd|passphrase|secret|client[_-]?secret|api[_-]?key|"
            r"apikey|private[_-]?key|access[_-]?token|refresh[_-]?token|token|auth[_-]?token)"
            r"\s*[:=]\s*)[\"'][^\"']*[\"']",
            r'\1"***"', m, flags=re.IGNORECASE,
        )
        m = re.sub(
            r"((?:password|passwd|pwd|passphrase|secret|client[_-]?secret|api[_-]?key|"
            r"apikey|private[_-]?key|access[_-]?token|refresh[_-]?token|token|auth[_-]?token)"
            r"\s*[:=]\s*)(?!\$\{|os\.environ|getenv|_secret|secrets\.|<)([^\s\"']+)",
            r"\1***", m, flags=re.IGNORECASE,
        )
    # Authorization: Basic/Bearer（兼容字典与裸形式）
    m = re.sub(r'((?:"?Authorization"?)\s*:\s*"?\s*(?:Basic|Bearer)\s+)[A-Za-z0-9._~+/=+-]{6,}',
               r"\1***", m)
    # 已知前缀
    m = re.sub(r"AKIA[0-9A-Z]{16,}", "***", m)
    m = re.sub(r"sk-[A-Za-z0-9_-]{10,}", "***", m)
    m = re.sub(r"ghp_[A-Za-z0-9]{20,}", "***", m)
    m = re.sub(r"xox[baprs]-[A-Za-z0-9-]{10,}", "***", m)
    m = re.sub(r"Bearer\s+[A-Za-z0-9._~-]{10,}", "Bearer ***", m)
    m = re.sub(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "***", m)
    m = re.sub(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP |DSA )?PRIVATE KEY-----",
               "*** PRIVATE KEY ***", m)
    return m


def scan_text(text, mcp_context=False):
    """返回 (groups, ast_failed)。

    groups: {行号: (原始行, {类别, ...})}，同一行多规则命中只记一次（去重）。
    ast_failed: True=Python 文件语法错误，AST 审计未执行（已回退正则）。
    """
    groups = {}
    lines = text.splitlines()
    strong_weak = {}
    ast_failed = False
    if mcp_context:
        params, ok = scan_mcp_tool_params(text)
        if ok:
            strong_weak = params
        else:
            # 解析失败绝不静默通过：回退正则粗扫，并标记供上层提示
            strong_weak = scan_mcp_tool_params_regex(text)
            ast_failed = True
    for idx, line in enumerate(lines, 1):
        cats = set()
        # 凭证（分级）
        cred_cats = scan_cred(line)
        cats |= cred_cats
        # MCP 反模式（内联 keyword 形式，仅 mcp 上下文）
        if mcp_context and scan_mcp_antipattern(line):
            cats.add("MCP反模式")
        # 其它规则（argv 列表先拼接，便于写操作匹配）
        flat = flatten_argv(line)
        for cat, sev, pats in RULES:
            for pat in pats:
                if re.search(pat, flat, re.IGNORECASE):
                    cats.add(cat)
                    break
        # 反向 MCP 审计：tool 签名参数名（AST 或正则回退）
        if idx in strong_weak:
            bucket = strong_weak[idx]
            if bucket["strong"]:
                cats.add("MCP反模式")
            if bucket["weak"]:
                cats.add("MCP可疑参数")
        if cats:
            groups[idx] = (line, cats)
    return groups, ast_failed


# 确认门启发式：要求「真正确认步骤」短语，而非裸「确认」字样
GATE_PHRASE_RE = re.compile(
    r"确认(后|后再|前|步骤|无误|后执行)|二次确认|需(要|经)?(人工)?确认|"
    r"经(用户|人工)确认|请(先)?确认|询问用户|审批(通过|后)|"
    r"requires?\s+confirmation|confirm\s+(before|after)|before\s+(executing|running|deleting)",
    re.IGNORECASE)

# MCP 设计人工确认清单（逐条）
MCP_CHECKLIST = (
    "[MCP 设计人工确认清单] ①一个API一个tool(避免万能tool)? ②方法是否写进tool description"
    "(应留给skill)? ③是否纯计算也做成了MCP(本可做成skill)? ④写tool是否带限时一次性审批令牌+审计日志?"
    " ⑤SDK类比自检: 能力面(凭证/连接/权限/审计)是否只在服务端, 用法面(顺序/状态机/确认门)是否留给技能?"
    " ⑥读写语义是否进tool annotations(只读readOnlyHint/写false/不可逆destructiveHint,"
    "未标注按写侧处理)? tool是否薄委托(校验+护栏+1-3行委托, 超过15个拆client.py+映射表)?"
)


def is_scanner_own(path):
    abspath = os.path.abspath(path)
    try:
        return os.path.commonpath([abspath, SCANNER_PKG]) == SCANNER_PKG
    except ValueError:
        return False


def collect_files(root):
    files = []
    if os.path.isfile(root):
        if is_scanner_own(root):
            return []
        return [root]
    for dirpath, dirnames, fnames in os.walk(root):
        # 跨平台跳过：按路径组件判断，不依赖 "/"
        parts = set(os.path.normpath(dirpath).split(os.sep))
        if parts & SKIP_DIR_PARTS:
            continue
        for fn in fnames:
            if fn in (".DS_Store", "Thumbs.db"):
                continue
            keep = fn.endswith(SCAN_SUFFIXES) or fn in SCAN_NOEXT_NAMES
            if not keep:
                continue
            fp = os.path.join(dirpath, fn)
            if is_scanner_own(fp):
                continue
            files.append(fp)
    return files


RED_CATS = {"凭证字面值", "内网/连通性", "有状态/常驻", "万能tool", "写操作", "MCP反模式"}


def audit_path(path):
    """返回 (report, warnings)。

    report:   {file: {行号: (行, {类别})}}。
    warnings: {file: [提示文本, ...]}（解析失败等人工确认提示）。
    """
    report = {}
    warnings = {}
    for fp in collect_files(path):
        try:
            with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except OSError:
            continue
        ext = os.path.splitext(fp)[1].lower()
        lang = "py" if ext == ".py" else "other"
        mcp_ctx = bool(MCP_CONTEXT_RE.search(text))
        # AST 仅支持 Python；非 Python 文件不做反向 MCP 审计（团队只用 Python, 忽略）
        groups, ast_failed = scan_text(text, mcp_ctx and lang == "py")
        # 确认门检查（仅 SKILL.md）：含写操作/有状态/万能tool 但全文无真正确认步骤 → 提示
        if os.path.basename(fp) == "SKILL.md":
            red = {c for _, (_, cs) in groups.items() for c in cs if c in RED_CATS}
            if red & {"写操作", "有状态/常驻", "万能tool"} and \
               not GATE_PHRASE_RE.search(text):
                groups[0] = ("[人工确认] 该 skill 含写操作/有状态/万能tool 信号但未发现确认门"
                             "(执行前需经用户确认)；不可逆操作应加人工确认步骤", {"确认门提示"})
        # MCP 设计清单（mcp 文件含反模式/写操作/万能tool 时给出逐条人工确认项）
        if mcp_ctx and groups:
            file_red = {c for _, (_, cs) in groups.items() for c in cs if c in RED_CATS}
            if file_red & {"MCP反模式", "写操作", "万能tool"}:
                groups[-1] = (MCP_CHECKLIST, {"MCP设计清单"})
        # 人工确认提示：解析失败 / 非 Python MCP 文件 / tool 未标注读写语义
        wmsgs = []
        if ast_failed:
            wmsgs.append("该文件含 FastMCP/mcp 上下文但 Python 语法错误无法 AST 解析, 已回退正则粗扫"
                         "(可能漏检跨行签名), 请人工复核 tool 签名参数。")
        if mcp_ctx and lang == "py" and not ast_failed:
            unann = scan_tool_annotations(text)
            if unann:
                names = ", ".join(f"{n}(L{l})" for l, n in unann)
                wmsgs.append(f"以下 tool 未声明 annotations(读写语义): {names}。"
                             "只读标 readOnlyHint=True; 写标 readOnlyHint=False"
                             "(不可逆再开 destructiveHint=True)。"
                             "契约回归(contract_check)会把未标注的 tool 按写侧处理(保守)。")
        if mcp_ctx and lang != "py" and ext in CODE_EXTS:
            wmsgs.append("非 Python 的 MCP 文件, 反向参数审计未执行(仅 Python 支持 AST), "
                         "请人工复核 tool 参数是否含凭证类字段。")
        if wmsgs:
            warnings[fp] = wmsgs
        if groups:
            report[fp] = groups
    return report, warnings


def print_report(report, target, warnings=None):
    warnings = warnings or {}
    print(f"# skillormcp 审计: {target}")
    all_files = set(report) | set(warnings)
    if not all_files:
        print("OK: 未发现 MCP 形错位信号(红线)。")
        return 0
    red_found = set()
    hint_found = set()
    for fp in sorted(all_files):
        groups = report.get(fp, {})
        wmsgs = warnings.get(fp, [])
        if groups or wmsgs:
            print(f"\n## {fp}")
        for w in wmsgs:
            print(f"   ! [人工确认] {w}")
        for ln, (line, cats) in sorted(groups.items()):
            for cat in sorted(cats):
                sev = "红线" if cat in RED_CATS else "提示"
                literal = "凭证字面值" in cats
                masked = mask_line(line, literal)
                loc = "L0" if ln <= 0 else f"L{ln}"
                print(f"   - [{sev}|{cat}] {loc}: {masked[:160]}")
                if cat in RED_CATS:
                    red_found.add(cat)
                if cat == "MCP可疑参数":
                    hint_found.add(cat)
    print("\n--- 结论 ---")
    if "凭证字面值" in red_found:
        print("   命中'凭证字面值'(红线) -> 必须移除硬编码凭证, 改用密钥管理"
              "(环境变量/密钥库注入), 不要留在代码或 skill 里。这不是'下沉 MCP'能解决的。")
    other = red_found - {"凭证字面值"}
    if other:
        gates = {
            "内网/连通性": "MCP门槛3 执行位置受限/连通性缺口",
            "有状态/常驻": "MCP门槛2 有状态/常驻连接",
            "万能tool": "万能tool反模式(应下沉 MCP)",
            "写操作": "MCP门槛4 必须硬拦截(写操作/不可逆)",
            "MCP反模式": "MCP反模式(万能tool/凭证作tool参数)",
        }
        hits = "; ".join(gates[c] for c in sorted(other, key=lambda x: list(gates).index(x)) if c in gates)
        print("   命中红线信号(非凭证) -> 疑似能力错位/反模式, 建议下沉 mcps/ 或拆分写操作护栏。")
        print(f"   命中门槛: {hits}")
    if other & {"写操作", "有状态/常驻", "万能tool"}:
        print("   提示: 含写操作/有状态/万能tool 的技能, 应检查是否含'确认门'(不可逆操作需人工确认)。")
    if "MCP反模式" in red_found:
        print("   提示: 命中 MCP 设计人工确认清单(见上方 [MCP设计清单] 逐条项)。")
    if hint_found:
        print("   提示: 发现疑似凭证参数名(token/credentials 等)已降级为'提示'而非反模式红线; "
              "分页 token(page_token/next_token)等合法用途不视为反模式, 请人工确认。")
    if not red_found:
        print("   未发现红线信号(仅有提示/人工确认项, 见上方 [人工确认] 条目)。")
    if red_found & {"凭证字面值", "内网/连通性", "有状态/常驻", "写操作", "MCP反模式"}:
        print("   提示: 命中凭证/连通性/写操作类门槛但使用频率低时, 需人工过成本维度"
              "(频率 x 维护成本 x 复用客户端数)再决定是否常驻 MCP; 逃生舱只改部署形态, 不放宽凭证红线。")
    print("   注: 含内网/私有地址信号时, 是否适合公开仓库由人确认(本skill不裁决发布范围)。")
    print("(ADVISORY ONLY: 以上为建议, 不自动移动/修改任何文件。)")
    return 1 if red_found else 0


# ----------------------------------------------------------------------------
# 自检（覆盖用户复核报告中的全部正例/负例/脱敏/后缀/反例/绕过口/argv/字典Auth/确认门
#       + 解析失败回退 / 参数名强弱分级与白名单）
# ----------------------------------------------------------------------------
def self_test():
    ok = True

    # 1) 正向：每个红线类别都能被检出
    pos = {
        "凭证字面值": "password=Sup3rS3cret! and AKIAIOSFODNN7EXAMPLE",
        "内网/连通性": "ssh user@192.168.1.10 via bastion jumphost",
        "有状态/常驻": "token 刷新 websocket 长连接 subscription 常驻",
        "万能tool": "execute_sql('select') run_shell('ls') os.system('x') eval(1)",
        "写操作": "requests.post('https://x/delete') kubectl apply -f pod.yaml rm -rf /tmp/x",
    }
    for cat, text in pos.items():
        g, _ = scan_text(text)
        if not any(cat in cats for _, (_, cats) in g.items()):
            print(f"FAIL: 未检出 {cat}")
            ok = False

    # 1b) 新增写操作 / 连接串 / Authorization 正例
    for text in ["httpx.post('https://x/delete')",
                 "DROP TABLE users",
                 "terraform apply -auto-approve",
                 "Authorization: Basic abcdef123456",
                 "postgres://admin:Sup3rS3cret@host/db"]:
        g, _ = scan_text(text)
        reds = {c for _, (_, cats) in g.items() for c in cats if c in RED_CATS}
        if "写操作" not in reds and "凭证字面值" not in reds:
            print(f"FAIL: 新增写操作/凭证正例未命中 on '{text[:40]}'")
            ok = False

    # 2) 反向 MCP 审计（AST 解析 @mcp.tool 函数参数名，无视类型标注）
    mcp_src = ("from fastmcp import FastMCP\n"
               "@mcp.tool\n"
               "def login(password: str, api_key: str):\n"
               "    return do_login(password, api_key)\n")
    g, _ = scan_text(mcp_src, mcp_context=True)
    # 第3行 def login(...) 应命中 MCP反模式（AST 解析参数名，无视类型标注）
    def_line_hit = any("MCP反模式" in cats for ln, (_, cats) in g.items() if ln == 3)
    if not def_line_hit:
        print("FAIL: 反向 MCP 审计(@mcp.tool 函数凭证参数名)未检出")
        ok = False
    # 非 mcp 上下文不触发
    g2, _ = scan_text(mcp_src, mcp_context=False)
    if any("MCP反模式" in cats for _, (_, cats) in g2.items()):
        print("FAIL: 非 mcp 上下文误触发 MCP反模式")
        ok = False
    # 非 tool 装饰函数（同样有凭证参数名）不应触发
    g5, _ = scan_text("def helper(password: str):\n    return x", mcp_context=True)
    if any("MCP反模式" in cats for _, (_, cats) in g5.items()):
        print("FAIL: 非 tool 装饰函数 'password' 参数误触发 MCP反模式")
        ok = False
    # 安全调用（_secret 读取）不应触发反模式
    g4, _ = scan_text("client_secret = _secret('db')", mcp_context=True)
    if any("MCP反模式" in cats for _, (_, cats) in g4.items()):
        print("FAIL: 安全调用 '_secret' 误触发 MCP反模式")
        ok = False
    # 内联 keyword 形式（password=db_pass）仍应触发
    g6, _ = scan_text("def t():\n    return call(password=db_pass)", mcp_context=True)
    if not any("MCP反模式" in cats for _, (_, cats) in g6.items()):
        print("FAIL: 内联 keyword 形式 password=db_pass 未触发 MCP反模式")
        ok = False

    # 2b) argv 列表形式写操作（subprocess.run(["terraform","apply"]) 等）
    for text in ['subprocess.run(["terraform","apply"])',
                 'subprocess.run(["kubectl","delete","pod"])',
                 'subprocess.run(["rm","-rf","/tmp/x"])',
                 'os.system(["git","push","origin","main"])']:
        g, _ = scan_text(text)
        reds = {c for _, (_, cats) in g.items() for c in cats if c in RED_CATS}
        if "写操作" not in reds:
            print(f"FAIL: 列表形式写操作未命中 on '{text[:50]}'")
            ok = False

    # 2c) 字典形式 Authorization: Basic 检测 + 脱敏
    g, _ = scan_text('{"Authorization": "Basic dXNlcjpwYXNz"}')
    reds = {c for _, (_, cats) in g.items() for c in cats if c in RED_CATS}
    if "凭证字面值" not in reds:
        print("FAIL: 字典形式 Authorization: Basic 未检出")
        ok = False
    masked = mask_line('{"Authorization": "Basic dXNlcjpwYXNz"}', literal=True)
    if "dXNlcjpwYXNz" in masked or "***" not in masked:
        print(f"FAIL: 字典形式 Authorization 脱敏失败 -> {masked}")
        ok = False

    # 3) 负例：干净文本 / 已知误报源不应产生红线
    neg = [
        "disk-aware tokenizer for risk-based review",
        "使用凭证进行安全认证",
        "tokenizer splits text into tokens",
        "risk-based review approach with no secrets",
        "requests.get('https://api/readonly')",
        # 模板推荐写法（类型标注/环境变量/安全源/函数调用）
        'password = os.environ["DB_PASSWORD"]',
        "password: ${DB_PASSWORD}",
        'client_secret = _secret("db")',
        "token = secrets.token_urlsafe(16)",
        "approval_token: str",
        # 版本号
        "macOS 10.15.2",
        "Python 3.10.4.5",
        # model.eval()
        "model.eval()",
        # Jump to
        "Jump to the next section",
    ]
    for text in neg:
        g, _ = scan_text(text)
        reds = {c for _, (_, cats) in g.items() for c in cats if c in RED_CATS}
        if reds:
            print(f"FAIL: 误报红线 on '{text[:50]}': {reds}")
            ok = False

    # 3a) 遮罩仅限字面值行：非字面值行（token = _issue_token(...)）不应遮罩
    m = mask_line("token = _issue_token('apply_123')", literal=False)
    if "***" in m:
        print(f"FAIL: 非字面值行误遮罩 -> {m}")
        ok = False
    # 字面值行应遮罩
    m2 = mask_line('password = "realpass123"', literal=True)
    if "realpass123" in m2 or "***" not in m2:
        print(f"FAIL: 字面值行未遮罩 -> {m2}")
        ok = False

    # 4) 脱敏：字面凭证值必须被遮，密钥原文不得回显
    for raw, secret in [
        ("password=Sup3rS3cret! token=abc123def", "Sup3rS3cret"),
        ('password: "my pass phrase 123"', "pass phrase"),
        ("postgres://admin:Sup3rS3cret@host/db", "Sup3rS3cret"),
        ("Authorization: Basic abcdef123456", "abcdef123456"),
    ]:
        masked = mask_line(raw, literal=True)
        if secret in masked or "***" not in masked:
            print(f"FAIL: 脱敏失败 -> {masked}")
            ok = False

    # 5) 后缀：.env / .toml 必须纳入扫描
    d = tempfile.mkdtemp()
    try:
        with open(os.path.join(d, ".env"), "w") as f:
            f.write("DB_PASSWORD=hunter2\n")
        with open(os.path.join(d, "config.toml"), "w") as f:
            f.write("api_key = 'sk-live-abc123def456'\n")
        with open(os.path.join(d, "SKILL.md"), "w") as f:
            f.write("normal method, no secrets\n")
        files = collect_files(d)
        names = {os.path.basename(x) for x in files}
        if ".env" not in names or "config.toml" not in names:
            print(f"FAIL: .env/.toml 未纳入扫描: {names}")
            ok = False
        rep, _ = audit_path(d)
        if not any(any("凭证字面值" in c for c in cats)
                   for groups in rep.values() for _, (_, cats) in groups.items()):
            print("FAIL: .env/.toml 内凭证字面值未被检出")
            ok = False
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # 6) 绕过口修复：被审计 skill 自带的 templates/ 必须被扫描（不按目录名豁免）
    d2 = tempfile.mkdtemp()
    try:
        skill_x = os.path.join(d2, "some-skill")
        tpl = os.path.join(skill_x, "templates")
        os.makedirs(tpl)
        with open(os.path.join(skill_x, "SKILL.md"), "w") as f:
            f.write("normal skill\n")
        with open(os.path.join(tpl, ".env"), "w") as f:
            f.write("DB_PASSWORD=leaked_in_template\n")
        rep, _ = audit_path(skill_x)
        hit = any("凭证字面值" in cats
                  for groups in rep.values() for _, (_, cats) in groups.items())
        if not hit:
            print("FAIL: 被审计 skill 的 templates/.env 未被扫描(绕过口未修)")
            ok = False
    finally:
        shutil.rmtree(d2, ignore_errors=True)

    # 7) 确认门启发式 + MCP 设计清单
    d3 = tempfile.mkdtemp()
    try:
        # 7a 写操作但仅裸"确认"无关出现 -> 应提示确认门
        p1 = os.path.join(d3, "SKILL.md")
        with open(p1, "w") as f:
            f.write("---\nname: x\ndescription: x\n---\nrm -rf /tmp/x\n我们在确认需求时讨论过这个设计。\n")
        rep1, _ = audit_path(d3)
        gate1 = any("确认门提示" in cats for groups in rep1.values()
                    for _, (_, cats) in groups.items())
        if not gate1:
            print("FAIL: 有写操作但仅裸'确认'字样, 确认门提示未给出")
            ok = False
    finally:
        shutil.rmtree(d3, ignore_errors=True)
    d4 = tempfile.mkdtemp()
    try:
        # 7b 写操作且有真正确认步骤 -> 不应提示确认门
        p2 = os.path.join(d4, "SKILL.md")
        with open(p2, "w") as f:
            f.write("---\nname: y\ndescription: y\n---\nrm -rf /tmp/x\n执行删除前需经用户确认后再继续。\n")
        rep2, _ = audit_path(d4)
        gate2 = any("确认门提示" in cats for groups in rep2.values()
                    for _, (_, cats) in groups.items())
        if gate2:
            print("FAIL: 含真正确认步骤却仍提示确认门")
            ok = False
    finally:
        shutil.rmtree(d4, ignore_errors=True)
    d5 = tempfile.mkdtemp()
    try:
        # 7c mcp 文件含写操作 -> 应给 MCP 设计清单
        p3 = os.path.join(d5, "server.py")
        with open(p3, "w") as f:
            f.write("from fastmcp import FastMCP\nmcp=FastMCP('x')\n@mcp.tool\n"
                    "def wipe(confirm: bool):\n    import os\n    os.remove('/data/all')\n")
        rep3, _ = audit_path(d5)
        chk = any("MCP设计清单" in cats for groups in rep3.values()
                  for _, (_, cats) in groups.items())
        if not chk:
            print("FAIL: mcp 文件含写操作未给出 MCP 设计清单")
            ok = False
    finally:
        shutil.rmtree(d5, ignore_errors=True)

    # 8) 解析失败回退（最该修）：Python 语法错误 -> AST 失败, 回退正则, 仍报强红线 + ast_failed
    broken = ("from fastmcp import FastMCP\n"
              "@mcp.tool\n"
              "def login(password: str, api_key:  # 语法错误, 写到一半\n")
    g8, ast_failed = scan_text(broken, mcp_context=True)
    if not ast_failed:
        print("FAIL: 语法错误文件未标记 ast_failed")
        ok = False
    reds8 = {c for _, (_, cats) in g8.items() for c in cats if c in RED_CATS}
    if "MCP反模式" not in reds8:
        print("FAIL: 解析失败回退正则未检出 tool 签名凭证参数(password/api_key)")
        ok = False
    # audit_path 层应给出'无法 AST 解析'人工确认提示, 且命中进入报告
    d8 = tempfile.mkdtemp()
    try:
        with open(os.path.join(d8, "broken.py"), "w") as f:
            f.write(broken)
        rep8, warns8 = audit_path(d8)
        ast_warn = any("无法 AST 解析" in w for ws in warns8.values() for w in ws)
        if not ast_warn:
            print("FAIL: 解析失败文件未生成'无法 AST 解析'人工确认提示")
            ok = False
        rep_red = {c for groups in rep8.values() for _, (_, cats) in groups.items()
                   for c in cats if c in RED_CATS}
        if "MCP反模式" not in rep_red:
            print("FAIL: 解析失败回退的 MCP反模式未进入报告")
            ok = False
    finally:
        shutil.rmtree(d8, ignore_errors=True)

    # 9) 参数名强弱分级 + 白名单
    src9 = ("@mcp.tool\n"
            "def list_items(page_token: str, token: str, access_token: str):\n"
            "    pass\n")
    g9, _ = scan_text(src9, mcp_context=True)
    cats9 = {c for _, (_, cats) in g9.items() for c in cats}
    if "MCP反模式" not in cats9:
        print("FAIL: access_token 应报 MCP反模式(强, 红线)")
        ok = False
    if "MCP可疑参数" not in cats9:
        print("FAIL: 裸 token 应降为 MCP可疑参数(提示)")
        ok = False
    # 纯 page_token 函数: 白名单, 不应出现任何 MCP 类
    g9b, _ = scan_text("@mcp.tool\ndef list_items(page_token: str):\n    pass\n",
                       mcp_context=True)
    cats9b = {c for _, (_, cats) in g9b.items() for c in cats}
    if cats9b:
        print(f"FAIL: page_token 白名单被误报: {cats9b}")
        ok = False

    # 10) 非 Python MCP 文件（如 server.ts）：反向参数审计未执行, 应给人工确认提示
    #     （与 SKILL.md 报告格式"人工确认项: 非Python文件...提示"对齐；避免"没问题"错觉）
    d10 = tempfile.mkdtemp()
    try:
        p10 = os.path.join(d10, "server.ts")
        with open(p10, "w") as f:
            f.write('server.tool("login", { password: z.string(), apiKey: z.string() }, '
                    'async (args) => { /* implement */ });\n')
        rep10, warns10 = audit_path(d10)
        note = any("反向参数审计未执行" in w for ws in warns10.values() for w in ws)
        if not note:
            print("FAIL: 非 Python MCP 文件(server.ts)未给出'反向参数审计未执行'人工确认提示")
            ok = False
    finally:
        shutil.rmtree(d10, ignore_errors=True)

    # 10b) 凭证分级：环境变量名 / 占位符形态不得判为红线（端到端实测发现）
    try:
        _ref_cases = [
            ("service_access_token: SERVICE_API_TOKEN", False),
            ("service_client_secret: SERVICE_CLIENT_SECRET", False),
            ("api_key: API_KEY", False),
            ("aws_secret_access_key: AWS_SECRET_ACCESS_KEY", False),
            ('service_access_token: "<ENV_VAR_NAME>"', False),
            ("service_access_token: <ENV_VAR_NAME>", False),
            ("password: hunter2", True),
            ('password: "hunter2"', True),
            ("access_token: PASSWORD123", True),
            ("access_token: HUNTER2", True),
            ("api_key: abcdef123456", True),
            ("secret: s3cr3t-value", True),
        ]
        for _line, _want_red in _ref_cases:
            _cats = scan_cred(_line)
            _is_red = "凭证字面值" in _cats
            if _is_red != _want_red:
                print(f"FAIL: 凭证分级误判: {_line!r} 期望红线={_want_red} 实际={_is_red}")
                ok = False
    except Exception as e:
        print(f"FAIL: 凭证分级断言无法执行: {e}")
        ok = False

    # 11) MCP 设计清单含 SDK 类比自检项(第⑤条), 与 SKILL.md 逐条一致
    if "⑤SDK类比自检" not in MCP_CHECKLIST:
        print("FAIL: MCP 设计人工确认清单缺少第⑤条 SDK 类比自检")
        ok = False
    if "⑥读写语义是否进tool annotations" not in MCP_CHECKLIST:
        print("FAIL: MCP 设计人工确认清单缺少第⑥条 annotations/薄委托")
        ok = False

    # 11b) tool annotations 缺失检测: 无 annotations 的 @*.tool 应被提示
    d11 = tempfile.mkdtemp()
    try:
        p11 = os.path.join(d11, "server.py")
        with open(p11, "w") as f:
            f.write("from fastmcp import FastMCP\nmcp=FastMCP('x')\n@mcp.tool()\n"
                    "def list_all():\n    return []\n")
        rep11, warns11 = audit_path(d11)
        w11 = [w for ws in warns11.values() for w in ws]
        if not any("未声明 annotations" in w for w in w11):
            print(f"FAIL: @mcp.tool() 无 annotations 未提示: {w11}")
            ok = False
        # 有 annotations 的 tool 不应提示
        d11b = tempfile.mkdtemp()
        try:
            p11b = os.path.join(d11b, "server.py")
            with open(p11b, "w") as f:
                f.write("from fastmcp import FastMCP\nmcp=FastMCP('x')\n"
                        "@mcp.tool(annotations={'readOnlyHint': True})\n"
                        "def list_all():\n    return []\n")
            _, warns11b = audit_path(d11b)
            w11b = [w for ws in warns11b.values() for w in ws]
            if any("未声明 annotations" in w for w in w11b):
                print(f"FAIL: 已标注 annotations 仍被误提示: {w11b}")
                ok = False
        finally:
            shutil.rmtree(d11b, ignore_errors=True)
    finally:
        shutil.rmtree(d11, ignore_errors=True)

    # 11c) functools.wraps 装饰的 wrapper 不是 tool("functools" 含 "tools" 曾致误判,
    #      端到端物化冒烟实测): 反向审计与 annotations 提示都不得命中
    wrap_src = ("import functools\n"
                "def audited(fn):\n"
                "    @functools.wraps(fn)\n"
                "    def wrapper(*args, **kwargs):\n"
                "        return fn(*args, **kwargs)\n"
                "    return wrapper\n")
    g11c, _ = scan_text(wrap_src, mcp_context=True)
    if any("MCP反模式" in c or "MCP可疑参数" in c for _, (_, cs) in g11c.items() for c in cs):
        print("FAIL: functools.wraps 的 wrapper 被误判为 tool(反向审计)")
        ok = False
    if scan_tool_annotations(wrap_src):
        print(f"FAIL: functools.wraps 的 wrapper 被误判为 tool(annotations 提示): "
              f"{scan_tool_annotations(wrap_src)}")
        ok = False
    # 而真正的 @mcp.tool 仍要被认出
    real_src = ("from fastmcp import FastMCP\nmcp = FastMCP('x')\n"
                "@mcp.tool\n"
                "def list_all():\n    return []\n")
    if not scan_tool_annotations(real_src):
        print("FAIL: 真正的 @mcp.tool 反而未被识别(annotations 提示)")
        ok = False

    # 12) 清单第⑤条与 SKILL.md 描述一致(规范化空白/全角标点/反引号/破折后比对, 防止两处措辞漂移)
    def _norm(s):
        s = re.sub(r"\s+", "", s)
        s = s.replace("`", "")
        s = s.translate(str.maketrans("：；（）？，、／–—", ":;()?,,/--"))  # type: ignore[arg-type]
        return s
    try:
        with open(os.path.join(SCANNER_PKG, "SKILL.md"), "r", encoding="utf-8") as f:
            skill_doc = f.read()
        tail5 = MCP_CHECKLIST[MCP_CHECKLIST.index("⑤"):]
        if _norm(tail5) not in _norm(skill_doc):
            print("FAIL: 清单第⑤条与 SKILL.md 描述不一致(规范化后未包含)")
            ok = False
    except (OSError, ValueError) as e:
        print(f"FAIL: 清单一致性比对无法执行: {e}")
        ok = False

    if ok:
        print("OK: self-test 通过(正向/反向AST/脱敏/后缀/反例/绕过口/argv/字典Auth/"
              "确认门/解析失败回退/参数名强弱分级与白名单/非PythonMCP人工确认提示/"
              "SDK类比清单/清单第⑤条跨文档一致性均覆盖)。")
        return 0
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", help="skill/mcp 目录 或 skills/ 根")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if not args.path:
        ap.error("需要 path 或 --self-test")
    if not os.path.exists(args.path):
        print(f"路径不存在: {args.path}")
        return 2
    report, warnings = audit_path(args.path)
    return print_report(report, args.path, warnings)


if __name__ == "__main__":
    sys.exit(main())
