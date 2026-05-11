#!/usr/bin/env bash
# 通过 Claude 结合 GitHub URL、README 摘要分析项目；stdout 仅输出一行 main_type（供 pack 等脚本捕获）；日志走 stderr。
# 依赖：bash、mktemp、jq、claude
set -euo pipefail

classify_err() { printf '[classify.sh][%s]%s\n' "$(date '+%Y%m%d%H%M%S')" "$*" >&2; }

usage() {
  classify_err "用法: $0 <code目录> <github>"
  exit 1
}

[[ "${#}" -ge 2 ]] || usage
code_dir="${1}"
github="${2}"
[[ -n "${github}" ]] || usage
[[ -d "${code_dir}" ]] || { classify_err "错误: 目录不存在: ${code_dir}"; exit 1; }

readme_path="${code_dir}/README.md"
if [[ -f "${readme_path}" ]]; then
  summary="$(jq -Rs 'if length > 1000 then .[0:1000] else . end' -r <"${readme_path}")"
else
  summary="（项目中未找到 README.md）"
fi

query="$(jq -n --arg summary "${summary}" --arg github "${github}" -r '
  [
    "You are an expert software analyst. Analyze the following code project based on the summary below.",
    "",
    "GitHub repository:",
    $github,
    "",
    "Project summary:",
    $summary,
    "",
    "Classify the project into exactly one category. The value of main_type MUST be one of these English strings (use spelling and punctuation exactly):",
    "  - Platform Application",
    "  - Middleware",
    "  - Framework/Tools",
    "  - AI",
    "  - Game & Multimedia",
    "  - Security",
    "  - Learning/Tutorial",
    "  - Open Source Library / SDK",
    "  - Other",
    "  - Unknown",
    "",
    "Return ONLY a single JSON object with exactly one key: main_type (string). No markdown, no code fences, no extra keys or text.",
    "Example: {\"main_type\":\"Framework/Tools\"}",
  ] | join("\n")
')"

claude_tmp="$(mktemp)"
trap 'rm -f -- "${claude_tmp}"' EXIT

set +e
claude -p --model "claude-sonnet-4-6" --effort "medium" "${query}" >"${claude_tmp}" 2>/dev/null
claude_exit=$?
set -e
[[ "${claude_exit}" -eq 0 ]] || classify_err "警告: claude 失败（${claude_exit}），输出默认 main_type"

body="$(sed -e '1s/^\xEF\xBB\xBF//' -e '1s/^```[a-zA-Z]*[[:space:]]*//' -e '$s/[[:space:]]*```[[:space:]]*$//' "${claude_tmp}")"
result_json="$(printf '%s' "${body}" | jq -c . 2>/dev/null || printf '%s' '{}')"
main_type="$(jq -r '.main_type // "Unknown"' <<<"${result_json}" 2>/dev/null || printf 'Unknown')"

printf '%s\n' "${main_type}"
