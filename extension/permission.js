/**
 * permission.js: Handles microphone permission grant in a persistent extension tab.
 */

document.addEventListener("DOMContentLoaded", async () => {
  const requestBtn = document.getElementById("requestBtn");
  const statusBox = document.getElementById("statusBox");

  async function checkExistingPermission() {
    try {
      if (navigator.permissions && navigator.permissions.query) {
        const perm = await navigator.permissions.query({ name: "microphone" });
        if (perm.state === "granted") {
          showSuccess("Microphone permission is already granted. Closing tab...");
          setTimeout(() => window.close(), 1200);
          return true;
        }
      }
    } catch (e) {
      console.warn("Could not query microphone permission:", e);
    }
    return false;
  }

  const alreadyGranted = await checkExistingPermission();
  if (alreadyGranted) return;

  requestBtn.addEventListener("click", async () => {
    requestBtn.disabled = true;
    requestBtn.innerHTML = "<span>Requesting access...</span>";

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      // Stop tracks immediately - permission is now stored for the extension origin
      stream.getTracks().forEach((track) => track.stop());

      showSuccess("Microphone permission granted. You can now record meetings.");
      requestBtn.innerHTML = "<span>Microphone Enabled</span>";
      setTimeout(() => window.close(), 1500);
    } catch (err) {
      console.error("Microphone request failed:", err);
      showError(
        "Microphone permission was denied or dismissed. Please click the lock icon in the address bar to allow microphone access, then click the button again."
      );
      requestBtn.disabled = false;
      requestBtn.innerHTML = `
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/>
            <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
            <line x1="12" x2="12" y1="19" y2="22"/>
        </svg>
        <span>Try Again</span>
      `;
    }
  });

  function showSuccess(msg) {
    statusBox.textContent = msg;
    statusBox.className = "status-box success";
  }

  function showError(msg) {
    statusBox.textContent = msg;
    statusBox.className = "status-box error";
  }
});
