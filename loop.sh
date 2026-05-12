#!/usr/bin/env bash
# 在项目目录内按 questions 列表循环调用 claude；成功时 stdout 仅输出一行 session_id，其余日志走 stderr。
# 若 ~/.claude/projects/-home-$USER-projects-${repo//_/-} 下已有 session.jsonl 或 *.jsonl，则从该会话最后一个用户问题对应题号之后继续。
# 用法: loop.sh <target_path> <repo>
# 依赖: bash, sed, grep, mapfile, jq, claude
set -eu

loop_out() { printf '[loop.sh][%s]%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; }
loop_err() { printf '[loop.sh][%s]%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; }

loop_out "======================================================"
loop_out "=                    Start Loop                      ="
loop_out "======================================================"

_fold_one_line() {
  local s=${1//$'\r'/}
  s=${s//$'\n'/ }
  printf '%s' "$s"
}

_preview_text() {
  local s=$(_fold_one_line "$1") n=30
  if ((${#s} > n)); then printf '%s...' "${s:0:n}"; else printf '%s' "$s"; fi
}

_str_trim() {
  local s="$1"
  s="${s#"${s%%[![:space:]]*}"}"
  s="${s%"${s##*[![:space:]]}"}"
  printf '%s' "$s"
}

# 扫描 JSONL：统计「真实用户题」锚点数（与 clean.py parse_main_jsonl 一致），并记录最后一条用户正文
_resume_scan_jsonl() {
  local file="$1"
  RESUME_COMPLETED=0
  RESUME_LAST_USER=""
  local line is t
  while IFS= read -r line || [[ -n "${line}" ]]; do
    [[ -z "${line//[:space:]}" ]] && continue
    jq -e . >/dev/null 2>&1 <<<"${line}" || continue
    is="$(jq -r '
      if .type=="user" and (.message|type)=="object" and (.message.content|type)=="string" then
        (.message.content|gsub("^\\s+";"")|gsub("\\s+$";"")) as $t |
        if ($t|length)>0 and ($t|startswith("<task-notification>")|not) then "1" else "0" end
      else
        "0"
      end
    ' <<<"${line}" 2>/dev/null)" || is=0
    if [[ "${is}" == "1" ]]; then
      RESUME_COMPLETED=$((RESUME_COMPLETED + 1))
      t="$(jq -r '.message.content' <<<"${line}" 2>/dev/null || printf '')"
      RESUME_LAST_USER="${t}"
    fi
  done <"${file}"
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

REPO_SLUG="${REPO//_/-}"
CLAUDE_PROJECT_DIR="${HOME}/.claude/projects/-home-${USER}-projects-${REPO_SLUG}"

QUESTIONS_FILE=""
if [[ -f "${SCRIPT_DIR}/questions/${REPO}.txt" ]]; then
  QUESTIONS_FILE="${SCRIPT_DIR}/questions/${REPO}.txt"
else
  loop_err "错误: 未找到问题列表文件 ${SCRIPT_DIR}/questions/${REPO}.txt"
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

TOTAL="${#QUESTIONS[@]}"
SESSION_ID=""
START_I=0
SESSION_JSONL=""

if [[ -d "${CLAUDE_PROJECT_DIR}" ]]; then
  if [[ -f "${CLAUDE_PROJECT_DIR}/session.jsonl" ]]; then
    SESSION_JSONL="${CLAUDE_PROJECT_DIR}/session.jsonl"
  else
    shopt -s nullglob
    local_globs=("${CLAUDE_PROJECT_DIR}"/*.jsonl)
    shopt -u nullglob
    if [[ ${#local_globs[@]} -gt 0 ]]; then
      SESSION_JSONL="${local_globs[0]}"
      for f in "${local_globs[@]}"; do
        [[ -f "${f}" ]] || continue
        if [[ "${f}" -nt "${SESSION_JSONL}" ]]; then
          SESSION_JSONL="${f}"
        fi
      done
    fi
  fi
fi

if [[ -n "${SESSION_JSONL}" && -f "${SESSION_JSONL}" ]]; then
  SESSION_ID="$(basename -- "${SESSION_JSONL}" .jsonl)"
  RESUME_COMPLETED=0
  RESUME_LAST_USER=""
  _resume_scan_jsonl "${SESSION_JSONL}"
  loop_out "检测到已有会话: ${SESSION_JSONL}（session_id=${SESSION_ID}），已完成的用户锚点数: ${RESUME_COMPLETED}"

  if [[ "${RESUME_COMPLETED}" -eq 0 ]]; then
    loop_out "警告: 该 JSONL 无有效用户锚点，忽略并从新会话开始"
    SESSION_ID=""
    SESSION_JSONL=""
    START_I=0
  else
    last_trim="$(_str_trim "${RESUME_LAST_USER}")"
    START_I="${RESUME_COMPLETED}"
    if [[ -n "${last_trim}" ]]; then
      for ((j = 0; j < TOTAL; j++)); do
        qj="$(_str_trim "${QUESTIONS[j]}")"
        if [[ "${qj}" == "${last_trim}" ]]; then
          START_I=$((j + 1))
          loop_out "与 questions 第 $((j + 1)) 题匹配，从第 $((START_I + 1)) 题继续"
          break
        fi
      done
    fi

    if [[ "${START_I}" -ge "${TOTAL}" ]]; then
      loop_out "会话中用户题已覆盖全部 ${TOTAL} 题，无需再提问"
      printf '%s\n' "${SESSION_ID}"
      exit 0
    fi
    if [[ "${START_I}" -lt 0 ]]; then
      START_I=0
    fi
  fi
else
  loop_out "未检测到 ${CLAUDE_PROJECT_DIR} 下的会话 JSONL，从第 1 题开始新会话"
fi

loop_out "共 ${TOTAL} 个问题，起始题号: $((START_I + 1))，进入项目目录执行 claude"

cd "${TARGET_PATH}" || { loop_err "无法 cd 到 ${TARGET_PATH}"; exit 1; }

for ((i = START_I; i < TOTAL; i++)); do
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
    if [[ -z "${SESSION_ID}" ]]; then
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

    if [[ -z "${SESSION_ID}" ]]; then
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

loop_out "Session ID: ${SESSION_ID}"

loop_out "======================================================"
loop_out "=                    Loop Done                       ="
loop_out "======================================================"

printf '%s\n' "${SESSION_ID}"
