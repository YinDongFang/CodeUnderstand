#!/usr/bin/env bash
# 用法: ./run.sh <GitHub ZIP URL>
#   ZIP URL 与 download.sh 相同，例如:
#   ./run.sh https://github.com/storybookjs/storybook/archive/refs/heads/next.zip
#
# 逻辑概要:
#   1) 从 URL 解析 user/repo/branch（与 download.sh 一致）
#   2) 目标目录 ${PROJECTS_DIR}/<repo> 不存在则调用 download.sh（PROJECTS_DIR 默认 \$HOME/projects）
#   3) 从 ./questions/<repo>.txt 读取题目（同上路径），每行一题，忽略空行与 # 行；最多取 38 题，超出丢弃
#   4) cd 到目标目录，按列表循环 claude：首轮 --output-format json 取 session_id；后续 -r session_id -c；每轮带重试
#   5) 成功后调用 clean.py <REPO> <SESSION_ID>，清理 ~/.claude/projects 中该会话 JSONL 的重复对话

set -eu

run_out() { printf '[run.sh][%s]%s\n' "$(date '+%Y%m%d%H%M%S')" "$*"; }
run_err() { printf '[run.sh][%s]%s\n' "$(date '+%Y%m%d%H%M%S')" "$*" >&2; }

# 单行：换行压空格
_fold_one_line() {
  local s=${1//$'\r'/}
  s=${s//$'\n'/ }
  printf '%s' "$s"
}

# 单行摘要：最多 30 字符，超出加 ...
_preview_text() {
  local s=$(_fold_one_line "$1") n=30
  if ((${#s} > n)); then printf '%s...' "${s:0:n}"; else printf '%s' "$s"; fi
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${PROJECTS_DIR:=${HOME}/projects}"
MAX_TARGET_ATTEMPTS=4
MAX_QUESTIONS=38
RUN_FAILED=0

usage() {
  run_err "用法: $0 <GitHub ZIP URL>"
  run_err "  示例: $0 https://github.com/owner/repo/archive/refs/heads/main.zip"
  exit 1
}

if [[ $# -ne 1 ]]; then
  usage
fi

ZIP_URL="$1"

if [[ "$ZIP_URL" =~ github\.com/([^/]+)/([^/]+)/archive/refs/heads/([^.]+)\.zip ]]; then
  _GH_USER="${BASH_REMATCH[1]}"
  REPO="${BASH_REMATCH[2]}"
  BRANCH="${BASH_REMATCH[3]}"
else
  run_err "错误: URL 不符合 GitHub archive ZIP 格式（与 download.sh 一致）。"
  run_err "  期望: https://github.com/<user>/<repo>/archive/refs/heads/<branch>.zip"
  exit 1
fi

TARGET_PATH="${PROJECTS_DIR}/${REPO}"
run_out "解析: ${_GH_USER}/${REPO} @ ${BRANCH}"
run_out "目标目录: ${TARGET_PATH}"

# --- 若目录不存在则下载 ---
if [[ ! -d "$TARGET_PATH" ]]; then
  run_out "目标目录不存在，调用 download.sh ..."
  if [[ ! -x "${SCRIPT_DIR}/download.sh" ]] && [[ ! -f "${SCRIPT_DIR}/download.sh" ]]; then
    run_err "错误: 未找到 ${SCRIPT_DIR}/download.sh"
    exit 1
  fi
  bash "${SCRIPT_DIR}/download.sh" "$ZIP_URL" || { run_err "download.sh 失败"; exit 1; }
fi

if [[ ! -d "$TARGET_PATH" ]]; then
  run_err "错误: 下载后仍不存在目录: ${TARGET_PATH}"
  exit 1
fi

# --- 问题列表：questions/<repo>.txt（先当前目录 ./questions，再脚本目录下 questions）---
QUESTIONS_FILE=""
if [[ -f "${PWD}/questions/${REPO}.txt" ]]; then
  QUESTIONS_FILE="${PWD}/questions/${REPO}.txt"
elif [[ -f "${SCRIPT_DIR}/questions/${REPO}.txt" ]]; then
  QUESTIONS_FILE="${SCRIPT_DIR}/questions/${REPO}.txt"
else
  run_err "错误: 未找到问题列表文件 questions/${REPO}.txt"
  run_err "  已查找: ${PWD}/questions/${REPO}.txt 与 ${SCRIPT_DIR}/questions/${REPO}.txt"
  exit 1
fi

run_out "问题列表: ${QUESTIONS_FILE}"

mapfile -t QUESTIONS < <(
  sed 's/\r$//' "$QUESTIONS_FILE" |
    grep -v '^[[:space:]]*#' |
    sed '/^[[:space:]]*$/d'
)

if [[ "${#QUESTIONS[@]}" -eq 0 ]]; then
  run_err "错误: ${QUESTIONS_FILE} 中没有有效问题行"
  exit 1
fi

if [[ "${#QUESTIONS[@]}" -gt "$MAX_QUESTIONS" ]]; then
  run_err "有效题目共 ${#QUESTIONS[@]} 行，仅使用前 ${MAX_QUESTIONS} 题"
  QUESTIONS=("${QUESTIONS[@]:0:$MAX_QUESTIONS}")
fi

run_out "共 ${#QUESTIONS[@]} 个问题，进入项目目录执行 claude"

cd "$TARGET_PATH" || { run_err "无法 cd 到 ${TARGET_PATH}"; exit 1; }

SESSION_ID=""
TOTAL="${#QUESTIONS[@]}"

for ((i = 0; i < TOTAL; i++)); do
  Q="${QUESTIONS[$i]}"
  round=$((i + 1))
  run_out "========== 第 ${round}/${TOTAL} 题 =========="
  run_out "Q: $(_fold_one_line "$Q")"

  attempt=1
  success=0
  RAW=""
  ROUND_EXIT=0

  while [[ $attempt -le $MAX_TARGET_ATTEMPTS ]]; do
    run_out "尝试 ${attempt}/${MAX_TARGET_ATTEMPTS}"
    set +e
    if [[ "$i" -eq 0 ]]; then
      RAW=$(claude --output-format json -p "$Q" 2>&1)
      ROUND_EXIT=$?
    else
      RAW=$(claude -r "$SESSION_ID" -c -p "$Q" 2>&1)
      ROUND_EXIT=$?
    fi
    set -e

    if [[ "$ROUND_EXIT" -ne 0 ]]; then
      run_err "claude 退出码 ${ROUND_EXIT}，将重试"
      attempt=$((attempt + 1))
      continue
    fi

    if [[ "$i" -eq 0 ]]; then
      _r=$(printf '%s' "$RAW" | tr -d '\r')
      _doc=$(printf '%s' "$_r" | jq -ec . 2>/dev/null) || _doc=$(printf '%s' "$_r" | jq -Rrs 'split("\n")|map(select(test("^\\s*\\{")))|map(try fromjson catch empty)|map(select(type=="object"))|last')
      if ! jq -e 'type=="object"' <<<"$_doc" >/dev/null 2>&1; then
        run_err "首轮 JSON 解析失败，输出摘要:"
        run_err "A: $(_preview_text "$RAW")"
        attempt=$((attempt + 1))
        continue
      fi
      SESSION_ID=$(jq -r '(.session_id//"")|tostring' <<<"$_doc")
      if [[ -z "$SESSION_ID" || "$SESSION_ID" == "null" ]]; then
        run_err "首轮未解析到 uuid/session_id，将重试"
        attempt=$((attempt + 1))
        continue
      fi
      OUT_TEXT=$(jq -r '.result|if .==null then "" elif type=="string" then . elif type=="boolean" or type=="number" then tostring else tojson end' <<<"$_doc")
      run_err "session(uuid)=${SESSION_ID}"
      ANS="$OUT_TEXT"
    else
      ANS="$RAW"
    fi

    run_out "A: $(_preview_text "$ANS")"

    success=1
    break
  done

  if [[ "$success" -ne 1 ]]; then
    run_err "第 ${round} 题在 ${MAX_TARGET_ATTEMPTS} 次尝试后仍失败"
    RUN_FAILED=1
    break
  fi
done

if [[ "$RUN_FAILED" -ne 0 ]]; then
  run_err "流程未全部成功"
  exit 1
fi

# --- 成功后可选：清理 git（与旧逻辑一致）---
run_out "全部题目完成，开始清理 git 状态"
cd "$TARGET_PATH" || exit 1
if [[ -d .git ]]; then
  if ! git reset --hard HEAD; then
    run_err "git reset 失败"
    exit 1
  fi
  if ! rm -rf .git; then
    run_err "删除 .git 失败"
    exit 1
  fi
  run_out "已 reset 并移除 .git"
else
  run_out "未找到 .git，跳过清理"
fi

run_out "调用 clean.py 去重会话（target=${REPO} session=${SESSION_ID}）"
if [[ ! -f "${SCRIPT_DIR}/clean.py" ]]; then
  run_err "错误: 未找到 ${SCRIPT_DIR}/clean.py"
  exit 1
fi
python3 "${SCRIPT_DIR}/clean.py" "$REPO" "$SESSION_ID" || {
  run_err "clean.py 执行失败"
  exit 1
}

run_out "任务完成"
