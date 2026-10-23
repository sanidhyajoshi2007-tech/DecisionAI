/**
 * DecisionAI Web Application Core
 * Vanilla JavaScript (No frameworks, no icon libraries, no emojis, no em dashes)
 */

// Theme Management
const THEME_STORAGE_KEY = 'decisionai_theme';

function initTheme() {
  const saved = localStorage.getItem(THEME_STORAGE_KEY);
  if (saved === 'light' || saved === 'dark') {
    document.documentElement.setAttribute('data-theme', saved);
  } else {
    const prefersLight = window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches;
    document.documentElement.setAttribute('data-theme', prefersLight ? 'light' : 'dark');
  }
  updateThemeButtonText();
}

function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme') || 'dark';
  const next = current === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem(THEME_STORAGE_KEY, next);
  updateThemeButtonText();
}

function updateThemeButtonText() {
  const btn = document.getElementById('theme-toggle-btn');
  if (!btn) return;
  const current = document.documentElement.getAttribute('data-theme') || 'dark';
  const label = current === 'dark' ? 'Light Mode' : 'Dark Mode';
  btn.setAttribute('aria-label', `Switch to ${label}`);
  btn.title = `Switch to ${label}`;
}

// Inline SVG Icons (Hand-written, flat paths)
function getSvgArrow() {
  return '<svg class="arrow-icon" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M3 8h10M9 4l4 4-4 4"/></svg>';
}

function getSvgSun() {
  return '<svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor" aria-hidden="true"><circle cx="8" cy="8" r="3"/><path d="M8 1v2M8 13v2M1 8h2M13 8h2M3 3l1.5 1.5M11.5 11.5L13 13M3 13l1.5-1.5M11.5 4.5L13 3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>';
}

function getSvgMoon() {
  return '<svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor" aria-hidden="true"><path d="M6 2a6 6 0 1 0 8 8 7 7 0 1 1-8-8z"/></svg>';
}

// Toast Notifications
function showToast(message, duration = 3000) {
  let container = document.getElementById('toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toast-container';
    container.className = 'toast-container';
    document.body.appendChild(container);
  }
  const toast = document.createElement('div');
  toast.className = 'toast';
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => {
    toast.remove();
  }, duration);
}

// CSRF Token Helper
function getCsrfToken() {
  const match = document.cookie.match(new RegExp('(^| )decisionai_csrf=([^;]+)'));
  return match ? decodeURIComponent(match[2]) : '';
}

