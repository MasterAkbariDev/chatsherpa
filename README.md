# chatsherpa

Talk to AI chat web UIs — **ChatGPT, DeepSeek, Grok, Gemini** — by driving their
websites with headless [Playwright](https://playwright.dev/python/). You log in
once, the library reuses your **cookies / session**, sends prompts, and returns
the assistant's response text.

No official API keys required — it uses the same web session you use in your
browser.

> ⚠️ **Read this first.** You are automating logged-in web sessions. That may
> violate a provider's Terms of Service, and these UIs change often, which
> breaks CSS selectors. Use your own accounts, keep request volume human, and
> expect to occasionally update selectors (they're all overridable — see below).
> This is intended for personal automation and experimentation.

See [DEPLOY.md](docs/DEPLOY.md) covers putting the login server on a domain.

## Install

```bash
cd chatsherpa
python -m venv .venv && source .venv/bin/activate
pip install -e .                 # add [server] for the live-view login server,
                                 # [crypto] to encrypt sessions, [telegram] for the bot adapter
playwright install chromium
```

## The CLI

An animated CLI drives everything:

```
chatsherpa                      banner + command overview
chatsherpa doctor               environment diagnostics
chatsherpa login <provider>     open a browser, log in once, save the session
chatsherpa ask <provider> "…"   send a prompt, print the reply
chatsherpa test [all]           smoke-test stored sessions
chatsherpa sessions             list stored sessions
chatsherpa setup                configure the login-server domain
chatsherpa serve                run the live-view login server
```

## Use it as a library

```python
import asyncio
from chatsherpa import ChatSherpa, FileSessionStore, WebhookSink

async def main():
    bridge = ChatSherpa(store=FileSessionStore("sessions", encryption_key=KEY))
    bridge.add_sink(WebhookSink("https://host/ai-errors", secret=SECRET))  # errors POSTed out
    print(await bridge.ask("chatgpt", user_id=42, prompt="Summarize CRDTs"))

asyncio.run(main())
```

`ask()` loads the user's stored session, reuses a warm browser context from the
pool, and returns the reply. On an expired session it raises `LoginRequiredError`
**and** emits an `ERROR` event so your app can tell the user to re-login.

## Live-view login server (for bots / servers)

The user can't see a browser on your VPS, so the server hands them a link whose
page **is** a server-side browser, streamed live. Good for **password / 2FA**
logins. Note: `Continue with Google` is **blocked** in this mode — Google refuses
to sign in inside any automation-controlled browser. For Google-gated providers
(Gemini, or ChatGPT-via-Google) use **connect mode** below.

```bash
chatsherpa setup      # asks for your domain, writes .env, prints reverse-proxy help
chatsherpa serve      # runs the server
```

Your existing bot starts a login by POSTing to it:

```bash
curl -X POST https://login.yourdomain.com/api/login \
  -H "X-ChatSherpa-Secret: $API_SECRET" \
  -d '{"provider":"chatgpt","user_id":42}'
# → {"url":"https://login.yourdomain.com/login/<token>", ...}  → send url to the user
```

See [DEPLOY.md](docs/DEPLOY.md) for Caddy / Cloudflare Tunnel / nginx setup and
why you need a domain (not a bare IP).

## Google / OAuth logins (connect mode)

Since automated browsers can't complete a Google login, the user logs in with
**their own** browser and we collect the resulting session. Start a login with
`"mode":"connect"` and the server returns a `/connect/<token>` link instead:

```bash
curl -X POST https://login.yourdomain.com/api/login \
  -d '{"provider":"gemini","user_id":42,"mode":"connect"}'
# → {"url":"https://login.yourdomain.com/connect/<token>", ...}
```

The user opens that link and completes it one of two ways — both send the session
to `/api/ingest`:

- **Browser extension** ([extension/](extension/)) — one-time install; they log in
  normally, click **Capture session**. Best for non-technical users.
- **CLI grab** — `chatsherpa grab <provider> --cdp http://localhost:9222 --token <t> --server <url>`
  attaches to their own Chrome and uploads the session.

For your own local testing, `grab` can also store directly (no server):

```bash
# start your real Chrome with a debug port, log in, then:
chatsherpa grab chatgpt --cdp http://localhost:9222
chatsherpa ask chatgpt "hello"
```

Or the one-liner import from a Cookie-Editor export:

```bash
chatsherpa import gemini ~/Downloads/gemini-cookies.json
```

## Telegram bot

The library never owns a `Bot` — you mount its handlers onto your **existing**
bot. With [python-telegram-bot](https://python-telegram-bot.org) (`pip install
'python-telegram-bot>=21'`, or the `[telegram]` extra):

```python
from telegram.ext import Application
from chatsherpa import ChatSherpa, FileSessionStore
from chatsherpa.integrations import TelegramBridge

bridge = ChatSherpa(store=FileSessionStore("sessions"))
tg = TelegramBridge(bridge,
                    login_server_url="https://login.yourdomain.com",
                    api_secret="...")          # for /ai_login links

application = Application.builder().token(TOKEN).build()
tg.register(application)                        # adds handlers under the "ai" prefix
application.run_polling()
```

Your users then get, alongside your bot's own commands:

- `/ai <message>` or any plain message → the assistant's answer
- `/ai_use <provider>` → pick their provider (`chatgpt`, `grok`, `gemini`, `deepseek`)
- `/ai_status` → which providers they're logged in to
- `/ai_login <provider>` → a live-view login link (from the login server above)

Change the namespace with `TelegramBridge(bridge, command_prefix="assist")`.
An aiogram v3 adapter is available too: `tg.register_aiogram(dispatcher)`.

**Escape hatch** — the framework-agnostic core is just one coroutine, so you can
wire it into any framework (or test it) without the adapters:

```python
reply: str = await tg.handle(user_id, "what's the capital of France?")
```

## Configure

Copy the env template and fill in what you have:

```bash
cp .env.example .env   # (a pre-filled .env may already exist)
```

`.env` is git-ignored. You only strictly need the `*_SESSION` paths (created by
login). `*_EMAIL` / `*_PASSWORD` are optional and only used by the experimental
auto-login.

## 1. Log in (session catcher)

Interactive menu — pick a provider (or `all`); a real browser window opens, you
log in (CAPTCHA/2FA just work), and the session is saved:

```bash
chatsherpa login <provider>
```

Or target one provider directly:

```bash
chatsherpa login chatgpt                 # manual, or auto if creds are in .env
chatsherpa login chatgpt             # same via the CLI
chatsherpa login chatgpt --auto      # force email/password auto-login from .env
```

## 2. Test / send

```bash
chatsherpa test                  # smoke-test every provider
chatsherpa test all         # same, assuming sessions already exist
chatsherpa ask chatgpt "Explain CRDTs in one paragraph"
```

`ask`/`test` read the session path from `.env`, so no `--session` needed.

Or from Python:

```python
import asyncio
from chatsherpa import ChatGPTProvider, Session

async def main():
    session = Session.from_storage_state("sessions/chatgpt.json")
    async with ChatGPTProvider(session) as bot:
        print(await bot.send("Hello!"))
        print(await bot.send("And a follow-up in the same chat?"))

asyncio.run(main())
```

Ask several providers in parallel:

```bash
python examples/parallel_ask.py "What is a vector database?"
```

## Supplying auth

Both styles are supported (and can be combined):

```python
from chatsherpa import Session

# A) Playwright storageState file (recommended — captured via `chatsherpa login`)
Session.from_storage_state("sessions/grok.json")

# B) Raw cookie header exported from a browser extension
Session.from_cookie_string("sessionKey=...; other=...", "grok.com")

# C) A cookie array (e.g. EditThisCookie / Cookie-Editor JSON export)
Session.from_cookies([{ "name": "...", "value": "...", "domain": "grok.com" }])
```

Anything you pass to a provider is coerced automatically: a `.json` path, a
`Session`, a storageState dict, or a cookie list all work:
`ChatGPTProvider("sessions/chatgpt.json")`.

## Auto-login & session catcher

* **Session catcher** — implemented. `Provider.capture_session(out_path)` (and
  `chatsherpa login`) open a headful window, poll until you're authenticated, then
  save the session. `bot.save_session(path)` re-exports the live session so you
  can refresh it before cookies expire.
* **Auto-login** (email/password) — implemented but **experimental**. Put
  `<PROVIDER>_EMAIL` / `<PROVIDER>_PASSWORD` in `.env` and run
  `chatsherpa login <provider> --auto`. It
  fills the email → password → submit flow, then **leaves the window open** so
  you can finish any CAPTCHA/2FA by hand. Expect it to fail on Google (Gemini)
  and OAuth-only Grok sign-in — those really do need the headful capture above.
  Auto-login runs only when *you* invoke it; it is never triggered on its own.

## When the UI changes (fixing selectors)

Response/selector breakage is the main maintenance cost. Override any selector
per call without editing the library:

```python
ChatGPTProvider(
    session,
    selectors={
        "input_selector": "#prompt-textarea",
        "assistant_message_selector": '[data-message-author-role="assistant"]',
        "send_button_selector": 'button[data-testid="send-button"]',
    },
)
```

Tune timing/visibility with `ProviderConfig`:

```python
from chatsherpa import ProviderConfig
cfg = ProviderConfig(headless=False, response_timeout_ms=240_000, stability_window_ms=2_500)
```

## Architecture

```
chatsherpa/
  session.py          # Session: storageState files, cookie strings, cookie arrays
  base.py             # BaseProvider: browser lifecycle, send(), stable-response wait
  providers/
    chatgpt.py        # just selectors + modal handling
    deepseek.py
    grok.py
    gemini.py
  cli.py              # `chatsherpa login` / `chatsherpa ask`
```

`send()` records how many assistant blocks exist, submits the prompt, waits for
a new block, then polls its text until it stops changing (streaming-complete
heuristic) or the timeout hits.

## How responses are detected

Each provider declares `assistant_message_selector` (matching every assistant
turn). The newest match is polled; when its text is non-empty and unchanged for
`stability_window_ms` — and no streaming indicator is visible — it's returned.
This avoids guessing at per-token streaming internals.

## Tests

Session parsing is unit-tested without a browser:

```bash
pip install -e ".[dev]"
pytest
```

## License

MIT
