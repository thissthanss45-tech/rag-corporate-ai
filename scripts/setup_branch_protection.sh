#!/usr/bin/env bash
set -euo pipefail

if ! command -v gh >/dev/null 2>&1; then
  echo "gh CLI is required: https://cli.github.com/"
  exit 1
fi

REPO="${GH_REPO:-}"
BRANCH="${GH_BRANCH:-main}"

if [[ -z "$REPO" ]]; then
  echo "Set GH_REPO=owner/repo"
  exit 1
fi

echo "Applying branch protection for ${REPO}:${BRANCH}"

gh api \
  --method PUT \
  -H "Accept: application/vnd.github+json" \
  "/repos/${REPO}/branches/${BRANCH}/protection" \
  -f required_status_checks.strict=true \
  -F required_status_checks.contexts[]='pre-merge-quality-gate' \
  -F required_status_checks.contexts[]='lint-and-test' \
  -f enforce_admins=true \
  -f required_pull_request_reviews.dismiss_stale_reviews=true \
  -f required_pull_request_reviews.require_code_owner_reviews=false \
  -F required_pull_request_reviews.required_approving_review_count=1 \
  -f required_conversation_resolution=true \
  -f restrictions= \
  -f allow_force_pushes=false \
  -f allow_deletions=false \
  -f block_creations=false \
  -f required_linear_history=false \
  -f lock_branch=false \
  -f allow_fork_syncing=true

echo "Branch protection applied successfully"
