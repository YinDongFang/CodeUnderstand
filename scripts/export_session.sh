#!/usr/bin/env bash
# 把沙箱 ~/.claude/projects/... 的 session.jsonl + subagents 导出到产物 sessions/session1/，
# 并归一化 JSONL 内 model 字段。
# 用法: export_session.sh <repo> <session_id>
# 环境变量:
#   ARTIFACT_ROOT   code-understand-{repo} 产物根
#   HOME            沙箱 HOME（.claude/projects 在此之下）
set -euo pipefail

exp_out() { printf '[export_session.sh][%s]%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
exp_err() { printf '[export_session.sh][%s]%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; }

[[ "${#}" -eq 2 ]] || { exp_err "用法: $0 <repo> <session_id>"; exit 1; }
repo="${1}"
session="${2}"
: "${ARTIFACT_ROOT:?需要设置 ARTIFACT_ROOT}"
command -v jq >/dev/null 2>&1 || { exp_err "错误: 需要 jq"; exit 1; }

repo_slug="${repo//_/-}"
USER="${USER:-$(id -un 2>/dev/null || printf unknown)}"
if [[ -n "${CLAUDE_PROJECT_DIR:-}" && -d "${CLAUDE_PROJECT_DIR}" ]]; then
  CLAUDE_PROJECTS="${CLAUDE_PROJECT_DIR}"
else
  CLAUDE_PROJECTS="${HOME}/.claude/projects/-home-${USER}-projects-${repo_slug}"
fi
SESSION_FILE="${CLAUDE_PROJECTS}/${session}.jsonl"
SA_SRC="${CLAUDE_PROJECTS}/${session}/subagents"

[[ -f "${SESSION_FILE}" ]] || { exp_err "错误: session 文件不存在: ${SESSION_FILE}"; exit 1; }

SESSION1="${ARTIFACT_ROOT}/sessions/session1"
DST_JSONL="${SESSION1}/session.jsonl"
mkdir -p "${SESSION1}"

exp_out "复制 session → ${DST_JSONL}"
cp -a -- "${SESSION_FILE}" "${DST_JSONL}"

exp_out "归一化 JSONL 内 model 字段"
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
    exp_err "警告: 跳过无法解析的 JSONL 行"
    continue
  fi
  before_s="$(jq -cS . <<<"${line}")"
  out="$(jq -c "${jq_normalize_line}" <<<"${line}" 2>/dev/null)" || { exp_err "错误: jq 归一化失败"; exit 1; }
  after_s="$(jq -cS . <<<"${out}")"
  [[ "${before_s}" == "${after_s}" ]] || changed=$((changed + 1))
  printf '%s\n' "${out}" >>"${tmp_jsonl}"
done <"${DST_JSONL}"
mv -f -- "${tmp_jsonl}" "${DST_JSONL}"
trap - EXIT
exp_out "model 字段归一化完成（变更: ${changed}）"

if [[ -d "${SA_SRC}" ]]; then
  mkdir -p "${SESSION1}/subagents"
  cp -a -- "${SA_SRC}/." "${SESSION1}/subagents/"
  exp_out "subagents 复制完成"
else
  exp_out "未找到 subagents 目录（跳过）"
fi

exp_out "会话导出完成"
