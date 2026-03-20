#!/usr/bin/env bash

set -e

SESSION_NAME=${1:-main}

echo "===> Killing existing tmux server (if any)..."
tmux kill-server 2>/dev/null || true

echo "===> Starting new tmux session: $SESSION_NAME"
tmux new -s "$SESSION_NAME"