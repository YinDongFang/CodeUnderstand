#!/usr/bin/env bash
# 生成 metadata.json 与占位 questions.json。
# 用法: metadata.sh <github_url> <repo>
# 环境变量:
#   ARTIFACT_ROOT   code-understand-{repo} 产物根（必须已存在 code/{repo}/）
# 依赖: bash, jq, curl, 同仓库根的 evaluate.sh / classify.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_SCRIPT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
EVALUATE_SH="${REPO_SCRIPT_DIR}/evaluate.sh"
CLASSIFY_SH="${REPO_SCRIPT_DIR}/classify.sh"

meta_out() { printf '[metadata.sh][%s]%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
meta_err() { printf '[metadata.sh][%s]%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; }

[[ "${#}" -eq 2 ]] || { meta_err "用法: $0 <github_url> <repo>"; exit 1; }
github="${1}"
repo="${2}"

: "${ARTIFACT_ROOT:?需要设置 ARTIFACT_ROOT 环境变量}"
code_dir="${ARTIFACT_ROOT}/code/${repo}"
[[ -d "${code_dir}" ]] || { meta_err "错误: code 目录不存在: ${code_dir}"; exit 1; }

META="${ARTIFACT_ROOT}/metadata.json"

api_url="${github//github.com/api.github.com/repos}"
curl_url="https://gh-proxy.org/${api_url}"
main_language="unknown"
if api_json="$(curl -fsSL -- "${curl_url}" 2>/dev/null)"; then
  main_language="$(printf '%s' "${api_json}" | jq -r '(.language // "") | ascii_downcase' 2>/dev/null || printf '')"
  [[ -n "${main_language}" ]] || main_language="unknown"
  meta_out "main_language: ${main_language}"
else
  meta_err "警告: 无法拉取 GitHub API, main_language=unknown"
fi

difficulty_level="$(bash "${EVALUATE_SH}" "${code_dir}" | tr -d '\r' | head -n1)"
[[ -n "${difficulty_level}" ]] || difficulty_level="medium"
meta_out "difficulty_level: ${difficulty_level}"

main_type="$(bash "${CLASSIFY_SH}" "${code_dir}" "${github}" | tr -d '\r' | head -n1)"
[[ -n "${main_type}" ]] || main_type="Unknown"
meta_out "main_type: ${main_type}"

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

printf '%s\n' '[]' >"${ARTIFACT_ROOT}/questions.json"

meta_out "metadata.json 与 questions.json 已生成"
