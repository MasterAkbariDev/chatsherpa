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
| Grab can't connect to port 9222 | Edge was not started with `--remote-debugging-port=9222`; quit Edge fully first |
| Server returns 502 on `/chatsherpa/` | `systemctl status chatsherpa`; restart with `systemctl restart chatsherpa` |
