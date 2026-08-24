#!/usr/bin/env bash
# 배포: rsync → uv sync → 등록된 서비스 재시작
# 사용: SSHPASS='...' DEPLOY_HOST=user@server ./scripts/deploy.sh   (값은 CLAUDE.local.md 참고)
set -euo pipefail
HOST=${DEPLOY_HOST:?DEPLOY_HOST=user@server 를 지정하세요 (CLAUDE.local.md 참고)}
DIR=${DEPLOY_DIR:-/opt/cointrader}
cd "$(dirname "$0")/.."
SSH="ssh -o StrictHostKeyChecking=no"

sshpass -e rsync -az -e "$SSH" \
  --exclude .venv --exclude .git --exclude /data/ --exclude /reports --exclude CLAUDE.local.md \
  --exclude .claude --exclude __pycache__ --exclude .pytest_cache --exclude .env \
  ./ "$HOST:$DIR/"

sshpass -e $SSH "$HOST" "cd $DIR && export PATH=\$HOME/.local/bin:\$PATH && uv sync -q \
  && for svc in cointrader-paper cointrader-web; do \
       if systemctl is-enabled \$svc >/dev/null 2>&1; then \
         cp deploy/\$svc.service /etc/systemd/system/ && systemctl daemon-reload \
         && systemctl restart \$svc && echo \"\$svc: \$(systemctl is-active \$svc)\"; fi; done"
echo "deployed"
