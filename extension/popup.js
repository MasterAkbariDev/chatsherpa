// Fills the version from the manifest and adapts the status line to the tab.
(function () {
  "use strict";
  try {
    document.getElementById("ver").textContent = "v" + chrome.runtime.getManifest().version;
  } catch (e) { /* ignore */ }

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
