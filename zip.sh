#!/usr/bin/env bash
# 与 api_create_zip() 等价：在 exec_dir 的父目录下生成「目录名.zip」，
# 包内路径为相对于父目录的路径（顶层为目录名）；排除规则与 Python 一致。
# 依赖：bash、find、zip（Ubuntu 默认/预装常用）
set -euo pipefail

usage() {
  echo "用法: $0 <执行路径>" >&2
  echo "或: EXEC_PATH=<路径> $0" >&2
}

exec_path="${1:-${EXEC_PATH:-}}"
if [[ -z "${exec_path}" ]]; then
  usage
  echo "错误: 执行路径未找到" >&2
  exit 1
fi

if [[ ! -d "${exec_path}" ]]; then
  echo "错误: 执行路径不存在: ${exec_path}" >&2
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

echo "压缩包已创建！"
echo "zip_path=${zip_path}"
echo "zip_filename=${zip_filename}"
