#!/usr/bin/env bash
# report_gate.sh —— 建设期报告落盘前的「单一入口」机检。
#
# 为什么存在：两道机检器（coverage_table_check / stage_consistency_check）
# 分开跑时，靠人记得「跑完第一道还要跑第二道」，实测存在漏跑风险。本脚本把
# 两道收进一条命令——SKILL.md 审计循环 2a 只指向本入口，不再列两条命令。
# 本脚本只做调度与退出码聚合，不复制任何判定逻辑（逻辑仍归各机检器单一所有）。
#
# 退出码三态（沿用机检器约定）：
#   0  PASS —— 两道都过
#   1  VIOLATION —— 至少一道抓到 finding
#   42 NOT-WIRED —— 至少一道报未接线（无报告 / 无覆盖表 / 无 D 表）；
#                   42 优先于 1，因为「根本没接线」比「接了线有违规」更根本
#
# 聚合顺序：先 coverage 后 stage，两道都跑完再判总码——即使第一道 FAIL 也
# 跑第二道，避免「修好第一道才发现第二道也红」的二次往返。

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<EOF
用法：
  $(basename "$0") --report <审计报告.md>    依次跑两道报告机检
  $(basename "$0") --report <报告.md> --plan <落地计划.md>
                                             两道报告机检 + 第三道落地计划机检
  $(basename "$0") --self-test               依次跑各机检的内嵌自测
  $(basename "$0") --help                    显示本帮助

第三道为可选：产物级审计只跑前两道；建设期体检出落地计划时加 --plan。
退出码：0 PASS / 1 VIOLATION / 42 NOT-WIRED
EOF
}

report=""
plan=""
self_test=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --report)
      [[ $# -ge 2 ]] || { echo "错误：--report 缺少路径参数" >&2; usage; exit 42; }
      report="$2"
      shift 2
      ;;
    --plan)
      [[ $# -ge 2 ]] || { echo "错误：--plan 缺少路径参数" >&2; usage; exit 42; }
      plan="$2"
      shift 2
      ;;
    --self-test)
      self_test=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "错误：未知参数 $1" >&2
      usage
      exit 42
      ;;
  esac
done

if [[ "$self_test" == true ]]; then
  if [[ -n "$report" || -n "$plan" ]]; then
    echo "错误：--self-test 与 --report/--plan 不可并用" >&2
    exit 42
  fi
  echo "=== [1/3] coverage_table_check.py --self-test ==="
  python3 "$SCRIPT_DIR/coverage_table_check.py" --self-test
  rc1=$?
  echo "=== [2/3] stage_consistency_check.py --self-test ==="
  python3 "$SCRIPT_DIR/stage_consistency_check.py" --self-test
  rc2=$?
  echo "=== [3/3] landing_plan_check.py --self-test ==="
  python3 "$SCRIPT_DIR/landing_plan_check.py" --self-test
  rc3=$?
else
  if [[ -z "$report" ]]; then
    echo "错误：需指定 --report <审计报告.md>（或 --self-test）" >&2
    usage
    exit 42
  fi
  if [[ ! -f "$report" ]]; then
    echo "错误：报告文件不存在：$report" >&2
    exit 42
  fi
  echo "=== [1/3] coverage_table_check.py --report ==="
  python3 "$SCRIPT_DIR/coverage_table_check.py" --report "$report"
  rc1=$?
  echo "=== [2/3] stage_consistency_check.py --report ==="
  python3 "$SCRIPT_DIR/stage_consistency_check.py" --report "$report"
  rc2=$?
  rc3=0
  if [[ -n "$plan" ]]; then
    if [[ ! -f "$plan" ]]; then
      echo "错误：落地计划文件不存在：$plan" >&2
      exit 42
    fi
    echo "=== [3/3] landing_plan_check.py --report --plan ==="
    python3 "$SCRIPT_DIR/landing_plan_check.py" --report "$report" --plan "$plan"
    rc3=$?
  else
    echo "=== [3/3] landing_plan_check.py —— 未传 --plan，跳过（产物级审计）==="
  fi
fi

echo "=== report_gate 汇总：coverage_rc=$rc1 stage_rc=$rc2 plan_rc=$rc3 ==="
if [[ "$rc1" -eq 42 || "$rc2" -eq 42 || "$rc3" -eq 42 ]]; then
  echo "report_gate: NOT-WIRED（42）"
  exit 42
fi
if [[ "$rc1" -eq 1 || "$rc2" -eq 1 || "$rc3" -eq 1 ]]; then
  echo "report_gate: VIOLATION（1）"
  exit 1
fi
echo "report_gate: PASS（0）"
exit 0
