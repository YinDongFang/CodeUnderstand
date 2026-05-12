#!/usr/bin/env bash
# 供 loop.sh 在 LOOP_USE_MOCK_CLAUDE=1 时替换真实 claude；只写 stdout/stderr，不落盘（日志由 loop.sh 单文件记录）。
# 依赖：bash, jq
set -eu

command -v jq >/dev/null 2>&1 || {
  printf '%s\n' "mock_claude: 需要 jq" >&2
  exit 2
}

prompt="${!#}"

json_mode=0
prev=""
for a in "$@"; do
  if [[ "${prev}" == "--output-format" && "${a}" == "json" ]]; then
    json_mode=1
  fi
  prev="${a}"
done

_is_prompt_entry() {
  [[ "$1" == *只返回最终版本的英文* ]] || [[ "$1" == *整体架构* ]] || [[ "$1" == *业务流程方面* ]]
}

_emit_json() {
  jq -n --arg sid "$1" --arg body "$2" '{session_id:$sid, result:$body}'
}

if [[ "${json_mode}" -eq 1 ]]; then
  sid="mock-json-$$-${RANDOM}"
  if _is_prompt_entry "${prompt}"; then
    body=$(printf 'MockEntry-Q1 about architecture boundaries?\nMockEntry-Q2 about module responsibilities?\nMockEntry-Q3 about main data flow?\nMockEntry-Q4 about error handling strategy?\nMockEntry-Q5 about extension points?\n')
  else
    body=$(printf 'MOCK_JSON_FIRST_REPO_ANSWER\n(second line of mock answer)\n')
  fi
  _emit_json "${sid}" "${body}"
  exit 0
fi

if [[ "${prompt}" == *提出2个新的问题* ]]; then
  printf 'MOCK_DEEPER_SUBQ1?\nMOCK_DEEPER_SUBQ2?\n'
elif [[ "${prompt}" == *提出1个新的问题* ]]; then
  printf 'MOCK_SUMMARY_WRAPUP_Q?\n'
elif [[ "${prompt}" == *最后一个* ]] && [[ "${prompt}" == *综合性* ]]; then
  printf 'MOCK_FINAL_WRAPUP_ONE_LINE_Q?\n'
else
  printf 'MOCK_PLAIN_RESUME_REPLY\n(line 2 mock detail)\n'
fi
exit 0
