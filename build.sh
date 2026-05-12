#!/usr/bin/env bash
# 从 ~/.claude/projects/{session} 导出会话，归一化 JSONL 内 model 字段，再在项目目录续会话生成 doc/。
# 用法: build.sh <repo> <session>
#   repo     与 pack.sh 一致，项目在 PROJECTS_DIR 下；输出根为 OUTPUTS_DIR/code-understand-<repo>
#   session  对应 ~/.claude/projects/-home-$USER-projects-${repo//_/-}/<session>.jsonl 与 .../subagents/
#
# 环境变量（默认与 pack.sh 一致）: OUTPUTS_DIR, PROJECTS_DIR
# 依赖: bash, cp, mkdir, jq, claude, shuf
set -euo pipefail

: "${OUTPUTS_DIR:=${HOME}/outputs}"
: "${PROJECTS_DIR:=${HOME}/projects}"

build_out() { printf '[build.sh][%s]%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
build_err() { printf '[build.sh][%s]%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; }

usage() {
  build_err "用法: $0 <repo> <session>"
  build_err "  session 对应 ~/.claude/projects/<session>.jsonl 与 subagents/"
  exit 1
}

[[ "${#}" -eq 2 ]] || usage
repo="${1}"
session="${2}"
[[ -n "${repo}" && -n "${session}" ]] || usage
[[ "${repo}" != */* && "${repo}" != *..* ]] || { build_err "错误: repo 名称非法"; exit 1; }
command -v jq >/dev/null 2>&1 || { build_err "错误: 需要 jq"; exit 1; }

repo_slug="${repo//_/-}"
DIR="${OUTPUTS_DIR}/code-understand-${repo}"
PROJECT_DIR="${PROJECTS_DIR}/${repo}"
CLAUDE_PROJECTS="${HOME}/.claude/projects/-home-${USER}-projects-${repo_slug}"
SESSION_FILE="${CLAUDE_PROJECTS}/${session}.jsonl"
SA_SRC="${CLAUDE_PROJECTS}/${session}/subagents"

[[ -d "${PROJECT_DIR}" ]] || { build_err "错误: 项目目录不存在: ${PROJECT_DIR}"; exit 1; }
[[ -f "${SESSION_FILE}" ]] || { build_err "错误: session 文件不存在: ${SESSION_FILE}"; exit 1; }

SESSIONS_ROOT="${DIR}/sessions"
SESSION1="${SESSIONS_ROOT}/session1"
DST_JSONL="${SESSION1}/session.jsonl"

mkdir -p "${SESSION1}"
build_out "复制 session → ${DST_JSONL}"
cp -a -- "${SESSION_FILE}" "${DST_JSONL}"

build_out "归一化 JSONL 内 model 字段"

# 与参考 Python 一致：归一化 message.model 与 data.message.message.model 为字面量 "model"
jq_normalize_line='(
  if (.message | type) == "object" and (.message | has("model")) and .message.model != "model" then
    .message.model = "model"
  else . end
)
| (if (.data | type) == "object" and (.data.message | type) == "object" and (.data.message.message | type) == "object"
      and (.data.message.message | has("model")) and .data.message.message.model != "model" then
    .data.message.message.model = "model"
  else . end)'

tmp_jsonl="$(mktemp)"
trap 'rm -f -- "${tmp_jsonl}"' EXIT
changed=0
while IFS= read -r line || [[ -n "${line}" ]]; do
  [[ -z "${line//[:space:]}" ]] && continue
  if ! jq -e . >/dev/null 2>&1 <<<"${line}"; then
    build_err "警告: 跳过无法解析的 JSONL 行"
    continue
  fi
  before_s="$(jq -cS . <<<"${line}")"
  out="$(jq -c "${jq_normalize_line}" <<<"${line}" 2>/dev/null)" || {
    build_err "错误: jq 归一化失败"
    exit 1
  }
  after_s="$(jq -cS . <<<"${out}")"
  if [[ "${before_s}" != "${after_s}" ]]; then
    changed=$((changed + 1))
  fi
  printf '%s\n' "${out}" >>"${tmp_jsonl}"
done <"${DST_JSONL}"
mv -f -- "${tmp_jsonl}" "${DST_JSONL}"
trap - EXIT
build_out "已统一 JSONL 内 model 字段为 \"model\"（变更对象数: ${changed}）"

build_out "复制 subagents"
if [[ -d "${SA_SRC}" ]]; then
  mkdir -p "${SESSION1}/subagents"
  cp -a -- "${SA_SRC}/." "${SESSION1}/subagents/"
  build_out "subagents复制完成: ${SA_SRC} → ${SESSION1}/subagents"
  build_out "src: ${SA_SRC}"
  build_out "dst: ${SESSION1}/subagents"
else
  build_out "未找到 subagents 目录（跳过）: ${SA_SRC}"
fi

_OPENINGS=(
  "The codebase analysis is now complete. Please generate the full project documentation. Be comprehensive and technically precise."
  "We have thoroughly examined this project. Produce a complete technical documentation set. Prioritize depth over brevity."
  "Our code analysis has covered architecture, implementation, and usage patterns. Synthesize this into structured documentation."
  "Having completed the codebase exploration, produce formal project documentation with sufficient detail for an unfamiliar engineer."
  "This project has been analyzed in detail. Generate technical documentation covering architecture, implementation, and practical guidance."
  "Based on the comprehensive code review performed in this session, produce a complete documentation package for this project."
  "The analysis phase is complete. Please proceed to documentation generation with concrete references to the source code."
  "We have established a detailed understanding of this project through systematic code exploration. Formalize that understanding into well-structured technical documents."
)
_CLOSINGS=(
  "Begin writing all 4 files now."
  "Proceed to generate all documentation files."
  "Start producing the documentation immediately."
  "Generate all files and write them to the doc/ directory now."
  "Please create all documentation files at this time."
)

_core=(
  "Write all output files into the \`doc/\` folder under the current working directory."
  "Generate exactly 4 Markdown documents: overview.md, architecture.md, implementation.md, and a fourth determined by project type."
  "All documentation must be written in English."
  "Every document except overview.md must include exactly one Mermaid diagram with classDef coloring."
  "Content must be grounded in what was actually discovered — do not fabricate details."
)
_quality=(
  "Reference concrete file paths and module names wherever relevant."
  "Include key function signatures and class hierarchies."
  "Describe data flows with specifics: input formats, transformations, output structures."
  "Document non-obvious design decisions and trade-offs."
  "Include short code snippets when they clarify a critical mechanism."
  "List configuration options with default values and effects."
  "Note error handling patterns, retry strategies, and edge cases."
  "Use Markdown tables to organize structured information."
  "Map out the initialization sequence and component wiring at startup."
  "Identify key abstractions and how they compose to deliver functionality."
  "Describe the dependency structure and how external libraries are integrated."
  "Highlight patterns that help a new contributor understand the codebase quickly."
)

opening="${_OPENINGS[$((RANDOM % ${#_OPENINGS[@]}))]}"
closing="${_CLOSINGS[$((RANDOM % ${#_CLOSINGS[@]}))]}"
mapfile -t _core_shuf < <(printf '%s\n' "${_core[@]}" | shuf)
_qn=$((RANDOM % 3 + 4))
mapfile -t _qual_pick < <(printf '%s\n' "${_quality[@]}" | shuf -n "${_qn}")

prompt="${opening}"$'\n\n'"Requirements:"
i=0
for line in "${_core_shuf[@]}"; do
  i=$((i + 1))
  prompt+=$'\n'"${i}. ${line}"
done
prompt+=$'\n\n'"Additionally, ensure the following where applicable:"
for line in "${_qual_pick[@]}"; do
  prompt+=$'\n'"- ${line}"
done
prompt+=$'\n\n'"${closing}"

mkdir -p "${DIR}"
doc_abs_path="$(cd "${DIR}" && pwd)/doc"
mkdir -p "${doc_abs_path}"
prompt+=$'\n\n'"IMPORTANT: Write all doc/ output files to this absolute path: ${doc_abs_path}/ (not the current working directory)."

MAX_CLAUDE_ATTEMPTS=3
rc=1
for ((attempt = 1; attempt <= MAX_CLAUDE_ATTEMPTS; attempt++)); do
  build_out "claude --resume ${session}（第 ${attempt}/${MAX_CLAUDE_ATTEMPTS} 次）"
  set +e
  # claude 标准输出/错误丢弃，不打控制台、不写日志文件
  (cd -- "${PROJECT_DIR}" && printf '%s' "${prompt}" | claude -p \
    --allowedTools "Edit,Write,Read,Bash,MultiEdit" \
    --resume "${session}") >/dev/null 2>&1
  rc=$?
  set -e
  if [[ "${rc}" -eq 0 ]]; then
    break
  fi
  build_err "claude 退出码 ${rc}，将重试"
done

if [[ "${rc}" -ne 0 ]]; then
  build_err "claude 在 ${MAX_CLAUDE_ATTEMPTS} 次尝试后仍失败，最后退出码: ${rc}"
  exit "${rc}"
fi

build_out "文档生成完成"