// API Client
const API = {
  async fetchCurrentUser() {
    try {
      const res = await fetch('/auth/me');
      if (res.ok) {
        return await res.json();
      }
      return null;
    } catch {
      return null;
    }
  },

  async logout() {
    try {
      await fetch('/auth/logout', {
        method: 'POST',
        headers: {
          'X-CSRF-Token': getCsrfToken(),
        },
      });
    } finally {
      window.location.href = '/web/login.html';
    }
  },

  async listMeetings(page = 1, pageSize = 50) {
    const res = await fetch(`/api/meetings?page=${page}&page_size=${pageSize}`);
    if (!res.ok) {
      throw new Error(`Failed to load meetings (Status ${res.status})`);
    }
    return await res.json();
  },

  async getMeeting(id) {
    const res = await fetch(`/api/meetings/${id}`);
    if (!res.ok) {
      throw new Error(`Meeting not found (Status ${res.status})`);
    }
    return await res.json();
  },

  async updateMeetingTitle(id, title) {
    const res = await fetch(`/api/meetings/${id}`, {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': getCsrfToken(),
      },
      body: JSON.stringify({ title }),
    });
    if (!res.ok) {
      throw new Error(`Could not update title (Status ${res.status})`);
    }
    return await res.json();
  },

  async deleteMeeting(id) {
    const res = await fetch(`/api/meetings/${id}`, {
      method: 'DELETE',
      headers: {
        'X-CSRF-Token': getCsrfToken(),
      },
    });
    if (!res.ok) {
      throw new Error(`Could not delete meeting (Status ${res.status})`);
    }
    return await res.json();
  },

  async getTranscript(id) {
    const res = await fetch(`/api/meetings/${id}/transcript`);
    if (!res.ok) {
      throw new Error(`Could not load transcript (Status ${res.status})`);
    }
    return await res.json();
  },

  async getDecisions(id) {
    const res = await fetch(`/api/meetings/${id}/decisions`);
    if (!res.ok) {
      throw new Error(`Could not load decisions (Status ${res.status})`);
    }
    return await res.json();
  },

  async askStream(question, meetingId = null, onToken, onCitations, onDone, onError) {
    try {
      const payload = {
        question: question.trim(),
        meeting_id: meetingId ? parseInt(meetingId, 10) : null,
        stream: true,
      };

      const res = await fetch('/api/ask', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': getCsrfToken(),
        },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new Error(errJson.detail || `Server returned error (${res.status})`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buffer = '';

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n\n');
        buffer = lines.pop();

        for (const line of lines) {
          const trimmed = line.trim();
          if (trimmed.startsWith('data: ')) {
            try {
              const data = JSON.parse(trimmed.slice(6));
              if (data.token && onToken) {
                onToken(data.token);
              }
              if (data.citations && onCitations) {
                onCitations(data.citations, data.decisions, data.intent);
              }
              if (data.done && onDone) {
                onDone();
              }
            } catch (jsonErr) {
              // skip parse error
            }
          }
        }
      }
      if (onDone) onDone();
    } catch (err) {
      if (onError) onError(err);
    }
  },

  async getUserSettings() {
    const res = await fetch('/api/users/me/settings');
    if (!res.ok) {
      throw new Error(`Failed to load settings (Status ${res.status})`);
    }
    return await res.json();
  },

  async updateUserSettings(retentionDays) {
    const res = await fetch('/api/users/me/settings', {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': getCsrfToken(),
      },
      body: JSON.stringify({ retention_days: retentionDays }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Failed to update retention (Status ${res.status})`);
    }
    return await res.json();
  },

  async deleteAllUserData(confirmation) {
    const res = await fetch('/api/users/me/delete-all-data', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': getCsrfToken(),
      },
      body: JSON.stringify({ confirmation }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Failed to delete all data (Status ${res.status})`);
    }
    return await res.json();
  },

  async runRetentionCleanup() {
    const res = await fetch('/api/retention/run-cleanup', {
      method: 'POST',
      headers: {
        'X-CSRF-Token': getCsrfToken(),
      },
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Cleanup failed (Status ${res.status})`);
    }
    return await res.json();
  },
};

// Formatting Utilities
function formatDuration(seconds) {
  if (seconds === null || seconds === undefined || seconds <= 0) {
    return '0s';
  }
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  if (mins === 0) {
    return `${secs}s`;
  }
  return `${mins}m ${secs}s`;
}

function formatDateTime(isoString) {
  if (!isoString) return 'Not recorded';
  const date = new Date(isoString);
  if (isNaN(date.getTime())) return isoString;
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  }).format(date);
}

function formatTimestamp(seconds) {
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

function getStatusBadge(status) {
  const norm = (status || 'ready').toLowerCase();
  let label = norm;
  let cls = 'badge-ready';

  if (norm === 'recording') {
    label = 'Recording';
    cls = 'badge-recording';
  } else if (norm === 'processing' || norm === 'uploading') {
    label = 'Processing';
    cls = 'badge-processing';
  } else if (norm === 'failed' || norm === 'error') {
    label = 'Failed';
    cls = 'badge-failed';
  } else {
    label = 'Ready';
    cls = 'badge-ready';
  }

  return `<span class="badge ${cls}">${label}</span>`;
}

// Dashboard Page Controller
async function initDashboardPage() {
  const user = await API.fetchCurrentUser();
  const userSlot = document.getElementById('user-profile-slot');
  if (userSlot) {
    if (user && user.email) {
      userSlot.innerHTML = `
        <span style="font-size: var(--text-xs); color: var(--text-secondary);">${escapeHtml(user.name || user.email)}</span>
        <a href="/web/settings.html" class="btn btn-secondary btn-sm">Settings</a>
        <a href="/web/privacy.html" class="btn btn-secondary btn-sm">Privacy</a>
        <button id="logout-btn" class="btn btn-secondary btn-sm" type="button">Logout</button>
      `;
      document.getElementById('logout-btn')?.addEventListener('click', API.logout);
    } else {
      userSlot.innerHTML = `
        <a href="/web/privacy.html" class="btn btn-secondary btn-sm">Privacy</a>
        <a href="/web/login.html" class="btn btn-primary btn-sm">Login</a>
      `;
    }
  }

  const tableContainer = document.getElementById('meetings-table-container');
  const statsCountEl = document.getElementById('stat-total-count');
  const statsHoursEl = document.getElementById('stat-total-hours');
  const decisionsBoxEl = document.getElementById('recent-decisions-container');

  if (!tableContainer) return;

  // Loading state
  tableContainer.innerHTML = `
    <div class="state-box">
      <div class="spinner"></div>
      <div class="state-title">Loading Meetings</div>
      <p class="state-desc">Fetching recorded meetings from your database.</p>
    </div>
  `;

  try {
    const data = await API.listMeetings(1, 100);
    const meetings = data.meetings || [];

    // Update Stats
    const totalCount = data.total || meetings.length;
    let totalSeconds = 0;
    let readyCount = 0;
    let processingCount = 0;
    let failedCount = 0;

    meetings.forEach((m) => {
      if (m.duration_seconds) totalSeconds += m.duration_seconds;
      const s = (m.status || '').toLowerCase();
      if (s === 'ready') readyCount++;
      else if (s === 'processing' || s === 'uploading' || s === 'recording') processingCount++;
      else if (s === 'failed' || s === 'error') failedCount++;
    });

    if (statsCountEl) statsCountEl.textContent = String(totalCount);
    if (statsHoursEl) {
      const hrs = (totalSeconds / 3600).toFixed(1);
      statsHoursEl.textContent = `${hrs}h`;
    }
    const readyEl = document.getElementById('stat-ready-count');
    const processingEl = document.getElementById('stat-processing-count');
    const failedEl = document.getElementById('stat-failed-count');
    if (readyEl) readyEl.textContent = String(readyCount);
    if (processingEl) processingEl.textContent = String(processingCount);
    if (failedEl) failedEl.textContent = String(failedCount);

    // Empty State
    if (meetings.length === 0) {
      tableContainer.innerHTML = `
        <div class="state-box">
          <div class="state-title">No Meetings Found</div>
          <p class="state-desc">You have not recorded any meetings yet. Start a recording using the DecisionAI Chrome Extension on Google Meet.</p>
        </div>
      `;
      if (decisionsBoxEl) {
        decisionsBoxEl.innerHTML = '<p class="state-desc" style="margin: 0;">No decisions recorded yet.</p>';
      }
      return;
    }

    // Render Table Rows
    const rows = meetings.map((m) => {
      const title = m.title || 'Untitled Meeting';
      const created = formatDateTime(m.created_at);
      const duration = formatDuration(m.duration_seconds);
      const badge = getStatusBadge(m.status);

      return `
        <tr>
          <td>
            <a href="/web/meeting.html?id=${m.id}" class="link-arrow" style="font-weight: 600; color: var(--text-primary);">
              ${escapeHtml(title)} ${getSvgArrow()}
            </a>
          </td>
          <td style="color: var(--text-secondary);">${created}</td>
          <td style="font-family: var(--font-mono); color: var(--text-secondary);">${duration}</td>
          <td>${badge}</td>
          <td style="text-align: right;">
            <a href="/web/meeting.html?id=${m.id}" class="btn btn-secondary btn-sm link-arrow">
              View ${getSvgArrow()}
            </a>
          </td>
        </tr>
      `;
    }).join('');

    tableContainer.innerHTML = `
      <div class="table-wrapper">
        <table>
          <thead>
            <tr>
              <th>Meeting Title</th>
              <th>Date and Time</th>
              <th>Duration</th>
              <th>Status</th>
              <th style="text-align: right;">Actions</th>
            </tr>
          </thead>
          <tbody>
            ${rows}
          </tbody>
        </table>
      </div>
    `;

    // Populate Decisions Card with recent items from ready meetings
    if (decisionsBoxEl) {
      const readyWithDecisions = meetings.filter((m) => m.status === 'ready').slice(0, 3);
      if (readyWithDecisions.length === 0) {
        decisionsBoxEl.innerHTML = '<p class="state-desc" style="margin: 0;">No decisions extracted yet.</p>';
      } else {
        const decisionsPromises = readyWithDecisions.map((m) => API.getDecisions(m.id).catch(() => ({ decisions: [] })));
        const allResults = await Promise.all(decisionsPromises);
        let items = [];
        allResults.forEach((res, i) => {
          (res.decisions || []).forEach((d) => {
            items.push({ ...d, meetingId: readyWithDecisions[i].id, meetingTitle: readyWithDecisions[i].title });
          });
        });

        if (items.length === 0) {
          decisionsBoxEl.innerHTML = '<p class="state-desc" style="margin: 0;">No decisions extracted yet.</p>';
        } else {
          decisionsBoxEl.innerHTML = items.slice(0, 4).map((d) => {
            const statusBadge = getDecisionStatusBadge(d.status);
            return `
              <div style="padding: var(--space-3) 0; border-bottom: 1px solid var(--border-subtle);">
                <div style="display: flex; gap: var(--space-2); align-items: center; margin-bottom: 4px;">
                  <span class="pill-type">${escapeHtml(d.type || 'decision')}</span>
                  ${statusBadge}
                </div>
                <div style="font-size: var(--text-sm); color: var(--text-primary); margin-bottom: 4px;">${escapeHtml(d.text)}</div>
                <a href="/web/meeting.html?id=${d.meetingId}#decisions" class="link-arrow" style="font-size: var(--text-xs); color: var(--accent-primary);">
                  ${escapeHtml(d.meetingTitle)} ${getSvgArrow()}
                </a>
              </div>
            `;
          }).join('');
        }
      }
    }
  } catch (err) {
    tableContainer.innerHTML = `
      <div class="state-box state-error">
        <div class="state-title">Error Loading Meetings</div>
        <p class="state-desc">${escapeHtml(err.message)}</p>
        <button onclick="initDashboardPage()" class="btn btn-primary btn-sm" type="button">Try Again</button>
      </div>
    `;
  }
}

// Meeting Page Controller
let currentMeeting = null;
let currentTranscriptSegments = [];
let currentDecisions = [];

async function initMeetingPage() {
  const params = new URLSearchParams(window.location.search);
  const meetingId = parseInt(params.get('id'), 10);

  if (!meetingId || isNaN(meetingId)) {
    renderMeetingError('Missing or invalid meeting identifier in URL.');
    return;
  }

  // Bind Tabs
  document.querySelectorAll('.tab-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const tabTarget = btn.getAttribute('data-tab');
      switchTab(tabTarget);
    });
  });

  // Rename and Delete Modals
  setupMeetingModals(meetingId);

  // Search input real-time filtering
  const searchInput = document.getElementById('transcript-search');
  if (searchInput) {
    searchInput.addEventListener('input', (e) => {
      filterTranscript(e.target.value);
    });
  }

  // Copy transcript button
  document.getElementById('copy-transcript-btn')?.addEventListener('click', copyTranscript);

  // Export transcript button
  document.getElementById('export-transcript-btn')?.addEventListener('click', exportTranscript);

  // Load User in Header
  const user = await API.fetchCurrentUser();
  const userSlot = document.getElementById('user-profile-slot');
  if (userSlot) {
    if (user && user.email) {
      userSlot.innerHTML = `
        <span style="font-size: var(--text-xs); color: var(--text-secondary);">${user.name || user.email}</span>
        <button id="logout-btn" class="btn btn-secondary btn-sm" type="button">Logout</button>
      `;
      document.getElementById('logout-btn')?.addEventListener('click', API.logout);
    } else {
      userSlot.innerHTML = `<a href="/web/login.html" class="btn btn-primary btn-sm">Login</a>`;
    }
  }

  // Load Meeting Data
  await loadMeetingData(meetingId);
}

async function loadMeetingData(meetingId) {
  const container = document.getElementById('meeting-content-root');
  if (!container) return;

  try {
    const meeting = await API.getMeeting(meetingId);
    currentMeeting = meeting;

    // Header info
    document.getElementById('meeting-title-display').textContent = meeting.title || 'Untitled Meeting';
    document.getElementById('meeting-date-display').textContent = formatDateTime(meeting.created_at);
    document.getElementById('meeting-duration-display').textContent = formatDuration(meeting.duration_seconds);
    document.getElementById('meeting-status-display').innerHTML = getStatusBadge(meeting.status);

    // Audio Players setup
    setupAudioPlayers(meetingId, meeting);

    // Summary tab population
    const summaryTextEl = document.getElementById('summary-content-text');
    if (summaryTextEl) {
      if (meeting.summary) {
        summaryTextEl.textContent = meeting.summary;
      } else if (meeting.status === 'processing') {
        summaryTextEl.innerHTML = '<p class="state-desc">The meeting is currently being processed. Please refresh in a moment to view the executive summary.</p>';
      } else {
        summaryTextEl.innerHTML = '<p class="state-desc">No executive summary has been generated for this meeting.</p>';
      }
    }

    // Load Transcript Segments
    await loadTranscriptData(meetingId);

    // Load Decisions
    await loadDecisionsData(meetingId);
  } catch (err) {
    renderMeetingError(err.message);
  }
}

async function loadTranscriptData(meetingId) {
  const root = document.getElementById('transcript-segments-root');
  if (!root) return;

  root.innerHTML = `
    <div class="state-box">
      <div class="spinner"></div>
      <div class="state-title">Loading Transcript</div>
      <p class="state-desc">Retrieving speaker segments.</p>
    </div>
  `;

  try {
    const data = await API.getTranscript(meetingId);
    currentTranscriptSegments = data.segments || [];

    if (currentTranscriptSegments.length === 0) {
      if (currentMeeting && currentMeeting.status === 'processing') {
        root.innerHTML = `
          <div class="state-box">
            <div class="state-title">Processing Transcription</div>
            <p class="state-desc">Transcription pipeline is currently running. Please refresh shortly.</p>
          </div>
        `;
      } else {
        root.innerHTML = `
          <div class="state-box">
            <div class="state-title">No Transcript Available</div>
            <p class="state-desc">No speech was transcribed for this meeting.</p>
          </div>
        `;
      }
      return;
    }

    renderTranscriptSegments(currentTranscriptSegments);

    // Auto-scroll and seek if ?t= param was provided in URL (from citation click)
    const urlParams = new URLSearchParams(window.location.search);
    const tParam = urlParams.get('t');
    if (tParam) {
      const tNum = parseInt(tParam, 10);
      const targetSec = tNum > 1000 ? tNum / 1000 : parseFloat(tParam);
      if (!isNaN(targetSec)) {
        setTimeout(() => {
          let bestSeg = currentTranscriptSegments[0];
          let bestDiff = 999999;
          for (const s of currentTranscriptSegments) {
            const diff = Math.abs((s.start_time || 0) - targetSec);
            if (diff < bestDiff) {
              bestDiff = diff;
              bestSeg = s;
            }
          }
          if (bestSeg) {
            highlightSegment(bestSeg.id, targetSec);
          }
        }, 350);
      }
    }
  } catch (err) {
    root.innerHTML = `
      <div class="state-box state-error">
        <div class="state-title">Failed to Load Transcript</div>
        <p class="state-desc">${escapeHtml(err.message)}</p>
      </div>
    `;
  }
}

function renderTranscriptSegments(segments) {
  const root = document.getElementById('transcript-segments-root');
  if (!root) return;

  if (segments.length === 0) {
    root.innerHTML = `
      <div class="state-box">
        <div class="state-title">No Matching Segments</div>
        <p class="state-desc">No dialogue matched your search query.</p>
      </div>
    `;
    return;
  }

  root.innerHTML = segments.map((s) => {
    const speaker = s.speaker || 'Speaker';
    const timeStr = formatTimestamp(s.start_time);

    return `
      <div class="transcript-item" id="seg-${s.id}">
        <div class="transcript-meta">
          <span class="speaker-tag">${escapeHtml(speaker)}</span>
          <button class="timestamp-btn" onclick="highlightSegment(${s.id}, ${s.start_time})" type="button" title="Jump to timestamp">
            ${timeStr}
          </button>
        </div>
        <div class="transcript-text">${escapeHtml(s.text)}</div>
      </div>
    `;
  }).join('');
}

function filterTranscript(query) {
  const q = (query || '').trim().toLowerCase();
  const counter = document.getElementById('search-matches-counter');

  if (!q) {
    if (counter) counter.textContent = '';
    renderTranscriptSegments(currentTranscriptSegments);
    return;
  }

  const filtered = currentTranscriptSegments.filter((s) => {
    return (s.text || '').toLowerCase().includes(q) || (s.speaker || '').toLowerCase().includes(q);
  });

  if (counter) {
    counter.textContent = `${filtered.length} of ${currentTranscriptSegments.length} segments match`;
  }

  renderTranscriptSegments(filtered);
}

function highlightSegment(segId, startTime) {
  // Clear other highlights
  document.querySelectorAll('.transcript-item.highlight').forEach((el) => {
    el.classList.remove('highlight');
  });

  const target = document.getElementById(`seg-${segId}`);
  if (target) {
    target.classList.add('highlight');
    target.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  // Seek tab audio if present
  const tabAudio = document.getElementById('player-tab-audio');
  if (tabAudio && !isNaN(startTime)) {
    tabAudio.currentTime = startTime;
  }
  const micAudio = document.getElementById('player-mic-audio');
  if (micAudio && !isNaN(startTime)) {
    micAudio.currentTime = startTime;
  }

  showToast(`Jumped to ${formatTimestamp(startTime)}`);
}

function copyTranscript() {
  if (!currentTranscriptSegments || currentTranscriptSegments.length === 0) {
    showToast('No transcript text available to copy.');
    return;
  }

  const lines = currentTranscriptSegments.map((s) => {
    const time = formatTimestamp(s.start_time);
    const speaker = s.speaker || 'Speaker';
    return `[${time}] ${speaker}: ${s.text}`;
  });

  const fullText = lines.join('\n');
  navigator.clipboard.writeText(fullText).then(() => {
    showToast('Transcript copied to clipboard.');
  }).catch(() => {
    showToast('Unable to copy to clipboard.');
  });
}

function exportTranscript() {
  if (!currentTranscriptSegments || currentTranscriptSegments.length === 0) {
    showToast('No transcript text available to export.');
    return;
  }

  const title = (currentMeeting?.title || 'meeting').replace(/[^a-zA-Z0-9_-]/g, '_');
  const lines = currentTranscriptSegments.map((s) => {
    const time = formatTimestamp(s.start_time);
    const speaker = s.speaker || 'Speaker';
    return `[${time}] ${speaker}: ${s.text}`;
  });

  const content = lines.join('\n');
  const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${title}_transcript.txt`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
  showToast('Transcript exported as text file.');
}

async function loadDecisionsData(meetingId) {
  const root = document.getElementById('decisions-root');
  if (!root) return;

  root.innerHTML = `
    <div class="state-box">
      <div class="spinner"></div>
      <div class="state-title">Loading Decisions</div>
      <p class="state-desc">Retrieving decisions and action items.</p>
    </div>
  `;

  try {
    const data = await API.getDecisions(meetingId);
    currentDecisions = data.decisions || [];

    if (currentDecisions.length === 0) {
      root.innerHTML = `
        <div class="state-box">
          <div class="state-title">No Decisions Found</div>
          <p class="state-desc">No decisions, action items, or client requests were recorded for this meeting.</p>
        </div>
      `;
      return;
    }

    root.innerHTML = currentDecisions.map((d) => {
      const typeLabel = escapeHtml((d.type || 'decision').replace('_', ' ').toUpperCase());
      const statusBadge = getDecisionStatusBadge(d.status);
      const timeStr = d.timestamp_ms ? formatTimestamp(d.timestamp_ms / 1000) : '00:00';

      return `
        <div class="decision-card">
          <div class="decision-content">
            <div class="decision-meta">
              <span class="pill-type">${typeLabel}</span>
              ${statusBadge}
              <button class="timestamp-btn" onclick="highlightByTimeMs(${d.timestamp_ms})" type="button">
                ${timeStr}
              </button>
            </div>
            <div class="decision-text">${escapeHtml(d.text)}</div>
          </div>
        </div>
      `;
    }).join('');
  } catch (err) {
    root.innerHTML = `
      <div class="state-box state-error">
        <div class="state-title">Failed to Load Decisions</div>
        <p class="state-desc">${escapeHtml(err.message)}</p>
      </div>
    `;
  }
}

function highlightByTimeMs(ms) {
  if (!ms) return;
  switchTab('transcript');
  const sec = ms / 1000.0;
  // Find closest segment
  let closest = null;
  let minDiff = Infinity;
  currentTranscriptSegments.forEach((s) => {
    const diff = Math.abs(s.start_time - sec);
    if (diff < minDiff) {
      minDiff = diff;
      closest = s;
    }
  });

  if (closest) {
    highlightSegment(closest.id, closest.start_time);
  }
}

function getDecisionStatusBadge(status) {
  const s = (status || 'accepted').toLowerCase();
  if (s === 'accepted') {
    return '<span class="badge badge-ready">Accepted</span>';
  } else if (s === 'rejected') {
    return '<span class="badge badge-failed">Rejected</span>';
  } else {
    return '<span class="badge badge-processing">Pending</span>';
  }
}

function switchTab(tabId) {
  document.querySelectorAll('.tab-btn').forEach((btn) => {
    btn.classList.toggle('active', btn.getAttribute('data-tab') === tabId);
  });
  document.querySelectorAll('.tab-panel').forEach((panel) => {
    panel.classList.toggle('active', panel.id === `tab-panel-${tabId}`);
  });
}

function setupAudioPlayers(meetingId, meeting) {
  const container = document.getElementById('audio-players-container');
  if (!container) return;

  const tabSrc = `/meetings/${meetingId}/audio/tab`;
  const micSrc = `/meetings/${meetingId}/audio/mic`;

  container.innerHTML = `
    <div class="audio-section">
      <h3 style="margin-bottom: var(--space-4);">Recorded Call Audio</h3>
      <div class="audio-row">
        <span class="audio-label">Call Participants (Tab Audio Track)</span>
        <audio id="player-tab-audio" controls preload="metadata" src="${tabSrc}"></audio>
      </div>
      <div class="audio-row">
        <span class="audio-label">Your Voice (Microphone Audio Track)</span>
        <audio id="player-mic-audio" controls preload="metadata" src="${micSrc}"></audio>
      </div>
    </div>
  `;
}

function setupMeetingModals(meetingId) {
  // Rename Modal
  const renameModal = document.getElementById('rename-modal');
  const renameInput = document.getElementById('rename-title-input');
  const openRenameBtn = document.getElementById('open-rename-btn');
  const closeRenameBtn = document.getElementById('cancel-rename-btn');
  const saveRenameBtn = document.getElementById('save-rename-btn');

  if (openRenameBtn && renameModal) {
    openRenameBtn.addEventListener('click', () => {
      if (currentMeeting) {
        renameInput.value = currentMeeting.title || '';
      }
      renameModal.classList.add('active');
      renameInput.focus();
    });

    closeRenameBtn.addEventListener('click', () => {
      renameModal.classList.remove('active');
    });

    saveRenameBtn.addEventListener('click', async () => {
      const newTitle = renameInput.value.trim();
      if (!newTitle) {
        showToast('Meeting title cannot be empty.');
        return;
      }
      try {
        saveRenameBtn.disabled = true;
        await API.updateMeetingTitle(meetingId, newTitle);
        document.getElementById('meeting-title-display').textContent = newTitle;
        if (currentMeeting) currentMeeting.title = newTitle;
        renameModal.classList.remove('active');
        showToast('Meeting title updated.');
      } catch (err) {
        showToast(`Failed to update title: ${err.message}`);
      } finally {
        saveRenameBtn.disabled = false;
      }
    });
  }

  // Delete Modal
  const deleteModal = document.getElementById('delete-modal');
  const openDeleteBtn = document.getElementById('open-delete-btn');
  const cancelDeleteBtn = document.getElementById('cancel-delete-btn');
  const confirmDeleteBtn = document.getElementById('confirm-delete-btn');

  if (openDeleteBtn && deleteModal) {
    openDeleteBtn.addEventListener('click', () => {
      deleteModal.classList.add('active');
    });

    cancelDeleteBtn.addEventListener('click', () => {
      deleteModal.classList.remove('active');
    });

    confirmDeleteBtn.addEventListener('click', async () => {
      try {
        confirmDeleteBtn.disabled = true;
        await API.deleteMeeting(meetingId);
        showToast('Meeting deleted. Returning to dashboard.');
        setTimeout(() => {
          window.location.href = '/web/index.html';
        }, 800);
      } catch (err) {
        showToast(`Failed to delete meeting: ${err.message}`);
        confirmDeleteBtn.disabled = false;
      }
    });
  }
}

function renderMeetingError(message) {
  const root = document.getElementById('meeting-content-root');
  if (root) {
    root.innerHTML = `
      <div class="state-box state-error" style="margin-top: var(--space-8);">
        <div class="state-title">Unable to Load Meeting</div>
        <p class="state-desc">${escapeHtml(message)}</p>
        <a href="/web/index.html" class="btn btn-secondary btn-sm link-arrow">
          Back to Dashboard ${getSvgArrow()}
        </a>
      </div>
    `;
  }
}

// Ask AI Page Controller
async function initAskAIPage() {
  const user = await API.fetchCurrentUser();
  const userSlot = document.getElementById('user-profile-slot');
  if (userSlot) {
    if (user && user.email) {
      userSlot.innerHTML = `
        <span style="font-size: var(--text-xs); color: var(--text-secondary);">${user.name || user.email}</span>
        <button id="logout-btn" class="btn btn-secondary btn-sm" type="button">Logout</button>
      `;
      document.getElementById('logout-btn')?.addEventListener('click', API.logout);
    } else {
      userSlot.innerHTML = `<a href="/web/login.html" class="btn btn-primary btn-sm">Login</a>`;
    }
  }

  // Populate meeting scope dropdown
  const scopeSelect = document.getElementById('meeting-scope-select');
  if (scopeSelect) {
    try {
      const data = await API.listMeetings(1, 100);
      const meetings = data.meetings || [];
      meetings.forEach((m) => {
        const opt = document.createElement('option');
        opt.value = String(m.id);
        const title = m.title || `Meeting #${m.id}`;
        opt.textContent = `${title} (${formatDateTime(m.created_at)})`;
        scopeSelect.appendChild(opt);
      });
    } catch {
      // Fallback silently if meeting list fails
    }
  }

  // Form submit
  const form = document.getElementById('chat-form');
  const input = document.getElementById('chat-input');
  const sendBtn = document.getElementById('chat-send-btn');
  const messagesContainer = document.getElementById('chat-messages');
  const clearBtn = document.getElementById('clear-chat-btn');

  if (clearBtn && messagesContainer) {
    clearBtn.addEventListener('click', () => {
      messagesContainer.innerHTML = `
        <div class="chat-bubble chat-bubble-assistant">
          <div class="chat-role-label">DecisionAI Assistant</div>
          <div>Chat history cleared. You can ask a new question about your meeting archive.</div>
        </div>
      `;
    });
  }

  if (form && input && sendBtn && messagesContainer) {
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const question = input.value.trim();
      if (!question) return;

      const meetingId = scopeSelect ? scopeSelect.value : null;

      // 1. Append User Message
      const userBubble = document.createElement('div');
      userBubble.className = 'chat-bubble chat-bubble-user';
      userBubble.innerHTML = `
        <div class="chat-role-label">You</div>
        <div>${escapeHtml(question)}</div>
      `;
      messagesContainer.appendChild(userBubble);
      input.value = '';
      sendBtn.disabled = true;

      // 2. Append Assistant Placeholder Bubble with Streaming Cursor
      const assistantBubble = document.createElement('div');
      assistantBubble.className = 'chat-bubble chat-bubble-assistant';
      assistantBubble.innerHTML = `
        <div class="chat-role-label">DecisionAI Assistant</div>
        <div class="assistant-content"><span class="streaming-cursor"></span></div>
        <div class="citation-container" style="display: none;">
          <div class="citation-title">Grounded Citations</div>
          <div class="citation-chips"></div>
        </div>
      `;
      messagesContainer.appendChild(assistantBubble);
      messagesContainer.scrollTop = messagesContainer.scrollHeight;

      const contentEl = assistantBubble.querySelector('.assistant-content');
      const citationContainer = assistantBubble.querySelector('.citation-container');
      const chipsEl = assistantBubble.querySelector('.citation-chips');

      let accumulatedText = '';

      await API.askStream(
        question,
        meetingId,
        (token) => {
          accumulatedText += token;
          contentEl.innerHTML = `${escapeHtml(accumulatedText)}<span class="streaming-cursor"></span>`;
          messagesContainer.scrollTop = messagesContainer.scrollHeight;
        },
        (citations) => {
          contentEl.innerHTML = escapeHtml(accumulatedText);

          if (citations && citations.length > 0) {
            citationContainer.style.display = 'block';
            chipsEl.innerHTML = citations.map((c) => {
              const timeDisplay = c.timestamp || '00:00';
              const meetingTitle = c.meeting_title || `Meeting #${c.meeting_id}`;
              const targetUrl = `/web/meeting.html?id=${c.meeting_id}&t=${c.start_ms}`;

              return `
                <a href="${targetUrl}" class="citation-chip" title="Click to view transcript excerpt in ${escapeHtml(meetingTitle)}">
                  <span>${escapeHtml(meetingTitle)}</span>
                  <span class="citation-time">@ ${timeDisplay}</span>
                  ${getSvgArrow()}
                </a>
              `;
            }).join('');
          }
          messagesContainer.scrollTop = messagesContainer.scrollHeight;
        },
        () => {
          contentEl.innerHTML = escapeHtml(accumulatedText);
          sendBtn.disabled = false;
          input.focus();
        },
        (err) => {
          contentEl.innerHTML = `<span style="color: var(--accent-complement);">Error: ${escapeHtml(err.message)}</span>`;
          sendBtn.disabled = false;
        }
      );
    });
  }
}

