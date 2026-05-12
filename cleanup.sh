#!/usr/bin/env bash
# 按 repo 名称清理本机产物（与 pack.sh / loop.sh 命名规则对齐）。
# 删除：
#   1) ~/.claude/projects/-home-$USER-projects-${repo//_/-}   （与 loop.sh 中 REPO_SLUG 一致）
#   2) $OUTPUTS_DIR/code-understand-<repo>/                   （与 pack.sh 中 OUT 一致）
#   3) $OUTPUTS_DIR/code-understand-<repo>.zip                （与 zip.sh 在 OUT 上生成的包名一致）
#
# 依赖：bash、rm
# 用法: ./cleanup_repo.sh <repo>
# 可选环境变量 OUTPUTS_DIR（默认 $HOME/outputs，与 pack.sh 相同）
set -euo pipefail

: "${OUTPUTS_DIR:=${HOME}/outputs}"

USER="${USER:-$(id -un 2>/dev/null || printf unknown)}"

clean_out() { printf '[cleanup_repo.sh][%s]%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
clean_err() { printf '[cleanup_repo.sh][%s]%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; }

usage() {
  clean_err "用法: $0 <repo>"
  clean_err "  repo  与 pack.sh 第二参数相同（projects 下目录名），如 react、my_lib"
  clean_err "  将删除 Claude 会话目录、outputs 下 code-understand-<repo> 目录及同名 zip"
  clean_err "  可选: OUTPUTS_DIR（默认 \$HOME/outputs）"
}

[[ "${#}" -eq 1 ]] || { usage; exit 1; }
repo="${1}"
[[ -n "${repo}" ]] || { usage; exit 1; }
[[ "${repo}" != */* && "${repo}" != *..* ]] || { clean_err "错误: repo 名称非法（不能含 / 或 ..）"; exit 1; }

# loop.sh：REPO_SLUG="${REPO//_/-}"
REPO_SLUG="${repo//_/-}"
CLAUDE_PROJECT_DIR="${HOME}/.claude/projects/-home-${USER}-projects-${REPO_SLUG}"

# pack.sh：OUT="${OUTPUTS_DIR}/code-understand-${repo}"；zip.sh：在 OUT 的父目录生成 basename(OUT).zip
OUT_DIR="${OUTPUTS_DIR}/code-understand-${repo}"
ZIP_FILE="${OUTPUTS_DIR}/code-understand-${repo}.zip"

clean_out "======================================================"
clean_out "repo=${repo} REPO_SLUG=${REPO_SLUG}"
clean_out "OUTPUTS_DIR=${OUTPUTS_DIR}"
clean_out "======================================================"

if [[ -d "${CLAUDE_PROJECT_DIR}" ]]; then
  clean_out "删除 Claude 项目目录: ${CLAUDE_PROJECT_DIR}"
  rm -rf -- "${CLAUDE_PROJECT_DIR}"
else
  clean_out "跳过（不存在）: ${CLAUDE_PROJECT_DIR}"
fi

if [[ -d "${OUT_DIR}" ]]; then
  clean_out "删除 outputs 目录: ${OUT_DIR}"
  rm -rf -- "${OUT_DIR}"
else
  clean_out "跳过（不存在）: ${OUT_DIR}"
fi

if [[ -f "${ZIP_FILE}" ]]; then
  clean_out "删除 zip: ${ZIP_FILE}"
  rm -f -- "${ZIP_FILE}"
else
  clean_out "跳过（不存在）: ${ZIP_FILE}"
fi

clean_out "清理完成"
exit 0
