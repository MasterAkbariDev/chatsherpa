#!/usr/bin/env bash
# Ship ChatSherpa to a server and keep it running.
#
#   SERVER=root@95.182.94.71 SSH_PORT=2112 ./deploy/deploy.sh [options]
#   SSH_PROXY='nc -X 5 -x 127.0.0.1:10808 %h %p' SERVER=root@host ./deploy/deploy.sh
#
# Always: sends the committed code (git archive HEAD), installs/updates the
# package in /opt/chatsherpa, links `chatsherpa` onto PATH, (re)installs the
# systemd service and restarts it, then checks health.
#
# Options:
#   --chromium   install Chromium + its OS deps + Xvfb, trying several download
#                mirrors (needed only for live-view login and server-side ask;
#                connect mode does not need it). Raises the service memory cap.
#   --nginx      ensure the /chatsherpa/ proxy block exists in NGINX_SITE and
#                reload nginx (needs NGINX_SITE and NGINX_DOMAIN env vars).
#   --remote-dir DIR   install location on the server (default /opt/chatsherpa)
set -euo pipefail
cd "$(dirname "$0")/.."

: "${SERVER:?set SERVER=user@host}"
SSH_PORT="${SSH_PORT:-22}"
REMOTE_DIR="${REMOTE_DIR:-/opt/chatsherpa}"
DO_CHROMIUM=0
DO_NGINX=0
while [ $# -gt 0 ]; do
    case "$1" in
        --chromium) DO_CHROMIUM=1 ;;
        --nginx) DO_NGINX=1 ;;
        --remote-dir) REMOTE_DIR="$2"; shift ;;
        -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=25 -p "$SSH_PORT")
[ -n "${SSH_PROXY:-}" ] && SSH_OPTS+=(-o "ProxyCommand=$SSH_PROXY")

if ! git diff --quiet HEAD -- || [ -n "$(git ls-files --others --exclude-standard)" ]; then
    echo "note: only committed code (HEAD) is deployed; local changes are skipped." >&2
fi

echo "==> shipping code to $SERVER:$REMOTE_DIR"
git archive --format=tar HEAD | ssh "${SSH_OPTS[@]}" "$SERVER" \
  "REMOTE_DIR='$REMOTE_DIR' DO_CHROMIUM='$DO_CHROMIUM' DO_NGINX='$DO_NGINX' \
   NGINX_SITE='${NGINX_SITE:-}' NGINX_DOMAIN='${NGINX_DOMAIN:-}' bash -s" <<'REMOTE'
set -euo pipefail
mkdir -p "$REMOTE_DIR"
tar -x -C "$REMOTE_DIR" -f -
cd "$REMOTE_DIR"

echo "==> python venv + package"
if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv 2>/dev/null || { apt-get update -qq && apt-get install -y -qq python3-venv && python3 -m venv .venv; }
fi
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e ".[server,crypto]"

echo "==> chatsherpa on PATH"
ln -sf "$REMOTE_DIR/.venv/bin/chatsherpa" /usr/local/bin/chatsherpa
chatsherpa --help >/dev/null && echo "    chatsherpa -> $(command -v chatsherpa)"

MEM=350M
if [ "$DO_CHROMIUM" = "1" ]; then
    echo "==> Chromium + OS deps + Xvfb"
    apt-get update -qq || true
    apt-get install -y -qq xvfb >/dev/null 2>&1 || echo "    (could not apt-get xvfb; headed mode will not work)"
    .venv/bin/python -m playwright install-deps chromium >/dev/null 2>&1 \
        || echo "    (playwright install-deps failed; may need manual apt libs)"
    MIRRORS="https://playwright.azureedge.net \
             https://playwright-akamai.azureedge.net \
             https://playwright-verizon.azureedge.net \
             https://cdn.npmmirror.com/binaries/playwright \
             https://npmmirror.com/mirrors/playwright"
    ok=0
    for m in $MIRRORS; do
        echo "    trying mirror: $m"
        if PLAYWRIGHT_DOWNLOAD_HOST="$m" timeout 360 .venv/bin/python -m playwright install chromium >/dev/null 2>&1; then
            echo "    Chromium installed via $m"; ok=1; break
        fi
    done
    [ "$ok" = "1" ] || echo "    WARNING: all Chromium mirrors failed; live-view/ask will not work (connect mode still works)"
    MEM=900M
fi

echo "==> systemd service (MemoryMax=$MEM)"
id chatsherpa >/dev/null 2>&1 || useradd --system --home-dir "$REMOTE_DIR" --shell /usr/sbin/nologin chatsherpa
mkdir -p sessions
# Give Playwright's browser cache to the service user when Chromium is installed.
[ -d /root/.cache/ms-playwright ] && { mkdir -p "$REMOTE_DIR/.cache"; cp -rn /root/.cache/ms-playwright "$REMOTE_DIR/.cache/" 2>/dev/null || true; }
chown -R chatsherpa:chatsherpa "$REMOTE_DIR"
sed "s#MemoryMax=.*#MemoryMax=$MEM#" deploy/chatsherpa.service > /etc/systemd/system/chatsherpa.service
systemctl daemon-reload
systemctl enable chatsherpa >/dev/null 2>&1 || true
systemctl restart chatsherpa
sleep 4
echo "    service: $(systemctl is-active chatsherpa)"
curl -fsS http://127.0.0.1:8080/healthz && echo || echo "    health check FAILED — see: journalctl -u chatsherpa -n 40"

if [ "$DO_NGINX" = "1" ]; then
    echo "==> nginx /chatsherpa/ proxy"
    if [ -z "$NGINX_SITE" ] || [ -z "$NGINX_DOMAIN" ]; then
        echo "    set NGINX_SITE=/etc/nginx/sites-available/<file> and NGINX_DOMAIN=<domain>; skipping."
    elif [ ! -f "$NGINX_SITE" ]; then
        echo "    NGINX_SITE not found: $NGINX_SITE; skipping."
    elif grep -q "location /chatsherpa/" "$NGINX_SITE"; then
        echo "    /chatsherpa/ already present; nothing to do."
    else
        cp -p "$NGINX_SITE" "$NGINX_SITE.bak-chatsherpa-$(date +%Y%m%d-%H%M%S)"
        BLOCK=$'    location = /chatsherpa {\n        return 301 https://'"$NGINX_DOMAIN"$'/chatsherpa/;\n    }\n\n    location /chatsherpa/ {\n        proxy_pass http://127.0.0.1:8080/;\n        proxy_http_version 1.1;\n        proxy_set_header Upgrade $http_upgrade;\n        proxy_set_header Connection "upgrade";\n        proxy_set_header Host $host;\n        proxy_read_timeout 3600s;\n        proxy_send_timeout 3600s;\n    }\n'
        awk -v block="$BLOCK" 'NR==1{found=0} /location \/ \{/ && !found {print block; found=1} {print}' \
            "$NGINX_SITE" > "$NGINX_SITE.tmp" && mv "$NGINX_SITE.tmp" "$NGINX_SITE"
        if nginx -t 2>/tmp/nginx-t; then
            systemctl reload nginx && echo "    added and reloaded."
        else
            echo "    nginx test FAILED, rolling back:"; cat /tmp/nginx-t
            mv "$NGINX_SITE".bak-chatsherpa-* "$NGINX_SITE"
        fi
    fi
fi
echo "==> done."
REMOTE
