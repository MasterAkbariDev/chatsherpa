#!/usr/bin/env bash
# Fresh install of the ChatSherpa login server on a Linux host. Run as root.
#
#   bash install.sh                      # interactive (asks domain, name, nginx)
#   PUBLIC_BASE_URL=https://example.com/chatsherpa bash install.sh
#   curl -fsSL .../install.sh | bash     # blank domain → http://<this-ip>:<free port from 8091>
#
# Env (all optional; you are asked for anything missing):
#   PUBLIC_BASE_URL  full public URL incl. the name path, e.g. https://example.com/chatsherpa
#   MOUNT            project name = URL path = nginx location (default chatsherpa)
#   NGINX_DOMAIN     the public domain (when PUBLIC_BASE_URL is not given).
#                    Leave empty to serve directly on this server's IP (no nginx).
#   NGINX_SITE       nginx site file to edit (skips the interactive picker)
#   INSTALL_DIR      default /opt/chatsherpa   SERVER_PORT  asked for (default: .env, else 8091);
#                    a busy port is refused. 8080 is often taken by nginx.
#   REPO_URL         the git repo              WITH_CHROMIUM=1  install Chromium
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/MasterAkbariDev/chatsherpa.git}"
INSTALL_DIR="${INSTALL_DIR:-/opt/chatsherpa}"
# Port: never picked silently. Given via SERVER_PORT, or asked for (default: the one in
# .env, else 8091). A busy port is refused and asked again.
port_in_use() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
EXISTING_PORT="$(sed -n 's/^SERVER_PORT=//p' "$INSTALL_DIR/.env" 2>/dev/null | head -1 || true)"
SERVER_PORT="${SERVER_PORT:-}"
BASE="${PUBLIC_BASE_URL:-}"
MOUNT="${MOUNT:-}"
DOMAIN="${NGINX_DOMAIN:-}"
BIND=127.0.0.1
VIA_IP=""
NGINX_ON=""

# ---- colors + helpers -------------------------------------------------------
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    RST=$'\e[0m'; B=$'\e[1m'; DIM=$'\e[2m'; RED=$'\e[31m'; GRN=$'\e[32m'
    YLW=$'\e[33m'; CYN=$'\e[36m'; TEAL=$'\e[38;5;37m'; COLOR=1
else
    RST=""; B=""; DIM=""; RED=""; GRN=""; YLW=""; CYN=""; TEAL=""; COLOR=""
fi
TTY=""; { [ -e /dev/tty ] && [ -r /dev/tty ]; } && TTY=/dev/tty

step() { printf "\n${B}${CYN}▸ %s${RST}\n" "$1"; }
ok()   { printf "  ${GRN}✓${RST} %s\n" "$1"; }
warn() { printf "  ${YLW}!${RST} %s\n" "$1"; }
die()  { printf "  ${RED}✗ %s${RST}\n" "$1"; exit 1; }
ask()  { local p="$1" d="$2" a=""; [ -n "$TTY" ] && read -rp "$p" a <"$TTY" || true; printf '%s' "${a:-$d}"; }

# ---- port -------------------------------------------------------------------
if [ -z "$SERVER_PORT" ]; then
    if [ -z "$TTY" ]; then
        [ -n "$EXISTING_PORT" ] && SERVER_PORT="$EXISTING_PORT" \
            || { printf "  ✗ no port: set SERVER_PORT=<free port> (e.g. 8091) or run in a terminal\n" >&2; exit 1; }
    else
        while :; do
            SERVER_PORT="$(ask "  Port for the login server [${EXISTING_PORT:-8091}]: " "${EXISTING_PORT:-8091}")"
            if ! printf '%s' "$SERVER_PORT" | grep -qE '^[0-9]+$' || [ "$SERVER_PORT" -lt 1024 ] || [ "$SERVER_PORT" -gt 65535 ]; then
                printf "  ✗ %s is not a usable port (1024-65535)\n" "$SERVER_PORT" >&2; continue
            fi
            if port_in_use "$SERVER_PORT"; then
                printf "  ✗ port %s is already in use on this server; pick another\n" "$SERVER_PORT" >&2; continue
            fi
            break
        done
    fi
