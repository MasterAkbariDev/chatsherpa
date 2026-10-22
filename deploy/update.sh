#!/usr/bin/env bash
# Update an existing ChatSherpa install in place. Run as root ON the server.
# Pulls the latest code, reinstalls the package, restarts the service, and
# health-checks it. Keeps your .env, project name, and nginx config untouched.
#
#   bash update.sh
#   curl -fsSL https://raw.githubusercontent.com/MasterAkbariDev/chatsherpa/main/deploy/update.sh | bash
#
# Env (optional):
#   INSTALL_DIR   default /opt/chatsherpa
#   REF           git ref to update to (default: current branch's upstream)
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/chatsherpa}"
REF="${REF:-}"

# ---- colors + helpers (same as install.sh) ---------------------------------
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    RST=$'\e[0m'; B=$'\e[1m'; DIM=$'\e[2m'; RED=$'\e[31m'; GRN=$'\e[32m'
    YLW=$'\e[33m'; CYN=$'\e[36m'; TEAL=$'\e[38;5;37m'; COLOR=1
else
    RST=""; B=""; DIM=""; RED=""; GRN=""; YLW=""; CYN=""; TEAL=""; COLOR=""
fi

step() { printf "\n${B}${CYN}▸ %s${RST}\n" "$1"; }
ok()   { printf "  ${GRN}✓${RST} %s\n" "$1"; }
warn() { printf "  ${YLW}!${RST} %s\n" "$1"; }
die()  { printf "  ${RED}✗ %s${RST}\n" "$1"; exit 1; }

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
printf "${TEAL}${B}  ChatSherpa updater${RST}\n"

# ---- checks -----------------------------------------------------------------
step "Checks"
[ -d "$INSTALL_DIR/.git" ] || die "$INSTALL_DIR is not a ChatSherpa install — run install.sh first"
[ -x "$INSTALL_DIR/.venv/bin/python" ] || die "no virtualenv at $INSTALL_DIR/.venv — run install.sh first"
git config --global --add safe.directory "$INSTALL_DIR" 2>/dev/null || true
cd "$INSTALL_DIR"
OLD="$(git rev-parse --short HEAD 2>/dev/null || echo '?')"
ok "install at $INSTALL_DIR (currently $OLD)"

# ---- pull + install ---------------------------------------------------------
step "Code"
run "fetch latest" "git fetch -q --all" || die "git fetch failed"
if [ -n "$REF" ]; then run "checkout $REF" "git checkout -q '$REF' && git pull -q --ff-only || git reset -q --hard '$REF'" || die "checkout failed"
else run "fast-forward" "git pull -q --ff-only" || die "git pull failed (local changes? commit or stash them)"; fi
NEW="$(git rev-parse --short HEAD)"
if [ "$OLD" = "$NEW" ]; then ok "already up to date ($NEW)"; else ok "updated $OLD → $NEW"; fi

step "Python package"
run "reinstall (picks up dependency changes)" ".venv/bin/pip install -q -e '.[server,crypto]'" || die "pip install failed"
# Keep the chatsherpa command on PATH in case the symlink was lost.
ln -sf "$INSTALL_DIR/.venv/bin/chatsherpa" /usr/local/bin/chatsherpa 2>/dev/null || true
chown -R chatsherpa:chatsherpa "$INSTALL_DIR" 2>/dev/null || true

# ---- restart ----------------------------------------------------------------
step "Service"
PORT="$(sed -n 's/^SERVER_PORT=//p' .env 2>/dev/null | head -1)"; PORT="${PORT:-8091}"
run "restart service" "systemctl restart chatsherpa && sleep 3" || warn "service may not be up"
if curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then ok "service healthy on 127.0.0.1:$PORT"
else warn "health check failed — see: journalctl -u chatsherpa -n 40"; fi

step "Done"
ok "updated to ${B}$NEW${RST}"
[ "$OLD" != "$NEW" ] && printf "  ${DIM}changes:${RST} git -C %s log --oneline %s..%s\n" "$INSTALL_DIR" "$OLD" "$NEW" || true
