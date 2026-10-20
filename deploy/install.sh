#!/usr/bin/env bash
# Fresh install of the ChatSherpa login server on a Linux host. Run as root ON
# the server. Clones the repo, installs the package, generates secrets, and
# starts the systemd service.
#
#   PUBLIC_BASE_URL=https://login.example.com bash install.sh
#   curl -fsSL https://raw.githubusercontent.com/MasterAkbariDev/chatsherpa/main/deploy/install.sh \
#       | PUBLIC_BASE_URL=https://login.example.com bash
#
# Env vars:
#   PUBLIC_BASE_URL   required — the public https URL the login links use
#   INSTALL_DIR       default /opt/chatsherpa
#   SERVER_PORT       default 8080 (loopback only; put nginx in front)
#   REPO_URL          default the GitHub repo
#   WITH_CHROMIUM=1   also install Chromium + OS deps + Xvfb (mirror fallback)
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/MasterAkbariDev/chatsherpa.git}"
INSTALL_DIR="${INSTALL_DIR:-/opt/chatsherpa}"
SERVER_PORT="${SERVER_PORT:-8080}"
BASE="${PUBLIC_BASE_URL:-}"

[ "$(id -u)" = "0" ] || { echo "run as root"; exit 1; }
if [ -z "$BASE" ]; then
    if [ -t 0 ]; then read -rp "Public base URL (https://domain[/path]): " BASE; fi
    [ -n "$BASE" ] || { echo "set PUBLIC_BASE_URL"; exit 1; }
fi

echo "==> packages"
command -v git >/dev/null || { apt-get update -qq && apt-get install -y -qq git; }
python3 -m venv --help >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y -qq python3-venv; }
command -v openssl >/dev/null || { apt-get update -qq && apt-get install -y -qq openssl; }

echo "==> code -> $INSTALL_DIR"
git config --global --add safe.directory "$INSTALL_DIR" 2>/dev/null || true
if [ -d "$INSTALL_DIR/.git" ]; then git -C "$INSTALL_DIR" pull -q --ff-only; else git clone -q "$REPO_URL" "$INSTALL_DIR"; fi
cd "$INSTALL_DIR"

echo "==> venv + package"
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e ".[server,crypto]"
ln -sf "$INSTALL_DIR/.venv/bin/chatsherpa" /usr/local/bin/chatsherpa

MEM=350M
if [ "${WITH_CHROMIUM:-0}" = "1" ]; then
    echo "==> Chromium + Xvfb (mirror fallback)"
    apt-get update -qq || true
    apt-get install -y -qq xvfb >/dev/null 2>&1 || echo "    (xvfb apt failed)"
    .venv/bin/python -m playwright install-deps chromium >/dev/null 2>&1 || echo "    (install-deps failed)"
    for m in https://playwright.azureedge.net https://playwright-akamai.azureedge.net \
             https://cdn.npmmirror.com/binaries/playwright https://npmmirror.com/mirrors/playwright; do
        echo "    mirror: $m"
        PLAYWRIGHT_DOWNLOAD_HOST="$m" timeout 360 .venv/bin/python -m playwright install chromium >/dev/null 2>&1 \
            && { echo "    Chromium ok"; break; }
    done
    MEM=900M
fi

echo "==> config (.env)"
if [ ! -f .env ]; then
    SECRET="$(openssl rand -hex 32)"
    KEY="$(.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
    umask 177
    cat > .env <<EOF
PUBLIC_BASE_URL=$BASE
SERVER_HOST=127.0.0.1
SERVER_PORT=$SERVER_PORT
API_SECRET=$SECRET
ENCRYPTION_KEY=$KEY
EOF
    umask 022
    echo "    generated API_SECRET and ENCRYPTION_KEY"
else
    echo "    .env already present, keeping it"
fi

echo "==> systemd service"
id chatsherpa >/dev/null 2>&1 || useradd --system --home-dir "$INSTALL_DIR" --shell /usr/sbin/nologin chatsherpa
mkdir -p sessions
sed "s#MemoryMax=.*#MemoryMax=$MEM#" deploy/chatsherpa.service > /etc/systemd/system/chatsherpa.service
chown -R chatsherpa:chatsherpa "$INSTALL_DIR"
systemctl daemon-reload
systemctl enable --now chatsherpa
sleep 4
echo "    service: $(systemctl is-active chatsherpa)"
curl -fsS "http://127.0.0.1:$SERVER_PORT/healthz" && echo || echo "    health check FAILED: journalctl -u chatsherpa -n 40"

if [ -n "${NGINX_SITE:-}" ] && [ -n "${NGINX_DOMAIN:-}" ]; then
    echo "==> nginx /chatsherpa/ proxy"
    if [ ! -f "$NGINX_SITE" ]; then
        echo "    NGINX_SITE not found: $NGINX_SITE; skipping."
    elif grep -q "location /chatsherpa/" "$NGINX_SITE"; then
        echo "    already present."
    else
        cp -p "$NGINX_SITE" "$NGINX_SITE.bak-chatsherpa-$(date +%Y%m%d-%H%M%S)"
        BLOCK=$'    location = /chatsherpa {\n        return 301 https://'"$NGINX_DOMAIN"$'/chatsherpa/;\n    }\n\n    location /chatsherpa/ {\n        proxy_pass http://127.0.0.1:'"$SERVER_PORT"$'/;\n        proxy_http_version 1.1;\n        proxy_set_header Upgrade $http_upgrade;\n        proxy_set_header Connection "upgrade";\n        proxy_set_header Host $host;\n        proxy_read_timeout 3600s;\n        proxy_send_timeout 3600s;\n    }\n'
        awk -v b="$BLOCK" '/location \/ \{/ && !d {print b; d=1} {print}' "$NGINX_SITE" > "$NGINX_SITE.tmp" && mv "$NGINX_SITE.tmp" "$NGINX_SITE"
        if nginx -t 2>/dev/null; then systemctl reload nginx && echo "    added and reloaded."
        else echo "    nginx test failed, rolling back."; mv "$NGINX_SITE".bak-chatsherpa-* "$NGINX_SITE"; fi
    fi
fi

echo
echo "Installed at $INSTALL_DIR."
echo "Next:"
if [ -z "${NGINX_SITE:-}" ]; then
    echo "  1. Put nginx in front: see deploy/nginx-snippet.conf, then: nginx -t && systemctl reload nginx"
    echo "     (or re-run with NGINX_SITE=/etc/nginx/sites-available/<file> NGINX_DOMAIN=<domain>)"
fi
echo "  * Your bot posts to $BASE/api/login with header X-ChatSherpa-Secret"
echo "    (read it: grep ^API_SECRET $INSTALL_DIR/.env)"
