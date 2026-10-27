/**
 * service-worker.js: Manifest V3 Background Service Worker
 *
 * Coordinates recording lifecycle:
 * 1. Checks and locks state in chrome.storage.session to prevent double-starts.
 * 2. Creates meeting via POST http://localhost:8000/meetings.
 * 3. Obtains tab capture stream ID via chrome.tabCapture.getMediaStreamId.
 * 4. Creates offscreen document and starts audio capture & streaming.
 * 5. On finish, closes offscreen document and opens http://localhost:8000/meetings/{id}.
 */
importScripts("config.js");

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
    handleStartRecording(message.tabId, message.title, message.hasMicPermission, message.consentRecorded)
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

async function getAuthHeaders(url) {
  const headers = {};
  try {
    if (chrome.cookies && chrome.cookies.get) {
      const cookie = await chrome.cookies.get({
        url: url,
        name: "decisionai_session",
      });
      if (cookie && cookie.value) {
        headers["Authorization"] = `Bearer ${cookie.value}`;
      }
    }
  } catch (err) {
    console.warn("Could not read cookie in service worker:", err);
  }
  return headers;
}

async function handleStartRecording(tabId, title, hasMicPermission, consentRecorded) {
  const { recordingState = "idle" } = await chrome.storage.session.get("recordingState");

  if (recordingState !== "idle") {
    return { success: false, error: `Recording already in state: ${recordingState}` };
  }

  // Lock state
  await chrome.storage.session.set({ recordingState: "starting" });

  try {
    const config = await getBackendConfig();
    const backendBase = config.httpBase;
    const wsBase = config.wsBase;
    const authHeaders = await getAuthHeaders(backendBase);

    // 1. Verify personal account authentication
    let authRes;
    try {
      authRes = await fetch(`${backendBase}/auth/me`, {
        headers: authHeaders,
        credentials: "include",
      });
    } catch (netErr) {
      throw new Error(`Cannot reach backend at ${backendBase}. Make sure your server is running.`);
    }

    if (!authRes.ok) {
      throw new Error("Please sign in to your DecisionAI account before recording.");
    }

    // 2. Create meeting in backend scoped to authenticated user
    const createRes = await fetch(`${backendBase}/meetings`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-DecisionAI-Client": "extension",
        ...authHeaders,
      },
      credentials: "include",
      body: JSON.stringify({
        title: title || "Google Meet Call",
        consent_recorded: Boolean(consentRecorded),
      }),
    });

    if (!createRes.ok) {
      let errDetail = `Backend returned status ${createRes.status}`;
      try {
        const errJson = await createRes.json();
        if (errJson && errJson.detail) {
          errDetail = errJson.detail;
        }
      } catch {}
      throw new Error(errDetail);
    }

    const meetingData = await createRes.json();
    const meetingId = meetingData.id;
    const streamToken = meetingData.stream_token || "";

    // 3. Get stream ID for the target Google Meet tab
    const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tabId });

    // 4. Ensure offscreen document exists
    const hasDoc = await chrome.offscreen.hasDocument();
    if (!hasDoc) {
      await chrome.offscreen.createDocument({
        url: "offscreen.html",
        reasons: ["USER_MEDIA"],
        justification: "Capture Google Meet tab audio and user microphone",
      });
    }

    // 5. Instruct offscreen document to begin capture and WebSocket streaming
    const captureRes = await chrome.runtime.sendMessage({
      type: "START_OFFSCREEN_CAPTURE",
      streamId,
      meetingId,
      streamToken,
      hasMicPermission,
      wsBase,
      httpBase: backendBase,
    });

    if (!captureRes || !captureRes.success) {
      throw new Error(captureRes?.error || "Offscreen audio capture failed to start");
    }

    // 6. Update state and set visual REC badge
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

  // 2. Set transcribing status on extension badge and storage
  await chrome.storage.session.set({
    recordingState: "transcribing",
    isRecording: false,
    meetingId,
  });
  await chrome.action.setBadgeText({ text: "AI" });
  await chrome.action.setBadgeBackgroundColor({ color: "#4F78E3" });

  const config = await getBackendConfig();
  const backendBase = config.httpBase;

  // 3. Poll meeting status until AI transcription and post-processing finish
  if (meetingId) {
    for (let i = 0; i < 60; i++) {
      try {
        const authHeaders = await getAuthHeaders(backendBase);
        const checkRes = await fetch(`${backendBase}/api/meetings/${meetingId}`, {
          headers: authHeaders,
          credentials: "include",
        });
        if (checkRes.ok) {
          const m = await checkRes.json();
          if (m.status === "ready" || m.status === "failed" || m.status === "error") {
            break;
          }
        }
      } catch {
        // Retry next interval
      }
      await new Promise((r) => setTimeout(r, 1500));
    }
  }

  // 4. Reset session state
  await chrome.storage.session.set({
    recordingState: "idle",
    isRecording: false,
    meetingId: null,
    startTime: null,
  });

  // 5. Clear action badge
  await chrome.action.setBadgeText({ text: "" });

  // 6. Redirect user directly to the DecisionAI Dashboard
  await chrome.tabs.create({ url: `${backendBase}/web/index.html` });
}

