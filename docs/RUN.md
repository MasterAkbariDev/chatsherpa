# Run guide

Everything you need to run ChatSherpa yourself, from a clean checkout to the
live login server. Commands assume you are in the project folder:

```bash
cd ~/Developer/ai-browser-bridge
```

Run `make help` to list every target.

## 1. Set up on your Mac

```bash
make setup      # creates .venv, installs the package with all extras, creates .env
make browsers   # downloads Chromium (needed for ask, test and login)
make check      # runs the 36 unit tests
make doctor     # shows what is ready and what is missing
```

## 2. Get a real session without automated Google login

Google blocks sign-in inside automated browsers, so the session is taken from a
browser you log into yourself. The profile below keeps your login between runs.

Quit Edge completely (Cmd+Q), then start it with a debug port:

```bash
"/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge" --remote-debugging-port=9222 --user-data-dir="$HOME/.aibridge/edge-grab"
```

In that window, open the provider and log in normally (Google and 2FA work).
Then grab the session and send a test prompt:

```bash
make grab PROVIDER=chatgpt CDP=http://localhost:9222
make ask PROVIDER=chatgpt PROMPT="Reply with exactly: ok"
```

The same works for `gemini`, `grok` and `deepseek`. Each provider was tested
live against a real account.

Other ways to get a session:

- `make login PROVIDER=chatgpt` opens a browser window for you to log in
  (works for password logins, not for Google).
- `chatsherpa import chatgpt ~/Downloads/cookies.json` imports a
  Cookie-Editor JSON export.

## 2b. Multiple accounts

There are two independent ways to hold more than one account.

**Separate sessions (works for every provider).** Each stored session has an
account label (`--user`, default `local`). Grab or import under different labels:

```bash
make grab PROVIDER=chatgpt CDP=http://localhost:9222        # label: local
chatsherpa grab chatgpt --cdp http://localhost:9222 --user work
chatsherpa import gemini ~/Downloads/personal.json --user personal
```

Ask with a specific account, and see or remove them:

```bash
chatsherpa ask chatgpt "hi" --user work
chatsherpa sessions                 # lists provider + account + age
chatsherpa remove chatgpt --user work
chatsherpa remove gemini --all      # every stored Gemini account
```

**Several Google accounts in one Gemini session (`--account`).** A single Gemini
login can hold multiple Google accounts, addressed by the `/u/N/` slot (0 is the
first signed-in account, 1 the next). List them and pick one per ask:

```bash
chatsherpa models gemini            # reads the signed-in account's models
chatsherpa ask gemini "hi" --account 1
```

The browser extension also detects the signed-in Google accounts when you
capture a Gemini session, so the right `/u/N/` slot is stored.

## 3. Use it from your own Python code

```python
import asyncio
from chatsherpa import ChatSherpa, FileSessionStore

async def main():
    bridge = ChatSherpa(store=FileSessionStore("sessions"))
    print(await bridge.ask("chatgpt", user_id="local", prompt="Hello"))
    await bridge.aclose()

asyncio.run(main())
```

See `examples/` for Telegram bots and parallel asks.

## 4. Test a Telegram bot locally

Put your token in `.env` (`BOT_TOKEN=...`), then run one of the bot examples:

```bash
.venv/bin/python examples/telegram_fresh_ptb.py
```

Your users then get `/ai <message>`, `/ai_login <provider>`, `/ai_use <provider>`
and `/ai_status`.

## 5. The live login server on your VPS

It is already running on the server (`95.182.94.71`, SSH port 2112) as the
`chatsherpa` systemd service, behind nginx at:

    https://bv.akbaridev.com/chatsherpa/

Check it from anywhere:

```bash
curl https://bv.akbaridev.com/chatsherpa/healthz
```

Read the API secret your bot must send (never commit it):

```bash
ssh -p 2112 -o "ProxyCommand=nc -X 5 -x 127.0.0.1:10808 %h %p" root@95.182.94.71 'grep ^API_SECRET /opt/chatsherpa/.env'
```

