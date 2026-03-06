#!/bin/bash
set -e

REPO_DIR="/home/user/dashboard"
BRANCH="main"

cd "$REPO_DIR"

echo "==> Fetch & merge..."
git fetch origin "$BRANCH"
git merge origin/"$BRANCH"

echo "==> Install/update dependencies..."
.venv/bin/pip install -r requirements.txt -q

echo "==> Reload PM2..."
if pm2 list | grep -q "dashboard"; then
  pm2 reload dashboard
else
  pm2 start ecosystem.config.js
fi

pm2 save

echo "==> Done."
pm2 status dashboard
