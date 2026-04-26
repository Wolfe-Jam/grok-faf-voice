#!/bin/bash
set -e

docker build -t ghcr.io/wolfejam/grok-faf-voice:latest .
docker push ghcr.io/wolfejam/grok-faf-voice:latest

uv run lk agent create radiofaf-crew \
  --env XAI_API_KEY="$XAI_API_KEY" \
  --image "ghcr.io/wolfejam/grok-faf-voice:latest" \
  --deploy

echo "✅ RadioFAF crew deployed to LiveKit Cloud"