Create a connect-mode login link for a user, the same request your bot makes:

```bash
API_SECRET=<paste the value>
curl -X POST https://bv.akbaridev.com/chatsherpa/api/login \
  -H "Content-Type: application/json" \
  -H "X-ChatSherpa-Secret: $API_SECRET" \
  -d '{"provider":"gemini","user_id":"me","mode":"connect"}'
```

The JSON reply contains `url` (the user opens it) and `token`. The user opens
that link in their own browser, logs in, then captures the session with the
extension or with:

```bash
make grab PROVIDER=gemini CDP=http://localhost:9222 TOKEN=<token> SERVER=https://bv.akbaridev.com/chatsherpa
```

Live-view login (the server runs the browser and streams it) needs Chromium on
the server. It is not installed there yet, because the disk is tight. Connect
mode works without it.

## 6. Change the server

Commit first, then deploy only the committed code:

```bash
git add -A
~/commit.sh -m "Describe the change"
make deploy SERVER=root@95.182.94.71 SSH_PORT=2112 SSH_PROXY='nc -X 5 -x 127.0.0.1:10808 %h %p'
```

The deploy script also links `chatsherpa` onto the server PATH, restarts the
systemd service, and checks health. Extra flags:

```bash
# install Chromium + OS deps + Xvfb, trying several download mirrors
make deploy ARGS=--chromium         # or: ./deploy/deploy.sh --chromium
# add the /chatsherpa/ proxy block to an nginx site and reload (idempotent)
NGINX_SITE=/etc/nginx/sites-available/your-site NGINX_DOMAIN=bv.akbaridev.com \
  SERVER=root@95.182.94.71 SSH_PORT=2112 ./deploy/deploy.sh --nginx
```

`--chromium` is only needed for live-view login and server-side `ask`. This VPS
has ~1 GB RAM, so a server browser is tight; connect mode needs no browser.

Logs and status on the server:

```bash
ssh -p 2112 -o "ProxyCommand=nc -X 5 -x 127.0.0.1:10808 %h %p" root@95.182.94.71 'systemctl status chatsherpa --no-pager; journalctl -u chatsherpa -n 50 --no-pager'
```

The nginx block that serves `/chatsherpa/` is in `deploy/nginx-snippet.conf`.
The live config on the server is `/etc/nginx/sites-available/b.aliakbar-private-vps.shop`
(a backup from before the change sits next to it). After any nginx edit run
`sudo nginx -t && sudo systemctl reload nginx`.

## 7. Publish to GitHub

Create an empty repository named `chatsherpa` at https://github.com/new (do not
add a README or license there, the repo already has them). Then, in the project
folder:

```bash
git remote add origin git@github.com:<your-github-username>/chatsherpa.git
git push -u origin main
```

If `git push` asks for a password, GitHub SSH is not set up yet: run
`ssh -T git@github.com` to test. The `gh` command-line tool is optional, and
installing it on this Mac requires an Xcode Command Line Tools update first.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Chromium not found` | `make browsers` |
| `No session for ...` | `make grab` (or `make login`) for that provider |
| `session expired` in the bot | the user runs `/ai_login <provider>` again |
| Google says "This browser may not be secure" | use `make grab` with your own Edge, not `make login` |
| ChatGPT says "not authenticated" but the session is fresh | ChatGPT is blocked in headless Chromium (Cloudflare). Add `--headful` locally; the server runs headed via Xvfb |
| Reply prints a couple of seconds after it finished | expected settle window; it is short now. Gemini in headless is genuinely slow — prefer a headed run for Gemini |
| Grab can't connect to port 9222 | Edge was not started with `--remote-debugging-port=9222`; quit Edge fully first |
| Server returns 502 on `/chatsherpa/` | `systemctl status chatsherpa`; restart with `systemctl restart chatsherpa` |
