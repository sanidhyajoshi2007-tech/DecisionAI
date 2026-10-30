/**
 * DecisionAI Extension Configuration
 * Loads the active backend URL from chrome.storage.sync,
 * defaulting to http://localhost:8000 when not customized.
 */

const DEFAULT_SERVER_URL = "https://decisionai-c9jc.onrender.com";

function getBackendConfig() {
  return new Promise((resolve) => {
    chrome.storage.sync.get(["customBackendUrl"], (data) => {
      let rawUrl = (data.customBackendUrl || DEFAULT_SERVER_URL).trim().replace(/\/+$/, "");
      if (!rawUrl.startsWith("http://") && !rawUrl.startsWith("https://")) {
        rawUrl = "https://" + rawUrl;
      }
      const isHttps = rawUrl.startsWith("https://");
      const wsProtocol = isHttps ? "wss://" : "ws://";
      const hostPart = rawUrl.replace(/^https?:\/\//, "");
      const wsBase = `${wsProtocol}${hostPart}`;
      resolve({
        httpBase: rawUrl,
        wsBase: wsBase,
      });
    });
  });
}

function setCustomBackendUrl(newUrl) {
  return new Promise((resolve) => {
    const cleaned = (newUrl || DEFAULT_SERVER_URL).trim().replace(/\/+$/, "");
    chrome.storage.sync.set({ customBackendUrl: cleaned }, () => {
      resolve(cleaned);
    });
  });
}
