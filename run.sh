#!/usr/bin/env bash
# 用法: ./run.sh <GitHub ZIP URL>
#   ZIP URL 与 download.sh 相同，例如:
#   ./run.sh https://github.com/storybookjs/storybook/archive/refs/heads/next.zip
#
# 逻辑概要:
#   0) 调用 evaluate.sh：若为 easy 难度则直接退出（不跑 loop / build / pack）
#   1) 从 URL 解析 user/repo/branch（与 download.sh 一致）
#   2) 目标目录 ${PROJECTS_DIR}/<repo> 不存在则调用 download.sh
#   3) 调用 loop.sh 完成多轮 claude 对话（stdout 仅返回 session_id）
#   4) 清理 git、调用 clean.py
#   5) 调用 build.sh（repo / session）再调用 pack.sh（github / repo / session）
#   全程 stdout/stderr 同时写入 ${CODE_UNDERSTAND_STATE_ROOT}/logs/<repo>_YYYY-MM-DD_HH-MM-SS.log
#   （默认 CODE_UNDERSTAND_STATE_ROOT=$HOME/Documents；URL 解析成功后启用）
set -eu
set -o pipefail

run_out() { printf '[run.sh][%s]%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
run_err() { printf '[run.sh][%s]%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOOP_SH="${SCRIPT_DIR}/loop.sh"
BUILD_SH="${SCRIPT_DIR}/build.sh"
PACK_SH="${SCRIPT_DIR}/pack.sh"
EVALUATE_SH="${SCRIPT_DIR}/evaluate.sh"
: "${PROJECTS_DIR:=${HOME}/projects}"
: "${CODE_UNDERSTAND_STATE_ROOT:=${HOME}/Documents}"
export CODE_UNDERSTAND_STATE_ROOT

usage() {
  run_err "用法: $0 <GitHub ZIP URL>"
  run_err "  示例: $0 https://github.com/owner/repo/archive/refs/heads/main.zip"
  exit 1
}

[[ "${#}" -eq 1 ]] || usage
ZIP_URL="${1}"

if [[ "${ZIP_URL}" =~ github\.com/([^/]+)/([^/]+)/archive/refs/heads/([^.]+)\.zip ]]; then
  _GH_USER="${BASH_REMATCH[1]}"
  REPO="${BASH_REMATCH[2]}"
  BRANCH="${BASH_REMATCH[3]}"
else
  run_err "错误: URL 不符合 GitHub archive ZIP 格式（与 download.sh 一致）。"
  run_err "  期望: https://github.com/<user>/<repo>/archive/refs/heads/<branch>.zip"
  exit 1
fi

mkdir -p "${CODE_UNDERSTAND_STATE_ROOT}/logs"
LOG_FILE="${CODE_UNDERSTAND_STATE_ROOT}/logs/${REPO}_$(date '+%Y-%m-%d_%H-%M-%S').log"
exec > >(tee -a "${LOG_FILE}") 2>&1
run_out "日志文件: ${LOG_FILE}"

cd "${SCRIPT_DIR}"
git reset --hard HEAD
git pull
chmod -R 777 "${SCRIPT_DIR}"

TARGET_PATH="${PROJECTS_DIR}/${REPO}"
run_out "解析: ${_GH_USER}/${REPO} @ ${BRANCH}"
run_out "目标目录: ${TARGET_PATH}"

if [[ ! -d "${TARGET_PATH}" ]]; then
  run_out "目标目录不存在，调用 download.sh ..."
  if [[ ! -f "${SCRIPT_DIR}/download.sh" ]]; then
    run_err "错误: 未找到 ${SCRIPT_DIR}/download.sh"
    exit 1
  fi
  bash "${SCRIPT_DIR}/download.sh" "${ZIP_URL}" || { run_err "download.sh 失败"; exit 1; }
fi

if [[ ! -d "${TARGET_PATH}" ]]; then
  run_err "错误: 下载后仍不存在目录: ${TARGET_PATH}"
  exit 1
fi

if [[ ! -f "${EVALUATE_SH}" ]]; then
  run_err "错误: 未找到 ${EVALUATE_SH}"
  exit 1
fi
difficulty="$(bash "${EVALUATE_SH}" "${TARGET_PATH}" | tr -d '\r' | head -n1)"
[[ -n "${difficulty}" ]] || difficulty="medium"
run_out "evaluate.sh → difficulty=${difficulty}"
if [[ "${difficulty}" == "easy" ]]; then
  run_out "easy 项目，跳过后续（loop / clean / build / pack）并退出。"
  exit 0
fi

SESSION_ID="$(bash "${LOOP_SH}" "${TARGET_PATH}" "${REPO}" | tr -d '\r' | head -n1)"
[[ -n "${SESSION_ID}" ]] || { run_err "错误: 未取得 session_id"; exit 1; }
run_out "SESSION_ID=${SESSION_ID}"

run_out "全部题目完成，开始清理 git 状态"
cd "${TARGET_PATH}" || exit 1
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
python3 "${SCRIPT_DIR}/clean.py" "${REPO}" "${SESSION_ID}" || {
  run_err "clean.py 执行失败"
  exit 1
}

GITHUB_URL="https://github.com/${_GH_USER}/${REPO}"

if [[ ! -f "${BUILD_SH}" ]]; then
  run_err "错误: 未找到 ${BUILD_SH}"
  exit 1
fi
run_out "调用 build.sh（repo=${REPO} session=${SESSION_ID}）"
bash "${BUILD_SH}" "${REPO}" "${SESSION_ID}" || {
  run_err "build.sh 执行失败"
  exit 1
}

if [[ ! -f "${PACK_SH}" ]]; then
  run_err "错误: 未找到 ${PACK_SH}"
  exit 1
fi
run_out "调用 pack.sh（github / repo / session）"
bash "${PACK_SH}" "${GITHUB_URL}" "${REPO}" "${SESSION_ID}" || {
  run_err "pack.sh 执行失败"
  exit 1
}

run_out "任务完成"
