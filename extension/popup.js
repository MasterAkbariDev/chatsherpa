// Fills the version from the manifest and adapts the status line to the tab.
(function () {
  "use strict";
  try {
    document.getElementById("ver").textContent = "v" + chrome.runtime.getManifest().version;
  } catch (e) { /* ignore */ }

  // Provider logos load from each provider's own favicon. If one can't load
  // (offline, blocked), fall back to a colored tile with the provider's letter.
  const TILE = { C: "#10a37f", G: "#3b7cf6", X: "#1d1d1f", D: "#4d6bfe" };
  document.querySelectorAll(".pi img[data-fallback]").forEach((img) => {
    const fail = () => {
      const letter = img.getAttribute("data-fallback");
      const span = document.createElement("span");
      span.className = "fallback";
      span.textContent = letter;
      span.style.color = "#fff";
      const tile = img.parentElement;
      if (tile) {
        tile.style.background = TILE[letter] || "#f4f5f8";
        img.replaceWith(span);
      }
    };
    img.addEventListener("error", fail);
    // Already finished loading but broken (listener attached too late).
    if (img.complete && img.naturalWidth === 0) fail();
  });

  const title = document.getElementById("st-title");
  const sub = document.getElementById("st-sub");
  const dot = document.getElementById("dot");

  function set(onState, t, s) {
    if (title) title.textContent = t;
    if (sub) sub.textContent = s;
    if (dot) dot.classList.toggle("on", onState);
  }

  try {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      const url = (tabs && tabs[0] && tabs[0].url) || "";
      if (/\/connect\//.test(url)) {
        set(true, "On a connect page", "Click Capture session below the steps");
      } else if (/chatgpt\.com|deepseek\.com|grok\.com|gemini\.google\.com/.test(url)) {
        set(true, "Signed-in tab detected", "Open your connect link, then capture");
      } else {
        set(true, "Ready to capture", "Open your login link, then click Capture");
      }
    });
  } catch (e) { /* popup still renders without tabs permission */ }
})();
