#!/usr/bin/env bash
# Publish issuer proactive sales platform static MVP to GitHub Pages.
# Usage:
#   ./deploy/publish_github_pages.sh [repo_name] [public|private] [branch]
# Example:
#   ./deploy/publish_github_pages.sh issuer-sales-platform public main

set -euo pipefail

REPO_NAME="${1:-issuer-sales-platform}"
VISIBILITY="${2:-public}"
BRANCH="${3:-main}"

if [[ "$VISIBILITY" != "public" && "$VISIBILITY" != "private" ]]; then
  echo "VISIBILITY must be 'public' or 'private'." >&2
  exit 1
fi

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    echo "Install it and re-run this script." >&2
    exit 1
  fi
}

need_cmd git
need_cmd gh

if ! gh auth status >/dev/null 2>&1; then
  echo "GitHub CLI is not authenticated. Starting gh auth login..."
  gh auth login
fi

OWNER="$(gh api user --jq '.login')"
REPO="$OWNER/$REPO_NAME"

# Ensure we run at repository root, not deploy/.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

# Git init/commit.
if [[ ! -d .git ]]; then
  git init
fi

git add .
if ! git diff --cached --quiet; then
  git commit -m "Initial 발행사 선제 영업 플랫폼 정적 MVP"
else
  echo "No staged changes to commit. Continuing..."
fi

git branch -M "$BRANCH"

# Create repo if needed and push.
if gh repo view "$REPO" >/dev/null 2>&1; then
  echo "Repository already exists: $REPO"
  if ! git remote get-url origin >/dev/null 2>&1; then
    git remote add origin "https://github.com/$REPO.git"
  fi
else
  echo "Creating repository: $REPO ($VISIBILITY)"
  if [[ "$VISIBILITY" == "public" ]]; then
    gh repo create "$REPO" --public --source=. --remote=origin --push
  else
    gh repo create "$REPO" --private --source=. --remote=origin --push
  fi
fi

git push -u origin "$BRANCH"

# Set secrets interactively. Empty input skips.
echo
echo "Register GitHub Actions secrets. Press Enter to skip a secret."
read -rsp "OPENDART_API_KEY: " OPENDART_API_KEY || true; echo
read -rsp "NAVER_CLIENT_ID: " NAVER_CLIENT_ID || true; echo
read -rsp "NAVER_CLIENT_SECRET: " NAVER_CLIENT_SECRET || true; echo

if [[ -n "${OPENDART_API_KEY:-}" ]]; then
  printf "%s" "$OPENDART_API_KEY" | gh secret set OPENDART_API_KEY --repo "$REPO"
fi
if [[ -n "${NAVER_CLIENT_ID:-}" ]]; then
  printf "%s" "$NAVER_CLIENT_ID" | gh secret set NAVER_CLIENT_ID --repo "$REPO"
fi
if [[ -n "${NAVER_CLIENT_SECRET:-}" ]]; then
  printf "%s" "$NAVER_CLIENT_SECRET" | gh secret set NAVER_CLIENT_SECRET --repo "$REPO"
fi

# Enable GitHub Pages from branch root.
echo "Enabling GitHub Pages from $BRANCH:/ ..."
if gh api "repos/$REPO/pages" >/dev/null 2>&1; then
  gh api --method PUT "repos/$REPO/pages" \
    -H "Accept: application/vnd.github+json" \
    -f "source[branch]=$BRANCH" \
    -f "source[path]=/" >/dev/null
else
  gh api --method POST "repos/$REPO/pages" \
    -H "Accept: application/vnd.github+json" \
    -f "source[branch]=$BRANCH" \
    -f "source[path]=/" >/dev/null
fi

# Trigger first snapshot update. If keys were skipped, it will still generate sample snapshot.
echo "Triggering first daily snapshot workflow..."
if gh workflow run update-daily-snapshot.yml --repo "$REPO" >/dev/null 2>&1; then
  echo "Workflow dispatched."
else
  echo "Could not dispatch workflow yet. It can take a moment after initial push; run it manually from Actions if needed."
fi

echo
echo "Done."
echo "Repository: https://github.com/$REPO"
echo "Pages URL, usually: https://$OWNER.github.io/$REPO_NAME/"
echo "Check Pages status: gh api repos/$REPO/pages --jq '.html_url, .status'"
