#!/usr/bin/env bash
# claude-session.sh — start or attach to the persistent claude tmux session
# idempotent: safe to run multiple times

SESSION="claude"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "Session '$SESSION' already exists — attaching..."
  tmux attach-session -t "$SESSION"
else
  echo "Creating new tmux session '$SESSION'..."
  tmux new-session -d -s "$SESSION" -c "$PROJECT_ROOT"
  tmux send-keys -t "$SESSION" "cd '$PROJECT_ROOT' && clear && echo 'claude session ready — project: $PROJECT_ROOT'" Enter
  tmux attach-session -t "$SESSION"
fi
