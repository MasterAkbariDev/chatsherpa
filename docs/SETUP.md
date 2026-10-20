# Setup & developer guide

- [Install](#install)
- [Run the login server on a VPS](#run-the-login-server-on-a-vps)
- [Set up a Telegram bot](#set-up-a-telegram-bot) — fresh (×2) or existing
- [Working with the library (developers)](#working-with-the-library-developers)

---

## Install

```bash
git clone <repo> && cd chatsherpa
python -m venv .venv && source .venv/bin/activate
pip install -e '.[server,crypto,telegram]'   # extras you need
playwright install chromium
```

Extras: `server` (aiohttp login server), `crypto` (encrypt sessions at rest),
`telegram` (python-telegram-bot). `rich` and `playwright` come by default.

---

## Run the login server on a VPS

The server hands users a link to log in; it stores the captured session and
emits events. It needs an HTTPS **domain** (see
[DEPLOY.md](DEPLOY.md) for why, and for Caddy / Cloudflare / nginx configs).

```bash
# 1. configure — writes .env, prints a reverse-proxy snippet for your domain
chatsherpa setup

# 2. generate a key so sessions are encrypted at rest (recommended)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
#   → put it in .env as ENCRYPTION_KEY=...

# 3. run it (behind your HTTPS reverse proxy)
chatsherpa serve
```

`.env` keys the server reads:

```
PUBLIC_BASE_URL=https://login.yourdomain.com   # how links are built
SERVER_HOST=0.0.0.0
SERVER_PORT=8080
API_SECRET=<shared secret your bot sends>
ENCRYPTION_KEY=<fernet key>                     # encrypt stored sessions
WEBHOOK_URL=https://yourhost/ai-errors          # optional: errors POSTed here
WEBHOOK_SECRET=<hmac secret>
```

Two login link modes (the bot picks per provider):

- **`live`** — the server runs the browser and streams it to the user. Good for
  password / 2FA logins.
- **`connect`** — the user logs in with **their own** browser (needed for Google
  / OAuth, which block automated browsers). They finish via the
  [browser extension](../extension/) or `chatsherpa grab`.

Health check: `GET {PUBLIC_BASE_URL}/healthz`.

---

## Set up a Telegram bot

Get a bot token from [@BotFather](https://t.me/BotFather) first. Your users get
`/ai <msg>`, `/ai_login <provider>`, `/ai_use <provider>`, `/ai_status`.

The login server (`chatsherpa serve`) runs **separately** and the bot talks to it
over `/api/login`, so the bot process never launches browsers.

### Fresh bot — way 1: python-telegram-bot

Full file: [examples/telegram_fresh_ptb.py](../examples/telegram_fresh_ptb.py).

```python
from telegram.ext import ApplicationBuilder
from chatsherpa import ChatSherpa, FileSessionStore
from chatsherpa.integrations import TelegramBridge

bridge = ChatSherpa(store=FileSessionStore("sessions", encryption_key=KEY))
app = ApplicationBuilder().token(BOT_TOKEN).build()
TelegramBridge(bridge, login_server_url=PUBLIC_BASE_URL, api_secret=API_SECRET,
               connect_providers=("gemini",)).register(app)
app.run_polling()
```

### Fresh bot — way 2: aiogram v3

Full file: [examples/telegram_fresh_aiogram.py](../examples/telegram_fresh_aiogram.py).

```python
from aiogram import Bot, Dispatcher
from chatsherpa.integrations import TelegramBridge

dp = Dispatcher()
TelegramBridge(bridge, login_server_url=PUBLIC_BASE_URL,
               api_secret=API_SECRET).register_aiogram(dp)
await dp.start_polling(Bot(BOT_TOKEN))
```

### Add to an EXISTING bot

Full file: [examples/telegram_existing.py](../examples/telegram_existing.py). Keep
your Application and handlers; add two lines. Everything is namespaced under
`command_prefix` ("ai") so nothing collides:

```python
bridge = ChatSherpa(store=FileSessionStore("sessions"))
TelegramBridge(bridge, command_prefix="ai",
               login_server_url=PUBLIC_BASE_URL, api_secret=API_SECRET).register(app)
```

Want to keep routing yourself (no auto handlers)? Don't call `register()` — call
the framework-agnostic core from your own handler:

```python
tg = TelegramBridge(bridge, login_server_url=PUBLIC_BASE_URL, api_secret=API_SECRET)
reply = await tg.handle(user_id, message_text)   # returns a string
```

### Telling users about errors

Register a webhook or callback so an expired session / rate-limit reaches you:

```python
from chatsherpa import WebhookSink
bridge.add_sink(WebhookSink("https://yourhost/ai-errors", secret=SECRET))
# or in-process:
bridge.on_error(lambda ev: notify_user(ev.user_id, ev.message))
```

---

## Working with the library (developers)

Everything goes through one object. No Playwright types leak out — you pass a
provider name, a user id, and text.

```python
import asyncio
from chatsherpa import ChatSherpa, FileSessionStore

async def main():
    bridge = ChatSherpa(store=FileSessionStore("sessions"))
    print(await bridge.ask("chatgpt", user_id=42, prompt="Summarize CRDTs"))

asyncio.run(main())
```

### Sessions (auth)

Each (user, provider) has a stored session. Create one by:

- `chatsherpa login <provider>` — headful, password/2FA (not Google).
- `chatsherpa grab <provider> --cdp http://localhost:9222` — from the user's own
  Chrome/Edge (the Google-safe path).
- `chatsherpa import <provider> cookies.json` — from a Cookie-Editor export.
- In a bot: `/ai_login <provider>` → a link → extension or `grab`.

Stores: `FileSessionStore(dir, encryption_key=...)`, `MemorySessionStore`, or
your own `SessionStore` subclass (Redis/DB).

### Model selection

```python
await bridge.ask("grok", 42, "hi", model="Expert")                  # one model
await bridge.ask("gemini", 42, "hi", model_order=["2.5 Pro", "Flash"])  # first available
```

Or set a default on the config: `ProviderConfig(model_order=[...])`. Selection is
best-effort — availability depends on the account (e.g. Grok's Expert is
Premium-gated); the first selectable model in the order wins, else it proceeds
with whatever is current.

### Errors & events

`ask()` raises typed errors and emits events:

| Error | ErrorType event | When |
|---|---|---|
| `LoginRequiredError` | `LoginRequired` | no/expired session |
| `RateLimitedError` | `RateLimited` | "high demand" / capacity / limit message |
| `ResponseTimeoutError` | `ResponseTimeout` | no stable reply in time |
| `ProviderError` | `SelectorMiss` | UI/selector failure |

```python
from chatsherpa import RateLimitedError
try:
    await bridge.ask("grok", 42, "hi")
except RateLimitedError:
    ...  # tell the user Grok is busy
```

Listeners: `bridge.on_event(cb)`, `bridge.on_error(cb)`, `bridge.add_sink(WebhookSink(...))`.

### Providers & selectors

Provider adapters (`providers/*.py`) are mostly CSS selectors. These web UIs
change often — when one breaks, override selectors per call without editing the
library:

```python
from chatsherpa import ChatGPTProvider
ChatGPTProvider(session, selectors={"input_selector": "…", "assistant_message_selector": "…"})
```

ChatGPT's reply reading is JS-based (anchored on the "ChatGPT said:" a11y label)
because its DOM is obfuscated; other providers use `assistant_message_selector`.
Add a new provider by subclassing `BaseProvider` and setting its selectors.

### Concurrency

`ChatSherpa` reuses a warm browser context per (user, provider) via `BrowserPool`,
serializes sends per context, and caps total concurrency. Call `await
bridge.aclose()` on shutdown.
