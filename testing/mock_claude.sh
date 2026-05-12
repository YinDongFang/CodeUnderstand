#!/usr/bin/env bash
# 供 loop.sh 在 LOOP_USE_MOCK_CLAUDE=1 时替换真实 claude；只写 stdout/stderr。
# 题目：MD5(完整 prompt) + 递增序号，带前缀区分场景（ENTRY / L1 / L2 / SUM×2 / FINAL）。
# 答案：在作为「题目」的 prompt 正文后拼接固定后缀（便于与题目区分）。
# 依赖：bash, jq；序号文件由 loop.sh 设置 LOOP_MOCK_SEQ_FILE
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

_md5_32() {
  local t="$1"
  if command -v md5sum >/dev/null 2>&1; then
    printf '%s' "${t}" | md5sum | awk '{print $1}'
  elif command -v md5 >/dev/null 2>&1; then
    printf '%s' "${t}" | md5 -q
  else
    printf '%s\n' "mock_claude: 需要 md5sum 或 md5" >&2
    exit 2
  fi
}

_next_seq() {
  local f="${LOOP_MOCK_SEQ_FILE:?mock: loop.sh 应 export LOOP_MOCK_SEQ_FILE}"
  local n
  n=$(($(cat "${f}") + 1))
  printf '%s\n' "${n}" >"${f}"
  printf '%s' "${n}"
}

_is_prompt_entry() {
  [[ "$1" == *只返回最终版本的英文* ]] || [[ "$1" == *整体架构* ]] || [[ "$1" == *业务流程方面* ]]
}

# 二层 deeper：仍用 PromptDeeper.md，但 <question> 里已是 L1-Q-... 子题
_is_second_layer_deeper() {
  [[ "$1" == *"提出2个新的问题"* ]] && [[ "$1" == *"L1-Q-"* ]]
}

_emit_json() {
  jq -n --arg sid "$1" --arg body "$2" '{session_id:$sid, result:$body}'
}

# 纯文本「答案」：整段 prompt 后接后缀（repo / agent 续聊通用）
_mock_answer_suffix() {
  local tag="$1"
  local s
  s="$(_next_seq)"
  printf '%s__MOCK_%s__seq=%s\n' "${prompt}" "${tag}" "${s}"
}

if [[ "${json_mode}" -eq 1 ]]; then
  sid="mock-json-$$-${RANDOM}"
  if _is_prompt_entry "${prompt}"; then
    h="$(_md5_32 "${prompt}")"
    body=""
    for _i in 1 2 3 4 5; do
      s="$(_next_seq)"
      body+="ENTRY-Q-${h}-${s}"$'\n'
    done
  else
    body="$(_mock_answer_suffix "JSONFIRST_REPO")"
  fi
  _emit_json "${sid}" "${body}"
  exit 0
fi

# PromptEntry 第二轮起走 resume，仍应产出多行 ENTRY-Q-
if _is_prompt_entry "${prompt}"; then
  h="$(_md5_32 "${prompt}")"
  body=""
  for _i in 1 2 3 4 5; do
    s="$(_next_seq)"
    body+="ENTRY-Q-${h}-${s}"$'\n'
  done
  printf '%s' "${body}"
  exit 0
fi

# PromptSummary：含「汇总以上」且要求 2 题，与 PromptDeeper 的「提出2个」区分
_is_prompt_summary() {
  [[ "$1" == *汇总以上* ]] && [[ "$1" == *提出2个新的问题* ]]
}

if [[ "${prompt}" == *最后一个* ]] && [[ "${prompt}" == *综合性* ]]; then
  h="$(_md5_32 "${prompt}")"
  s="$(_next_seq)"
  printf 'FINAL-Q-%s-%s\n' "${h}" "${s}"
  exit 0
fi

if _is_prompt_summary "${prompt}"; then
  h="$(_md5_32 "${prompt}")"
  s1="$(_next_seq)"
  s2="$(_next_seq)"
  printf 'SUM-Q-%s-%s\nSUM-Q-%s-%s\n' "${h}" "${s1}" "${h}" "${s2}"
  exit 0
fi

if [[ "${prompt}" == *提出2个新的问题* ]]; then
  h="$(_md5_32 "${prompt}")"
  if _is_second_layer_deeper "${prompt}"; then
    s1="$(_next_seq)"
    s2="$(_next_seq)"
    printf 'L2-Q-%s-%s\nL2-Q-%s-%s\n' "${h}" "${s1}" "${h}" "${s2}"
  else
    s1="$(_next_seq)"
    s2="$(_next_seq)"
    printf 'L1-Q-%s-%s\nL1-Q-%s-%s\n' "${h}" "${s1}" "${h}" "${s2}"
  fi
  exit 0
fi

# 其余 resume：按「题目」形态选后缀，便于区分 repo 在答哪一类题
if [[ "${prompt}" == SUM-Q-* ]]; then
  _mock_answer_suffix "REPO_SUM"
elif [[ "${prompt}" == L2-Q-* ]]; then
  _mock_answer_suffix "REPO_L2"
elif [[ "${prompt}" == L1-Q-* ]]; then
  _mock_answer_suffix "REPO_L1"
elif [[ "${prompt}" == ENTRY-Q-* ]]; then
  _mock_answer_suffix "REPO_ENTRY"
else
  _mock_answer_suffix "PLAIN"
fi
exit 0
