#!/usr/bin/env bash
# Remove the ChatSherpa login server from a host. Run as root ON the server.
#
#   bash uninstall.sh
#   NGINX_SITE=/etc/nginx/sites-available/your-site bash uninstall.sh   # also strip the nginx block
#
# Env vars:
#   INSTALL_DIR   default /opt/chatsherpa  (DELETED, including .env and sessions)
#   NGINX_SITE    if set, remove the /chatsherpa/ location block and reload nginx
#   KEEP_USER=1   do not delete the 'chatsherpa' system user
set -uo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/chatsherpa}"
[ "$(id -u)" = "0" ] || { echo "run as root"; exit 1; }

echo "==> stop + remove service"
systemctl stop chatsherpa 2>/dev/null || true
systemctl disable chatsherpa 2>/dev/null || true
rm -f /etc/systemd/system/chatsherpa.service
systemctl daemon-reload 2>/dev/null || true

echo "==> remove PATH link"
rm -f /usr/local/bin/chatsherpa

# MOUNT is the URL-path name used at install (default chatsherpa).
MOUNT="${MOUNT:-chatsherpa}"
if [ -n "${NGINX_SITE:-}" ] && [ -f "$NGINX_SITE" ]; then
    echo "==> strip nginx /$MOUNT/ block"
    cp -p "$NGINX_SITE" "$NGINX_SITE.bak-uninstall-$(date +%Y%m%d-%H%M%S)"
    perl -0777 -i -pe "s/[ \\t]*location = \\/\\Q$ENV{MOUNT}\\E \\{[^}]*\\}\\s*location \\/\\Q$ENV{MOUNT}\\E\\/ \\{[^}]*\\}\\n?//s" "$NGINX_SITE"
    if nginx -t 2>/dev/null; then systemctl reload nginx && echo "    reloaded"; else
        echo "    nginx test failed; restoring"; mv "$NGINX_SITE".bak-uninstall-* "$NGINX_SITE"; fi
fi

echo "==> remove files"
rm -rf "$INSTALL_DIR"

if [ "${KEEP_USER:-0}" != "1" ]; then
    id chatsherpa >/dev/null 2>&1 && userdel chatsherpa 2>/dev/null || true
fi

echo "done. ChatSherpa removed."
