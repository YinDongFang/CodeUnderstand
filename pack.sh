#!/usr/bin/env bash
# 依赖：bash、cp、jq、curl、同目录 build.sh / classify.sh（需 claude）/ evaluate.sh / zip.sh（及 zip）
#
# 环境变量（可选，有默认值；由调用方保证为已展开路径）：
#   OUTPUTS_DIR   输出根目录，默认 ${HOME}/outputs
#   PROJECTS_DIR  本地项目根目录，默认 ${HOME}/projects
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ZIP_SH="${SCRIPT_DIR}/zip.sh"
BUILD_SH="${SCRIPT_DIR}/build.sh"
EVALUATE_SH="${SCRIPT_DIR}/evaluate.sh"
CLASSIFY_SH="${SCRIPT_DIR}/classify.sh"

: "${OUTPUTS_DIR:=${HOME}/outputs}"
: "${PROJECTS_DIR:=${HOME}/projects}"

pack_out() { printf '[pack.sh][%s]%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
pack_err() { printf '[pack.sh][%s]%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; }

pack_out "======================================================"
pack_out "=                    Start Package                   ="
pack_out "======================================================"

usage() {
  pack_err "用法: $0 <github> <repo名称> <session> [step]"
  pack_err "  github     仓库 https 地址（无末尾/），如 https://github.com/org/repo"
  pack_err "  repo名称   项目在 PROJECTS_DIR 下的目录名，如 react"
  pack_err "  session    Claude 会话 id，步骤 1 调用 build.sh 时使用"
  pack_err "  step       从第几步开始执行，默认 1（不跳过）。2=从复制项目代码开始，跳过步骤 1"
  pack_err "             步骤: 1=导出会话并生成 doc  2=复制代码  3=metadata+questions  4=压缩"
  pack_err "  可选环境变量 OUTPUTS_DIR（默认 \$HOME/outputs）、PROJECTS_DIR（默认 \$HOME/projects）"
}

[[ "${#}" -ge 3 ]] || { usage; exit 1; }
github="${1}"
repo="${2}"
session="${3}"
step="${4:-1}"
[[ -n "${github}" && -n "${repo}" && -n "${session}" ]] || { usage; exit 1; }
[[ "${step}" =~ ^[1-4]$ ]] || { pack_err "错误: step 须为 1–4 的整数，当前: ${step}"; exit 1; }
[[ "${repo}" != */* && "${repo}" != *..* ]] || { pack_err "错误: repo 名称非法（不能含 / 或 ..）"; exit 1; }

project_path="${PROJECTS_DIR}/${repo}"
[[ -d "${project_path}" ]] || { pack_err "错误: 项目目录不存在: ${project_path}"; exit 1; }

OUT="${OUTPUTS_DIR}/code-understand-${repo}"
META="${OUT}/metadata.json"
code_dir="${OUT}/code"

mkdir -p "${OUT}"
pack_out "起始步骤: ${step}（1=步骤 1 起，不跳过）"

# 步骤 1：导出会话并生成 doc/（与 build.sh 一致，写入 OUTPUTS_DIR/code-understand-<repo>）
if ((step <= 1)); then
  pack_out "====================步骤 1：导出会话并生成 doc/===================="
  bash "${BUILD_SH}" "${repo}" "${session}"
else
  pack_out "====================跳过步骤 1（从步骤 ${step} 开始）===================="
fi

# 步骤 2：复制项目代码
if ((step <= 2)); then
  pack_out "====================步骤 2：复制项目代码===================="
  rm -rf "${OUT}/code"
  cp -a -- "${project_path}" "${OUT}/code/${repo}"
  pack_out "代码复制完成"
  pack_out "src: ${project_path}"
  pack_out "dst: ${OUT}/code/${repo}"
else
  pack_out "====================跳过步骤 2===================="
fi

# 步骤 3：生成 metadata.json、questions.json
if ((step <= 3)); then
  pack_out "====================步骤 3：生成metadata.json===================="
# GitHub API：github 参数无末尾/；将 github.com 替换为 api.github.com/repos；经 gh-proxy 转发
api_url="${github//github.com/api.github.com/repos}"
curl_url="https://gh-proxy.com/${api_url}"
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
else
  pack_out "====================跳过步骤 3===================="
fi

# 步骤 4：压缩输出目录
if ((step <= 4)); then
  pack_out "====================步骤 4：压缩输出目录===================="
  [[ -f "${ZIP_SH}" ]] || { pack_err "错误: 未找到 zip.sh"; exit 1; }
  bash "${ZIP_SH}" "${OUT}"
else
  pack_out "====================跳过步骤 4===================="
fi

pack_out "======================================================"
pack_out "=                    Package Done                    ="
pack_out "======================================================"