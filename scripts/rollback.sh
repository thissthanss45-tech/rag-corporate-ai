#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <previous_image_tag>"
  exit 1
fi

PREV_TAG="$1"

export BOT_IMAGE_TAG="$PREV_TAG"

echo "Rolling back to image tag: $BOT_IMAGE_TAG"
docker compose up -d --build --no-deps rag-bot
docker compose ps

echo "Rollback finished."