fi

run() { # run "message" "shell command"  — spinner + ok/fail, logs on failure
    local msg="$1" cmd="$2" log rc; log="$(mktemp)"
    bash -c "$cmd" >"$log" 2>&1 & local pid=$!
    if [ -n "$COLOR" ]; then
        local fr='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏' i=0
        while kill -0 "$pid" 2>/dev/null; do
            i=$(((i+1)%10)); printf "\r  ${CYN}%s${RST} %s" "${fr:i:1}" "$msg"; sleep 0.1
        done
    else printf "  … %s\n" "$msg"; fi
    if wait "$pid"; then rc=0; else rc=$?; fi
    if [ "$rc" -eq 0 ]; then printf "\r  ${GRN}✓${RST} %s\e[K\n" "$msg"
    else printf "\r  ${RED}✗${RST} %s\e[K\n" "$msg"; tail -6 "$log" | sed 's/^/      /'; fi
    rm -f "$log"; return "$rc"
}

[ "$(id -u)" = "0" ] || die "run as root"
printf "${TEAL}${B}  ChatSherpa installer${RST}\n"

# ---- name + url -------------------------------------------------------------
step "Configuration"
if [ -z "$BASE" ]; then
    if [ -z "$DOMAIN" ]; then
        DOMAIN="$(ask "  public domain (e.g. example.com) [${DIM}blank = this server IP${RST}]: " "")"
    fi
    if [ -z "$DOMAIN" ]; then
        IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i <= NF; i++) if ($i == "src") { print $(i + 1); exit }}')"
        [ -n "$IP" ] || IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
        if [ -n "$TTY" ]; then
            if [ -n "$IP" ]; then
                printf "  ${DIM}detected${RST} ${B}%s${RST} ${DIM}— a VPN can show the tunnel address${RST}\n" "$IP"
                yn="$(ask "  use this IP? [${DIM}Y/n${RST}]: " y)"
                case "$(printf '%s' "$yn" | tr '[:upper:]' '[:lower:]')" in
                    n|no)
                        IP="$(ask "  public IP: " "")"
                        IP="$(printf '%s' "$IP" | tr -d '[:space:]')"
                        ;;
                esac
            else
                IP="$(ask "  public IP [${DIM}type it${RST}]: " "")"
                IP="$(printf '%s' "$IP" | tr -d '[:space:]')"
            fi
        fi
        printf '%s' "$IP" | grep -qE '^[0-9]{1,3}(\.[0-9]{1,3}){3}$' \
            || die "need an IPv4 address — type one at the prompt, or set PUBLIC_BASE_URL"
        BIND=0.0.0.0
        VIA_IP=1
        BASE="http://$IP:$SERVER_PORT"
    else
        [ -n "$MOUNT" ] || MOUNT="$(ask "  project name (URL path) [${DIM}chatsherpa${RST}]: " chatsherpa)"
        MOUNT="${MOUNT:-chatsherpa}"
        BASE="https://$DOMAIN/$MOUNT"
    fi
else
    [ -n "$DOMAIN" ] || DOMAIN="$(printf '%s' "$BASE" | sed -E 's#^https?://([^/]+).*#\1#')"
    [ -n "$MOUNT" ] || MOUNT="$(printf '%s' "$BASE" | sed -E 's#^https?://[^/]+/?##; s#/$##')"
    case "$DOMAIN" in
        [0-9]*.[0-9]*.[0-9]*.[0-9]*) BIND=0.0.0.0; VIA_IP=1 ;;
    esac
fi
if [ -n "$VIA_IP" ]; then
    ok "direct ${B}$BASE${RST}   (no domain, no nginx)"
else
    MOUNT="$(printf '%s' "$MOUNT" | tr '[:upper:]' '[:lower:]' | sed 's#[^a-z0-9/-]##g; s#^/##; s#/$##')"
    ok "name ${B}${MOUNT:-<root>}${RST}   url ${B}$BASE${RST}"
fi

