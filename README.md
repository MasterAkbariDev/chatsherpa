# ChatSherpa

Talk to **ChatGPT, Gemini, Grok and DeepSeek** from Python or a Telegram bot by
driving their websites with a real browser session — no API keys. You log in
once in a browser, ChatSherpa reuses that session headlessly to send prompts and
return replies.

> You are automating logged-in web sessions. That may violate a provider's terms
> of service, and these web UIs change often (selectors break). Use your own
> accounts and keep the request volume human. For personal use.

## Quick start

```bash
make setup        # venv + install
make browsers     # Chromium for Playwright
make check        # run the tests
```

Get a session from your own browser (Google logins are blocked in automated
browsers, so capture from a browser you control). Quit Edge, then:

```bash
"/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge" --remote-debugging-port=9222 --user-data-dir="$HOME/.aibridge/edge-grab"
```

Log in to the provider in that window, then:

```bash
chatsherpa grab chatgpt --cdp http://localhost:9222
chatsherpa ask chatgpt "Reply with exactly: ok"
```

## From Python

```python
import asyncio
from chatsherpa import ChatSherpa, FileSessionStore

async def main():
    bridge = ChatSherpa(store=FileSessionStore("sessions"))
    print(await bridge.ask("chatgpt", user_id="local", prompt="Hello"))
    await bridge.aclose()

asyncio.run(main())
```

`ask()` raises typed errors (`LoginRequiredError`, `RateLimitedError`,
`ResponseTimeoutError`) and emits events you can forward to a webhook.

## CLI

```
chatsherpa providers | doctor | sessions
chatsherpa grab <provider> --cdp <url> [--user <account>]
chatsherpa import <provider> <cookies.json> [--user <account>]
chatsherpa ask <provider> "<prompt>" [--user <account>] [--model <name>]
chatsherpa remove <provider> [--user <account> | --all]
chatsherpa serve | link | models
```

Multiple accounts per provider live under the `--user` label; `sessions` lists
them. Gemini's several Google logins in one session use `ask --account N`.

## More

- **Login server, Telegram bots, multiple accounts, deploying to a VPS** →
  [docs/USAGE.md](docs/USAGE.md)
- **Browser extension** (connect-mode capture) → [extension/](extension/)
- **Examples** → [examples/](examples/)

## License

MIT — see [LICENSE](LICENSE).
