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

function reply(ok, error) {
  window.dispatchEvent(new CustomEvent("chatsherpa:captured", { detail: { ok, error } }));
}

window.addEventListener("chatsherpa:capture", () => {
  const cfg = readConfig();
  if (!cfg || !cfg.token) return reply(false, "missing config");
  chrome.runtime.sendMessage({ type: "chatsherpa-capture", config: cfg }, (resp) => {
    if (chrome.runtime.lastError) return reply(false, chrome.runtime.lastError.message);
    reply(!!(resp && resp.ok), resp && resp.error);
  });
});
