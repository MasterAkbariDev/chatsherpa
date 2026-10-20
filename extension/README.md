# ChatSherpa Connector (browser extension)

Captures your **signed-in** AI provider session from your real browser and sends
it to your ChatSherpa login server — so Google / 2FA logins work (nothing
is automated during login).

## How it fits

```
bot → POST /api/login {mode:"connect"} → link: https://your-server/connect/<token>
user opens link → logs in to the provider normally → clicks "Capture session"
  → this extension reads the provider cookies (chrome.cookies) and POSTs them to
    /api/ingest → server stores the session → done
```

The extension only acts when **you** click capture on a `/connect/<token>` page,
and only sends cookies to the server named in that page.

## Install (Chrome / Edge, unpacked)

1. Go to `chrome://extensions`, enable **Developer mode**.
2. **Load unpacked** → select this `extension/` folder.
3. Pin it if you like. That's it.

## Use

1. Log in to the provider (chatgpt.com, gemini.google.com, grok.com,
   chat.deepseek.com) in a normal tab.
2. Open the `https://<your-server>/connect/<token>` link your bot sent.
3. Click **Capture session**. You'll see “✓ Session captured”.

## Narrowing permissions

`manifest.json` lists `"https://*/*"` in `host_permissions` so it can POST to
whatever domain your server runs on. If your server domain is fixed, replace
that entry with just your server (e.g. `https://login.yourdomain.com/*`) to keep
the extension least-privilege.

## Notes

- Cookies include session tokens — treat them like a password. The server should
  run over HTTPS and store sessions encrypted (`ENCRYPTION_KEY`).
- Firefox: the same files load via `about:debugging` → *Load Temporary Add-on*;
  MV3 service-worker support varies by version.