# ---- packages + code --------------------------------------------------------
step "Packages"
command -v git     >/dev/null || run "install git"         "apt-get update -qq && apt-get install -y -qq git" || die "git"
python3 -m venv --help >/dev/null 2>&1 || run "install python3-venv" "apt-get update -qq && apt-get install -y -qq python3-venv" || die "python3-venv"
command -v openssl >/dev/null || run "install openssl"     "apt-get update -qq && apt-get install -y -qq openssl" || die "openssl"
ok "system packages ready"

step "Code → $INSTALL_DIR"
git config --global --add safe.directory "$INSTALL_DIR" 2>/dev/null || true
if [ -d "$INSTALL_DIR/.git" ]; then run "update repository" "git -C '$INSTALL_DIR' pull -q --ff-only" || die "git pull"
else run "clone repository" "git clone -q '$REPO_URL' '$INSTALL_DIR'" || die "git clone"; fi
cd "$INSTALL_DIR"

step "Python environment"
[ -x .venv/bin/python ] || run "create virtualenv" "python3 -m venv .venv" || die "venv"
run "install ChatSherpa (+ server, crypto)" ".venv/bin/pip install -q --upgrade pip && .venv/bin/pip install -q -e '.[server,crypto]'" || die "pip install"
ln -sf "$INSTALL_DIR/.venv/bin/chatsherpa" /usr/local/bin/chatsherpa
ok "chatsherpa command installed"

MEM=350M
if [ "${WITH_CHROMIUM:-0}" = "1" ]; then
    step "Chromium (headed-on-server support)"
    run "xvfb + chromium OS deps" "apt-get update -qq; apt-get install -y -qq xvfb; .venv/bin/python -m playwright install-deps chromium" || warn "some deps failed"
    cdone=""
    for m in https://playwright.azureedge.net https://playwright-akamai.azureedge.net \
             https://cdn.npmmirror.com/binaries/playwright https://npmmirror.com/mirrors/playwright; do
        if run "download Chromium — $m" "PLAYWRIGHT_BROWSERS_PATH='$INSTALL_DIR/.cache/ms-playwright' PLAYWRIGHT_DOWNLOAD_HOST='$m' timeout 360 .venv/bin/python -m playwright install chromium"; then cdone=1; break; fi
    done
    [ -n "$cdone" ] && MEM=900M || warn "all Chromium mirrors failed (connect mode still works)"
fi

# ---- config -----------------------------------------------------------------
# One browser copy, in the service's cache. Root's commands (chatsherpa ask/doctor) link to it.
mkdir -p "$INSTALL_DIR/.cache" /root/.cache
if [ -d "$INSTALL_DIR/.cache/ms-playwright" ] && [ ! -e /root/.cache/ms-playwright ]; then
    ln -s "$INSTALL_DIR/.cache/ms-playwright" /root/.cache/ms-playwright
fi
chown -R chatsherpa:chatsherpa "$INSTALL_DIR/.cache" 2>/dev/null || true

step "Configuration file (.env)"
if [ ! -f .env ]; then
    SECRET="$(openssl rand -hex 32)"
    KEY="$(.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
    ( umask 177; cat > .env <<EOF
PUBLIC_BASE_URL=$BASE
SERVER_HOST=$BIND
SERVER_PORT=$SERVER_PORT
API_SECRET=$SECRET
ENCRYPTION_KEY=$KEY
EOF
    )
    ok "generated API_SECRET and ENCRYPTION_KEY (encrypted sessions)"
else
    warn ".env already present — keeping it (delete it for a clean config)"
fi

# ---- service ----------------------------------------------------------------
step "System service"
id chatsherpa >/dev/null 2>&1 || useradd --system --home-dir "$INSTALL_DIR" --shell /usr/sbin/nologin chatsherpa
mkdir -p sessions
sed "s#MemoryMax=.*#MemoryMax=$MEM#" deploy/chatsherpa.service > /etc/systemd/system/chatsherpa.service
chown -R chatsherpa:chatsherpa "$INSTALL_DIR"
systemctl daemon-reload
run "start service" "systemctl enable --now chatsherpa && sleep 4" || warn "service may not be up"
# The service needs a few seconds to bind; retry instead of failing on the first try.
healthy=0
for _ in $(seq 1 30); do
    curl -fsS "http://127.0.0.1:$SERVER_PORT/healthz" >/dev/null 2>&1 && { healthy=1; break; }
    sleep 1
