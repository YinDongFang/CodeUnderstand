#!/usr/bin/env bash
set -euo pipefail

target="${1:?用法: $0 <target>}"
root="$(cd "$(dirname "$0")" && pwd)"

echo "root: $root"
echo "target: $target"

tmux kill-session -t "$target" 2>/dev/null || true
tmux new-session -s "$target" bash -c 'cd "$0" && python3 main.py "$1" --session="$target"; bash' "$root" "$target"
