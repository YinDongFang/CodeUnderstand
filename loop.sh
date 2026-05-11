#!/usr/bin/env bash
# 在项目目录内按 questions 列表循环调用 claude；成功时 stdout 仅输出一行 session_id，其余日志走 stderr。
# 用法: loop.sh <target_path> <repo>
# 依赖: bash, sed, grep, mapfile, jq, claude
set -eu

loop_out() { printf '[loop.sh][%s]%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; }
loop_err() { printf '[loop.sh][%s]%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; }

_fold_one_line() {
  local s=${1//$'\r'/}
  s=${s//$'\n'/ }
  printf '%s' "$s"
}

_preview_text() {
  local s=$(_fold_one_line "$1") n=30
  if ((${#s} > n)); then printf '%s...' "${s:0:n}"; else printf '%s' "$s"; fi
}

usage() {
  loop_err "用法: $0 <target_path> <repo>"
  exit 1
}

[[ "${#}" -eq 2 ]] || usage
TARGET_PATH="${1}"
REPO="${2}"
[[ -d "${TARGET_PATH}" ]] || { loop_err "错误: 目录不存在: ${TARGET_PATH}"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAX_TARGET_ATTEMPTS=4
MAX_QUESTIONS=38
RUN_FAILED=0

QUESTIONS_FILE=""
if [[ -f "${PWD}/questions/${REPO}.txt" ]]; then
  QUESTIONS_FILE="${PWD}/questions/${REPO}.txt"
elif [[ -f "${SCRIPT_DIR}/questions/${REPO}.txt" ]]; then
  QUESTIONS_FILE="${SCRIPT_DIR}/questions/${REPO}.txt"
else
  loop_err "错误: 未找到问题列表文件 questions/${REPO}.txt"
  loop_err "  已查找: ${PWD}/questions/${REPO}.txt 与 ${SCRIPT_DIR}/questions/${REPO}.txt"
  exit 1
fi

loop_out "问题列表: ${QUESTIONS_FILE}"

mapfile -t QUESTIONS < <(
  sed 's/\r$//' "${QUESTIONS_FILE}" |
    grep -v '^[[:space:]]*#' |
    sed '/^[[:space:]]*$/d'
)

if [[ "${#QUESTIONS[@]}" -eq 0 ]]; then
  loop_err "错误: ${QUESTIONS_FILE} 中没有有效问题行"
  exit 1
fi

if [[ "${#QUESTIONS[@]}" -gt "${MAX_QUESTIONS}" ]]; then
  loop_err "有效题目共 ${#QUESTIONS[@]} 行，仅使用前 ${MAX_QUESTIONS} 题"
  QUESTIONS=("${QUESTIONS[@]:0:${MAX_QUESTIONS}}")
fi

loop_out "共 ${#QUESTIONS[@]} 个问题，进入项目目录执行 claude"

cd "${TARGET_PATH}" || { loop_err "无法 cd 到 ${TARGET_PATH}"; exit 1; }

SESSION_ID=""
TOTAL="${#QUESTIONS[@]}"

for ((i = 0; i < TOTAL; i++)); do
  Q="${QUESTIONS[$i]}"
  round=$((i + 1))
  loop_out "========== 第 ${round}/${TOTAL} 题 =========="
  loop_out "Q: $(_fold_one_line "$Q")"

  attempt=1
  success=0
  RAW=""
  ROUND_EXIT=0

  while [[ ${attempt} -le ${MAX_TARGET_ATTEMPTS} ]]; do
    loop_out "尝试 ${attempt}/${MAX_TARGET_ATTEMPTS}"
    set +e
    if [[ "${i}" -eq 0 ]]; then
      RAW=$(claude --output-format json -p "$Q" 2>&1)
      ROUND_EXIT=$?
    else
      RAW=$(claude -r "${SESSION_ID}" -c -p "$Q" 2>&1)
      ROUND_EXIT=$?
    fi
    set -e

    if [[ "${ROUND_EXIT}" -ne 0 ]]; then
      loop_err "claude 退出码 ${ROUND_EXIT}，将重试"
      attempt=$((attempt + 1))
      continue
    fi

    if [[ "${i}" -eq 0 ]]; then
      _r=$(printf '%s' "${RAW}" | tr -d '\r')
      _doc=$(printf '%s' "${_r}" | jq -ec . 2>/dev/null) || _doc=$(printf '%s' "${_r}" | jq -Rrs 'split("\n")|map(select(test("^\\s*\\{")))|map(try fromjson catch empty)|map(select(type=="object"))|last')
      if ! jq -e 'type=="object"' <<<"${_doc}" >/dev/null 2>&1; then
        loop_err "首轮 JSON 解析失败，输出摘要:"
        loop_err "A: $(_preview_text "${RAW}")"
        attempt=$((attempt + 1))
        continue
      fi
      SESSION_ID=$(jq -r '(.session_id//"")|tostring' <<<"${_doc}")
      if [[ -z "${SESSION_ID}" || "${SESSION_ID}" == "null" ]]; then
        loop_err "首轮未解析到 uuid/session_id，将重试"
        attempt=$((attempt + 1))
        continue
      fi
      OUT_TEXT=$(jq -r '.result|if .==null then "" elif type=="string" then . elif type=="boolean" or type=="number" then tostring else tojson end' <<<"${_doc}")
      loop_err "session(uuid)=${SESSION_ID}"
      ANS="${OUT_TEXT}"
    else
      ANS="${RAW}"
    fi

    loop_out "A: $(_preview_text "${ANS}")"

    success=1
    break
  done

  if [[ "${success}" -ne 1 ]]; then
    loop_err "第 ${round} 题在 ${MAX_TARGET_ATTEMPTS} 次尝试后仍失败"
    RUN_FAILED=1
    break
  fi
done

if [[ "${RUN_FAILED}" -ne 0 ]]; then
  loop_err "流程未全部成功"
  exit 1
fi

printf '%s\n' "${SESSION_ID}"
