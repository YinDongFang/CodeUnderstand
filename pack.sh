#!/usr/bin/env bash
# 打包输出目录（不含 build：导出会话与 doc 由同目录 build.sh 单独执行）。
# 依赖：bash、cp、jq、curl、python3、同目录 classify.sh（需 claude）/ evaluate.sh / rewrite.py / zip.sh（及 zip）
#
# 环境变量（可选，有默认值；由调用方保证为已展开路径）：
#   OUTPUTS_DIR                  输出根目录，默认 ${HOME}/outputs
#   PROJECTS_DIR                 本地项目根目录，默认 ${HOME}/projects
#   CODE_UNDERSTAND_STATE_ROOT   日志与 rewrite 临时目录根，默认 ${HOME}/Documents（与 run.sh / loop.sh 一致）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ZIP_SH="${SCRIPT_DIR}/zip.sh"
EVALUATE_SH="${SCRIPT_DIR}/evaluate.sh"
CLASSIFY_SH="${SCRIPT_DIR}/classify.sh"
REWRITE_PY="${SCRIPT_DIR}/rewrite.py"

: "${OUTPUTS_DIR:=${HOME}/outputs}"
: "${PROJECTS_DIR:=${HOME}/projects}"
: "${CODE_UNDERSTAND_STATE_ROOT:=${HOME}/Documents}"
export CODE_UNDERSTAND_STATE_ROOT

pack_out() { printf '[pack.sh][%s]%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
pack_err() { printf '[pack.sh][%s]%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; }

pack_out "======================================================"
pack_out "=                    Start Package                   ="
pack_out "======================================================"

usage() {
  pack_err "用法: $0 <github> <repo名称> <session>"
  pack_err "  github     仓库 https 地址（无末尾/），如 https://github.com/org/repo"
  pack_err "  repo名称   项目在 PROJECTS_DIR 下的目录名，如 react"
  pack_err "  session    Claude 会话 id（与 build.sh 第二参数一致）"
  pack_err "  说明: 仅复制代码、生成 metadata、rewrite 会话 JSONL、压缩；"
  pack_err "        若尚未导出会话并生成 doc/，请先在同目录执行 build.sh <repo> <session>"
  pack_err "  可选环境变量 OUTPUTS_DIR（默认 \$HOME/outputs）、PROJECTS_DIR（默认 \$HOME/projects）"
}

[[ "${#}" -eq 3 ]] || { usage; exit 1; }
github="${1}"
repo="${2}"
session="${3}"
[[ -n "${github}" && -n "${repo}" && -n "${session}" ]] || { usage; exit 1; }
[[ "${repo}" != */* && "${repo}" != *..* ]] || { pack_err "错误: repo 名称非法（不能含 / 或 ..）"; exit 1; }

project_path="${PROJECTS_DIR}/${repo}"
[[ -d "${project_path}" ]] || { pack_err "错误: 项目目录不存在: ${project_path}"; exit 1; }

OUT="${OUTPUTS_DIR}/code-understand-${repo}"
META="${OUT}/metadata.json"
code_dir="${OUT}/code/${repo}"

mkdir -p "${OUT}"

# 步骤 1：复制项目代码
pack_out "====================步骤 1：复制项目代码===================="
rm -rf "${OUT}/code"
mkdir -p "${OUT}/code"
cp -a -- "${project_path}" "${OUT}/code/${repo}"
pack_out "代码复制完成"
pack_out "src: ${project_path}"
pack_out "dst: ${OUT}/code/${repo}"

# 步骤 2：生成 metadata.json、questions.json
pack_out "====================步骤 2：生成 metadata.json===================="
# GitHub API：github 参数无末尾/；将 github.com 替换为 api.github.com/repos；经 gh-proxy 转发
api_url="${github//github.com/api.github.com/repos}"
curl_url="https://gh-proxy.org/${api_url}"
main_language="unknown"
if api_json="$(curl -fsSL -- "${curl_url}" 2>/dev/null)"; then
  main_language="$(printf '%s' "${api_json}" | jq -r '(.language // "") | ascii_downcase' 2>/dev/null || printf '')"
  pack_out "main_language: ${main_language}"
  [[ -n "${main_language}" ]] || main_language="unknown"
else
  pack_err "警告: 无法拉取 GitHub API（${curl_url}），main_language=unknown"
fi

difficulty_level="$(bash "${EVALUATE_SH}" "${project_path}" | tr -d '\r' | head -n1)"
[[ -n "${difficulty_level}" ]] || difficulty_level="medium"
pack_out "difficulty_level: ${difficulty_level}"

main_type="$(bash "${CLASSIFY_SH}" "${code_dir}" "${github}" | tr -d '\r' | head -n1)"
[[ -n "${main_type}" ]] || main_type="Unknown"
pack_out "main_type: ${main_type}"

jq -n \
  --arg github "${github}" \
  --arg ml "${main_language}" \
  --arg mt "${main_type}" \
  --arg dl "${difficulty_level}" \
  '{
    basic_info: {
      repo_name: $github,
      main_language: $ml,
      github: { url: $github, star: 0 }
    },
    category_info: { main_type: $mt },
    difficulty: { level: $dl },
    extra_info: { input_token: 0, output_token: 0 }
  }' >"${META}"

printf '%s\n' '[]' >"${OUT}/questions.json"

# 步骤 3：交互改写会话 JSONL 后压缩输出目录
pack_out "====================步骤 3：rewrite 会话 JSONL===================="
[[ -f "${REWRITE_PY}" ]] || { pack_err "错误: 未找到 rewrite.py: ${REWRITE_PY}"; exit 1; }
pack_out "调用: SESSION_ID=${session} python3 ${REWRITE_PY} ${repo}"
(export SESSION_ID="${session}"
 cd "${SCRIPT_DIR}" && python3 "${REWRITE_PY}" "${repo}") || {
  pack_err "错误: rewrite.py 退出非零"
  exit 1
}
pack_out "====================步骤 3（续）：压缩输出目录===================="
[[ -f "${ZIP_SH}" ]] || { pack_err "错误: 未找到 zip.sh"; exit 1; }
bash "${ZIP_SH}" "${OUT}"

pack_out "======================================================"
pack_out "=                    Package Done                    ="
pack_out "======================================================"
