// Service worker: reads the signed-in provider cookies and POSTs them to the
// ChatSherpa login server's /api/ingest endpoint.

// Provider -> cookie domain filter (chrome.cookies.getAll matches subdomains).
const DOMAINS = {
  chatgpt: ["chatgpt.com", "openai.com"],
  deepseek: ["deepseek.com"],
  grok: ["grok.com"],
  gemini: ["google.com"],
};

// DeepSeek stores the login in the page (userToken), not in a cookie the
// extension can replay on its own. The tab has to be open and signed in.
const LOCAL_STORAGE_REQUIRED = {
  deepseek: ["userToken"],
};

const TAB_URL = {
  chatgpt: "https://chatgpt.com/*",
  deepseek: "https://chat.deepseek.com/*",
  grok: "https://grok.com/*",
  gemini: "https://gemini.google.com/*",
};

async function allCookies(cfg) {
  const domains = DOMAINS[cfg.provider] || [new URL(cfg.home).hostname];
  const seen = new Set();
  const cookies = [];
  for (const domain of domains) {
    let batch = [];
    try {
      batch = await chrome.cookies.getAll({ domain });
    } catch (e) {
      continue;
    }
    for (const c of batch) {
      const key = [c.name, c.domain, c.path].join("|");
      if (seen.has(key)) continue;
      seen.add(key);
      cookies.push(c);
    }
  }
  return cookies;
}

function emailIn(value) {
  if (typeof value !== "string") return "";
  const match = value.match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i);
  return match ? match[0] : "";
}

function parseListAccounts(text) {
  const posted = text.match(/postMessage\('([\s\S]+?)'\s*,/);
  if (posted) {
    text = posted[1].replace(/\\'/g, "'").replace(/\\\//g, "/").replace(/\\"/g, '"');
  }
  const start = text.indexOf("[");
  if (start < 0) return [];
  let data;
  try {
    data = JSON.parse(text.slice(start));
  } catch (e) {
    return [];
  }
  const rows = Array.isArray(data) && Array.isArray(data[1]) ? data[1] : [];
  const accounts = [];
  rows.forEach((row, order) => {
    if (!Array.isArray(row)) return;
    const email = emailIn(row[3]) || row.map(emailIn).find(Boolean) || "";
    if (!email) return;
    accounts.push({ index: order, email: email });
  });
  return accounts;
}

function parseAccountChooser(html) {
  const accounts = [];
  const seen = new Set();
  const re = /data-(?:email|identifier)="([^"]+@[^"]+)"/gi;
  let match;
  while ((match = re.exec(html))) {
    const email = match[1];
    const key = email.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    const start = html.lastIndexOf("<", match.index);
    const end = html.indexOf(">", match.index);
    const tag = start >= 0 && end > start ? html.slice(start, end + 1) : "";
    const auth = tag.match(/data-authuser="(\d+)"/i);
    accounts.push({ index: auth ? Number(auth[1]) : null, email: email });
  }
  if (accounts.length && accounts.every((item) => item.index == null)) {
    accounts.forEach((item, index) => { item.index = index; });
  }
  return accounts;
}

function mergeAccounts(lists) {
  const byEmail = new Map();
  const bare = [];
  for (const list of lists) {
    for (const item of list) {
      const email = (item.email || "").trim();
      if (!email.includes("@")) {
        if (item.index != null) bare.push({ index: item.index, email: "" });
        continue;
      }
      const key = email.toLowerCase();
      const prev = byEmail.get(key);
      if (!prev) byEmail.set(key, { email: email, index: item.index });
      else if (prev.index == null && item.index != null) prev.index = item.index;
    }
  }
  const accounts = [...byEmail.values()];
  const used = new Set(accounts.map((item) => item.index).filter((index) => index != null));
  let next = 0;
  for (const item of accounts) {
    if (item.index != null) continue;
    while (used.has(next)) next += 1;
    item.index = next;
    used.add(next);
  }
  for (const item of bare) {
    if (used.has(item.index)) continue;
    accounts.push(item);
    used.add(item.index);
  }
  accounts.sort((a, b) => a.index - b.index);
  return accounts;
}

async function accountsFromList() {
  const url = "https://accounts.google.com/ListAccounts?gpsia=1&source=ogb&json=standard&listPages=1&authuser=-1&mo=1";
  try {
    const res = await fetch(url, { credentials: "include" });
    if (!res.ok) return [];
    return parseListAccounts(await res.text());
  } catch (e) {
    return [];
  }
}

async function accountsFromChooser() {
  const url = "https://accounts.google.com/AccountChooser?continue=https%3A%2F%2Fgemini.google.com%2Fapp&hl=en";
  try {
    const res = await fetch(url, { credentials: "include" });
    if (!res.ok) return [];
    return parseAccountChooser(await res.text());
  } catch (e) {
    return [];
  }
}

async function accountsFromTabs() {
  const tabs = await chrome.tabs.query({ url: "https://gemini.google.com/*" });
  const byIndex = new Map();
  for (const tab of tabs) {
    if (tab.id == null || !tab.url || tab.url.includes("/sign_in")) continue;
    const home = geminiHome(tab.url);
    if (!home || byIndex.has(home.index)) continue;
    let email = "";
    try {
      const [injected] = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: () => {
          const nodes = document.querySelectorAll("[data-email], [data-identifier], [aria-label]");
          for (const node of nodes) {
            const raw = node.getAttribute("data-email") || node.getAttribute("data-identifier") || node.getAttribute("aria-label") || "";
            const match = raw.match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i);
            if (match) return match[0];
          }
          return "";
        },
      });
      email = (injected && injected.result) || "";
    } catch (e) {
      // A tab URL still identifies the account slot.
    }
    byIndex.set(home.index, { index: home.index, email: email });
  }
  return [...byIndex.values()];
}

