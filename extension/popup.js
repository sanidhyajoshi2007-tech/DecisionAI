/**
 * popup.js: DecisionAI Extension Popup Controller
 *
 * Controls personal user authentication and recording lifecycle:
 * - Checks personal authentication with backend (/auth/me) before permitting recording
 * - Checks if the active browser tab is on meet.google.com
 * - Enables/disables Start Recording button accordingly
 * - Displays live timer and visible recording indicator
 * - Displays AI transcription progress bar when call finishes and redirects to dashboard
 */

document.addEventListener("DOMContentLoaded", async () => {
  const startBtn = document.getElementById("startBtn");
  const stopBtn = document.getElementById("stopBtn");
  const tabNotice = document.getElementById("tabNotice");
  const recordingBadge = document.getElementById("recordingBadge");
  const recordingDetails = document.getElementById("recordingDetails");
  const recordingTimer = document.getElementById("recordingTimer");
  const activeMeetingId = document.getElementById("activeMeetingId");
  const inputSection = document.getElementById("inputSection");
  const meetingTitle = document.getElementById("meetingTitle");
  const statusMessage = document.getElementById("statusMessage");
  const micStatusCard = document.getElementById("micStatusCard");
  const micStatusText = document.getElementById("micStatusText");
  const enableMicBtn = document.getElementById("enableMicBtn");
  const consentSection = document.getElementById("consentSection");
  const consentCheckbox = document.getElementById("consentCheckbox");

  // Auth & Progress DOM elements
  const accountCard = document.getElementById("accountCard");
  const accountEmail = document.getElementById("accountEmail");
  const loginGateCard = document.getElementById("loginGateCard");
  const openLoginBtn = document.getElementById("openLoginBtn");
  const recordingControls = document.getElementById("recordingControls");
  const transcriptionCard = document.getElementById("transcriptionCard");
  const transcriptionPercent = document.getElementById("transcriptionPercent");
  const transcriptionBar = document.getElementById("transcriptionBar");
  const transcriptionStatus = document.getElementById("transcriptionStatus");

  let timerInterval = null;
  let currentActiveMeetingId = null;

  // 1. Check Authentication with Backend
  async function checkAuth() {
    try {
      const res = await fetch("http://localhost:8000/auth/me", {
        credentials: "include",
      });
      if (res.ok) {
        return await res.json();
      }
      return null;
    } catch {
      return null;
    }
  }

  if (openLoginBtn) {
    openLoginBtn.addEventListener("click", () => {
      chrome.tabs.create({ url: "http://localhost:8000/web/login.html" });
    });
  }

  const currentUser = await checkAuth();

  if (!currentUser || !currentUser.email) {
    // Show login gate, lock recording
    if (loginGateCard) loginGateCard.classList.remove("hidden");
    if (accountCard) accountCard.classList.add("hidden");
    if (recordingControls) recordingControls.classList.add("hidden");
    if (transcriptionCard) transcriptionCard.classList.add("hidden");
    setStatus("Please sign in to your DecisionAI account to record meetings.", false);
    return;
  }

  // User is authenticated
  if (loginGateCard) loginGateCard.classList.add("hidden");
  if (accountCard) {
    accountCard.classList.remove("hidden");
    if (accountEmail) accountEmail.textContent = currentUser.email;
  }
  if (recordingControls) recordingControls.classList.remove("hidden");

  // 2. Microphone Permission Check
  async function checkMicPermission() {
    try {
      if (navigator.permissions && navigator.permissions.query) {
        const perm = await navigator.permissions.query({ name: "microphone" });
        updateMicUI(perm.state);
        perm.onchange = () => updateMicUI(perm.state);
        return perm.state === "granted";
      }
    } catch (e) {
      console.warn("Permissions query failed:", e);
    }
    return false;
  }

  function updateMicUI(state) {
    if (state === "granted") {
      micStatusText.textContent = "Ready (Your voice will be recorded)";
      micStatusText.style.color = "#4ade80";
      enableMicBtn.classList.add("hidden");
    } else if (state === "denied") {
      micStatusText.textContent = "Blocked in browser settings";
      micStatusText.style.color = "#f87171";
      enableMicBtn.classList.remove("hidden");
      enableMicBtn.textContent = "Fix";
    } else {
      micStatusText.textContent = "Permission needed for your voice";
      micStatusText.style.color = "#fbbf24";
      enableMicBtn.classList.remove("hidden");
      enableMicBtn.textContent = "Enable";
    }
  }

  enableMicBtn.addEventListener("click", () => {
    chrome.tabs.create({ url: chrome.runtime.getURL("permission.html") });
  });

  await checkMicPermission();

  // 3. Check Current Active Tab
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const isMeetTab = Boolean(
    tab && tab.url && tab.url.startsWith("https://meet.google.com/")
  );

  // 4. Fetch Current Session State
  const sessionData = await chrome.storage.session.get([
    "isRecording",
    "meetingId",
    "startTime",
    "recordingState",
  ]);

  const isRecording = Boolean(sessionData.isRecording);
  currentActiveMeetingId = sessionData.meetingId;
  const startTime = sessionData.startTime || Date.now();
  const recordingState = sessionData.recordingState || "idle";

  if (recordingState === "transcribing" && currentActiveMeetingId) {
    showTranscriptionUI(currentActiveMeetingId);
  } else {
    updateUI(isRecording, isMeetTab, currentActiveMeetingId, startTime);
  }

  // 5. Listen for Storage Changes
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "session") {
      const state = changes.recordingState ? changes.recordingState.newValue : recordingState;
      const mId = changes.meetingId ? changes.meetingId.newValue : currentActiveMeetingId;
      const isRec = changes.isRecording ? changes.isRecording.newValue : isRecording;
      const sTime = changes.startTime ? changes.startTime.newValue : startTime;

      if (state === "transcribing" && mId) {
        showTranscriptionUI(mId);
      } else if (state === "idle" && transcriptionCard && !transcriptionCard.classList.contains("hidden")) {
        // Background service worker finalized the meeting
        if (transcriptionBar) transcriptionBar.style.width = "100%";
        if (transcriptionPercent) transcriptionPercent.textContent = "100%";
        if (transcriptionStatus) transcriptionStatus.textContent = "Complete! Redirecting to Dashboard...";
        setTimeout(() => {
          chrome.tabs.create({ url: "http://localhost:8000/web/index.html" });
          window.close();
        }, 500);
      } else {
        if (transcriptionCard) transcriptionCard.classList.add("hidden");
        if (recordingControls) recordingControls.classList.remove("hidden");
        updateUI(isRec, isMeetTab, mId, sTime);
      }
    }
  });

  // Consent Checkbox
  if (consentCheckbox) {
    consentCheckbox.addEventListener("change", () => {
      if (isMeetTab && !isRecording) {
        startBtn.disabled = !consentCheckbox.checked;
      }
    });
  }

  // 6. Start Recording Action
  startBtn.addEventListener("click", async () => {
    if (!isMeetTab) return;

    if (!consentCheckbox || !consentCheckbox.checked) {
      setStatus("Please confirm participant consent before recording.", true);
      return;
    }

    const isMicGranted = await checkMicPermission();
    if (!isMicGranted) {
      chrome.tabs.create({ url: chrome.runtime.getURL("permission.html") });
      setStatus("Please click 'Allow' on the microphone tab that just opened.", true);
      return;
    }

    startBtn.disabled = true;
    startBtn.innerHTML = "<span>Starting...</span>";
    setStatus("Connecting to tab audio and mic...", false);

    try {
      const response = await chrome.runtime.sendMessage({
        type: "START_RECORDING",
        tabId: tab.id,
        title: meetingTitle.value.trim() || "Google Meet Call",
        hasMicPermission: true,
        consentRecorded: true,
      });

      if (!response || !response.success) {
        throw new Error(response?.error || "Failed to start recording");
      }

      currentActiveMeetingId = response.meetingId;
      setStatus("", false);
      updateUI(true, isMeetTab, response.meetingId, Date.now());
    } catch (err) {
      console.error("Start recording error:", err);
      setStatus(err.message, true);
      startBtn.disabled = false;
      startBtn.innerHTML = `
        <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
            <circle cx="12" cy="12" r="9"/>
        </svg>
        <span>Start Recording</span>
      `;
    }
  });

  // 7. Stop Recording Action
  stopBtn.addEventListener("click", async () => {
    stopBtn.disabled = true;
    stopBtn.innerHTML = "<span>Stopping...</span>";

    const mId = currentActiveMeetingId;
    showTranscriptionUI(mId);

    try {
      const response = await chrome.runtime.sendMessage({
        type: "STOP_RECORDING",
      });

      if (!response || !response.success) {
        throw new Error(response?.error || "Failed to stop recording");
      }
    } catch (err) {
      console.error("Stop recording error:", err);
      setStatus(err.message, true);
    }
  });

  // Transcription Progress Controller
  function showTranscriptionUI(mId) {
    clearInterval(timerInterval);
    if (recordingControls) recordingControls.classList.add("hidden");
    if (transcriptionCard) transcriptionCard.classList.remove("hidden");
    if (recordingBadge) recordingBadge.classList.add("hidden");

    let progress = 20;
    if (transcriptionBar) transcriptionBar.style.width = "20%";
    if (transcriptionPercent) transcriptionPercent.textContent = "20%";
    if (transcriptionStatus) transcriptionStatus.textContent = "Saving audio tracks and initializing AI pipeline...";

    const progressTimer = setInterval(() => {
      if (progress < 94) {
        progress += progress < 70 ? 6 : 2;
        if (transcriptionBar) transcriptionBar.style.width = `${progress}%`;
        if (transcriptionPercent) transcriptionPercent.textContent = `${progress}%`;

        if (progress > 30 && progress <= 55) {
          if (transcriptionStatus) transcriptionStatus.textContent = "AI is transcribing Hindi and English channels...";
        } else if (progress > 55 && progress <= 75) {
          if (transcriptionStatus) transcriptionStatus.textContent = "Extracting key decisions and action items...";
        } else if (progress > 75) {
          if (transcriptionStatus) transcriptionStatus.textContent = "Indexing transcript into hybrid vector search...";
        }
      }
    }, 1200);

    let pollCount = 0;
    const pollInterval = setInterval(async () => {
      if (!mId) return;
      pollCount++;
      try {
        const res = await fetch(`http://localhost:8000/api/meetings/${mId}`, {
          credentials: "include",
        });
        if (res.ok) {
          const data = await res.json();
          if (data.status === "ready" || (pollCount >= 40 && data.status !== "failed")) {
            clearInterval(progressTimer);
            clearInterval(pollInterval);
            if (transcriptionBar) transcriptionBar.style.width = "100%";
            if (transcriptionPercent) transcriptionPercent.textContent = "100%";
            if (transcriptionStatus) transcriptionStatus.textContent = "Transcription complete! Redirecting to Dashboard...";

            await chrome.storage.session.set({
              recordingState: "idle",
              isRecording: false,
              meetingId: null,
              startTime: null,
            });

            setTimeout(() => {
              chrome.tabs.create({ url: "http://localhost:8000/web/index.html" });
              window.close();
            }, 600);
          } else if (data.status === "failed" || data.status === "error") {
            clearInterval(progressTimer);
            clearInterval(pollInterval);
            if (transcriptionBar) transcriptionBar.style.width = "100%";
            if (transcriptionPercent) transcriptionPercent.textContent = "Error";
            if (transcriptionStatus) transcriptionStatus.textContent = data.error_message || "Processing failed.";
          }
        }
      } catch (err) {
        console.warn("Status poll error:", err);
      }
    }, 1500);
  }

  function updateUI(recording, onMeet, mId, sTime) {
    clearInterval(timerInterval);

    if (recording) {
      // Active Recording State
      tabNotice.classList.add("hidden");
      inputSection.classList.add("hidden");
      micStatusCard.classList.add("hidden");
      if (consentSection) consentSection.classList.add("hidden");
      startBtn.classList.add("hidden");
      stopBtn.classList.remove("hidden");
      stopBtn.disabled = false;
      stopBtn.innerHTML = `
        <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
            <rect x="6" y="6" width="12" height="12" rx="2"/>
        </svg>
        <span>Stop Recording</span>
      `;

      recordingBadge.classList.remove("hidden");
      recordingDetails.classList.remove("hidden");
      activeMeetingId.textContent = mId ? `#${mId}` : "Active";

      const updateTimer = () => {
        const elapsedSeconds = Math.max(0, Math.floor((Date.now() - sTime) / 1000));
        const minutes = String(Math.floor(elapsedSeconds / 60)).padStart(2, "0");
        const seconds = String(elapsedSeconds % 60).padStart(2, "0");
        recordingTimer.textContent = `${minutes}:${seconds}`;
      };

      updateTimer();
      timerInterval = setInterval(updateTimer, 1000);
    } else {
      // Idle State
      recordingBadge.classList.add("hidden");
      recordingDetails.classList.add("hidden");
      stopBtn.classList.add("hidden");
      startBtn.classList.remove("hidden");
      inputSection.classList.remove("hidden");
      if (consentSection) consentSection.classList.remove("hidden");

      if (onMeet) {
        tabNotice.classList.remove("warning");
        tabNotice.classList.add("success");
        tabNotice.classList.remove("hidden");
        tabNotice.innerHTML = `
          <div class="notice-icon">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
                <path d="M13.78 4.22a.75.75 0 0 1 0 1.06l-7.25 7.25a.75.75 0 0 1-1.06 0L2.22 9.28a.75.75 0 0 1 1.06-1.06L6 10.94l6.72-6.72a.75.75 0 0 1 1.06 0z"/>
            </svg>
          </div>
          <div class="notice-content">
              <p>Google Meet detected. Ready to record.</p>
          </div>
        `;
        const hasConsent = Boolean(consentCheckbox && consentCheckbox.checked);
        startBtn.disabled = !hasConsent;
        startBtn.innerHTML = `
          <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
              <circle cx="12" cy="12" r="9"/>
          </svg>
          <span>Start Recording</span>
        `;
      } else {
        tabNotice.classList.remove("success");
        tabNotice.classList.add("warning");
        tabNotice.classList.remove("hidden");
        tabNotice.innerHTML = `
          <div class="notice-icon">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
                <path d="M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1zm0 3a.75.75 0 0 1 .75.75v3.5a.75.75 0 0 1-1.5 0v-3.5A.75.75 0 0 1 8 4zm0 8a1 1 0 1 1 0-2 1 1 0 0 1 0 2z"/>
            </svg>
          </div>
          <div class="notice-content">
              <p>Open or switch to a <strong>meet.google.com</strong> tab to start recording.</p>
          </div>
        `;
        startBtn.disabled = true;
        startBtn.innerHTML = `
          <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
              <circle cx="12" cy="12" r="9"/>
          </svg>
          <span>Start Recording</span>
        `;
      }
    }
  }

  function setStatus(msg, isError) {
    statusMessage.textContent = msg;
    if (isError) {
      statusMessage.classList.add("error");
    } else {
      statusMessage.classList.remove("error");
    }
  }
});
