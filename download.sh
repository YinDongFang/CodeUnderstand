#!/usr/bin/env bash

# 用法: ./download.sh <GitHub ZIP URL>
# 例如: ./download.sh https://github.com/storybookjs/storybook/archive/refs/heads/next.zip
#
# 环境变量 PROJECTS_DIR：与 pack.sh / run.sh 一致，本地项目根目录，默认 ${HOME}/projects（由调用方保证已展开为绝对路径）

set -e

download_out() { printf '[download.sh][%s]%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
download_err() { printf '[download.sh][%s]%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; }

download_out "======================================================"
download_out "=                    Start Download                  ="
download_out "======================================================"

if [ $# -ne 1 ]; then
  download_err "Usage: $0 <GitHub ZIP URL>"
  exit 1
fi

ZIP_URL="$1"

# 使用正则提取 user, repo, branch
if [[ "$ZIP_URL" =~ github\.com/([^/]+)/([^/]+)/archive/refs/heads/([^.]+)\.zip ]]; then
  USER="${BASH_REMATCH[1]}"
  REPO="${BASH_REMATCH[2]}"
  BRANCH="${BASH_REMATCH[3]}"
else
  download_err "Error: URL does not match GitHub ZIP format."
  exit 1
fi

: "${PROJECTS_DIR:=${HOME}/projects}"
mkdir -p "$PROJECTS_DIR"

# ZIP 文件保存路径
ZIP_FILE="$PROJECTS_DIR/${REPO}.zip"

download_out "Downloading $ZIP_URL via gh-proxy.org ..."
wget -O "$ZIP_FILE" "https://gh-proxy.org/$ZIP_URL"

download_out "Download complete. Saved as $ZIP_FILE"

# 解压到 projects 目录
download_out "Unzipping $ZIP_FILE ..."
unzip -q "$ZIP_FILE" -d "$PROJECTS_DIR"

# 解压目录通常是 {repo}-{branch}
EXTRACTED_DIR="$PROJECTS_DIR/${REPO}-${BRANCH}"
TARGET_DIR="$PROJECTS_DIR/$REPO"

if [ ! -d "$EXTRACTED_DIR" ]; then
  download_err "Error: extracted directory $EXTRACTED_DIR not found"
  exit 1
fi

# 如果目标目录已存在，先删除
if [ -d "$TARGET_DIR" ]; then
  download_out "Directory $TARGET_DIR already exists. Removing ..."
  rm -rf "$TARGET_DIR"
fi

# 重命名目录
mv "$EXTRACTED_DIR" "$TARGET_DIR"

# 进入目录初始化 git
cd "$TARGET_DIR"

download_out "Initializing git repository ..."
git -c user.email="temp@example.com" -c user.name="temp" init >/dev/null 2>&1
git -c user.email="temp@example.com" -c user.name="temp" add . >/dev/null 2>&1
git -c user.email="temp@example.com" -c user.name="temp" commit -m "Initial commit from $ZIP_URL" >/dev/null 2>&1

download_out "All done! Repository initialized in $(pwd)"

download_out "======================================================"
download_out "=                    Download Done                   ="
download_out "======================================================"