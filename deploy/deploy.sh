#!/usr/bin/env bash
# Ship the committed code to the server, install it, and restart the login service.
#
#   SERVER=root@95.182.94.71 SSH_PORT=2112 ./deploy/deploy.sh
#   SSH_PROXY='nc -X 5 -x 127.0.0.1:10808 %h %p' SERVER=root@host ./deploy/deploy.sh
#
# Only committed files (git archive HEAD) are sent, so local .env, sessions and
# virtualenvs never leave your machine.
set -euo pipefail
cd "$(dirname "$0")/.."

: "${SERVER:?set SERVER=user@host}"
SSH_PORT="${SSH_PORT:-22}"
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=25 -p "$SSH_PORT")
if [ -n "${SSH_PROXY:-}" ]; then
    SSH_OPTS+=(-o "ProxyCommand=$SSH_PROXY")
fi

if ! git diff --quiet HEAD -- || [ -n "$(git ls-files --others --exclude-standard)" ]; then
    echo "warning: uncommitted changes are NOT deployed. Only HEAD is sent." >&2
fi

git archive --format=tar HEAD | ssh "${SSH_OPTS[@]}" "$SERVER" '
set -e
mkdir -p /opt/chatsherpa
tar -x -C /opt/chatsherpa -f -
cd /opt/chatsherpa
if [ ! -x .venv/bin/python ]; then python3 -m venv .venv; fi
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e ".[server,crypto]"
install -m 0644 deploy/chatsherpa.service /etc/systemd/system/chatsherpa.service
id chatsherpa >/dev/null 2>&1 || useradd --system --home-dir /opt/chatsherpa --shell /usr/sbin/nologin chatsherpa
mkdir -p sessions
chown -R chatsherpa:chatsherpa /opt/chatsherpa
systemctl daemon-reload
systemctl enable chatsherpa >/dev/null
systemctl restart chatsherpa
sleep 4
echo "service: $(systemctl is-active chatsherpa)"
curl -fsS http://127.0.0.1:8080/healthz; echo
'
