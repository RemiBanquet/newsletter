#!/usr/bin/env bash
# Deploy code changes to the newsletter repo via git (not web upload).
#
# Usage:
#   ./deploy.sh "commit message" [path ...]
#
# Examples:
#   ./deploy.sh "brief: ground publications + salvage" newsletter_v5/market_brief.py
#   ./deploy.sh "fetcher: add curl_cffi fallback"      # stages all modified tracked files
#
# What it does, in order:
#   1. Refuses to run unless you are on main.
#   2. Pulls origin first (fast-forward) so the daily bot state commits never
#      cause a rejected push.
#   3. Stages the paths you name, or every modified TRACKED file if you name
#      none. Untracked files (About me/, mocks, push_ready/) are never staged.
#   4. Commits and pushes.

set -euo pipefail

REPO="/Users/remibanquet/Documents/Claude/Projects/Daily Agri-News Digest"
cd "$REPO"

# 1. Commit message is required.
if [ $# -lt 1 ] || [ -z "${1:-}" ]; then
  echo "Usage: ./deploy.sh \"commit message\" [path ...]" >&2
  exit 1
fi
MSG="$1"; shift

# 2. Must be on main.
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ "$BRANCH" != "main" ]; then
  echo "Not on main (on '$BRANCH'). Switch with: git checkout main" >&2
  exit 1
fi

# Clear any stale lock from an interrupted git run.
[ -f .git/index.lock ] && rm -f .git/index.lock

# 3. Pull first so the push fast-forwards over the bot's state commits.
echo "Pulling origin/main..."
if ! git pull --ff-only; then
  echo "Pull is not a clean fast-forward. Resolve by hand, then re-run." >&2
  exit 1
fi

# 4. Stage. Named paths if given, otherwise all modified tracked files only.
if [ $# -gt 0 ]; then
  git add -- "$@"
else
  git add -u
fi

# Nothing to ship? Stop.
if git diff --cached --quiet; then
  echo "No staged changes. Nothing to deploy."
  exit 0
fi

echo "About to commit:"
git diff --cached --stat

git commit -m "$MSG"
git push
echo "Deployed: $(git log --oneline -1)"
