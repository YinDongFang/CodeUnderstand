#!/usr/bin/env bash
set -euo pipefail

target="${1:?用法: $0 <target>}"
root="$(cd "$(dirname "$0")" && pwd)"

echo "tmux session: $target  (python3 main.py $target)"
echo "连接: tmux attach -t $target"

tmux kill-session -t "$target" 2>/dev/null || true
tmux new-session -s "$target" bash -c 'cd "$0" && exec python3 main.py "$1"' "$root" "$target"
