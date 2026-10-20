// Service worker: reads the signed-in provider cookies and POSTs them to the
// ChatSherpa login server's /api/ingest endpoint.

// Provider -> cookie domain filter (chrome.cookies.getAll matches subdomains).
const DOMAINS = {
  chatgpt: "chatgpt.com",
  deepseek: "deepseek.com",
  grok: "grok.com",
  gemini: "google.com",
};

async function capture(cfg) {
  try {
    const domain = DOMAINS[cfg.provider] || new URL(cfg.home).hostname;
    const cookies = await chrome.cookies.getAll({ domain });
    if (!cookies.length) return { ok: false, error: "no cookies — are you logged in?" };
    const payload = {
      token: cfg.token,
      provider: cfg.provider,
      cookies: cookies.map((c) => ({
        name: c.name,
        value: c.value,
        domain: c.domain,
        path: c.path,
        secure: c.secure,
        httpOnly: c.httpOnly,
        sameSite: c.sameSite,
        expirationDate: c.expirationDate,
      })),
    };
    const res = await fetch(cfg.ingest, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    return { ok: res.ok, status: res.status };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg && msg.type === "chatsherpa-capture") {
    capture(msg.config).then(sendResponse);
    return true; // async response
  }
});