function setQueryAndSubmit(queryText) {
  const input = document.getElementById('chat-input');
  const form = document.getElementById('chat-form');
  if (input && form) {
    input.value = queryText;
    form.dispatchEvent(new Event('submit', { cancelable: true }));
  }
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}


// Settings Page Controller
async function initSettingsPage() {
  initTheme();
  const user = await API.fetchCurrentUser();
  const userSlot = document.getElementById('user-profile-slot');
  if (userSlot) {
    if (user && user.email) {
      userSlot.innerHTML = `
        <span style="font-size: var(--text-xs); color: var(--text-secondary);">${escapeHtml(user.name || user.email)}</span>
        <a href="/web/privacy.html" class="btn btn-secondary btn-sm">Privacy</a>
        <button id="logout-btn" class="btn btn-secondary btn-sm" type="button">Logout</button>
      `;
      document.getElementById('logout-btn')?.addEventListener('click', API.logout);
    } else {
      userSlot.innerHTML = `
        <a href="/web/privacy.html" class="btn btn-secondary btn-sm">Privacy</a>
        <a href="/web/login.html" class="btn btn-primary btn-sm">Login</a>
      `;
    }
  }

  // Load Retention Settings
  try {
    const settingsData = await API.getUserSettings();
    const days = settingsData.retention_days;
    if (days === 30) {
      const el = document.getElementById('retention-30');
      if (el) el.checked = true;
    } else if (days === 90) {
      const el = document.getElementById('retention-90');
      if (el) el.checked = true;
    } else if (days === 365) {
      const el = document.getElementById('retention-365');
      if (el) el.checked = true;
    } else {
      const el = document.getElementById('retention-forever');
      if (el) el.checked = true;
    }
  } catch (err) {
    console.error('Could not load user settings:', err);
  }

  // Retention Form Submission
  const retentionForm = document.getElementById('retention-form');
  const retentionStatus = document.getElementById('retention-status');
  if (retentionForm) {
    retentionForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const selected = document.querySelector('input[name="retention_days"]:checked');
      if (!selected) return;
      const days = parseInt(selected.value, 10);
      try {
        await API.updateUserSettings(days === 0 ? null : days);
        showToast('Retention policy updated successfully.');
        if (retentionStatus) {
          retentionStatus.textContent = days === 0 ? 'Data retention set to: Keep Forever.' : `Data retention set to: ${days} days.`;
        }
      } catch (err) {
        showToast(`Failed to update retention: ${err.message}`);
        if (retentionStatus) {
          retentionStatus.textContent = err.message;
        }
      }
    });
  }

  // Manual Retention Cleanup Button
  const runCleanupBtn = document.getElementById('run-cleanup-btn');
  if (runCleanupBtn) {
    runCleanupBtn.addEventListener('click', async () => {
      runCleanupBtn.disabled = true;
      runCleanupBtn.textContent = 'Running Cleanup...';
      try {
        const res = await API.runRetentionCleanup();
        showToast(`Cleanup completed: ${res.deleted_meetings} expired meetings removed.`);
      } catch (err) {
        showToast(`Cleanup error: ${err.message}`);
      } finally {
        runCleanupBtn.disabled = false;
        runCleanupBtn.textContent = 'Run Retention Cleanup Now';
      }
    });
  }

  // Delete All My Data Confirmation
  const confirmInput = document.getElementById('delete-confirm-input');
  const deleteAllBtn = document.getElementById('delete-all-btn');
  const deleteStatus = document.getElementById('delete-status');

  if (confirmInput && deleteAllBtn) {
    confirmInput.addEventListener('input', () => {
      const matches = confirmInput.value.trim() === 'DELETE ALL MY DATA';
      deleteAllBtn.disabled = !matches;
      if (matches) {
        deleteAllBtn.style.cursor = 'pointer';
        deleteAllBtn.style.opacity = '1';
      } else {
        deleteAllBtn.style.cursor = 'not-allowed';
        deleteAllBtn.style.opacity = '0.5';
      }
    });

    deleteAllBtn.addEventListener('click', async () => {
      if (confirmInput.value.trim() !== 'DELETE ALL MY DATA') return;
      if (!confirm('Are you absolutely certain? This will delete all your meetings, audio, and account permanently.')) {
        return;
      }
      deleteAllBtn.disabled = true;
      deleteAllBtn.textContent = 'Deleting All Data...';
      try {
        await API.deleteAllUserData('DELETE ALL MY DATA');
        showToast('All your data has been permanently deleted.');
        setTimeout(() => {
          window.location.href = '/web/login.html';
        }, 1200);
      } catch (err) {
        if (deleteStatus) deleteStatus.textContent = err.message;
        showToast(`Deletion failed: ${err.message}`);
        deleteAllBtn.disabled = false;
        deleteAllBtn.textContent = 'Permanently Delete All My Data';
      }
    });
  }
}

// Global initialization
document.addEventListener('DOMContentLoaded', () => {
  initTheme();
  const themeToggle = document.getElementById('theme-toggle-btn');
  if (themeToggle) {
    themeToggle.addEventListener('click', toggleTheme);
  }
});

