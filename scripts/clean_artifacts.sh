#!/usr/bin/env bash
# 清理产物树中不应提交的文件（macOS 残留、隐藏占位文件）。
# 用法: clean_artifacts.sh <artifact_root>
#   artifact_root 即 code-understand-{repo}/ 根
set -euo pipefail

clean_out() { printf '[clean_artifacts.sh][%s]%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

[[ "${#}" -eq 1 ]] || { echo "用法: $0 <artifact_root>" >&2; exit 1; }
artifact_root="${1}"
[[ -d "${artifact_root}" ]] || { echo "错误: 目录不存在: ${artifact_root}" >&2; exit 1; }

deleted=0

while IFS= read -r -d '' f; do
  rm -f -- "${f}"
  deleted=$((deleted + 1))
done < <(find "${artifact_root}" -type f -name '.DS_Store' -print0 2>/dev/null)

while IFS= read -r -d '' d; do
  rm -rf -- "${d}"
  deleted=$((deleted + 1))
done < <(find "${artifact_root}" -type d -name '__MACOSX' -print0 2>/dev/null)

while IFS= read -r -d '' f; do
  rm -f -- "${f}"
  deleted=$((deleted + 1))
done < <(find "${artifact_root}" -type f -name '._*' -print0 2>/dev/null)

while IFS= read -r -d '' f; do
  rm -f -- "${f}"
  deleted=$((deleted + 1))
done < <(find "${artifact_root}" -type f -name '.gitkeep' -print0 2>/dev/null)

clean_out "已清理 ${deleted} 项"
