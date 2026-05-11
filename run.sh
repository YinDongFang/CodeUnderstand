#!/usr/bin/env bash
# 用法: ./run.sh <GitHub ZIP URL>
#   ZIP URL 与 download.sh 相同，例如:
#   ./run.sh https://github.com/storybookjs/storybook/archive/refs/heads/next.zip
#
# 逻辑概要:
#   1) 从 URL 解析 user/repo/branch（与 download.sh 一致）
#   2) 目标目录 ~/projects/<repo> 不存在则调用 download.sh
#   3) 从 ./questions/<repo>.txt 读取题目（同上路径），每行一题，忽略空行与 # 行；最多取 38 题，超出丢弃
#   4) cd 到目标目录，按列表循环 claude：首轮 --output-format json 取 uuid；后续 -r uuid -c；每轮带重试

set -eu

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
PROJECTS_DIR="${HOME}/projects"
MAX_TARGET_ATTEMPTS=4
MAX_QUESTIONS=38
RUN_FAILED=0

usage() {
  echo "用法: $0 <GitHub ZIP URL>" >&2
  echo "  示例: $0 https://github.com/owner/repo/archive/refs/heads/main.zip" >&2
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
  echo "[run.sh] 错误: URL 不符合 GitHub archive ZIP 格式（与 download.sh 一致）。" >&2
  echo "  期望: https://github.com/<user>/<repo>/archive/refs/heads/<branch>.zip" >&2
  exit 1
fi

TARGET_PATH="${PROJECTS_DIR}/${REPO}"
echo "[run.sh] 解析: ${_GH_USER}/${REPO} @ ${BRANCH}"
echo "[run.sh] 目标目录: ${TARGET_PATH}"

# --- 若目录不存在则下载 ---
if [[ ! -d "$TARGET_PATH" ]]; then
  echo "[run.sh] 目标目录不存在，调用 download.sh ..."
  if [[ ! -x "${SCRIPT_DIR}/download.sh" ]] && [[ ! -f "${SCRIPT_DIR}/download.sh" ]]; then
    echo "[run.sh] 错误: 未找到 ${SCRIPT_DIR}/download.sh" >&2
    exit 1
  fi
  bash "${SCRIPT_DIR}/download.sh" "$ZIP_URL" || { echo "[run.sh] download.sh 失败" >&2; exit 1; }
fi

if [[ ! -d "$TARGET_PATH" ]]; then
  echo "[run.sh] 错误: 下载后仍不存在目录: ${TARGET_PATH}" >&2
  exit 1
fi

# --- 问题列表：questions/<repo>.txt（先当前目录 ./questions，再脚本目录下 questions）---
QUESTIONS_FILE=""
if [[ -f "${PWD}/questions/${REPO}.txt" ]]; then
  QUESTIONS_FILE="${PWD}/questions/${REPO}.txt"
elif [[ -f "${SCRIPT_DIR}/questions/${REPO}.txt" ]]; then
  QUESTIONS_FILE="${SCRIPT_DIR}/questions/${REPO}.txt"
else
  echo "[run.sh] 错误: 未找到问题列表文件 questions/${REPO}.txt" >&2
  echo "  已查找: ${PWD}/questions/${REPO}.txt 与 ${SCRIPT_DIR}/questions/${REPO}.txt" >&2
  exit 1
fi

echo "[run.sh] 问题列表: ${QUESTIONS_FILE}"

mapfile -t QUESTIONS < <(
  sed 's/\r$//' "$QUESTIONS_FILE" |
    grep -v '^[[:space:]]*#' |
    sed '/^[[:space:]]*$/d'
)

if [[ "${#QUESTIONS[@]}" -eq 0 ]]; then
  echo "[run.sh] 错误: ${QUESTIONS_FILE} 中没有有效问题行" >&2
  exit 1
fi

if [[ "${#QUESTIONS[@]}" -gt "$MAX_QUESTIONS" ]]; then
  echo "[run.sh] 有效题目共 ${#QUESTIONS[@]} 行，仅使用前 ${MAX_QUESTIONS} 题" >&2
  QUESTIONS=("${QUESTIONS[@]:0:$MAX_QUESTIONS}")
fi

echo "[run.sh] 共 ${#QUESTIONS[@]} 个问题，进入项目目录执行 claude"

cd "$TARGET_PATH" || { echo "[run.sh] 无法 cd 到 ${TARGET_PATH}" >&2; exit 1; }

SESSION_ID=""
TOTAL="${#QUESTIONS[@]}"

for ((i = 0; i < TOTAL; i++)); do
  Q="${QUESTIONS[$i]}"
  round=$((i + 1))
  echo "[run.sh] ========== 第 ${round}/${TOTAL} 题 =========="
  echo "[run.sh] Q: $(_fold_one_line "$Q")"

  attempt=1
  success=0
  RAW=""
  ROUND_EXIT=0

  while [[ $attempt -le $MAX_TARGET_ATTEMPTS ]]; do
    echo "[run.sh] 尝试 ${attempt}/${MAX_TARGET_ATTEMPTS}"
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
      echo "[run.sh] claude 退出码 ${ROUND_EXIT}，将重试" >&2
      attempt=$((attempt + 1))
      continue
    fi

    if [[ "$i" -eq 0 ]]; then
      _r=$(printf '%s' "$RAW" | tr -d '\r')
      _doc=$(printf '%s' "$_r" | jq -ec . 2>/dev/null) || _doc=$(printf '%s' "$_r" | jq -Rrs 'split("\n")|map(select(test("^\\s*\\{")))|map(try fromjson catch empty)|map(select(type=="object"))|last')
      if ! jq -e 'type=="object"' <<<"$_doc" >/dev/null 2>&1; then
        echo "[run.sh] 首轮 JSON 解析失败，输出摘要:" >&2
        echo "[run.sh] A: $(_preview_text "$RAW")" >&2
        attempt=$((attempt + 1))
        continue
      fi
      SESSION_ID=$(jq -r '(.session_id//"")|tostring' <<<"$_doc")
      if [[ -z "$SESSION_ID" || "$SESSION_ID" == "null" ]]; then
        echo "[run.sh] 首轮未解析到 uuid/session_id，将重试" >&2
        attempt=$((attempt + 1))
        continue
      fi
      OUT_TEXT=$(jq -r '.result|if .==null then "" elif type=="string" then . elif type=="boolean" or type=="number" then tostring else tojson end' <<<"$_doc")
      echo "[run.sh] session(uuid)=${SESSION_ID}" >&2
      ANS="$OUT_TEXT"
    else
      ANS="$RAW"
    fi

    echo "[run.sh] A: $(_preview_text "$ANS")"

    success=1
    break
  done

  if [[ "$success" -ne 1 ]]; then
    echo "[run.sh] 第 ${round} 题在 ${MAX_TARGET_ATTEMPTS} 次尝试后仍失败" >&2
    RUN_FAILED=1
    break
  fi
done

if [[ "$RUN_FAILED" -ne 0 ]]; then
  echo "[run.sh] 流程未全部成功" >&2
  exit 1
fi

# --- 成功后可选：清理 git（与旧逻辑一致）---
echo "[run.sh] 全部题目完成，开始清理 git 状态"
cd "$TARGET_PATH" || exit 1
if [[ -d .git ]]; then
  if ! git reset --hard HEAD; then
    echo "[run.sh] git reset 失败" >&2
    exit 1
  fi
  if ! rm -rf .git; then
    echo "[run.sh] 删除 .git 失败" >&2
    exit 1
  fi
  echo "[run.sh] 已 reset 并移除 .git"
else
  echo "[run.sh] 未找到 .git，跳过清理"
fi

echo "[run.sh] 任务完成"
