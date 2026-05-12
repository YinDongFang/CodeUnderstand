#!/usr/bin/env bash
# =============================================================================
# loop.sh — 双 Agent 编排脚本（给不熟悉 shell 的阅读说明）
# =============================================================================
# 【正式运行 vs 测试（mock）】
#   正式（默认）：不要设置 LOOP_USE_MOCK_CLAUDE；PATH 中需要真实 claude，以及 jq、python3。
#   测试：export LOOP_USE_MOCK_CLAUDE=1；不调用真实 claude，改用 testing/mock_claude.sh；仍需要 jq、python3。
#   切回正式：unset LOOP_USE_MOCK_CLAUDE（或 export LOOP_USE_MOCK_CLAUDE=0）后照常执行即可。
#   测试常用：export LOOP_PHASE1_TARGET=4 LOOP_TOTAL_TARGET=6 缩短路径；
#             export LOOP_MOCK_USE_JSONL_RESUME=1 若要在 mock 下仍走 ~/.claude jsonl 恢复逻辑。
#   会话日志单文件：默认 ./loop_logs/run__<REPO>__<RUN>.txt，恢复且已有 repo session 时为 repo__<session_id>__<RUN>.txt；
#   可用 LOOP_LOG_DIR / LOOP_LOG_FILE 覆盖目录或完整路径。
# =============================================================================
#
# 【这个脚本在干什么】
#   1) 在「代码仓库目录」TARGET_PATH 里跑 claude，让它像「答题方」回答技术问题。
#   2) 在「提问方目录」agent-{repo} 里跑另一个 claude，让它根据 Prompt*.md 生成下一批问题。
#   3) 两阶段计数（都在「repo 侧」数用户发出的有效题目次数）：
#        阶段一：累计到 PHASE1_TARGET（环境变量 LOOP_PHASE1_TARGET，未设时见脚本内默认值）为止，
#                反复「入口题 + dive」大循环；
#        阶段二：反复 PromptFinal，把累计补到 TOTAL_TARGET（LOOP_TOTAL_TARGET）。
#
# 【可选：二层 deeper 随机追问】
#   每条一级 deeper 子题 (dq→AD) 答完后，以概率再跑一轮 PromptDeeper 并追问 repo：
#     export LOOP_DEEPER2_PROB=40      # 0–100，40 表示约 40% 会触发；0=关闭（默认）
#     export LOOP_DEEPER2_MAX_QS=3    # 单次触发最多几行子题（防题量爆炸）
#
# 【分析角度 {views}】
#   PromptDeeper / PromptSummary 使用单一占位符 {views}：从 VIEW_ANGLES 共 10 项中随机无放回抽 2–4 个拼成一段（顿号分隔）。
#     export LOOP_VIEWS_ANGLE_MIN=2
#     export LOOP_VIEWS_ANGLE_MAX=4
#
# 【为什么有两套目录】
#   ./agent        只是模板；真正运行时复制成 ./agent-{repo}，避免多个仓库互相污染。
#
# 【怎么调用】
#   ./loop.sh <target_path> <repo>
#   例：./loop.sh /path/to/myproject myproject
#
# 【依赖】bash, sed, grep, jq, python3；正式模式另需 PATH 中的 claude
#
# 【shell 小知识：set -eu】
#   -e：任意命令返回非 0（失败）时，整个脚本立刻退出，避免「错了还往下跑」。
#   u：使用未定义变量时报错，避免打错变量名静默变成空字符串。
#
# =============================================================================
set -eu

