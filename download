#!/usr/bin/env bash
# Check GitHub Pages and workflow status.
# Usage: ./deploy/check_github_pages.sh owner/repo
set -euo pipefail
REPO="${1:-}"
if [[ -z "$REPO" ]]; then
  echo "Usage: $0 owner/repo" >&2
  exit 1
fi
command -v gh >/dev/null 2>&1 || { echo "Missing gh CLI" >&2; exit 1; }

echo "Pages:"
gh api "repos/$REPO/pages" --jq '{html_url, status, source}'
echo
echo "Secrets registered:"
gh secret list --repo "$REPO"
echo
echo "Recent workflow runs:"
gh run list --repo "$REPO" --limit 5
