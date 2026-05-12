#!/usr/bin/env bash
# 按后缀白名单统计代码行数与文件数，stdout 只输出一行难度：easy | medium | difficult；参数为项目目录 target_path
# 依赖：bash、find(GNU)、wc、xargs
set -euo pipefail

evaluate_err() { printf '[evaluate.sh][%s]%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; }

usage() { evaluate_err "用法: $0 <target_path>"; }

[[ "${#}" -ge 1 ]] || { usage; exit 1; }
target_path="${1}"
[[ -d "${target_path}" ]] || { evaluate_err "错误: 目录不存在: ${target_path}"; exit 1; }

# 排除常见依赖/构建目录（仅按目录名匹配）
find_repo() {
  find "${target_path}" -type d \( \
    -name '.git' -o -name 'node_modules' -o -name 'bower_components' \
    -o -name 'vendor' -o -name '.venv' -o -name 'venv' -o -name '__pycache__' \
    -o -name '.tox' -o -name '.gradle' -o -name 'build' -o -name 'dist' \
    -o -name 'out' -o -name 'target' -o -name '.next' -o -name 'coverage' -o -name 'Pods' \
  \) -prune -o -type f \( \
    -name '*.c' -o -name '*.h' -o -name '*.cc' -o -name '*.cpp' -o -name '*.cxx' \
    -o -name '*.hpp' -o -name '*.hh' -o -name '*.m' -o -name '*.mm' -o -name '*.swift' \
    -o -name '*.java' -o -name '*.kt' -o -name '*.kts' -o -name '*.scala' -o -name '*.groovy' \
    -o -name '*.py' -o -name '*.pyw' -o -name '*.pyi' -o -name '*.rb' -o -name '*.php' \
    -o -name '*.pl' -o -name '*.pm' -o -name '*.r' -o -name '*.R' -o -name '*.jl' -o -name '*.lua' \
    -o -name '*.js' -o -name '*.jsx' -o -name '*.mjs' -o -name '*.cjs' -o -name '*.ts' -o -name '*.tsx' \
    -o -name '*.vue' -o -name '*.svelte' -o -name '*.astro' \
    -o -name '*.css' -o -name '*.scss' -o -name '*.sass' -o -name '*.less' -o -name '*.styl' \
    -o -name '*.html' -o -name '*.htm' -o -name '*.svg' \
    -o -name '*.yaml' -o -name '*.yml' -o -name '*.toml' -o -name '*.ini' -o -name '*.cfg' -o -name '*.conf' \
    -o -name '*.json' -o -name '*.jsonc' -o -name '*.xml' -o -name '*.xsl' \
    -o -name '*.gradle' -o -name '*.gradle.kts' -o -name '*.properties' \
    -o -name '*.go' -o -name '*.rs' -o -name '*.zig' -o -name '*.nim' -o -name '*.dart' \
    -o -name '*.ex' -o -name '*.exs' -o -name '*.erl' -o -name '*.hrl' \
    -o -name '*.clj' -o -name '*.cljs' -o -name '*.edn' \
    -o -name '*.fs' -o -name '*.fsx' -o -name '*.cs' -o -name '*.vb' -o -name '*.fsproj' \
    -o -name '*.sql' -o -name '*.graphql' -o -name '*.gql' -o -name '*.proto' -o -name '*.thrift' \
    -o -name '*.sh' -o -name '*.bash' -o -name '*.zsh' -o -name '*.fish' -o -name '*.ps1' -o -name '*.bat' -o -name '*.cmd' \
    -o -name '*.cmake' -o -name 'CMakeLists.txt' -o -name 'Makefile' -o -name 'makefile' -o -name 'GNUmakefile' \
    -o -name 'Dockerfile' -o -name 'dockerfile' -o -name '*.dockerfile' \
    -o -name '*.jsp' -o -name '*.jspx' -o -name '*.asp' -o -name '*.aspx' -o -name '*.cshtml' \
    -o -name '*.mdx' -o -name '*.rsx' -o -name '*.hbs' -o -name '*.ejs' -o -name '*.pug' \
  \) "$@"
}

lines=0
files=0
wc_out="$(find_repo -print0 | xargs -r -0 wc -l 2>/dev/null || true)"
if [[ -n "${wc_out}" ]]; then
  lines="$(printf '%s\n' "${wc_out}" | tail -n1 | awk '{print $1}')"
  [[ -n "${lines}" ]] || lines=0
  files="$(printf '%s\n' "${wc_out}" | awk '$2=="total"{next} {n++} END{print n+0}')"
fi

# 规则与需求字面一致；未命中任一区间 → medium（见注释）
# Easy: LOC < 20_000 且 文件数 < 50
# Medium: 20_000 ≤ LOC ≤ 50_000 且 50 ≤ 文件 ≤ 500
# Difficult: LOC > 200_000 且 文件数 > 500
difficulty=medium
if ((lines < 16000 && files < 45)); then
  difficulty=easy
elif ((lines > 160000 && files > 450)); then
  difficulty=difficult
elif ((lines >= 16000 && lines <= 50000 && files >= 45 && files <= 450)); then
  difficulty=medium
fi

printf '%s\n' "${difficulty}"
