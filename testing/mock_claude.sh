#!/usr/bin/env bash
# 供 loop.sh 在 LOOP_USE_MOCK_CLAUDE=1 时替换真实 claude：落盘每次收到的 prompt，并返回可解析的固定 JSON/纯文本。
# 依赖：bash, jq（与 loop.sh 一致）
set -eu

command -v jq >/dev/null 2>&1 || {
  printf '%s\n' "mock_claude: 需要 jq" >&2
  exit 2
}

if [[ -z "${LOOP_MOCK_DUMP_DIR:-}" ]]; then
  printf '%s\n' "mock_claude: 请设置 LOOP_MOCK_DUMP_DIR（loop.sh 会自动导出）" >&2
  exit 2
fi

mkdir -p "${LOOP_MOCK_DUMP_DIR}"
seqf="${LOOP_MOCK_DUMP_DIR}/.seq"
n=$(($(cat "${seqf}" 2>/dev/null || echo 0) + 1))
echo "${n}" >"${seqf}"

# loop.sh 总是把 prompt 作为最后一个参数传给 -p
prompt="${!#}"

json_mode=0
prev=""
for a in "$@"; do
  if [[ "${prev}" == "--output-format" && "${a}" == "json" ]]; then
    json_mode=1
  fi
  prev="${a}"
done

kind="resume"
[[ "${json_mode}" -eq 1 ]] && kind="json_first"
base="${LOOP_MOCK_DUMP_DIR}/$(printf '%04d' "${n}")_${kind}"
printf '%s' "${prompt}" >"${base}.prompt.txt"
printf '%q ' "$@" >"${base}.argv.txt" 2>/dev/null || true

# ---------- 根据 prompt 形态返回 loop.sh 可消费的假数据 ----------
# PromptEntry（json）：含「只返回最终版本的英文」等指令 → 多行英文「题」，供截取 ENTRY_N
_is_prompt_entry() {
  [[ "$1" == *只返回最终版本的英文* ]] || [[ "$1" == *整体架构* ]] || [[ "$1" == *业务流程方面* ]]
}

_emit_json() {
  jq -n --arg sid "$1" --arg body "$2" '{session_id:$sid, result:$body}'
}

if [[ "${json_mode}" -eq 1 ]]; then
  sid="mock-json-${n}-$$"
  if _is_prompt_entry "${prompt}"; then
    body=$(printf 'MockEntry-%s-Q1 about architecture boundaries?\nMockEntry-%s-Q2 about module responsibilities?\nMockEntry-%s-Q3 about main data flow?\nMockEntry-%s-Q4 about error handling strategy?\nMockEntry-%s-Q5 about extension points?\n' "${n}" "${n}" "${n}" "${n}" "${n}")
  else
    body=$(printf 'MOCK_JSON_FIRST_REPO_ANSWER_%s\n(second paragraph of mock answer)\n' "${n}")
  fi
  _emit_json "${sid}" "${body}" >"${base}.response.json"
  cat "${base}.response.json"
  exit 0
fi

# resume 纯文本：按模板关键字区分 deeper / summary / final / 其它
out=""
if [[ "${prompt}" == *提出2个新的问题* ]]; then
  out=$(printf 'MOCK_DEEPER_SUBQ1-%04d?\nMOCK_DEEPER_SUBQ2-%04d?\n' "${n}" "${n}")
elif [[ "${prompt}" == *提出1个新的问题* ]]; then
  out=$(printf 'MOCK_SUMMARY_WRAPUP_Q-%04d?\n' "${n}")
elif [[ "${prompt}" == *最后一个* ]] && [[ "${prompt}" == *综合性* ]]; then
  out=$(printf 'MOCK_FINAL_WRAPUP_ONE_LINE_Q_%04d?\n' "${n}")
else
  out=$(printf 'MOCK_PLAIN_RESUME_REPLY_%04d\n(line 2 mock detail for repo/agent)\n' "${n}")
fi
printf '%s' "${out}" >"${base}.response.txt"
printf '%s' "${out}"
exit 0