done
if [ "$healthy" = 1 ]; then ok "service healthy on 127.0.0.1:$SERVER_PORT"
else warn "health check failed — see: journalctl -u chatsherpa -n 40"; fi

# ---- nginx ------------------------------------------------------------------
NGINX_DIR=/etc/nginx/sites-available
[ -d "$NGINX_DIR" ] || NGINX_DIR=/etc/nginx/conf.d

proxy_block() {  # prints the two location blocks for $MOUNT
    cat <<EOF
    location = /$MOUNT {
        return 301 https://$DOMAIN/$MOUNT/;
    }

    location /$MOUNT/ {
        proxy_pass http://127.0.0.1:$SERVER_PORT/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
EOF
}

apply_existing() { # $1 = file
    local f="$1"
    if grep -q "location /$MOUNT/" "$f"; then
        # Already routed: make sure it points at the port this install runs on.
        perl -0777 -i -pe "s#(location /$MOUNT/ \{\s*proxy_pass http://127\.0\.0\.1:)[0-9]+#\${1}$SERVER_PORT#" "$f"
        nginx -t >/dev/null 2>&1 && systemctl reload nginx
        NGINX_ON=1; ok "already routed in $(basename "$f") (port $SERVER_PORT)"; return
    fi
    cp -p "$f" "$f.bak-chatsherpa-$(date +%Y%m%d-%H%M%S)"
    BLOCK="$(proxy_block)" perl -0777 -i -pe 's/^([ \t]*location \/ \{)/$ENV{BLOCK}\n\n$1/m' "$f"
    grep -q "location /$MOUNT/" "$f" || { mv "$f".bak-chatsherpa-* "$f"; warn "no 'location /' in $(basename "$f") — add deploy/nginx-snippet.conf manually"; return; }
    if nginx -t >/dev/null 2>&1; then systemctl reload nginx; NGINX_ON=1; ok "added to $(basename "$f") and reloaded"
        cert_step "$f"
    else mv "$f".bak-chatsherpa-* "$f"; warn "nginx test failed — rolled back"; fi
}

cert_step() { # $1 = nginx file serving $DOMAIN over HTTP
    local live="/etc/letsencrypt/live/$DOMAIN"
    if [ -f "$live/fullchain.pem" ]; then ok "certificate found: $live"; return; fi
    if [ -z "$TTY" ]; then warn "no certificate for $DOMAIN — HTTPS needs one: certbot --nginx -d $DOMAIN"; return; fi
    local ans email
    ans="$(ask "  No certificate for ${B}$DOMAIN${RST}. Get a free Let's Encrypt one now? [y/N]: " n)"
    case "$ans" in y|Y|yes) ;; *) warn "HTTP only. Later: certbot --nginx -d $DOMAIN"; return ;; esac
    email="$(ask "  Email for Let's Encrypt notices: " "")"
    [ -n "$email" ] || { warn "certbot needs an email; skipped"; return; }
    command -v certbot >/dev/null 2>&1 || apt-get install -y -qq certbot python3-certbot-nginx >/dev/null 2>&1 \
        || { warn "could not install certbot; install it, then: certbot --nginx -d $DOMAIN"; return; }
    if certbot --nginx -d "$DOMAIN" -m "$email" --agree-tos -n --redirect >/dev/null 2>&1; then
        nginx -t >/dev/null 2>&1 && systemctl reload nginx; ok "certificate installed for $DOMAIN"
    else
        warn "certbot failed: $DOMAIN must resolve to this server and port 80 must be open. Retry: certbot --nginx -d $DOMAIN"
    fi
}

