# Publishing the ChatSherpa Connector extension

The extension lives in this `extension/` folder. It has a brand icon
(`icons/icon16|32|48|128.png`) and a popup UI, so it is ready to package.

## Test it unpacked first (no store needed)

This is all you need for personal use.

1. Open `chrome://extensions` (or `edge://extensions`), turn on **Developer mode**.
2. Click **Load unpacked** and select this `extension/` folder.
3. The ChatSherpa icon appears in the toolbar.

Firefox: `about:debugging` → This Firefox → **Load Temporary Add-on** → pick
`manifest.json`.

## Package a zip for a store

```bash
cd extension
zip -r ../chatsherpa-extension-$(grep -o '"version":[^,]*' manifest.json | tr -dc '0-9.').zip . -x '*.DS_Store'
```

The zip must have `manifest.json` at its root (zip the folder's contents, not the
folder).

## Chrome Web Store

1. One-time: register a developer account at
   https://chrome.google.com/webstore/devconsole (US$5 one-off fee).
2. **New item** → upload the zip.
3. Fill the listing: name, summary, a description, at least one 1280×800 or
   640×400 screenshot, and the 128px icon (already in the zip).
4. **Privacy**: declare that the extension reads cookies and sends them to the
   user's own server. Justify each permission:
   - `cookies` — read the signed-in session to hand to your ChatSherpa server.
   - `scripting` — read the page's localStorage token (DeepSeek, Gemini account).
   - host permissions — the provider sites and your server.
   - Data use: the session is sent only to the server named in the connect link;
     you do not sell or transfer it.
5. Submit for review (usually a few days). Consider **Unlisted** visibility so
   only people with the link install it.

## Microsoft Edge Add-ons

https://partner.microsoft.com/dashboard/microsoftedge — free registration, same
zip, similar listing and review.

## Firefox Add-ons (AMO)

https://addons.mozilla.org/developers/ — free. MV3 service-worker support varies
by Firefox version; test the unpacked add-on first.

## Tightening before you publish

- `host_permissions` includes `"https://*/*"` so the extension can POST to any
  server domain. Reviewers dislike this. If your server domain is fixed, replace
  it with just that domain (e.g. `https://bv.akbaridev.com/*`); keep the provider
  entries.
- Bump `version` in `manifest.json` for every store upload (stores reject a
  re-used version).
- Session cookies are as sensitive as a password. The listing should say the
  data goes only to the user's own server and is used only to drive their own
  chats.