# -----------------------------------------------------------------------------
# 日志：写到标准错误 stderr（>&2），这样「真正的输出」stdout 仍可只放 session_id
# $* 表示「所有参数拼成一段」；"$@" 会保留每个参数的边界，这里用 $* 即可。
# -----------------------------------------------------------------------------
loop_out() { printf '[loop.sh][%s]%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; }
loop_err() { printf '[loop.sh][%s]%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; }

# 把多行文本压成一行（日志里好看）；${var//A/B} 表示把 var 里所有 A 换成 B
_fold_one_line() {
  local s=${1//$'\r'/}
  s=${s//$'\n'/ }
  printf '%s' "$s"
}

# 只显示前 n 个字符，避免日志里刷一整屏模型输出
_preview_text() {
  local s=$(_fold_one_line "$1") n=30
  if ((${#s} > n)); then printf '%s...' "${s:0:n}"; else printf '%s' "$s"; fi
}

# 去掉首尾空白：用的是 bash 参数展开技巧，不必依赖外部命令
_str_trim() {
  local s="$1"
  s="${s#"${s%%[![:space:]]*}"}"
  s="${s%"${s##*[![:space:]]}"}"
  printf '%s' "$s"
}

usage() {
  loop_err "用法: $0 <target_path> <repo>"
  exit 1
}

# ${#} 是参数个数；必须正好 2 个，否则打印用法并退出
[[ "${#}" -eq 2 ]] || usage

# 把第一个参数规范成「绝对路径」：
#   若以 / 开头，已是绝对路径，直接用；
#   否则：先 cd 到该路径的目录取 pwd，再拼上文件名。
if [[ "${1}" = /* ]]; then
  TARGET_PATH="${1}"
else
  TARGET_PATH="$(cd "$(dirname "${1}")" && pwd)/$(basename "${1}")"
fi
REPO="${2}"
USER="${USER:-$(id -un 2>/dev/null || printf unknown)}"
[[ -d "${TARGET_PATH}" ]] || { loop_err "错误: 目录不存在: ${TARGET_PATH}"; exit 1; }

# BASH_SOURCE[0] 是当前脚本路径；dirname + cd + pwd 得到脚本所在目录的绝对路径
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_SRC="${SCRIPT_DIR}/agent"
AGENT_DIR="${SCRIPT_DIR}/agent-${REPO}"
RENDER_PY="${SCRIPT_DIR}/loop_render_prompt.py"

# claude 调用失败时的重试次数（网络/模型偶发失败）
MAX_TARGET_ATTEMPTS=4

# 下面这些默认值可被环境变量覆盖：VAR=${ENV_VAR:-默认值}
# LOOP_PHASE1_TARGET：阶段一在 repo 侧累计多少条「用户题」后停
# LOOP_TOTAL_TARGET：阶段二结束后 repo 侧总共多少条
PHASE1_TARGET="${LOOP_PHASE1_TARGET:-37}"
TOTAL_TARGET="${LOOP_TOTAL_TARGET:-38}"

# 每轮 PromptEntry 希望模型生成几道题（脚本会再截取前 ENTRY_N 行）
ENTRY_N_MIN="${LOOP_ENTRY_N_MIN:-4}"
ENTRY_N_MAX="${LOOP_ENTRY_N_MAX:-5}"

# 每个「大循环」里 dive into 执行几轮（每轮 = 3.2.1~3.2.4 一整条链）
DIVE_ROUNDS_MIN="${LOOP_DIVE_ROUNDS_MIN:-2}"
DIVE_ROUNDS_MAX="${LOOP_DIVE_ROUNDS_MAX:-3}"

# 二层 deeper（再跑一轮 PromptDeeper → 新子题再问 repo）：
#   LOOP_DEEPER2_PROB：0–100，每条一级 deeper 子题答完后「再触发一层」的概率；0=关闭（默认）
#   LOOP_DEEPER2_MAX_QS：单次触发时最多采纳几行子题（防止爆炸）
DEEPER2_PROB="${LOOP_DEEPER2_PROB:-60}"
DEEPER2_MAX_QS="${LOOP_DEEPER2_MAX_QS:-3}"

# 「分析角度」池：每次渲染 PromptDeeper / PromptSummary 前，随机无放回抽取 LOOP_VIEWS_ANGLE_MIN–MAX 个（默认 2–4），
# 用顿号拼成一段传入占位符 {views}（见 VIEW_ANGLES 数组，含模块职责、设计思想、业务流程等）。
VIEW_ANGLES=(
  "实现原理"
  "输出原理"
  "逻辑链路"
  "函数细节"
  "变量使用"
  "异常场景"
  "边界条件"
  "模块职责"
  "设计思想"
  "业务流程"
)
VIEWS_ANGLE_MIN="${LOOP_VIEWS_ANGLE_MIN:-2}"
VIEWS_ANGLE_MAX="${LOOP_VIEWS_ANGLE_MAX:-4}"

# SESSION_*：claude 的会话 id（uuid），用于 -r 续聊同一上下文
# REPO_PROMPT_COUNT：repo 侧已经问过几轮（含恢复时从 jsonl 扫出来的历史）
SESSION_REPO=""
SESSION_AGENT=""
REPO_PROMPT_COUNT=0

[[ -d "${AGENT_SRC}" ]] || { loop_err "错误: 未找到 ${AGENT_SRC}"; exit 1; }
[[ -f "${AGENT_SRC}/PromptEntry.md" ]] || { loop_err "错误: 未找到 PromptEntry.md"; exit 1; }
[[ -f "${AGENT_SRC}/PromptDeeper.md" ]] || { loop_err "错误: 未找到 PromptDeeper.md"; exit 1; }
[[ -f "${AGENT_SRC}/PromptSummary.md" ]] || { loop_err "错误: 未找到 PromptSummary.md"; exit 1; }
[[ -f "${AGENT_SRC}/PromptFinal.md" ]] || { loop_err "错误: 未找到 PromptFinal.md"; exit 1; }
[[ -f "${RENDER_PY}" ]] || { loop_err "错误: 未找到 ${RENDER_PY}"; exit 1; }

command -v jq >/dev/null 2>&1 || { loop_err "错误: 未找到 jq"; exit 1; }
command -v python3 >/dev/null 2>&1 || { loop_err "错误: 未找到 python3"; exit 1; }
if [[ -z "${LOOP_USE_MOCK_CLAUDE:-}" ]]; then
  command -v claude >/dev/null 2>&1 || { loop_err "错误: 未找到 claude（测试请设 LOOP_USE_MOCK_CLAUDE=1）"; exit 1; }
fi

# 临时文件目录：优先用系统 TMPDIR；$$ 是当前 shell 进程号，保证并发运行不撞名
TMPDIR="${TMPDIR:-/tmp}"
RUN_ID="${REPO}_$$"
WORK="${TMPDIR}/loop_${RUN_ID}"
mkdir -p "${WORK}"

# 脚本无论正常结束还是被中断，EXIT 时都会删临时目录，避免磁盘垃圾
cleanup() { rm -rf "${WORK}"; }
trap cleanup EXIT

# 真实 claude 或 mock（见文件头「测试：不调用真实 AI」）
_claude_invoke() {
  if [[ -n "${LOOP_USE_MOCK_CLAUDE:-}" ]]; then
    bash "${SCRIPT_DIR}/testing/mock_claude.sh" "$@"
  else
    command claude "$@"
  fi
}

# 根据 cwd 判断是提问方（Agent）还是答题仓库（Repo）
_loop_infer_side() {
  local cwd="$1"
  if [[ "${cwd}" == "${TARGET_PATH}" ]]; then
    printf '%s' "Repo"
  elif [[ "${cwd}" == "${AGENT_DIR}" ]]; then
    printf '%s' "Agent"
  else
    printf '%s' "Unknown"
  fi
}

# 追加一条「Prompt / Result」块到会话日志（单文件；LOOP_LOG_FILE 在 main 中初始化）
_loop_log_turn() {
  [[ -z "${LOOP_LOG_FILE:-}" ]] && return 0
  local role="$1" p="$2" r="$3"
  {
    printf '%s\n' "====================="
    printf '%s\n\n' "${role}"
    printf '%s\n' "Prompt："
    printf '%s\n\n' "${p}"
    printf '%s\n' "Result："
    printf '%s\n\n' "${r}"
  } >>"${LOOP_LOG_FILE}"
}

# 首次拿到 repo session_id 后，将 run__*.txt 重命名为 repo__<session>__*.txt，便于与 session 关联
_loop_log_bind_repo_session() {
  [[ -z "${SESSION_REPO}" || -z "${LOOP_LOG_FILE:-}" ]] && return 0
  local base parent want
  parent="$(dirname "${LOOP_LOG_FILE}")"
  base="$(basename "${LOOP_LOG_FILE}")"
  [[ "${base}" == repo__* ]] && return 0
  want="${parent}/repo__${SESSION_REPO}__${RUN_ID}.txt"
  if [[ -e "${want}" && "${LOOP_LOG_FILE}" != "${want}" ]]; then
    want="${parent}/repo__${SESSION_REPO}__${REPO}__${RUN_ID}.txt"
  fi
  if [[ "${LOOP_LOG_FILE}" != "${want}" ]]; then
    if mv -f "${LOOP_LOG_FILE}" "${want}" 2>/dev/null; then
      LOOP_LOG_FILE="${want}"
      export LOOP_LOG_FILE
      loop_out "会话日志已关联 repo session: ${LOOP_LOG_FILE}"
    fi
  fi
}

if [[ -n "${LOOP_USE_MOCK_CLAUDE:-}" ]]; then
  loop_out "LOOP_USE_MOCK_CLAUDE=1（会话日志仍写入 LOOP_LOG_FILE）"
fi

# 把模型返回的一整段文字「拆成一行一行的题目」：
#   - 去掉 Windows 风格的 \r
#   - 去掉以 # 开头的注释行
#   - 去掉空行
# <<<"..." 叫「here-string」，等价于 echo 管道进命令，但更省事
_questions_from_text_to_lines() {
  sed 's/\r$//' <<<"${1}" |
    grep -v '^[[:space:]]*#' |
    sed '/^[[:space:]]*$/d' || true
}

# 读取 Claude Code 写出的 jsonl（每行一个 JSON 对象）：
#   RESUME_COMPLETED：统计其中「算一次用户提问」的行数（与旧版 loop 逻辑对齐）
#   RESUME_LAST_USER：最后一条符合条件的用户消息全文（本脚本里未强依赖，保留给 jq 扫描副作用一致）
_resume_scan_jsonl() {
  local file="$1"
  RESUME_COMPLETED=0
  RESUME_LAST_USER=""
  local line is t
  # 逐行读 jsonl：IFS= 保留行内空格；read -r 不把反斜杠当转义
  # 「|| [[ -n "${line}" ]]」处理最后一行没有换行符的情况，避免漏读
  while IFS= read -r line || [[ -n "${line}" ]]; do
    # ${line//[:space:]/} 删掉所有空白类字符；若结果为空说明整行只有空格/制表符
    [[ -z "${line//[:space:]}" ]] && continue
    # 先验证整行是合法 JSON，否则跳过（避免 jq 报错刷屏）
    jq -e . >/dev/null 2>&1 <<<"${line}" || continue
    # 下面 jq 程序：只把「type=user 且 content 是非空字符串且不是任务通知」的行记为一次有效提问
    is="$(jq -r '
      if .type=="user" and (.message|type)=="object" and (.message.content|type)=="string" then
        (.message.content|gsub("^\\s+";"")|gsub("\\s+$";"")) as $t |
        if ($t|length)>0 and ($t|startswith("<task-notification>")|not) then "1" else "0" end
      else
        "0"
      end
    ' <<<"${line}" 2>/dev/null)" || is=0
    if [[ "${is}" == "1" ]]; then
      RESUME_COMPLETED=$((RESUME_COMPLETED + 1))
      t="$(jq -r '.message.content' <<<"${line}" 2>/dev/null || printf '')"
      RESUME_LAST_USER="${t}" # 循环结束后即为「最后一条」用户正文
    fi
  done <"${file}" # 从文件定向到 while 的标准输入
}

# claude --output-format json 时，stdout 里可能夹带其它文本；这里尽量抽出「第一个合法 JSON 对象」
# 返回：打印 JSON 字符串到 stdout；失败则打印空串并 return 1
_parse_first_json_doc() {
  local raw="$1"
  local _r _doc
  _r=$(printf '%s' "${raw}" | tr -d '\r')
  _doc=$(printf '%s' "${_r}" | jq -ec . 2>/dev/null) || _doc=$(printf '%s' "${_r}" | jq -Rrs 'split("\n")|map(select(test("^\\s*\\{")))|map(try fromjson catch empty)|map(select(type=="object"))|last')
  if ! jq -e 'type=="object"' <<<"${_doc}" >/dev/null 2>&1; then
    printf ''
    return 1
  fi
  printf '%s' "${_doc}"
}

# 在目录 cwd 里「新开」一次 claude 会话，并要求 JSON 输出（便于取 session_id）。
# 成功时通过全局变量返回：
#   PARSE_SID   = session_id
#   PARSE_TEXT  = .result 字段（模型最终文本）
_claude_json_first() {
  local cwd="$1"
  local prompt="$2"
  local attempt=1 raw doc sid out
  while [[ "${attempt}" -le "${MAX_TARGET_ATTEMPTS}" ]]; do
    loop_out "  [json-first] 尝试 ${attempt}/${MAX_TARGET_ATTEMPTS} cwd=${cwd}"
    # 临时关闭 -e：我们要自己判断 claude 的退出码，而不是让它直接杀脚本
    set +e
    raw="$(cd "${cwd}" && _claude_invoke --output-format json -p "${prompt}" 2>&1)"
    local ex=$?
    set -e
    if [[ "${ex}" -ne 0 ]]; then
      loop_err "  claude 退出码 ${ex}，重试"
      attempt=$((attempt + 1))
      continue
    fi
    doc="$(_parse_first_json_doc "${raw}")" || doc=""
    if [[ -z "${doc}" ]]; then
      loop_err "  JSON 解析失败，摘要: $(_preview_text "${raw}")"
      attempt=$((attempt + 1))
      continue
    fi
    sid="$(jq -r '(.session_id//"")|tostring' <<<"${doc}")"
    if [[ -z "${sid}" || "${sid}" == "null" ]]; then
      loop_err "  未解析到 session_id，重试"
      attempt=$((attempt + 1))
      continue
    fi
    out="$(jq -r '.result|if .==null then "" elif type=="string" then . elif type=="boolean" or type=="number" then tostring else tojson end' <<<"${doc}")"
    PARSE_SID="${sid}"
    PARSE_TEXT="${out}"
    _loop_log_turn "$(_loop_infer_side "${cwd}")" "${prompt}" "${out}"
    return 0
  done
  return 1
}

# 在已有会话 sid 上续聊：claude -r <sid> -c -p ...
# 成功时 PARSE_TEXT = 模型纯文本输出（非 JSON）
_claude_resume_text() {
  local cwd="$1"
  local sid="$2"
  local prompt="$3"
  local attempt=1 raw ex
  while [[ "${attempt}" -le "${MAX_TARGET_ATTEMPTS}" ]]; do
    loop_out "  [resume] 尝试 ${attempt}/${MAX_TARGET_ATTEMPTS} cwd=${cwd}"
    # 与 json-first 相同：先关 -e，自行处理 claude 退出码
    set +e
    raw="$(cd "${cwd}" && _claude_invoke -r "${sid}" -c -p "${prompt}" 2>&1)"
    ex=$?
    set -e
    if [[ "${ex}" -ne 0 ]]; then
      loop_err "  claude 退出码 ${ex}，重试"
      attempt=$((attempt + 1))
      continue
    fi
    PARSE_TEXT="${raw}"
    _loop_log_turn "$(_loop_infer_side "${cwd}")" "${prompt}" "${raw}"
    return 0
  done
  return 1
}

# 计算本轮 PromptEntry.md 里要替换的 {n}（整数，夹在 ENTRY_N_MIN~ENTRY_N_MAX）：
#   rem = 距离阶段一目标还差几条 repo 侧用户题
#   cycles ≈ 还剩几轮「大循环」（用 rem/7 粗估，7 来自「每轮约 5~10 条」的经验中值）
#   n ≈ rem/cycles 向上取整，再限制在 2~3
# 小优化：rem 不大且 cycles>=2 时，避免仍要 3 题/轮，压成 2，便于两轮内收束
_compute_entry_n() {
  local rem=$((PHASE1_TARGET - REPO_PROMPT_COUNT))
  if [[ "${rem}" -le 0 ]]; then
    printf '%s' "${ENTRY_N_MIN}"
    return
  fi
  # 预估剩余大循环轮数：按每轮约 7 次 repo 提问粗估（5–10 的中值）
  # bash 整数除法默认向下取整；(rem+6)/7 等价于「除以 7 向上取整」的常见写法
  local cycles=$(( (rem + 6) / 7 ))
  [[ "${cycles}" -lt 1 ]] && cycles=1
  # 在 cycles 轮里摊平 rem 条：ceil(rem/cycles) = (rem+cycles-1)/cycles
  local n=$(( (rem + cycles - 1) / cycles ))
  [[ "${n}" -lt "${ENTRY_N_MIN}" ]] && n=${ENTRY_N_MIN}
  [[ "${n}" -gt "${ENTRY_N_MAX}" ]] && n=${ENTRY_N_MAX}
  # 示例：剩 15 题、2 轮左右时压成 2 题/轮
  if [[ "${rem}" -le 18 && "${cycles}" -ge 2 && "${n}" -ge 3 ]]; then
    n=2
  fi
  printf '%s' "${n}"
}

# 含端点的随机整数：[lo, hi]。bash 内置 RANDOM（0~32767），对 span 取模得到均匀-ish 的偏移
_rand_between() {
  local lo="$1" hi="$2"
  local span=$((hi - lo + 1))
  printf '%s' $((RANDOM % span + lo))
}

# 从 VIEW_ANGLES 中无放回随机选 k 个（k ∈ [VIEWS_ANGLE_MIN, VIEWS_ANGLE_MAX] 且不超过角度总数），用顿号连接输出到 stdout
_pick_views_text() {
  local n=${#VIEW_ANGLES[@]}
  local lo="${VIEWS_ANGLE_MIN}" hi="${VIEWS_ANGLE_MAX}" k
  [[ "${lo}" -lt 1 ]] && lo=1
  [[ "${hi}" -lt "${lo}" ]] && hi=${lo}
  [[ "${hi}" -gt "${n}" ]] && hi=${n}
  k="$(_rand_between "${lo}" "${hi}")"
  [[ "${k}" -gt "${n}" ]] && k=${n}
  local picked=() i p dup tries=0
  while [[ "${#picked[@]}" -lt "${k}" && "${tries}" -lt 200 ]]; do
    tries=$((tries + 1))
    i=$((RANDOM % n))
    dup=0
    for p in "${picked[@]}"; do
      if [[ "${p}" -eq "${i}" ]]; then dup=1; break; fi
    done
    [[ "${dup}" -eq 1 ]] && continue
    picked+=("${i}")
  done
  # 极端情况下未凑满 k，按顺序补足未选过的下标
  local j=0
  while [[ "${#picked[@]}" -lt "${k}" && "${j}" -lt "${n}" ]]; do
    dup=0
    for p in "${picked[@]}"; do
      if [[ "${p}" -eq "${j}" ]]; then dup=1; break; fi
    done
    [[ "${dup}" -eq 0 ]] && picked+=("${j}")
    j=$((j + 1))
  done
  local out="" sep=""
  for i in "${picked[@]}"; do
    out+="${sep}${VIEW_ANGLES[i]}"
    sep="、"
  done
  printf '%s' "${out}"
}

# -----------------------------------------------------------------------------
# 主流程起点（上面全是「工具函数」；从这里开始顺序执行）
# 「! 函数名 ...」表示：函数返回非 0（失败）则进入 if / 否定测试为真
# -----------------------------------------------------------------------------

loop_out "======================================================"
loop_out "=                    Start Loop                      ="
loop_out "======================================================"
loop_out "PHASE1_TARGET=${PHASE1_TARGET} TOTAL_TARGET=${TOTAL_TARGET}"

# Claude Desktop/Code 把项目路径编码成目录名：下划线 _ 会变成 -
# 例如 repo 名 my_repo → my-repo，对应 ~/.claude/projects/-home-<用户>-projects-my-repo
REPO_SLUG="${REPO//_/-}"
CLAUDE_PROJECT_DIR="${HOME}/.claude/projects/-home-${USER}-projects-${REPO_SLUG}"

# 在该目录下找「记录 repo 侧对话」的 jsonl：
#   优先 session.jsonl；
#   否则在 *.jsonl 里选「修改时间最新」的一个（多人/多会话时尽量贴近当前工作）
SESSION_JSONL=""
if [[ -d "${CLAUDE_PROJECT_DIR}" ]]; then
  if [[ -f "${CLAUDE_PROJECT_DIR}/session.jsonl" ]]; then
    SESSION_JSONL="${CLAUDE_PROJECT_DIR}/session.jsonl"
  else
    # nullglob：没有匹配文件时 glob 变成空数组，而不是字面量 '*.jsonl'
    shopt -s nullglob
    local_globs=("${CLAUDE_PROJECT_DIR}"/*.jsonl)
    shopt -u nullglob
    if [[ ${#local_globs[@]} -gt 0 ]]; then
      SESSION_JSONL="${local_globs[0]}"
      for f in "${local_globs[@]}"; do
        [[ -f "${f}" ]] || continue
        # -nt：newer than，选更新的文件
        if [[ "${f}" -nt "${SESSION_JSONL}" ]]; then
          SESSION_JSONL="${f}"
        fi
      done
    fi
  fi
fi

# 若找到历史 jsonl：basename 去掉 .jsonl 后缀 ≈ session_id（与 claude -r 使用的 id 对齐）
# 同时扫描文件内容，把「已完成的用户题条数」写进 REPO_PROMPT_COUNT，实现中断后续跑
if [[ -n "${SESSION_JSONL}" && -f "${SESSION_JSONL}" ]]; then
  SESSION_REPO="$(basename -- "${SESSION_JSONL}" .jsonl)"
  RESUME_COMPLETED=0
  RESUME_LAST_USER=""
  _resume_scan_jsonl "${SESSION_JSONL}"
  # 把扫描得到的「历史用户题条数」同步到主计数器；后续大循环会在此基础上继续累加
  REPO_PROMPT_COUNT=${RESUME_COMPLETED}
  loop_out "恢复 repo 会话: ${SESSION_JSONL} session_id=${SESSION_REPO} 已完成用户锚点=${REPO_PROMPT_COUNT}"
else
  loop_out "未检测到 repo 侧历史 jsonl，从零开始"
fi

# Mock 默认忽略 jsonl 恢复，避免本机历史把计数顶满导致阶段一不进循环；需要测恢复路径时设 LOOP_MOCK_USE_JSONL_RESUME=1
if [[ -n "${LOOP_USE_MOCK_CLAUDE:-}" && -z "${LOOP_MOCK_USE_JSONL_RESUME:-}" ]]; then
  SESSION_JSONL=""
  SESSION_REPO=""
  REPO_PROMPT_COUNT=0
  loop_out "Mock：已忽略 jsonl 恢复（LOOP_MOCK_USE_JSONL_RESUME=1 可保留）"
fi

# ---------- 会话日志（MOCK/正式共用，单 txt；文件名含 RUN_ID，拿到 repo session 后尽量改为 repo__<id>__）----------
LOOP_LOG_DIR="${LOOP_LOG_DIR:-${SCRIPT_DIR}/loop_logs}"
mkdir -p "${LOOP_LOG_DIR}"
if [[ -n "${SESSION_REPO}" ]]; then
  LOOP_LOG_FILE="${LOOP_LOG_FILE:-${LOOP_LOG_DIR}/repo__${SESSION_REPO}__${RUN_ID}.txt}"
else
  LOOP_LOG_FILE="${LOOP_LOG_FILE:-${LOOP_LOG_DIR}/run__${REPO}__${RUN_ID}.txt}"
fi
: >"${LOOP_LOG_FILE}"
export LOOP_LOG_FILE
loop_out "会话日志: ${LOOP_LOG_FILE}"

# 已经满 38：脚本唯一「成功 stdout」仍是打印 session_id，供外层 run.sh 等脚本捕获
if [[ "${REPO_PROMPT_COUNT}" -ge "${TOTAL_TARGET}" ]]; then
  loop_out "已达 TOTAL_TARGET=${TOTAL_TARGET}，无需运行"
  printf '%s\n' "${SESSION_REPO}"
  exit 0
fi

# 已经 ≥35：阶段一 while 根本不会进入，直接从下面的阶段二开始「PromptFinal 补齐」
if [[ "${REPO_PROMPT_COUNT}" -ge "${PHASE1_TARGET}" ]]; then
  loop_out "已超过阶段一目标，直接进入 PromptFinal 补齐阶段"
fi

# 默认每次运行都「删旧 agent-{repo} 再从模板复制」，保证提问方 prompt 干净。
# 若你本地调试想保留 agent-{repo} 里的缓存/状态：export LOOP_SKIP_AGENT_RESET=1
if [[ -z "${LOOP_SKIP_AGENT_RESET:-}" ]]; then
  loop_out "复制模板 ${AGENT_SRC} -> ${AGENT_DIR}"
  rm -rf "${AGENT_DIR}"
  cp -a "${AGENT_SRC}" "${AGENT_DIR}"
else
  loop_out "LOOP_SKIP_AGENT_RESET 已设置，跳过删除/复制 agent-${REPO}"
  [[ -d "${AGENT_DIR}" ]] || cp -a "${AGENT_SRC}" "${AGENT_DIR}"
fi

# 累积所有 dive 块文本，供阶段二 PromptFinal 渲染 {transcript}
TRANSCRIPT_ALL=""

# =============================================================================
# 阶段一：大循环（PromptEntry → 多轮 dive），直到 REPO_PROMPT_COUNT >= PHASE1_TARGET
# =============================================================================
# 每一轮「大循环」内部顺序：
#   A) 用 sed 替换 PromptEntry.md 里的 {url}、{n}，在 agent-{repo} 里跑 claude → ENTRY_TEXT
#   B) 从 ENTRY_TEXT 解析多行题目，只取前 ENTRY_N 行 → 数组 ENTRY_QUESTIONS
#   C) 重复 DIVE_ROUNDS 次「dive」：
#        3.2.1 在 TARGET_PATH 用入口题问 repo（json-first 或 -r 续聊）
#        3.2.2 把 q/a 填进 PromptDeeper.md（占位符 {views} 为随机 2–4 个分析角度），agent 侧生成更深子题列表
#        3.2.3 每个子题再问 repo（每条子题都使计数 +1）
#        3.2.3b（可选）每条子题后以 LOOP_DEEPER2_PROB 概率再跑 PromptDeeper → 二层子题
#        3.2.4（若未满 35）用 PromptSummary 让 repo 总结本轮 transcript
#   入口题索引用 dr % 题数 轮询：dive 次数可能多于入口题条数，避免数组越界。
# =============================================================================
while [[ "${REPO_PROMPT_COUNT}" -lt "${PHASE1_TARGET}" ]]; do
  # $(...)「命令替换」：执行子命令并把其标准输出字符串贴到当前位置
  ENTRY_N="$(_compute_entry_n)"
  DIVE_ROUNDS="$(_rand_between "${DIVE_ROUNDS_MIN}" "${DIVE_ROUNDS_MAX}")"
  loop_out "========== 大循环: repo_count=${REPO_PROMPT_COUNT}/${PHASE1_TARGET} entry_n=${ENTRY_N} dive_rounds=${DIVE_ROUNDS} =========="

  # sed 用 # 作分隔符，避免 TARGET_PATH（Windows 盘符等）里的 / 与 sed 默认 / 冲突
  PROMPT_ENTRY="$(sed -e "s#{url}#${TARGET_PATH}#g" -e "s#{n}#${ENTRY_N}#g" "${AGENT_DIR}/PromptEntry.md")"

  # 提问方 agent：第一次用 json-first 拿 session_id；之后同一 shell 内用 SESSION_AGENT 续聊
  if [[ -z "${SESSION_AGENT}" ]]; then
    # 首次提问方会话：必须 json-first，才能从返回 JSON 里取出 session_id
    if ! _claude_json_first "${AGENT_DIR}" "${PROMPT_ENTRY}"; then
      loop_err "PromptEntry（首轮）失败"
      exit 1
    fi
    SESSION_AGENT="${PARSE_SID}"
    ENTRY_TEXT="${PARSE_TEXT}"
    loop_err "session_agent(uuid)=${SESSION_AGENT}"
  else
    # 同一 shell 进程内继续用已保存的 SESSION_AGENT 续聊（不重新 json-first）
    if ! _claude_resume_text "${AGENT_DIR}" "${SESSION_AGENT}" "${PROMPT_ENTRY}"; then
      loop_err "PromptEntry（resume）失败"
      exit 1
    fi
    ENTRY_TEXT="${PARSE_TEXT}"
  fi

  # mapfile：把进程替换 <(...) 输出的多行读入数组 _ALL_ENTRY；-t 去掉每行行尾换行
  mapfile -t _ALL_ENTRY < <(_questions_from_text_to_lines "${ENTRY_TEXT}")
  if [[ "${#_ALL_ENTRY[@]}" -eq 0 ]]; then
    loop_err "PromptEntry 未解析到有效题目行"
    exit 1
  fi
  # 只保留前 ENTRY_N 条「入口题」；模型可能多生成，脚本硬截断保证节奏可控
  ENTRY_QUESTIONS=()
  for ((i = 0; i < ENTRY_N && i < ${#_ALL_ENTRY[@]}; i++)); do
    ENTRY_QUESTIONS+=("${_ALL_ENTRY[$i]}")
  done
  if [[ "${#ENTRY_QUESTIONS[@]}" -eq 0 ]]; then
    loop_err "截取后无有效题目"
    exit 1
  fi
  loop_out "本轮入口题数: ${#ENTRY_QUESTIONS[@]}"

  # dr：dive round 索引，从 0 到 DIVE_ROUNDS-1
  for ((dr = 0; dr < DIVE_ROUNDS; dr++)); do
    if [[ "${REPO_PROMPT_COUNT}" -ge "${PHASE1_TARGET}" ]]; then
      loop_out "已达阶段一目标，提前结束本轮 dive"
      break
    fi
    idx=$((dr % ${#ENTRY_QUESTIONS[@]}))
    q="${ENTRY_QUESTIONS[idx]}"
    loop_out "---------- dive $((dr + 1))/${DIVE_ROUNDS}（入口题 $((idx + 1))）----------"
    loop_out "3.2.1 repo Q: $(_preview_text "${q}")"

    # 答题方 repo：同样「首次 json-first，之后 -r 续聊」
    if [[ -z "${SESSION_REPO}" ]]; then
      if ! _claude_json_first "${TARGET_PATH}" "${q}"; then
        loop_err "3.2.1 repo 首轮失败"
        exit 1
      fi
      SESSION_REPO="${PARSE_SID}"
      A_REPO="${PARSE_TEXT}"
      _loop_log_bind_repo_session
      loop_err "session_repo(uuid)=${SESSION_REPO}"
    else
      if ! _claude_resume_text "${TARGET_PATH}" "${SESSION_REPO}" "${q}"; then
        loop_err "3.2.1 repo resume 失败"
        exit 1
      fi
      A_REPO="${PARSE_TEXT}"
    fi
    # 每在 repo 侧成功发一条用户消息并拿到回复，就把「阶段一计数器」+1
    REPO_PROMPT_COUNT=$((REPO_PROMPT_COUNT + 1))

    # printf -v VAR "格式" ...：把格式化结果写进变量 VAR，避免子 shell 里赋值丢失
    printf -v BLOCK "Q1:\n%s\nA1:\n%s\n" "${q}" "${A_REPO}"
    TRANSCRIPT_DIVE="${BLOCK}"

    # loop_render_prompt.py：把模板里的占位符（如 {question}）替换成文件内容，生成最终 prompt 文件
    loop_out "3.2.2 agent-${REPO} deeper"
    printf '%s' "${q}" >"${WORK}/q.txt"
    printf '%s' "${A_REPO}" >"${WORK}/a1.txt"
    printf '%s' "$(_pick_views_text)" >"${WORK}/views.txt"
    python3 "${RENDER_PY}" "${AGENT_DIR}/PromptDeeper.md" "${WORK}/deeper.md" \
      "question=${WORK}/q.txt" "anwser=${WORK}/a1.txt" \
      "views=${WORK}/views.txt"
    PROMPT_DEEPER="$(cat "${WORK}/deeper.md")"
    # deeper 只在「提问方」会话里跑；repo 侧不参与生成子题
    if ! _claude_resume_text "${AGENT_DIR}" "${SESSION_AGENT}" "${PROMPT_DEEPER}"; then
      loop_err "3.2.2 agent deeper 失败"
      exit 1
    fi
    DEEPER_PACK="${PARSE_TEXT}"
    mapfile -t DEEPER_QS < <(_questions_from_text_to_lines "${DEEPER_PACK}")
    loop_out "3.2.2 新题数: ${#DEEPER_QS[@]}"

    # 子题逐条问 repo；若中途已达 35，立刻 break，避免超量
    qi=0
    # "${数组[@]}"：展开成「多个独立单词」，带引号也能保留每项里的空格
    for dq in "${DEEPER_QS[@]}"; do
      qi=$((qi + 1))
      if [[ "${REPO_PROMPT_COUNT}" -ge "${PHASE1_TARGET}" ]]; then
        loop_out "已达阶段一目标，跳过剩余 deeper 子题"
        break
      fi
      loop_out "3.2.3 repo 子题 ${qi}: $(_preview_text "${dq}")"
      if ! _claude_resume_text "${TARGET_PATH}" "${SESSION_REPO}" "${dq}"; then
        loop_err "3.2.3 repo 失败"
        exit 1
      fi
      AD="${PARSE_TEXT}"
      REPO_PROMPT_COUNT=$((REPO_PROMPT_COUNT + 1))
      printf -v BLOCK "Q_deeper_%s:\n%s\nA_deeper_%s:\n%s\n" "${qi}" "${dq}" "${qi}" "${AD}"
      TRANSCRIPT_DIVE+="${BLOCK}"

      # ---------- 可选：随机「二层 deeper」——再用 (dq, AD) 渲染 PromptDeeper，追问一批子题 ----------
      if [[ "${DEEPER2_PROB}" -gt 0 ]] && [[ "${REPO_PROMPT_COUNT}" -lt "${PHASE1_TARGET}" ]]; then
        if [[ "$((RANDOM % 100))" -lt "${DEEPER2_PROB}" ]]; then
          loop_out "3.2.3b 二层 deeper（概率 ${DEEPER2_PROB}% 已命中，最多 ${DEEPER2_MAX_QS} 条子题）"
          printf '%s' "${dq}" >"${WORK}/q2.txt"
          printf '%s' "${AD}" >"${WORK}/a2.txt"
          printf '%s' "$(_pick_views_text)" >"${WORK}/views2.txt"
          python3 "${RENDER_PY}" "${AGENT_DIR}/PromptDeeper.md" "${WORK}/deeper2.md" \
            "question=${WORK}/q2.txt" "anwser=${WORK}/a2.txt" \
            "views=${WORK}/views2.txt"
          PROMPT_DEEPER2="$(cat "${WORK}/deeper2.md")"
          if ! _claude_resume_text "${AGENT_DIR}" "${SESSION_AGENT}" "${PROMPT_DEEPER2}"; then
            loop_err "3.2.3b agent deeper2 失败"
            exit 1
          fi
          DEEPER2_PACK="${PARSE_TEXT}"
          mapfile -t DEEPER2_QS < <(_questions_from_text_to_lines "${DEEPER2_PACK}")
          loop_out "3.2.3b 二层新题数: ${#DEEPER2_QS[@]}（将截断至 ${DEEPER2_MAX_QS}）"
          d2i=0
          for d2q in "${DEEPER2_QS[@]}"; do
            d2i=$((d2i + 1))
            [[ "${d2i}" -gt "${DEEPER2_MAX_QS}" ]] && break
            if [[ "${REPO_PROMPT_COUNT}" -ge "${PHASE1_TARGET}" ]]; then
              loop_out "已达阶段一目标，跳过剩余二层子题"
              break
            fi
            loop_out "3.2.3b repo 二层子题 ${qi}.${d2i}: $(_preview_text "${d2q}")"
            if ! _claude_resume_text "${TARGET_PATH}" "${SESSION_REPO}" "${d2q}"; then
              loop_err "3.2.3b repo 二层失败"
              exit 1
            fi
            A2="${PARSE_TEXT}"
            REPO_PROMPT_COUNT=$((REPO_PROMPT_COUNT + 1))
            printf -v BLOCK "Q_deeper2_%s_%s:\n%s\nA_deeper2_%s_%s:\n%s\n" \
              "${qi}" "${d2i}" "${d2q}" "${qi}" "${d2i}" "${A2}"
            TRANSCRIPT_DIVE+="${BLOCK}"
          done
        fi
      fi
    done

    # Summary 也算 repo 侧一次用户消息 → 计数 +1；若已达阶段一目标则整段 Summary 跳过
    if [[ "${REPO_PROMPT_COUNT}" -ge "${PHASE1_TARGET}" ]]; then
      loop_out "已达阶段一目标，跳过 Summary"
    else
      loop_out "3.2.4 PromptSummary -> repo"
      printf '%s' "${TRANSCRIPT_DIVE}" >"${WORK}/transcript_dive.txt"
      printf '%s' "$(_pick_views_text)" >"${WORK}/views_summary.txt"
      python3 "${RENDER_PY}" "${AGENT_DIR}/PromptSummary.md" "${WORK}/summary.md" \
        "question=${WORK}/q.txt" "anwser=${WORK}/a1.txt" \
        "views=${WORK}/views_summary.txt" \
        "transcript=${WORK}/transcript_dive.txt"
      SUM_PROMPT="$(cat "${WORK}/summary.md")"
      if ! _claude_resume_text "${TARGET_PATH}" "${SESSION_REPO}" "${SUM_PROMPT}"; then
        loop_err "3.2.4 summary repo 失败"
        exit 1
      fi
      ASUM="${PARSE_TEXT}"
      REPO_PROMPT_COUNT=$((REPO_PROMPT_COUNT + 1))
      TRANSCRIPT_DIVE+=$'\nQ_summary:\n'"${SUM_PROMPT}"$'\nA_summary:\n'"${ASUM}"$'\n'
    fi

    # $'\n' 是 bash 的 C 风格转义，表示真正的换行符
    TRANSCRIPT_ALL+="${TRANSCRIPT_DIVE}"$'\n'
  done
done

# =============================================================================
# 阶段二：PromptFinal 循环，把 repo 侧从当前计数补到 TOTAL_TARGET（默认 38）
# =============================================================================
# 每一轮：
#   1) 用「至今为止的全局 transcript」渲染 PromptFinal.md
#   2) agent 侧根据 FINAL 提示生成「下一道补齐题」（解析成多行时只取第一行）
#   3) repo 侧回答该题，计数 +1，并把 Q/A 追加进 TRANSCRIPT_ALL 供下一轮 Final 使用
# =============================================================================
if [[ -z "${SESSION_REPO}" ]]; then
  loop_err "错误: repo session 为空"
  exit 1
fi

# 阶段二仍需要「提问方」SESSION_AGENT：通常由阶段一第一次 PromptEntry 创建。
# 若你从外部手工恢复场景，请确保本进程里 SESSION_AGENT 已非空，否则下面 resume 会失败。

loop_out "========== 阶段二 PromptFinal 补齐至 ${TOTAL_TARGET}（当前 ${REPO_PROMPT_COUNT}）=========="
while [[ "${REPO_PROMPT_COUNT}" -lt "${TOTAL_TARGET}" ]]; do
  # 把内存中的大字符串落盘，供 Python 渲染器按路径读取占位符 transcript
  printf '%s' "${TRANSCRIPT_ALL}" >"${WORK}/transcript_all.txt"
  python3 "${RENDER_PY}" "${AGENT_DIR}/PromptFinal.md" "${WORK}/final.md" \
    "transcript=${WORK}/transcript_all.txt"
  FINAL_ASK="$(cat "${WORK}/final.md")"

  if ! _claude_resume_text "${AGENT_DIR}" "${SESSION_AGENT}" "${FINAL_ASK}"; then
    loop_err "PromptFinal（agent）失败"
    exit 1
  fi
  FINAL_Q="${PARSE_TEXT}"
  mapfile -t FQL < <(_questions_from_text_to_lines "${FINAL_Q}")
  # 模型若返回「一行一题」，只取第一题，保证每轮阶段二恰好推进 1 次 repo 计数
  if [[ "${#FQL[@]}" -gt 0 ]]; then
    FINAL_Q_ONE="${FQL[0]}"
  else
    FINAL_Q_ONE="$(_str_trim "${FINAL_Q}")"
  fi
  loop_out "补齐题: $(_preview_text "${FINAL_Q_ONE}")"

  if ! _claude_resume_text "${TARGET_PATH}" "${SESSION_REPO}" "${FINAL_Q_ONE}"; then
    loop_err "PromptFinal（repo）回答失败"
    exit 1
  fi
  FA="${PARSE_TEXT}"
  REPO_PROMPT_COUNT=$((REPO_PROMPT_COUNT + 1))
  TRANSCRIPT_ALL+=$'\nQ_final_fill:\n'"${FINAL_Q_ONE}"$'\nA_final_fill:\n'"${FA}"$'\n'
  loop_out "补齐后 repo_count=${REPO_PROMPT_COUNT}/${TOTAL_TARGET}"
done

# 说明：loop_out / loop_err 都写到 stderr；留给上游脚本「管道捕获」的只有最后一行 stdout
loop_out "repo 侧累计用户题次数: ${REPO_PROMPT_COUNT}"
loop_out "Session ID (repo): ${SESSION_REPO}"
loop_out "======================================================"
loop_out "=                    Loop Done                       ="
loop_out "======================================================"

# 约定：stdout 仅输出 repo 的 session_id（uuid），便于 run.sh 等外层用 VAR=$(./loop.sh ...) 接住
printf '%s\n' "${SESSION_REPO}"
