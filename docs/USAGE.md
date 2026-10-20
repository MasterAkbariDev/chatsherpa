# Usage

Everything beyond the README: sessions, multiple accounts, the login server,
Telegram bots, and deploying to a VPS.

## Sessions

Each session is stored per `(account, provider)`. Create one with:

- `chatsherpa grab <provider> --cdp http://localhost:9222` — from your own
  Chrome/Edge started with `--remote-debugging-port`. The Google-safe path.
- `chatsherpa login <provider>` — opens a browser window (password logins, not
  Google).
- `chatsherpa import <provider> cookies.json` — from a Cookie-Editor export.

Stores: `FileSessionStore(dir, encryption_key=...)`, `MemorySessionStore`, or
your own `SessionStore` subclass.

### Multiple accounts

Separate sessions per provider live under the `--user` label (default `local`):

```bash
chatsherpa grab chatgpt --cdp http://localhost:9222 --user work
chatsherpa ask chatgpt "hi" --user work
chatsherpa sessions                 # provider + account + age
chatsherpa remove chatgpt --user work   # or --all for every account
```

A single Gemini login can hold several Google accounts, addressed by the `/u/N/`
slot (0 is the first signed-in account):

```bash
chatsherpa ask gemini "hi" --account 1
```

### Notes on providers

- **ChatGPT** is blocked in headless Chromium (Cloudflare). Run headed: `--headful`
  locally, or via Xvfb on the server (installed by `deploy.sh --chromium`).
- **Gemini** is slow in headless; prefer a headed run.
- Selectors break when a provider changes its UI; override per call with
  `Provider(session, selectors={...})`.

## Login server

The server hands users a link to log in, stores the captured session, and emits
events. It needs an HTTPS domain (not a bare IP — certs aren't issued for IPs).

```bash
chatsherpa setup     # writes .env, prints a reverse-proxy snippet
chatsherpa serve     # runs on SERVER_HOST:SERVER_PORT
```

`.env` keys: `PUBLIC_BASE_URL`, `SERVER_HOST`, `SERVER_PORT`, `API_SECRET`,
`ENCRYPTION_KEY` (encrypt sessions at rest), `WEBHOOK_URL`/`WEBHOOK_SECRET`.

Login link modes (chosen per provider by the bot):

- `live` — the server runs the browser and streams it (password / 2FA).
- `connect` — the user logs in with their own browser (needed for Google). They
  finish with the [extension](../extension/) or `chatsherpa grab --token ... --server ...`.

nginx: put the block from [../deploy/nginx-snippet.conf](../deploy/nginx-snippet.conf)
above your catch-all `location /`, then `nginx -t && systemctl reload nginx`.

## Telegram

The library never owns a bot — you mount its handlers on your existing one.
Users get `/ai <msg>`, `/ai_login`, `/ai_use`, `/ai_status`.

```python
from chatsherpa import ChatSherpa, FileSessionStore
from chatsherpa.integrations import TelegramBridge

bridge = ChatSherpa(store=FileSessionStore("sessions"))
tg = TelegramBridge(bridge, command_prefix="ai",
                    login_server_url=PUBLIC_BASE_URL, api_secret=API_SECRET,
                    connect_providers=("gemini",))
tg.register(application)            # python-telegram-bot
# tg.register_aiogram(dispatcher)   # aiogram v3
# reply = await tg.handle(user_id, text)   # any framework
```

Runnable examples: [telegram_fresh_ptb.py](../examples/telegram_fresh_ptb.py),
[telegram_fresh_aiogram.py](../examples/telegram_fresh_aiogram.py),
[telegram_existing.py](../examples/telegram_existing.py). The login server runs
separately (`chatsherpa serve`); the bot talks to it over `/api/login`.

## Deploy to a VPS

```bash
make deploy SERVER=root@HOST SSH_PORT=22 \
  [SSH_PROXY='nc -X 5 -x 127.0.0.1:1080 %h %p']
```

Ships only committed code (`git archive HEAD`), installs the package, links
`chatsherpa` onto PATH, (re)installs the systemd service, restarts, and checks
health. Flags:

- `make deploy ARGS=--chromium` — install Chromium + OS deps + Xvfb, trying
  several download mirrors (needed only for live-view / server-side `ask`;
  connect mode needs no browser).
- `--nginx` with `NGINX_SITE` and `NGINX_DOMAIN` — add the proxy block to an
  nginx site (idempotent, with backup and rollback).

Service logs: `journalctl -u chatsherpa -n 50 --no-pager`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Chromium not found` | `make browsers` |
| `No session for ...` | `chatsherpa grab` / `login` / `import` for that provider |
| Google "browser may not be secure" | use `grab` with your own Edge, not `login` |
| ChatGPT "not authenticated" but session is fresh | add `--headful` (headless is blocked) |
| grab can't reach port 9222 | start Edge with `--remote-debugging-port=9222`; quit Edge fully first |
| Server returns 502 on `/chatsherpa/` | `systemctl restart chatsherpa` |
