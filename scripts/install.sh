#!/bin/bash

# 初始化脚本
# curl -fsSL https://gh-proxy.org/https://raw.githubusercontent.com/YinDongFang/CodeUnderstand/refs/heads/main/scripts/install.sh | bash

rm -rf ~/CodeUnderstand

git clone https://gh-proxy.org/https://github.com/YinDongFang/CodeUnderstand.git ~/CodeUnderstand
chmod -R 777 ~/CodeUnderstand

# 初始化环境
export GITLAB_TOKEN=glpat-715OYKqh4csG_JXaCKTVjW86MQp1OmQH.01.0w02nysxy
wget -O ~/sync_config.py "http://118.196.99.121:8000/public/%E5%88%9D%E5%A7%8B%E5%8C%96%E8%84%9A%E6%9C%AC/sync_config.py"
python3 -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
python3 -m pip install chardet flask  -i https://pypi.tuna.tsinghua.edu.cn/simple
python3 ~/sync_config.py CodeUnderstand

# 打补丁更新.claude/settings.json
python3 ~/CodeUnderstand/scripts/merge_claude_settings.py

# 安装smux
curl -fsSL https://gh-proxy.org/https://raw.githubusercontent.com/ShawnPana/smux/main/install.sh | bash
source ~/.bashrc
sed -i '/^set -g pane-border-indicators arrows$/d' ~/.smux/tmux.conf

# 检查版本
claude --version
