/**
 * popup.js — DecisionAI Extension Popup Controller
 *
 * Controls recording lifecycle from the popup UI:
 * - Checks if the active browser tab is on meet.google.com
 * - Enables/disables Start Recording button accordingly
 * - Displays live timer and visible recording indicator
 * - Dispatches START_RECORDING and STOP_RECORDING messages to service-worker.js
 */

document.addEventListener("DOMContentLoaded", async () => {
  const startBtn = document.getElementById("startBtn");
  const stopBtn = document.getElementById("stopBtn");
  const tabNotice = document.getElementById("tabNotice");
  const noticeText = document.getElementById("noticeText");
  const recordingBadge = document.getElementById("recordingBadge");
  const recordingDetails = document.getElementById("recordingDetails");
  const recordingTimer = document.getElementById("recordingTimer");
  const activeMeetingId = document.getElementById("activeMeetingId");
  const inputSection = document.getElementById("inputSection");
  const meetingTitle = document.getElementById("meetingTitle");
  const statusMessage = document.getElementById("statusMessage");

  let timerInterval = null;

  // 1. Check current active tab
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const isMeetTab = Boolean(
    tab && tab.url && tab.url.startsWith("https://meet.google.com/")
  );

  // 2. Fetch current recording state from session storage
  const sessionData = await chrome.storage.session.get([
    "isRecording",
    "meetingId",
    "startTime",
    "recordingState",
  ]);

  const isRecording = Boolean(sessionData.isRecording);
  const meetingId = sessionData.meetingId;
  const startTime = sessionData.startTime || Date.now();

  updateUI(isRecording, isMeetTab, meetingId, startTime);

  // 3. Listen for storage changes (e.g. if recording stops from background)
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "session") {
      const isRec = changes.isRecording
        ? changes.isRecording.newValue
        : isRecording;
      const mId = changes.meetingId
        ? changes.meetingId.newValue
        : meetingId;
      const sTime = changes.startTime
        ? changes.startTime.newValue
        : startTime;
      updateUI(isRec, isMeetTab, mId, sTime);
    }
  });

  // 4. Start Recording Button
  startBtn.addEventListener("click", async () => {
    if (!isMeetTab) return;

    startBtn.disabled = true;
    startBtn.innerHTML = "<span>Starting...</span>";
    setStatus("Connecting to tab audio and mic...", false);

    try {
      const response = await chrome.runtime.sendMessage({
        type: "START_RECORDING",
        tabId: tab.id,
        title: meetingTitle.value.trim() || "Google Meet Call",
      });

      if (!response || !response.success) {
        throw new Error(response?.error || "Failed to start recording");
      }

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

  // 5. Stop Recording Button
  stopBtn.addEventListener("click", async () => {
    stopBtn.disabled = true;
    stopBtn.innerHTML = "<span>Finalizing...</span>";
    setStatus("Saving audio and opening summary...", false);

    try {
      const response = await chrome.runtime.sendMessage({
        type: "STOP_RECORDING",
      });

      if (!response || !response.success) {
        throw new Error(response?.error || "Failed to stop recording");
      }
      // UI will automatically update via session storage reset or window closing
    } catch (err) {
      console.error("Stop recording error:", err);
      setStatus(err.message, true);
      stopBtn.disabled = false;
      stopBtn.innerHTML = `
        <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
            <rect x="6" y="6" width="12" height="12" rx="2"/>
        </svg>
        <span>Stop Recording</span>
      `;
    }
  });

  function updateUI(recording, onMeet, mId, sTime) {
    clearInterval(timerInterval);

    if (recording) {
      // Recording Active State
      tabNotice.classList.add("hidden");
      inputSection.classList.add("hidden");
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

      // Live timer counter
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

      if (onMeet) {
        // Active on Google Meet
        tabNotice.classList.remove("warning");
        tabNotice.classList.add("success");
        tabNotice.classList.remove("hidden");
        tabNotice.innerHTML = `
          <div class="notice-icon">✅</div>
          <div class="notice-content">
              <p>Google Meet detected. Ready to record.</p>
          </div>
        `;
        startBtn.disabled = false;
        startBtn.innerHTML = `
          <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
              <circle cx="12" cy="12" r="9"/>
          </svg>
          <span>Start Recording</span>
        `;
      } else {
        // Not on Google Meet
        tabNotice.classList.remove("success");
        tabNotice.classList.add("warning");
        tabNotice.classList.remove("hidden");
        tabNotice.innerHTML = `
          <div class="notice-icon">⚠️</div>
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
