#!/usr/bin/env bash
# 与 api_create_zip() 等价：在 exec_dir 的父目录下生成「目录名.zip」，
# 包内路径为相对于父目录的路径（顶层为目录名）；排除规则与 Python 一致。
# 依赖：bash、find、zip（Ubuntu 默认/预装常用）
set -euo pipefail

zip_out() { printf '[zip.sh][%s]%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
zip_err() { printf '[zip.sh][%s]%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; }

usage() {
  zip_err "用法: $0 <执行路径>"
  zip_err "或: EXEC_PATH=<路径> $0"
}

exec_path="${1:-${EXEC_PATH:-}}"
if [[ -z "${exec_path}" ]]; then
  usage
  zip_err "错误: 执行路径未找到"
  exit 1
fi

if [[ ! -d "${exec_path}" ]]; then
  zip_err "错误: 执行路径不存在: ${exec_path}"
  exit 1
fi

# 与 Path.parent / Path.name 一致
parent="$(dirname -- "${exec_path}")"
base="$(basename -- "${exec_path}")"
zip_filename="${base}.zip"
zip_path="${parent}/${zip_filename}"

# 新建压缩包（与 ZipFile(..., 'w') 一致：覆盖已有文件）
rm -f -- "${zip_path}"

# 在 parent 下执行，使 find 输出路径与 Python arcname（relative_to(parent)）一致
# 排除：目录名以 __MACOSX 开头、目录名以 . 开头（不进入）；文件以 . 开头或名为 .DS_Store
(
  cd -- "${parent}" || exit 1
  find "${base}" \
    \( -type d \( -name '__MACOSX*' -o -name '.*' \) -prune \) -o \
    \( -type f ! -name '.*' ! -name '.DS_Store' -print \) |
    zip -q "${zip_filename}" -@
)

zip_out "压缩包已创建！"
zip_out "zip_path=${zip_path}"
zip_out "zip_filename=${zip_filename}"