create_new() { # $1 = file
    local f="$1"
    { printf 'server {\n    listen 80;\n    server_name %s;\n\n' "$DOMAIN"; proxy_block; printf '}\n'; } > "$f"
    [ -d /etc/nginx/sites-enabled ] && ln -sf "$f" "/etc/nginx/sites-enabled/$(basename "$f")"
    if nginx -t >/dev/null 2>&1; then systemctl reload nginx; NGINX_ON=1; ok "created $(basename "$f") (HTTP) and reloaded"
        cert_step "$f"
    else rm -f "$f" "/etc/nginx/sites-enabled/$(basename "$f")"; warn "nginx test failed — new site removed"; fi
}

step "nginx reverse proxy"
DOMAIN_FILE=""
if [ -n "$DOMAIN" ] && [ -d "$NGINX_DIR" ]; then
    DOMAIN_FILE="$(grep -ls "server_name[^;]*\b$DOMAIN\b" "$NGINX_DIR"/* /etc/nginx/sites-enabled/* 2>/dev/null \
        | grep -v '\.bak' | while read -r x; do grep -q "location /" "$x" && { echo "$x"; break; }; done || true)"
fi
if [ -n "$VIA_IP" ]; then
    ok "skipped — open port ${B}$SERVER_PORT${RST} if a firewall is on"
elif [ -z "$MOUNT" ]; then
    warn "root URL (no path) — configure nginx manually"
elif ! command -v nginx >/dev/null 2>&1; then
    warn "nginx not installed — skipping (proxy 127.0.0.1:$SERVER_PORT when you set it up)"
elif [ -z "${NGINX_SITE:-}" ] && [ -n "$DOMAIN_FILE" ]; then
    # Add the routes to the server block that already serves this domain. A second
    # block for the same name would shadow it (that caused a 404 on the live VPS).
    ok "found the existing $DOMAIN block in $(basename "$DOMAIN_FILE")"
    apply_existing "$DOMAIN_FILE"
elif [ -n "${NGINX_SITE:-}" ]; then
    [ -f "$NGINX_SITE" ] && apply_existing "$NGINX_SITE" || create_new "$NGINX_SITE"
elif [ -z "$TTY" ]; then
    warn "no terminal to choose a site — re-run with NGINX_SITE=<file>, or see deploy/nginx-snippet.conf"
else
    # interactive picker (menu to stderr so the chosen path isn't captured)
    files=(); while IFS= read -r x; do files+=("$x"); done < <(find "$NGINX_DIR" -maxdepth 1 -type f -printf '%f\n' 2>/dev/null | sort)
    {
        printf "  ${DIM}site files in %s${RST}\n" "$NGINX_DIR"
        n=1; for x in "${files[@]}"; do printf "    ${CYN}%2d${RST}) %s\n" "$n" "$x"; n=$((n+1)); done
        printf "    ${CYN} n${RST}) create a new file\n    ${CYN} s${RST}) skip\n"
    } >&2
    choice="$(ask "  choose [number/n/s]: " s)"
    if [ "$choice" = "n" ]; then
        nf="$(ask "  new file name [${DIM}$MOUNT${RST}]: " "$MOUNT")"; create_new "$NGINX_DIR/$nf"
    elif printf '%s' "$choice" | grep -qE '^[0-9]+$' && [ "$choice" -ge 1 ] && [ "$choice" -le "${#files[@]}" ]; then
        apply_existing "$NGINX_DIR/${files[$((choice-1))]}"
    else
        warn "skipped nginx — add a 'location /$MOUNT/' proxy to 127.0.0.1:$SERVER_PORT later"
    fi
fi

# ---- done -------------------------------------------------------------------
step "Done"
ok "installed at ${B}$INSTALL_DIR${RST}"
ok "public url  ${B}$BASE${RST}"
if [ -n "$VIA_IP" ] || [ -n "$NGINX_ON" ]; then
    ok "health      ${B}$BASE/healthz${RST}"
else
    ok "health      ${B}http://127.0.0.1:$SERVER_PORT/healthz${RST}"
    warn "public $BASE/healthz answers after nginx proxies 127.0.0.1:$SERVER_PORT"
fi
printf "  ${DIM}bot posts to${RST} %s/api/login ${DIM}with header${RST} X-ChatSherpa-Secret\n" "$BASE"
printf "  ${DIM}read the secret:${RST} grep ^API_SECRET %s/.env\n" "$INSTALL_DIR"
