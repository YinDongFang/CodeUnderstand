#!/usr/bin/env bash
# 用法: ./run.sh <folder> [turns]
#   folder: 目标项目名，对应路径 ~/projects/<folder>
#   turns:  对话轮数，默认 38

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECTS_DIR="${HOME}/projects"
TURNS=38
MAX_TARGET_ATTEMPTS=4
RUN_FAILED=0
# agent 侧通过此软链读代码库，避免跨路径权限问题（相对 agent 目录）
AGENT_TARGET_LINK_NAME="repo"

usage() {
  echo "用法: $0 <folder> [turns]" >&2
  echo "  folder  目标文件夹（~/projects/<folder>）" >&2
  echo "  turns   对话轮数，默认 38" >&2
  exit 1
}

if [[ $# -lt 1 ]]; then
  usage
fi

FOLDER="$1"
if [[ $# -ge 2 ]]; then
  if [[ "$2" =~ ^[0-9]+$ ]] && [[ "$2" -gt 0 ]]; then
    TURNS="$2"
  else
    echo "[run.sh][debug] 错误: 第二参数 turns 必须是正整数: $2，任务中断" >&2
    exit 1
  fi
fi

TARGET_PATH="${PROJECTS_DIR}/${FOLDER}"
AGENT_DIR="${SCRIPT_DIR}/agent"
AGENT_TARGET_LINK="${AGENT_DIR}/${AGENT_TARGET_LINK_NAME}"

cleanup_agent_target_link() {
  if [[ -n "${AGENT_TARGET_LINK:-}" && -L "$AGENT_TARGET_LINK" ]]; then
    echo "[run.sh][debug] 移除 agent 下目标软链: ${AGENT_TARGET_LINK}"
    rm -f "$AGENT_TARGET_LINK"
  fi
}

echo "[run.sh][debug] SCRIPT_DIR=${SCRIPT_DIR}"
echo "[run.sh][debug] FOLDER=${FOLDER} TURNS=${TURNS}"
echo "[run.sh][debug] TARGET_PATH=${TARGET_PATH}"
echo "[run.sh][debug] AGENT_DIR=${AGENT_DIR}"

# --- 步骤 1: 检查目标目录是否存在 ---
if [[ ! -d "$TARGET_PATH" ]]; then
  echo "[run.sh][debug][步骤1] 错误: 目标目录不存在: ${TARGET_PATH}，任务中断" >&2
  exit 1
fi

# --- 步骤 2: 在 agent 目录下创建指向目标仓库的软链（供 Learner 读代码，避免跨路径权限）---
echo "[run.sh][debug][步骤2] 目标软链: ${AGENT_TARGET_LINK} -> ${TARGET_PATH}"
if [[ -e "$AGENT_TARGET_LINK" ]] || [[ -L "$AGENT_TARGET_LINK" ]]; then
  echo "[run.sh][debug][步骤2] 已存在同名路径，删除后重新创建: ${AGENT_TARGET_LINK}"
  rm -rf "$AGENT_TARGET_LINK"
fi
if ! ln -s "$TARGET_PATH" "$AGENT_TARGET_LINK"; then
  echo "[run.sh][debug][步骤2] ln -s 失败，任务中断" >&2
  exit 1
fi
trap cleanup_agent_target_link EXIT

# --- 步骤 3: agent 目录生成第一个问题 ---
echo "[run.sh][debug][步骤3] cd ${AGENT_DIR}"
cd "$AGENT_DIR" || { echo "[run.sh][debug][步骤3] 无法进入 agent 目录，任务中断" >&2; exit 1; }

STEP3_PROMPT="读取 ${AGENT_TARGET_LINK} 目录（软链到目标仓库），生成第一个问题"
echo "[run.sh][debug][步骤3] 运行: claude -p -n ${FOLDER} ${STEP3_PROMPT}"

set +e
QUESTION=$(claude -p -n "$FOLDER" "$STEP3_PROMPT" 2>&1)
STEP3_EXIT=$?
set -e

echo "[run.sh][debug][步骤3] --- claude 输出（第一个问题）开始 ---"
printf '%s\n' "$QUESTION"
echo "[run.sh][debug][步骤3] --- claude 输出结束 ---"

if [[ "$STEP3_EXIT" -ne 0 ]]; then
  echo "[run.sh][debug][步骤3] claude 失败，任务中断" >&2
  exit 1
fi

if [[ -z "${QUESTION//[[:space:]]/}" ]]; then
  echo "[run.sh][debug][步骤3] 错误: 第一个问题为空，任务中断" >&2
  exit 1
fi

# --- 步骤 4: 循环 turns 轮 ---
for ((i = 1; i <= TURNS; i++)); do
  echo "[run.sh][debug][步骤4] ========== 第 ${i}/${TURNS} 轮 =========="

  echo "[run.sh][debug][步骤4.1] cd 项目目录 ${TARGET_PATH}"
  cd "$TARGET_PATH" || { echo "[run.sh][debug][步骤4.1] 无法进入项目目录" >&2; RUN_FAILED=1; break; }

  # 4.2 在真实仓库目录执行 Reader，首轮无 -c，之后带 -c；失败重试共 3 次重试（最多 4 次尝试）
  attempt=1
  success=0
  RESULT=""
  while [[ $attempt -le $MAX_TARGET_ATTEMPTS ]]; do
    echo "[run.sh][debug][步骤4.2] 第 ${i} 轮，尝试 ${attempt}/${MAX_TARGET_ATTEMPTS}"
    set +e
    if [[ "$i" -eq 1 ]]; then
      RESULT=$(claude -p "$QUESTION" 2>&1)
    else
      RESULT=$(claude -p -c "$QUESTION" 2>&1)
    fi
    ROUND_EXIT=$?
    set -e

    echo "[run.sh][debug][步骤4.2] --- Reader 输出开始 ---"
    printf '%s\n' "$RESULT"
    echo "[run.sh][debug][步骤4.2] --- Reader 输出结束 ---"

    if [[ "$ROUND_EXIT" -eq 0 ]]; then
      success=1
      break
    fi
    echo "[run.sh][debug][步骤4.2] 失败，将重试" >&2
    attempt=$((attempt + 1))
  done

  if [[ "$success" -ne 1 ]]; then
    echo "[run.sh][debug][步骤4.2] 第 ${i} 轮在 ${MAX_TARGET_ATTEMPTS} 次尝试后仍失败" >&2
    RUN_FAILED=1
    break
  fi

  # 4.3 agent：把 Reader 输出作为单一参数传入（bash 的 "$RESULT" 作为 argv 一项，无需对引号做二次转义）
  echo "[run.sh][debug][步骤4.3] cd ${AGENT_DIR}"
  cd "$AGENT_DIR" || { echo "[run.sh][debug][步骤4.3] 无法进入 agent" >&2; RUN_FAILED=1; break; }

  echo "[run.sh][debug][步骤4.3] 运行: claude -p -r ${FOLDER} \"\$RESULT\"（RESULT 长度=${#RESULT}）"
  set +e
  NEXT_QUESTION=$(claude -p -r "$FOLDER" "$RESULT" 2>&1)
  STEP43_EXIT=$?
  set -e

  echo "[run.sh][debug][步骤4.3] --- Learner 输出（下一问）开始 ---"
  printf '%s\n' "$NEXT_QUESTION"
  echo "[run.sh][debug][步骤4.3] --- Learner 输出结束 ---"

  if [[ "$STEP43_EXIT" -ne 0 ]]; then
    echo "[run.sh][debug][步骤4.3] claude 失败" >&2
    RUN_FAILED=1
    break
  fi

  if [[ "$i" -lt "$TURNS" ]]; then
    if [[ -z "${NEXT_QUESTION//[[:space:]]/}" ]]; then
      echo "[run.sh][debug][步骤4.3] 错误: 下一轮问题为空" >&2
      RUN_FAILED=1
      break
    fi
    QUESTION="$NEXT_QUESTION"
  fi
done

if [[ "$RUN_FAILED" -ne 0 ]]; then
  echo "[run.sh][debug] 流程未全部成功，任务中断" >&2
  exit 1
fi

# --- 步骤 5: 成功后清理 git ---
echo "[run.sh][debug][步骤5] 全部轮次成功，开始清理"
cd "$TARGET_PATH" || exit 1

if [[ -d .git ]]; then
  echo "[run.sh][debug][步骤5] 执行: git reset --hard HEAD"
  if ! git reset --hard HEAD; then
    echo "[run.sh][debug][步骤5] git reset 失败" >&2
    exit 1
  fi
  echo "[run.sh][debug][步骤5] 执行: rm -rf .git"
  if ! rm -rf .git; then
    echo "[run.sh][debug][步骤5] 删除 .git 失败" >&2
    exit 1
  fi
  echo "[run.sh][debug][步骤5] 清理完成"
else
  echo "[run.sh][debug][步骤5] 未找到 .git，跳过 reset 与删除"
fi

echo "[run.sh][debug] 任务完成"
