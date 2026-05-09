#!/bin/bash

# 用法: ./download.sh <GitHub ZIP URL>
# 例如: ./download.sh https://github.com/storybookjs/storybook/archive/refs/heads/next.zip

set -e  # 遇到错误就退出

if [ $# -ne 1 ]; then
    echo "Usage: $0 <GitHub ZIP URL>"
    exit 1
fi

ZIP_URL="$1"

# 使用正则提取 user, repo, branch
if [[ "$ZIP_URL" =~ github\.com/([^/]+)/([^/]+)/archive/refs/heads/([^.]+)\.zip ]]; then
    USER="${BASH_REMATCH[1]}"
    REPO="${BASH_REMATCH[2]}"
    BRANCH="${BASH_REMATCH[3]}"
else
    echo "Error: URL does not match GitHub ZIP format."
    exit 1
fi

# 确保 projects 目录存在
PROJECTS_DIR="~/CodeUnderstand/projects"
mkdir -p "$PROJECTS_DIR"

# ZIP 文件保存路径
ZIP_FILE="$PROJECTS_DIR/${REPO}.zip"

echo "Downloading $ZIP_URL via gh-proxy.org ..."
wget -O "$ZIP_FILE" "https://gh-proxy.org/$ZIP_URL"

echo "Download complete. Saved as $ZIP_FILE"

# 解压到 projects 目录
echo "Unzipping $ZIP_FILE ..."
unzip -q "$ZIP_FILE" -d "$PROJECTS_DIR"

# 解压目录通常是 {repo}-{branch}
EXTRACTED_DIR="$PROJECTS_DIR/${REPO}-${BRANCH}"
TARGET_DIR="$PROJECTS_DIR/$REPO"

if [ ! -d "$EXTRACTED_DIR" ]; then
    echo "Error: extracted directory $EXTRACTED_DIR not found"
    exit 1
fi

# 如果目标目录已存在，先删除
if [ -d "$TARGET_DIR" ]; then
    echo "Directory $TARGET_DIR already exists. Removing ..."
    rm -rf "$TARGET_DIR"
fi

# 重命名目录
mv "$EXTRACTED_DIR" "$TARGET_DIR"

# 进入目录初始化 git
cd "$TARGET_DIR"

echo "Initializing git repository ..."
git config --global user.email "temp@example.com"
git config --global user.name "temp"
git init
git add .
git commit -m "Initial commit from $ZIP_URL"

echo "All done! Repository initialized in $(pwd)"