async function listGoogleAccounts() {
  const [listed, chooser, tabs] = await Promise.all([
    accountsFromList(),
    accountsFromChooser(),
    accountsFromTabs(),
  ]);
  return mergeAccounts([chooser, listed, tabs]);
}

function geminiHome(tabUrl) {
  if (!tabUrl) return null;
  const match = tabUrl.match(/\/u\/(\d+)\//);
  const index = match ? match[1] : "0";
  return { index: Number(index), home: "https://gemini.google.com/u/" + index + "/app" };
}

async function pickTab(pattern) {
  const tabs = await chrome.tabs.query({ url: pattern });
  const open = tabs.filter((t) => t.id != null && t.url && !t.url.includes("/sign_in"));
  if (!open.length) return null;
  const active = open.find((t) => t.active);
  if (active) return active;
  return open.slice().sort((a, b) => (b.lastAccessed || 0) - (a.lastAccessed || 0))[0];
}

async function readOrigin(cfg) {
  const pattern = TAB_URL[cfg.provider];
  if (!pattern) return null;
  const tab = await pickTab(pattern);
  if (!tab) return null;
  const found = { tabUrl: tab.url, origin: null, localStorage: [] };
  try {
    const [injected] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => ({
        origin: location.origin,
        tabUrl: location.href,
        localStorage: Object.keys(localStorage).map((name) => ({
          name,
          value: localStorage.getItem(name),
        })).filter((item) => item.value != null),
      }),
    });
    if (injected && injected.result) {
      found.origin = injected.result.origin;
      found.localStorage = injected.result.localStorage || [];
      found.tabUrl = injected.result.tabUrl || tab.url;
    }
  } catch (e) {
    // The tab URL is enough to know which Gemini account was open.
  }
  return found;
}

async function capture(cfg) {
  try {
    const cookies = await allCookies(cfg);
    if (!cookies.length) return { ok: false, error: "no cookies — are you logged in?" };
    const origin = await readOrigin(cfg);
    const account = cfg.provider === "gemini"
      ? (cfg.account === 0 || cfg.account ? { index: Number(cfg.account), home: "https://gemini.google.com/u/" + cfg.account + "/app" } : geminiHome(origin && origin.tabUrl))
      : null;
    if (cfg.provider === "gemini" && !account) {
      return {
        ok: false,
        error: "Open gemini.google.com on the account you want, then capture again. The address shows /u/1/ for the second account.",
      };
    }
    const needed = LOCAL_STORAGE_REQUIRED[cfg.provider] || [];
    const have = new Set(((origin && origin.localStorage) || []).map((item) => item.name));
    const missing = needed.filter((name) => !have.has(name));
    if (missing.length) {
      return {
        ok: false,
        error: "Open " + (cfg.home || cfg.provider) + " in a tab, sign in, then capture again. Missing " + missing.join(", ") + ".",
      };
    }
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
      origins: origin ? [{ origin: origin.origin, localStorage: origin.localStorage }] : [],
      home: account ? account.home : undefined,
    };
    const res = await fetch(cfg.ingest, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    return {
      ok: res.ok,
      status: res.status,
      account: account ? account.index : undefined,
      email: cfg.email || undefined,
    };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg && msg.type === "chatsherpa-accounts") {
    listGoogleAccounts()
      .then((accounts) => sendResponse({ ok: true, accounts: accounts }))
      .catch((e) => sendResponse({ ok: false, error: String(e) }));
    return true;
  }
  if (msg && msg.type === "chatsherpa-capture") {
    capture(msg.config)
      .then(sendResponse)
      .catch((e) => sendResponse({ ok: false, error: String(e) }));
    return true; // async response
  }
});
