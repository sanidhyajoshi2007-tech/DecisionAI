/**
 * offscreen.js — Captures tab audio and microphone audio in the offscreen document.
 *
 * Runs AudioWorklets for both audio streams at 16 kHz mono 16-bit PCM.
 * Streams 1-second chunks over WebSocket to ws://localhost:8000/ws/meetings/{meeting_id}.
 * Buffers chunks locally on network drops and resends on reconnect.
 * On stop, calls POST /meetings/{meeting_id}/finish and notifies the service worker.
 */

let audioCtx = null;
let tabStream = null;
let micStream = null;
let tabWorklet = null;
let micWorklet = null;
let socket = null;
let currentMeetingId = null;
let isRecording = false;
const bufferQueue = [];
let reconnectTimer = null;

// ASCII headers for binary framing: "TAB:" and "MIC:"
const TAB_PREFIX = new Uint8Array([0x54, 0x41, 0x42, 0x3a]);
const MIC_PREFIX = new Uint8Array([0x4d, 0x49, 0x43, 0x3a]);

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "START_OFFSCREEN_CAPTURE") {
    startCapture(message.streamId, message.meetingId)
      .then(() => sendResponse({ success: true }))
      .catch((err) => {
        console.error("Failed to start offscreen capture:", err);
        sendResponse({ success: false, error: err.message });
      });
    return true; // Keep channel open for async response
  }

  if (message.type === "STOP_OFFSCREEN_CAPTURE") {
    stopCapture()
      .then(() => sendResponse({ success: true }))
      .catch((err) => {
        console.error("Failed to stop offscreen capture:", err);
        sendResponse({ success: false, error: err.message });
      });
    return true;
  }
});

async function startCapture(streamId, meetingId) {
  currentMeetingId = meetingId;
  isRecording = true;
  bufferQueue.length = 0;

  // 1. Capture Google Meet tab audio
  tabStream = await navigator.mediaDevices.getUserMedia({
    audio: {
      mandatory: {
        chromeMediaSource: "tab",
        chromeMediaSourceId: streamId,
      },
    },
    video: false,
  });

  // 2. Capture User's Microphone
  micStream = await navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
    video: false,
  });

  // 3. Initialize AudioContext at 16 kHz
  audioCtx = new AudioContext({ sampleRate: 16000 });
  await audioCtx.audioWorklet.addModule("pcm-processor.js");

  // Route tab audio to speakers so the user can continue hearing participants!
  const tabSource = audioCtx.createMediaStreamSource(tabStream);
  tabSource.connect(audioCtx.destination);

  // 4. Create separate AudioWorkletNodes for tab and mic
  tabWorklet = new AudioWorkletNode(audioCtx, "pcm-recorder-processor", {
    processorOptions: { channel: "tab" },
  });
  micWorklet = new AudioWorkletNode(audioCtx, "pcm-recorder-processor", {
    processorOptions: { channel: "mic" },
  });

  tabWorklet.port.onmessage = (e) => handlePCMChunk(e.data);
  micWorklet.port.onmessage = (e) => handlePCMChunk(e.data);

  tabSource.connect(tabWorklet);
  const micSource = audioCtx.createMediaStreamSource(micStream);
  micSource.connect(micWorklet);

  // 5. Connect WebSocket
  connectWebSocket();
}

function connectWebSocket() {
  if (!isRecording || !currentMeetingId) return;

  const wsUrl = `ws://localhost:8000/ws/meetings/${currentMeetingId}`;
  socket = new WebSocket(wsUrl);
  socket.binaryType = "arraybuffer";

  socket.onopen = () => {
    // Flush buffered chunks
    while (bufferQueue.length > 0 && socket.readyState === WebSocket.OPEN) {
      const chunk = bufferQueue.shift();
      socket.send(chunk);
    }
  };

  socket.onclose = () => {
    if (isRecording) {
      // Attempt reconnect after 1.5 seconds if still recording
      clearTimeout(reconnectTimer);
      reconnectTimer = setTimeout(connectWebSocket, 1500);
    }
  };

  socket.onerror = (err) => {
    console.warn("WebSocket streaming error:", err);
  };
}

function handlePCMChunk({ channel, pcmBuffer }) {
  if (!pcmBuffer || pcmBuffer.byteLength === 0) return;

  // Frame chunk with 4-byte prefix: "TAB:" or "MIC:"
  const prefix = channel === "tab" ? TAB_PREFIX : MIC_PREFIX;
  const pcmBytes = new Uint8Array(pcmBuffer);
  const packet = new Uint8Array(prefix.length + pcmBytes.length);
  packet.set(prefix, 0);
  packet.set(pcmBytes, prefix.length);

  sendOrBuffer(packet.buffer);
}

function sendOrBuffer(chunkBuffer) {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(chunkBuffer);
  } else {
    // Buffer locally if socket is connecting or disconnected
    bufferQueue.push(chunkBuffer);
  }
}

async function stopCapture() {
  isRecording = false;
  clearTimeout(reconnectTimer);

  // 1. Tell worklets to flush remaining samples
  if (tabWorklet) {
    tabWorklet.port.postMessage({ type: "STOP" });
  }
  if (micWorklet) {
    micWorklet.port.postMessage({ type: "STOP" });
  }

  // Small delay to let final flushes process
  await new Promise((r) => setTimeout(r, 150));

  // 2. Stop audio tracks
  if (tabStream) {
    tabStream.getTracks().forEach((track) => track.stop());
    tabStream = null;
  }
  if (micStream) {
    micStream.getTracks().forEach((track) => track.stop());
    micStream = null;
  }

  // 3. Close AudioContext
  if (audioCtx && audioCtx.state !== "closed") {
    await audioCtx.close();
    audioCtx = null;
  }

  // 4. Flush remaining buffer if WebSocket is open
  if (socket && socket.readyState === WebSocket.OPEN) {
    while (bufferQueue.length > 0) {
      socket.send(bufferQueue.shift());
    }
    socket.close();
  }

  // 5. Call backend finish endpoint
  if (currentMeetingId) {
    try {
      await fetch(`http://localhost:8000/meetings/${currentMeetingId}/finish`, {
        method: "POST",
      });
    } catch (err) {
      console.warn("Failed to call finish endpoint:", err);
    }

    // 6. Notify service worker that recording has completed
    chrome.runtime.sendMessage({
      type: "RECORDING_FINISHED",
      meetingId: currentMeetingId,
    });
  }
}
