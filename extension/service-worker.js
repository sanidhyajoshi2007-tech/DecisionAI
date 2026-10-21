/**
 * service-worker.js — Manifest V3 Background Service Worker
 *
 * Coordinates recording lifecycle:
 * 1. Checks and locks state in chrome.storage.session to prevent double-starts.
 * 2. Creates meeting via POST http://localhost:8000/meetings.
 * 3. Obtains tab capture stream ID via chrome.tabCapture.getMediaStreamId.
 * 4. Creates offscreen document and starts audio capture & streaming.
 * 5. On finish, closes offscreen document and opens http://localhost:8000/meetings/{id}.
 */

const BACKEND_BASE = "http://localhost:8000";

// Ensure initial state
chrome.runtime.onInstalled.addListener(async () => {
  await chrome.storage.session.set({
    recordingState: "idle",
    isRecording: false,
    meetingId: null,
  });
  await chrome.action.setBadgeText({ text: "" });
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "START_RECORDING") {
    handleStartRecording(message.tabId, message.title, message.hasMicPermission)
      .then((res) => sendResponse(res))
      .catch((err) => sendResponse({ success: false, error: err.message }));
    return true;
  }

  if (message.type === "STOP_RECORDING") {
    handleStopRecording()
      .then((res) => sendResponse(res))
      .catch((err) => sendResponse({ success: false, error: err.message }));
    return true;
  }

  if (message.type === "RECORDING_FINISHED") {
    handleRecordingFinished(message.meetingId);
    return false;
  }
});

async function handleStartRecording(tabId, title, hasMicPermission) {
  const { recordingState = "idle" } = await chrome.storage.session.get("recordingState");

  if (recordingState !== "idle") {
    return { success: false, error: `Recording already in state: ${recordingState}` };
  }

  // Lock state
  await chrome.storage.session.set({ recordingState: "starting" });

  try {
    // 1. Create meeting in backend
    let createRes;
    try {
      createRes = await fetch(`${BACKEND_BASE}/meetings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: title || "Google Meet Call" }),
      });
    } catch (netErr) {
      throw new Error(`Cannot reach backend at ${BACKEND_BASE}. Make sure the FastAPI server is running.`);
    }

    if (!createRes.ok) {
      throw new Error(`Backend returned status ${createRes.status}`);
    }

    const meetingData = await createRes.json();
    const meetingId = meetingData.id;

    // 2. Get stream ID for the target Google Meet tab
    const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tabId });

    // 3. Ensure offscreen document exists
    const hasDoc = await chrome.offscreen.hasDocument();
    if (!hasDoc) {
      await chrome.offscreen.createDocument({
        url: "offscreen.html",
        reasons: ["USER_MEDIA"],
        justification: "Capture Google Meet tab audio and user microphone",
      });
    }

    // 4. Instruct offscreen document to begin capture and WebSocket streaming
    const captureRes = await chrome.runtime.sendMessage({
      type: "START_OFFSCREEN_CAPTURE",
      streamId,
      meetingId,
      hasMicPermission,
    });

    if (!captureRes || !captureRes.success) {
      throw new Error(captureRes?.error || "Offscreen audio capture failed to start");
    }

    // 5. Update state and set visual REC badge
    await chrome.storage.session.set({
      recordingState: "recording",
      isRecording: true,
      meetingId,
      tabId,
      startTime: Date.now(),
    });

    await chrome.action.setBadgeText({ text: "REC" });
    await chrome.action.setBadgeBackgroundColor({ color: "#EF4444" });

    return { success: true, meetingId };
  } catch (err) {
    console.error("Start recording failed:", err);
    await chrome.storage.session.set({ recordingState: "idle", isRecording: false });
    await chrome.action.setBadgeText({ text: "" });

    // Clean up offscreen document if created
    if (await chrome.offscreen.hasDocument()) {
      await chrome.offscreen.closeDocument();
    }
    throw err;
  }
}

async function handleStopRecording() {
  const { recordingState = "idle" } = await chrome.storage.session.get("recordingState");

  if (recordingState !== "recording") {
    return { success: false, error: "Not currently recording" };
  }

  await chrome.storage.session.set({ recordingState: "stopping" });

  // Send stop instruction to offscreen document
  await chrome.runtime.sendMessage({ type: "STOP_OFFSCREEN_CAPTURE" });

  return { success: true };
}

async function handleRecordingFinished(meetingId) {
  // 1. Close offscreen document
  if (await chrome.offscreen.hasDocument()) {
    await chrome.offscreen.closeDocument();
  }

  // 2. Reset session state
  await chrome.storage.session.set({
    recordingState: "idle",
    isRecording: false,
    meetingId: null,
    startTime: null,
  });

  // 3. Clear action badge
  await chrome.action.setBadgeText({ text: "" });

  // 4. Open meeting viewer page in a new tab
  if (meetingId) {
    await chrome.tabs.create({ url: `${BACKEND_BASE}/meetings/${meetingId}` });
  }
}
