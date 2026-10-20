# ChatSherpa Connector — Privacy Policy

The ChatSherpa Connector extension helps you move your **own** signed-in session
for a supported AI provider (ChatGPT, Gemini, Grok, DeepSeek) to a **ChatSherpa
server that you or your administrator operate**.

## What it accesses

Only when you open a `/connect/<token>` page and click **Capture session**:

- The cookies for the provider you are capturing (via the `cookies` permission).
- For some providers, a login token the site stores in the page (via the
  `scripting` permission reading `localStorage` on an open provider tab).

The extension does nothing in the background and takes no action unless you click
Capture on a connect page.

## What it sends, and to whom

The captured session is sent **only** to the server address contained in the
connect link you opened (the `ingest` URL on that page). It is sent nowhere else.
The extension's authors do not receive your data and operate no server on your
behalf.

## What it does not do

- No analytics, tracking, or advertising.
- No selling or sharing of data with third parties.
- No collection of browsing history or activity on other sites.
- No data is stored by the extension itself.

## Logos

Provider logos shown in the popup are loaded from a public favicon service to
indicate which services are supported; no data about you is sent to display them.

## Control

Remove the captured session at any time on your ChatSherpa server (for example
`chatsherpa remove <provider>`), and uninstall the extension to stop all access.

## Contact

Source code and issues: https://github.com/MasterAkbariDev/chatsherpa
