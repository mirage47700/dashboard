#!/bin/bash
# update.sh — Met a jour le dashboard et relance les services
set -e

BRANCH="${1:-main}"
DIR="/home/dashboard"

echo "[update] Pull branche: $BRANCH"
cd "$DIR"
git fetch origin
git checkout "$BRANCH"
git pull origin "$BRANCH"

echo "[update] Redemarrage dashboard principal..."
pm2 restart dashboard 2>/dev/null || pm2 start ecosystem.config.js --only dashboard 2>/dev/null || true

pm2 save

echo "[update] OK — $(date '+%Y-%m-%d %H:%M:%S')"
