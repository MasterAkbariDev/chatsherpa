// Runs on the login server's /connect/<token> page. Reads the config the page
// embeds, and on the user's click asks the background worker to capture + upload.

function readConfig() {
  const el = document.getElementById("chatsherpa-config");
  if (!el) return null;
  try {
    return JSON.parse(el.textContent);
  } catch (e) {
    return null;
  }
}

function reply(ok, error, extra) {
  window.dispatchEvent(new CustomEvent("chatsherpa:captured", {
    detail: Object.assign({ ok: ok, error: error }, extra || {}),
  }));
}

// Shown when the extension was reloaded or updated while this page was already open. Chrome
// then cuts the page off from the extension, and only a page reload restores the link.
const STALE_MESSAGE = "The ChatSherpa Connector was reloaded or updated. Reload this page, then try again.";

function send(type, cfg, again, done) {
  try {
    chrome.runtime.sendMessage({ type: type, config: cfg }, (resp) => {
      const err = chrome.runtime.lastError;
      // The worker may have been asleep and dropped the first message.
      if (err && !again) return setTimeout(() => send(type, cfg, true, done), 400);
      if (err) return done({ ok: false, error: err.message });
      done(resp || { ok: false, error: "no response" });
    });
  } catch (e) {
    // Thrown synchronously when the extension context is gone (reload or update).
    done({ ok: false, error: STALE_MESSAGE });
  }
}

window.addEventListener("chatsherpa:accounts", () => {
  const cfg = readConfig();
  if (!cfg || !cfg.token) {
    window.dispatchEvent(new CustomEvent("chatsherpa:account-list", {
      detail: { ok: false, error: "missing config" },
    }));
    return;
  }
  send("chatsherpa-accounts", cfg, false, (resp) => {
    window.dispatchEvent(new CustomEvent("chatsherpa:account-list", { detail: resp }));
  });
});

window.addEventListener("chatsherpa:capture", () => {
  const cfg = readConfig();
  if (!cfg || !cfg.token) return reply(false, "missing config");
  send("chatsherpa-capture", cfg, false, (resp) => {
    reply(!!resp.ok, resp.error, resp);
  });
});
