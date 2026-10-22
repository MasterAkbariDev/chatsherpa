# ChatSherpa Guide

ChatSherpa drives the web chat UIs of AI providers (ChatGPT, Grok, Gemini,
DeepSeek) with a headless browser. It reuses a login you already have, sends
a prompt, and returns the reply. You can use it three ways:

1. **Command line** — `chatsherpa ask ...` from a terminal.
2. **Python library** — `ChatSherpa.ask(...)` inside your own code.
3. **Telegram bot** — mount handlers on a bot you already run, or use the
   framework-agnostic `TelegramBridge.handle(...)`.

This guide covers each of them, every option, and the login server that
hands out sign-in links. The examples use placeholder names (`user_id=42`,
`"Hello"`), so swap in your own values.

**Contents**

- [Concepts](#concepts)
- [Install](#install)
- [Command line reference](#command-line-reference)
  - [Overview of commands](#overview-of-commands)
  - [providers](#providers) · [doctor](#doctor) · [setup](#setup)
  - [login](#login) · [grab](#grab) · [import](#import) · [link](#link)
  - [ask](#ask) · [models](#models) · [test](#test)
  - [sessions](#sessions) · [chats](#chats) · [remove](#remove)
  - [serve](#serve)
- [Sessions: getting a login into ChatSherpa](#sessions-getting-a-login-into-chatsherpa)
- [Chats: continuing a conversation](#chats-continuing-a-conversation)
- [Python library](#python-library)
  - [Minimal use](#minimal-use)
  - [The ChatSherpa object](#the-chatsherpa-object)
  - [Stores](#stores)
  - [Sessions in code](#sessions-in-code)
  - [Provider configuration](#provider-configuration)
  - [Running several asks at once](#running-several-asks-at-once)
- [Events, errors and webhooks](#events-errors-and-webhooks)
  - [Webhooks](#webhooks)
- [Telegram bot](#telegram-bot)
  - [Commands the bot answers](#commands-the-bot-answers)
  - [Mount on python-telegram-bot](#mount-on-python-telegram-bot)
  - [Mount on aiogram](#mount-on-aiogram)
  - [Use it with any framework](#use-it-with-any-framework)
  - [Notes](#notes)
- [Login server](#login-server)
  - [Sign-in modes](#sign-in-modes)
  - [HTTP endpoints](#http-endpoints)
  - [Reverse proxy](#reverse-proxy)
- [Configuration (.env)](#configuration-env)
- [Deploying to a server](#deploying-to-a-server)
  - [Install a fresh server](#install-a-fresh-server)
  - [Update an existing install](#update-an-existing-install)
  - [Ship from your machine](#ship-from-your-machine)
  - [Uninstall](#uninstall)
  - [Service logs](#service-logs)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)

---

## Concepts

| Term | Meaning |
|---|---|
| **Provider** | One chat service: `chatgpt`, `grok`, `gemini`, `deepseek`. |
| **Session** | A stored login (cookies plus local storage) for one provider. It is saved per *account label* and provider. |
| **Account label** | The `--user` value on the CLI, or the `user_id` in the library. It picks which stored session to use. The default label is `local`. |
| **Chat** | One conversation on the provider's site. Each saved chat has a name (`default` unless you choose another) and a URL. |
| **Store** | Where sessions and chat URLs are kept: files on disk (`FileSessionStore`) or memory (`MemorySessionStore`). |

The provider keeps the conversation. ChatSherpa only stores the chat's URL,
so the next message opens that same chat and the provider remembers the
history.

---

## Install

Requires Python 3.9 or newer.

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[server,crypto,telegram]"   # extras are optional
.venv/bin/playwright install chromium                  # needed for ask, test, login
```

Extras:

| Extra | Adds | When you need it |
|---|---|---|
| `server` | the login server (aiohttp) | running `chatsherpa serve` |
| `crypto` | encryption at rest (cryptography) | setting `ENCRYPTION_KEY` |
| `telegram` | python-telegram-bot | mounting handlers with `register(application)` |

Check the environment at any time:

```bash
chatsherpa doctor
```

---

## Command line reference

Run `chatsherpa` with no arguments for the overview. Every command accepts
`--help`.

### Overview of commands

| Command | Purpose |
|---|---|
| `providers` | list the supported providers |
| `doctor` | check Python, the browser, and the .env file |
| `setup` | write `.env` and print the reverse-proxy snippet |
| `login` | open a browser, sign in once, save the session |
| `grab` | copy a session from your own Chrome or Edge (the Google-safe path) |
| `import` | save a session from a cookie export |
| `link` | create a sign-in link through the login server |
| `ask` | send one prompt and print the reply |
| `models` | list the model names the signed-in account can choose |
| `test` | send a smoke-test prompt to stored sessions |
| `sessions` | list stored logins, their named chats, and their age |
| `chats` | list the named chats for one account and provider |
| `remove` | delete a stored login |
| `serve` | run the login server |

### `providers`

```bash
chatsherpa providers
```

### `doctor`

```bash
chatsherpa doctor
```

Reports the Python version, whether Playwright and Chromium are installed,
which optional extras are present, whether `PUBLIC_BASE_URL` is set, and how
many sessions are saved.

### `setup`

```bash
chatsherpa setup
```

Asks for the public base URL of the login server, writes `.env`, and prints
the nginx snippet for the reverse proxy.

### `login`

Opens a browser window so you can sign in yourself.

```bash
chatsherpa login chatgpt
chatsherpa login grok --user work
chatsherpa login chatgpt --auto --headless --timeout 600
```

| Option | Default | Meaning |
|---|---|---|
| `--user LABEL` | `local` | account label to store the session under |
| `--auto` | off | try to fill the email and password from `.env` (`<PROVIDER>_EMAIL`, `<PROVIDER>_PASSWORD`) |
| `--headless` | off | run without a visible window (only works for logins that need no interaction) |
| `--timeout SECONDS` | `300` | how long to wait for you to finish signing in |

Google and other OAuth sign-ins are often blocked in automated browsers.
Use `grab` for those.

### `grab`

Takes the session from a Chrome or Edge you started yourself, so Google does
not flag the sign-in.

Start the browser with remote debugging first. Quit it fully before starting
it this way:

```bash
msedge --remote-debugging-port=9222 --user-data-dir="$HOME/chatsherpa-profile"
```

Then grab the session:

```bash
chatsherpa grab gemini --cdp http://localhost:9222
chatsherpa grab gemini --cdp http://localhost:9222 --user work
```

| Option | Meaning |
|---|---|
| `--cdp URL` | address of the running browser, for example `http://localhost:9222` |
| `--profile DIR` | use a persistent profile folder instead of `--cdp` |
| `--user LABEL` | account label to store under (default `local`) |
| `--token TOKEN` | upload to a login server instead of saving locally (used by the sign-in link) |
| `--server URL` | the login server base URL, required with `--token` |
| `--timeout SECONDS` | default `300` |

### `import`

Saves a session from a cookie export, a Playwright storage-state file, or a
raw cookie header.

```bash
chatsherpa import chatgpt cookies.json
chatsherpa import chatgpt state.json --user work
cat cookies.json | chatsherpa import chatgpt -
chatsherpa import grok "name1=value1; name2=value2" --cookie-header --domain grok.com
```

| Option | Meaning |
|---|---|
| `source` | a file path, `-` for stdin, or a raw string with `--cookie-header` |
| `--cookie-header` | treat `source` as a `k=v; k2=v2` string |
| `--domain DOMAIN` | cookie domain for `--cookie-header` (default: the provider's home) |
| `--user LABEL` | account label (default `local`) |

Importing one session per user is the usual pattern for a multi-user
service. Each user gets their own label, so their logins and chats stay
separate.

### `link`

Creates a sign-in link on a running login server. It uses `API_SECRET` from
`.env`.

```bash
chatsherpa link chatgpt --user 42
chatsherpa link chatgpt --user 42 --live
```

| Option | Meaning |
|---|---|
| `--user LABEL` | the account the link will store the session under |
| `--live` | use a server-side browser instead of the user's own browser |

### `ask`

Sends one prompt and prints the reply in a panel. The panel shows the model
name when the provider exposes it, and the elapsed time.

```bash
chatsherpa ask chatgpt "Explain recursion in one paragraph"
chatsherpa ask grok "Summarize the plot of a classic novel" --user work
chatsherpa ask gemini "Hello" --account 1
chatsherpa ask chatgpt "Draft a reply" --model "Thinking" --model-order "Thinking,Fast"
chatsherpa ask chatgpt "Start over" --chat support --new
```

| Option | Default | Meaning |
|---|---|---|
| `--user LABEL` | `local` | which stored login to use |
| `--chat NAME` | `default` | which named chat to continue. A new name starts a new chat and saves it under that name. See [Chats](#chats-continuing-a-conversation). |
| `--new` | off | reset the named chat so the next message starts a fresh conversation |
| `--account N` | — | Gemini only: pick the Google account slot. `0` is the first signed-in account. |
| `--model NAME` | — | preferred model, matched against the names in the provider's picker |
| `--model-order A,B,C` | — | fallback order if the first model is not available |
| `--timeout SECONDS` | `180` | how long to wait for the reply |
| `--headful` | off | show the browser window (needed for some providers, for example ChatGPT, when headless is blocked) |

### `models`

Reads the model names from the signed-in account. It does not send a prompt.

```bash
chatsherpa models
chatsherpa models chatgpt --user work
chatsherpa models gemini --account 1
```

| Option | Meaning |
|---|---|
| `provider` | optional, one provider (default: all) |
| `--account N` | Gemini account slot |
| `--headful` | show the browser |

Model menus depend on the subscription. A free account may show fewer models
than a paid one, and some providers show no picker at all.

### `test`

Sends a short prompt to each stored session and reports pass or fail.

```bash
chatsherpa test
chatsherpa test chatgpt --prompt "Reply with the word ok"
```

| Option | Default | Meaning |
|---|---|---|
| `provider` | `all` | one provider or all |
| `--prompt TEXT` | a short smoke prompt | the prompt to send. Use different prompts when you test often. |
| `--headful` | off | show the browser |

### `sessions`

Lists every stored login, with its account label, the number of named chats,
how old the session file is, and whether it is encrypted.

```bash
chatsherpa sessions
```

### `chats`

Lists the named chats saved for one account and provider, with their URLs.

```bash
chatsherpa chats chatgpt
chatsherpa chats chatgpt --user 42
```

| Option | Default | Meaning |
|---|---|---|
| `provider` | — | the provider (required) |
| `--user LABEL` | `local` | the account label whose chats to list |

### `remove`

Deletes a stored login. Saved chat URLs for that account are removed too.

```bash
chatsherpa remove chatgpt --user work
chatsherpa remove chatgpt --all
```

| Option | Meaning |
|---|---|
| `--user LABEL` | account to remove (default `local`) |
| `--all` | remove every account for this provider |

### `serve`

Runs the login server. See [Login server](#login-server).

```bash
chatsherpa serve
```

---

## Sessions: getting a login into ChatSherpa

There are four ways, from the most reliable for Google sign-ins to the least:

1. **`grab`** from your own browser (described above).
2. **`import`** from a cookie export made by a browser extension. Export the
   cookies for the provider's domain, then pass the file to `import`.
3. **`login`** in a window ChatSherpa opens. This works for password logins.
4. **Sign-in link** from the login server (`link` on the CLI, or the bot's
   `/login` command). The user signs in on their own page, and the session
   is sent back to the server.

A session stays valid until the provider signs the account out. When that
happens, `ask` raises `LoginRequiredError` (or the bot replies that the
session expired), and you repeat the sign-in.

---

## Chats: continuing a conversation

Each account and provider has one or more **named chats**. The default name is
`default`.

- `ask` **continues** the named chat when it exists.
- `ask` **starts** a chat when the name is new, and saves its URL under that name.
- `--new` **resets** the named chat.

Example for one user with two separate conversations:

```bash
chatsherpa ask chatgpt "Plan my week" --user 42 --chat planning
chatsherpa ask chatgpt "Draft an email" --user 42 --chat writing
chatsherpa ask chatgpt "And add Friday" --user 42 --chat planning   # continues planning
chatsherpa chats chatgpt --user 42                                  # lists both
```

In the library the same option is `chat=`:

```python
await bridge.ask("chatgpt", user_id=42, prompt="Plan my week", chat="planning")
await bridge.ask("chatgpt", user_id=42, prompt="And add Friday", chat="planning")
chats = await bridge.list_chats("chatgpt", user_id=42)   # {"planning": "https://..."}
```

Because the stored chat URL belongs to the account's session, a user's chats
do not mix with another user's, even on the same provider.

A user has one open browser page per provider. When you ask for a different
named chat, that page is reopened on the chat you asked for, so the reply
lands in the right conversation. Messages for the same user are sent one at
a time.

---

## Python library

ChatSherpa is an asyncio library. Every network call is `async`.

### Minimal use

```python
import asyncio
from chatsherpa import ChatSherpa, FileSessionStore

async def main():
    bridge = ChatSherpa(store=FileSessionStore("sessions"))
    print(await bridge.ask("chatgpt", user_id=42, prompt="Hello"))
    await bridge.aclose()

asyncio.run(main())
```

### The `ChatSherpa` object

`ChatSherpa(store=None, bus=None, pool=None, headless=True, config=None)`

| Argument | Default | Meaning |
|---|---|---|
| `store` | `MemorySessionStore()` | where sessions and chats are kept. Use `FileSessionStore` in real use. |
| `bus` | a new `EventBus` | event bus for logs and webhooks |
| `pool` | a new `BrowserPool` | keeps browser contexts warm. Usually leave this unset. |
| `headless` | `True` | run the browser without a window |
| `config` | `ProviderConfig(headless=...)` | timing and browser settings |

Methods:

| Method | What it does |
|---|---|
| `await ask(provider, user_id, prompt, ...)` | send one prompt, return the reply text |
| `await list_models(provider, user_id=...)` | read the model names for the account |
| `await has_session(provider, user_id=...)` | `True` when a non-empty session is stored |
| `await list_chats(provider, user_id=...)` | `{chat name: url}` for the account |
| `await login_local(provider, user_id=..., headless=False)` | open a browser, sign in, save the session |
| `on_event(callback)`, `on_error(callback)`, `add_sink(sink)` | subscribe to events |
| `await aclose()` | close browsers. Call it once when your app stops. |

`ask` arguments:

| Argument | Meaning |
|---|---|
| `provider` | `"chatgpt"`, `"grok"`, `"gemini"`, or `"deepseek"` |
| `user_id` | str or int. Your end-user identifier, for example a Telegram user id. |
| `prompt` | the text to send |
| `model` | preferred model name, matched against the picker |
| `model_order` | list of fallback model names |
| `new_chat` | `True` to reset the named chat before sending |
| `chat` | the named chat to use (default `"default"`) |
| `account` | Gemini account slot |
| `on_model` | callback that receives the model name once it is known |

`on_model` lets you show progress such as "thinking with model X":

```python
async def show(model):
    print(f"thinking with {model}")

reply = await bridge.ask("gemini", user_id=42, prompt="Hi", on_model=show)
```

### Stores

```python
from chatsherpa import FileSessionStore, MemorySessionStore

store = FileSessionStore("sessions")                          # plaintext
store = FileSessionStore("sessions", encryption_key=KEY)      # encrypted at rest
store = MemorySessionStore()                                  # for tests
```

`encryption_key` is a Fernet key. Generate one with
`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
Keep it outside the repository.

To use another backend, subclass `SessionStore` and implement `get`, `put`,
`delete`, and `list`. For chats, also implement `get_thread`, `put_thread`,
and `list_threads`. The base class returns empty results for chats, so a
store that skips them still works, but then chats are not remembered.

### Sessions in code

```python
from chatsherpa import Session

session = Session.from_json_file("cookies.json")       # Cookie-Editor or storage-state file
session = Session.from_cookie_string("a=1; b=2", "grok.com")
```

### Provider configuration

`ProviderConfig` holds the timing and browser settings. The most useful fields:

| Field | Default | Meaning |
|---|---|---|
| `headless` | `True` | run without a window |
| `response_timeout_ms` | `180000` | give up waiting for a reply after this long |
| `login_wait_ms` | `8000` | how long to wait for a sign-in page to settle |
| `nav_timeout_ms` | `60000` | page navigation timeout |
| `poll_interval_ms` | `120` | how often the reply is checked |
| `stability_window_ms` | `900` | fallback: how long text must stay unchanged, for providers with no done-signal |
| `proxy` | `None` | `{"server": "http://host:port"}` to route the browser through a proxy |

```python
from chatsherpa import ChatSherpa, ProviderConfig

bridge = ChatSherpa(store=store, headless=True,
                    config=ProviderConfig(headless=True, response_timeout_ms=240_000))
```

### Running several asks at once

Different users use separate browser contexts, so their asks can run in
parallel. Calls for the same user are queued behind each other so one chat
never receives two prompts at the same time.

```python
import asyncio

async def main():
    bridge = ChatSherpa(store=FileSessionStore("sessions"))
    replies = await asyncio.gather(
        bridge.ask("chatgpt", user_id=1, prompt="Name one color"),
        bridge.ask("chatgpt", user_id=2, prompt="Name one fruit"),
    )
    print(replies)
    await bridge.aclose()
```

---

## Events, errors and webhooks

Every stage emits an `Event`. You subscribe to the bus.

```python
from chatsherpa import EventType

def log_event(event):
    print(event.type, event.provider, event.user_id, event.message)

bridge.on_event(log_event)                     # every event
bridge.on_error(lambda e: print("error:", e.message))   # errors only
```

Event types (`EventType`):

| Type | When |
|---|---|
| `ask_started` | a prompt was sent |
| `ask_succeeded` | a reply was returned |
| `error` | something failed (see `ErrorType` below) |
| `login_started`, `login_succeeded`, `login_failed`, `login_timeout` | sign-in steps |

Error kinds (`ErrorType`):

| Kind | Meaning | What to do |
|---|---|---|
| `LoginRequired` | the stored session is missing or expired | sign in again (`login`, `grab`, or a sign-in link) |
| `ResponseTimeout` | no reply within `response_timeout_ms` | raise the timeout, or retry |
| `RateLimited` | the provider limited the account | wait, or use another account |
| `SelectorMiss` | the provider's page changed | update the provider module |
| `Unknown` | anything else | read the message |

Exceptions raised by `ask`: `LoginRequiredError`, `ResponseTimeoutError`,
`RateLimitedError`, `ProviderError`, `SessionError`, and `BridgeError` (the
base class). Catch them in your code:

```python
from chatsherpa import LoginRequiredError, ResponseTimeoutError

try:
    reply = await bridge.ask("chatgpt", user_id=42, prompt="Hello")
except LoginRequiredError:
    reply = "Please sign in again."
except ResponseTimeoutError:
    reply = "The provider took too long. Try again."
```

### Webhooks

`WebhookSink` posts events to your URL as JSON. It retries, and it can sign
the body with HMAC:

```python
from chatsherpa import WebhookSink

bridge.add_sink(WebhookSink("https://example.com/hooks/chatsherpa",
                            secret="a-long-random-string"))
```

By default only `error` events are sent. Pass `events=None` to send all of
them.

---

## Telegram bot

The library never owns your bot. You mount ChatSherpa's handlers on the bot
you already run, and the rest of your bot keeps working.

### Commands the bot answers

With the default prefix `ai`:

| Message | Effect |
|---|---|
| `/ai <text>` | send `<text>` to the user's current provider |
| any plain text | the same as `/ai <text>` |
| `/ai_use <provider>` | set this user's default provider (`chatgpt`, `grok`, `gemini`, `deepseek`) |
| `/ai_login <provider>` | send a sign-in link for that provider |
| `/ai_status` | show which providers have a session for this user |
| `/ai_chat` | list this user's saved chats, marking the current one with ▸ |
| `/ai_chat <name>` | switch to a named chat. A new name starts a new chat on the next message. |

The `/use` choice is kept in memory, so it resets when the process restarts.

### Mount on python-telegram-bot

```python
from telegram.ext import ApplicationBuilder
from chatsherpa import ChatSherpa, FileSessionStore
from chatsherpa.integrations import TelegramBridge

bridge = ChatSherpa(store=FileSessionStore("sessions"))
tg = TelegramBridge(
    bridge,
    command_prefix="ai",                          # command names: /ai, /ai_login, ...
    login_server_url="https://login.example.com", # where sign-in links come from
    api_secret="shared-secret",                   # must match API_SECRET on the server
    default_provider="chatgpt",
    link_mode="live",                             # "live" or "connect" (see Login server)
    connect_providers=("gemini",),                # providers that must use "connect"
)

application = ApplicationBuilder().token("123:your-bot-token").build()
tg.register(application)
application.run_polling()
```

`register` needs `python-telegram-bot` 21 or newer.

### Mount on aiogram

```python
from aiogram import Dispatcher

dp = Dispatcher()
tg.register_aiogram(dp)     # aiogram v3 Dispatcher or Router
```

### Use it with any framework

`handle` takes a user id and the text and returns the reply string. Use it
from a webhook, a Discord bot, or a custom server:

```python
reply = await tg.handle(user_id=42, text="/ai Hello")
```

A message that starts with an unknown `/command` returns an empty string, so
your own handlers can deal with it.

### Notes

- `command_prefix` puts every command under one prefix, so ChatSherpa does not
  clash with your own handlers.
- Each Telegram user has their own sessions, chats, and default provider.
- The current chat is kept in memory, so it resets to `default` when the process
  restarts. The chats themselves are saved and survive restarts. To pick the chat
  from your own code instead, call `bridge.ask(..., chat="name")` from your own
  handler instead of `tg.handle`.

---

## Login server

The login server gives users a sign-in link and stores the session it
receives. It runs with `chatsherpa serve` and needs a public HTTPS domain
(certificates are not issued for bare IP addresses).

```bash
chatsherpa setup     # writes .env and prints the reverse-proxy snippet
chatsherpa serve     # listens on SERVER_HOST:SERVER_PORT
```

### Sign-in modes

| Mode | Who runs the browser | Use it for |
|---|---|---|
| `live` | the server, streamed to the user's page | password logins and 2FA |
| `connect` | the user's own browser | Google and other OAuth sign-ins |

With `connect`, the user signs in in their own browser, then runs the grab
command the page shows (or uses the extension). The session is sent to the
server's `/api/ingest` endpoint.

### HTTP endpoints

| Method and path | Purpose |
|---|---|
| `GET /health` | `{"ok": true, "active_logins": N}` |
| `POST /api/login` | create a sign-in link. Body: `{"provider", "user_id", "mode"}`. Header `X-ChatSherpa-Secret` if `API_SECRET` is set. |
| `GET /connect/{token}` | the page a user opens to sign in with their own browser |
| `POST /api/ingest` | receive a session from the user's browser (`token`, `provider`, `storage_state` or `cookies`) |
| `GET /login/{token}` | the live-view page |
| `GET /ws/{token}` | the live-view stream |

Example request from your own service:

```bash
curl -X POST https://login.example.com/api/login \
  -H "Content-Type: application/json" \
  -H "X-ChatSherpa-Secret: shared-secret" \
  -d '{"provider": "chatgpt", "user_id": "42", "mode": "live"}'
```

The response contains the `url` to send to the user. The link expires after a
while.

### Reverse proxy

Put the proxy block from `deploy/nginx-snippet.conf` above your catch-all
`location /`, then reload nginx:

```bash
nginx -t && systemctl reload nginx
```

---

## Configuration (.env)

ChatSherpa reads `.env` from its home folder. Set `CHATSHERPA_HOME` to point
somewhere else. Values in the real environment win over the file.

| Key | Used by | Meaning |
|---|---|---|
| `PUBLIC_BASE_URL` | server | public URL of the login server, for example `https://login.example.com` |
| `SERVER_HOST` | server | address to listen on, usually `127.0.0.1` behind a proxy |
| `SERVER_PORT` | server | port to listen on |
| `API_SECRET` | server and bots | shared secret for `POST /api/login` |
| `ENCRYPTION_KEY` | stores | Fernet key for encrypting sessions at rest |
| `WEBHOOK_URL`, `WEBHOOK_SECRET` | events | where to send events, and the HMAC key |
| `<PROVIDER>_EMAIL`, `<PROVIDER>_PASSWORD` | `login --auto` | credentials for automatic sign-in. Keep them out of version control. |
| `<PROVIDER>_SESSION` | resolver | path to a session file for that provider |

Never commit `.env`, `sessions/`, or any cookie export.

---

## Deploying to a server

The deploy scripts live in `deploy/`. They ship committed code only, so
uncommitted work does not reach the server.

### Install a fresh server

Run as root on a Linux host:

```bash
bash deploy/install.sh
```

It asks for the public URL, the project name (used in the URL path and the
nginx location), and which nginx site to edit (existing or new). It can
also run without a domain, serving on the server's IP.

| Variable | Meaning |
|---|---|
| `PUBLIC_BASE_URL` | full public URL, including the path, for example `https://example.com/project-name` |
| `MOUNT` | project name = URL path = nginx location |
| `NGINX_DOMAIN` | public domain, when `PUBLIC_BASE_URL` is not set. Leave empty to serve on the IP. |
| `NGINX_SITE` | nginx site file to edit (skips the picker) |
| `INSTALL_DIR` | install location (default `/opt/chatsherpa`) |
| `SERVER_PORT` | port (install picks the first free one from `8091`; 8080 is often taken by nginx) |
| `REPO_URL` | git repository to install from |
| `WITH_CHROMIUM=1` | also install Chromium (needed for live view and server-side ask) |

The script asks for anything missing. Environment variables are optional.

### Update an existing install

```bash
bash deploy/update.sh
```

Pulls the latest code, reinstalls the package, restarts the service, and runs a
health check. Your `.env` and sessions are kept.

### Ship from your machine

```bash
SERVER=root@203.0.113.10 SSH_PORT=22 ./deploy/deploy.sh [options]
```

| Option | Meaning |
|---|---|
| `--chromium` | install Chromium, its OS dependencies, and Xvfb |
| `--nginx` | add the proxy block to `NGINX_SITE` and reload nginx. Needs `NGINX_SITE` and `NGINX_DOMAIN`. |
| `--remote-dir DIR` | install location on the server (default `/opt/chatsherpa`) |

If your SSH access goes through a proxy, set `SSH_PROXY`, for example
`SSH_PROXY='nc -X 5 -x 127.0.0.1:1080 %h %p'`.

### Uninstall

```bash
bash deploy/uninstall.sh
```

### Service logs

```bash
journalctl -u chatsherpa -n 50 --no-pager
```

---

## Testing

```bash
make test        # unit tests, no browser and no network
make check       # tests plus a compile check
```

Live checks against real providers need a stored session:

```bash
chatsherpa test
chatsherpa test chatgpt --prompt "Reply with the word ok"
```

Use a different prompt each time, or keep a reply you already checked and
show that instead of asking the provider again. Repeating an identical
question on a paid account is wasteful, and some providers rate-limit it.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Chromium not found` | `playwright install chromium` (or `make browsers`) |
| `no session for ...` | create one: `login`, `grab`, `import`, or a sign-in link |
| Google says the browser is not secure | use `grab` with your own Chrome or Edge, not `login` |
| ChatGPT says not authenticated but the session is fresh | run with `--headful`; headless browsers are often blocked |
| `grab` cannot reach port 9222 | start the browser with `--remote-debugging-port=9222`, and quit it fully first |
| the reply is cut short or empty | raise `--timeout` or `response_timeout_ms` |
| `Selector` or "no new message" errors | the provider changed its page. Update its module, or retry later. |
| server returns 502 on the project path | `systemctl restart chatsherpa` |
| sessions are not found after a move | check `CHATSHERPA_HOME` and the `sessions/` folder it points to |
