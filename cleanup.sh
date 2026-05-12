#!/usr/bin/env bash
# 按 repo 名称清理本机产物（与 pack.sh / loop.sh 命名规则对齐）。
# 默认可清理三类路径；若在 repo 之后传入选择项，则只清理指定项：
#   claude   ~/.claude/projects/-home-$USER-projects-${repo//_/-}
#   output   $OUTPUTS_DIR/code-understand-<repo>/  （目录；别名 outputs）
#   zip      $OUTPUTS_DIR/code-understand-<repo>.zip
#
# 依赖：bash、rm
# 用法: ./cleanup.sh <repo> [claude|output|outputs|zip ...]
# 无额外参数时：三类全部清理。
# 可选环境变量 OUTPUTS_DIR（默认 $HOME/outputs，与 pack.sh 相同）
set -euo pipefail

: "${OUTPUTS_DIR:=${HOME}/outputs}"

USER="${USER:-$(id -un 2>/dev/null || printf unknown)}"

clean_out() { printf '[cleanup.sh][%s]%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
clean_err() { printf '[cleanup.sh][%s]%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; }

usage() {
  clean_err "用法: $0 <repo> [claude|output|outputs|zip ...]"
  clean_err "  repo     与 pack.sh 第二参数相同（projects 下目录名），如 react、my_lib"
  clean_err "  无额外参数：删除 Claude 项目目录、outputs 下 code-understand-<repo> 目录及同名 zip"
  clean_err "  有额外参数：只删除列出的项（可多个，顺序任意）"
  clean_err "    claude          Claude 会话目录（-home-\$USER-projects-<repo_slug>）"
  clean_err "    output|outputs  \$OUTPUTS_DIR/code-understand-<repo>/"
  clean_err "    zip             \$OUTPUTS_DIR/code-understand-<repo>.zip"
  clean_err "  可选: OUTPUTS_DIR（默认 \$HOME/outputs）"
}

[[ "${#}" -ge 1 ]] || { usage; exit 1; }
repo="${1}"
shift
[[ -n "${repo}" ]] || { usage; exit 1; }
[[ "${repo}" != */* && "${repo}" != *..* ]] || { clean_err "错误: repo 名称非法（不能含 / 或 ..）"; exit 1; }

REPO_SLUG="${repo//_/-}"
CLAUDE_PROJECT_DIR="${HOME}/.claude/projects/-home-${USER}-projects-${REPO_SLUG}"
OUT_DIR="${OUTPUTS_DIR}/code-understand-${repo}"
ZIP_FILE="${OUTPUTS_DIR}/code-understand-${repo}.zip"

do_claude=1
do_output=1
do_zip=1

if (($# > 0)); then
  do_claude=0
  do_output=0
  do_zip=0
  for t in "$@"; do
    tl="$(printf '%s' "${t}" | tr '[:upper:]' '[:lower:]')"
    case "${tl}" in
      claude) do_claude=1 ;;
      output | outputs) do_output=1 ;;
      zip) do_zip=1 ;;
      *)
        clean_err "错误: 未知选择项 '${t}'（允许: claude output outputs zip）"
        usage
        exit 1
        ;;
    esac
  done
fi

clean_out "======================================================"
clean_out "repo=${repo} REPO_SLUG=${REPO_SLUG}"
clean_out "OUTPUTS_DIR=${OUTPUTS_DIR}"
if (($# == 0)); then
  clean_out "模式: 全部清理（claude + output + zip）"
else
  clean_out "模式: 仅清理 — claude=$([[ ${do_claude} -eq 1 ]] && echo yes || echo no) output=$([[ ${do_output} -eq 1 ]] && echo yes || echo no) zip=$([[ ${do_zip} -eq 1 ]] && echo yes || echo no)"
fi
clean_out "======================================================"

if ((do_claude)); then
  if [[ -d "${CLAUDE_PROJECT_DIR}" ]]; then
    clean_out "删除 Claude 项目目录: ${CLAUDE_PROJECT_DIR}"
    rm -rf -- "${CLAUDE_PROJECT_DIR}"
  else
    clean_out "跳过（不存在）: ${CLAUDE_PROJECT_DIR}"
  fi
fi

if ((do_output)); then
  if [[ -d "${OUT_DIR}" ]]; then
    clean_out "删除 outputs 目录: ${OUT_DIR}"
    rm -rf -- "${OUT_DIR}"
  else
    clean_out "跳过（不存在）: ${OUT_DIR}"
  fi
fi

if ((do_zip)); then
  if [[ -f "${ZIP_FILE}" ]]; then
    clean_out "删除 zip: ${ZIP_FILE}"
    rm -f -- "${ZIP_FILE}"
  else
    clean_out "跳过（不存在）: ${ZIP_FILE}"
  fi
fi

clean_out "清理完成"
exit 0
