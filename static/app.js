/**
 * app.js — Multi-Step Autonomous Agent Chat UI
 * Zero-dependency vanilla JS. Talks to FastAPI /chat and /approve.
 */

// ── State ───────────────────────────────────────────────────────────────────
const state = {
  domain: 'retail',
  threadId: null,
  waitingApproval: false,
  apiKey: localStorage.getItem('agentApiKey') || '',
};

// ── Sample prompts per domain ────────────────────────────────────────────────
const SAMPLE_PROMPTS = {
  retail: [
    'Where is my order ORD-1001?',
    'Can I return my headphones?',
    'Process a refund for order ORD-1003',
  ],
  airline: [
    'What is the status of flight AA101?',
    'Rebook flight FL-200 to the next available',
    'What meals are on flight FL-300?',
  ],
  telecom: [
    'Show me my current data usage',
    'Upgrade my plan to Unlimited Pro',
    'What add-ons are available?',
  ],
};

// ── DOM refs ─────────────────────────────────────────────────────────────────
const messagesEl      = document.getElementById('messages');
const userInput       = document.getElementById('user-input');
const sendBtn         = document.getElementById('send-btn');
const approvalCard    = document.getElementById('approval-card');
const approvalPrompt  = document.getElementById('approval-prompt');
const btnApprove      = document.getElementById('btn-approve');
const btnReject       = document.getElementById('btn-reject');
const threadIdDisplay = document.getElementById('thread-id-display');
const domainDisplay   = document.getElementById('domain-display');
const newThreadBtn    = document.getElementById('new-thread-btn');
const sbDomain        = document.getElementById('sb-domain');
const sbSteps         = document.getElementById('sb-steps');
const sbEscalated     = document.getElementById('sb-escalated');
const sbFlags         = document.getElementById('sb-flags');
const samplePromptsEl = document.getElementById('sample-prompts');
const apiKeyBanner    = document.getElementById('api-key-banner');
const apiKeyInput     = document.getElementById('api-key-input');
const apiKeySave      = document.getElementById('api-key-save');

// ── Init ─────────────────────────────────────────────────────────────────────
function init() {
  // Show API key banner if no key stored
  if (!state.apiKey) {
    apiKeyBanner.classList.remove('hidden');
  }
  apiKeyInput.value = state.apiKey;

  renderSamplePrompts(state.domain);
  setupListeners();
}

// ── Listeners ─────────────────────────────────────────────────────────────────
function setupListeners() {
  // Domain pills
  document.getElementById('domain-pills').addEventListener('click', (e) => {
    const pill = e.target.closest('[data-domain]');
    if (!pill) return;
    const domain = pill.dataset.domain;
    setDomain(domain);
  });

  // Send button
  sendBtn.addEventListener('click', handleSend);

  // Enter to send (Shift+Enter = newline)
  userInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  });

  // Auto-grow textarea
  userInput.addEventListener('input', () => {
    userInput.style.height = 'auto';
    userInput.style.height = Math.min(userInput.scrollHeight, 140) + 'px';
  });

  // Approve / Reject
  btnApprove.addEventListener('click', () => handleApproval(true));
  btnReject.addEventListener('click',  () => handleApproval(false));

  // New thread
  newThreadBtn.addEventListener('click', startNewThread);

  // API key save
  apiKeySave.addEventListener('click', () => {
    state.apiKey = apiKeyInput.value.trim();
    localStorage.setItem('agentApiKey', state.apiKey);
    apiKeyBanner.classList.add('hidden');
  });
}

// ── Domain ────────────────────────────────────────────────────────────────────
function setDomain(domain) {
  state.domain = domain;
  domainDisplay.textContent = domain;
  sbDomain.textContent = domain;

  // Update pills
  document.querySelectorAll('.domain-pill').forEach((p) => {
    p.classList.toggle('active', p.dataset.domain === domain);
  });

  renderSamplePrompts(domain);
}

function renderSamplePrompts(domain) {
  samplePromptsEl.innerHTML = '';
  (SAMPLE_PROMPTS[domain] || []).forEach((prompt) => {
    const btn = document.createElement('button');
    btn.className = 'prompt-chip';
    btn.textContent = prompt;
    btn.addEventListener('click', () => {
      userInput.value = prompt;
      userInput.dispatchEvent(new Event('input'));
      userInput.focus();
    });
    samplePromptsEl.appendChild(btn);
  });
}

// ── Chat ──────────────────────────────────────────────────────────────────────
async function handleSend() {
  const text = userInput.value.trim();
  if (!text || state.waitingApproval) return;

  appendBubble('user', text);
  userInput.value = '';
  userInput.style.height = 'auto';
  setSending(true);

  const typingId = appendTyping();

  try {
    const body = {
      message: text,
      domain: state.domain,
      ...(state.threadId && { thread_id: state.threadId }),
    };

    const data = await apiPost('/chat', body);
    removeTyping(typingId);

    // Store thread
    if (!state.threadId) {
      state.threadId = data.thread_id;
      threadIdDisplay.textContent = shortId(data.thread_id);
    }

    appendBubble('agent', data.reply);
    updateSidebar(data);

    if (data.requires_approval) {
      showApprovalCard(data.approval_payload);
    }

  } catch (err) {
    removeTyping(typingId);
    appendError(err.message);
  } finally {
    setSending(false);
  }
}

// ── Approval ──────────────────────────────────────────────────────────────────
function showApprovalCard(payload) {
  state.waitingApproval = true;
  const prompt = (payload && payload.prompt) ? payload.prompt : JSON.stringify(payload);
  approvalPrompt.textContent = prompt;
  approvalCard.classList.remove('hidden');
  sendBtn.disabled = true;
  userInput.disabled = true;
}

function hideApprovalCard() {
  state.waitingApproval = false;
  approvalCard.classList.add('hidden');
  sendBtn.disabled = false;
  userInput.disabled = false;
}

async function handleApproval(approved) {
  hideApprovalCard();
  const typingId = appendTyping();

  try {
    const data = await apiPost('/approve', {
      thread_id: state.threadId,
      approved,
    });
    removeTyping(typingId);
    appendBubble('agent', data.reply || (approved ? '✅ Action approved and executed.' : '❌ Action rejected.'));
    updateSidebar(data);
  } catch (err) {
    removeTyping(typingId);
    appendError(err.message);
  }
}

// ── New Thread ────────────────────────────────────────────────────────────────
function startNewThread() {
  state.threadId = null;
  state.waitingApproval = false;
  threadIdDisplay.textContent = '—';
  hideApprovalCard();
  clearSidebar();

  // Clear messages and add welcome bubble again
  messagesEl.innerHTML = '';
  appendBubble('agent', '🔄 New conversation started. What can I help you with?');
}

// ── API ───────────────────────────────────────────────────────────────────────
async function apiPost(path, body) {
  const headers = { 'Content-Type': 'application/json' };
  if (state.apiKey) headers['X-API-Key'] = state.apiKey;

  const res = await fetch(path, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
  });

  if (res.status === 401) {
    // Show API key banner so user can enter their key
    apiKeyBanner.classList.remove('hidden');
    throw new Error('Authentication failed. Please enter your API key above.');
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }

  return res.json();
}

// ── DOM helpers ───────────────────────────────────────────────────────────────
function appendBubble(role, text) {
  const isAgent = role === 'agent';
  const wrapper = document.createElement('div');
  wrapper.className = `flex ${isAgent ? 'justify-start' : 'justify-end'}`;

  const bubble = document.createElement('div');
  bubble.className = `max-w-lg rounded-2xl px-4 py-3 text-sm leading-relaxed whitespace-pre-wrap ${
    isAgent
      ? 'agent-bubble rounded-tl-sm'
      : 'user-bubble rounded-tr-sm'
  }`;

  if (isAgent) {
    const label = document.createElement('p');
    label.className = 'font-medium text-indigo-300 mb-1 text-xs uppercase tracking-wide';
    label.textContent = 'AI Agent';
    bubble.appendChild(label);
  }

  const content = document.createElement('p');
  content.textContent = text;
  bubble.appendChild(content);
  wrapper.appendChild(bubble);
  messagesEl.appendChild(wrapper);
  scrollBottom();
  return wrapper;
}

function appendTyping() {
  const id = 'typing-' + Date.now();
  const wrapper = document.createElement('div');
  wrapper.id = id;
  wrapper.className = 'flex justify-start';
  wrapper.innerHTML = `
    <div class="agent-bubble rounded-2xl rounded-tl-sm px-4 py-3 flex items-center gap-1">
      <span class="typing-dot"></span>
      <span class="typing-dot"></span>
      <span class="typing-dot"></span>
    </div>`;
  messagesEl.appendChild(wrapper);
  scrollBottom();
  return id;
}

function removeTyping(id) {
  const el = document.getElementById(id);
  if (el) el.remove();
}

function appendError(message) {
  const wrapper = document.createElement('div');
  wrapper.className = 'flex justify-center';
  const pill = document.createElement('div');
  pill.className = 'bg-red-900/60 border border-red-700 text-red-300 text-xs rounded-full px-4 py-1';
  pill.textContent = '⚠ ' + message;
  wrapper.appendChild(pill);
  messagesEl.appendChild(wrapper);
  scrollBottom();
}

function scrollBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function setSending(loading) {
  sendBtn.disabled = loading || state.waitingApproval;
  sendBtn.textContent = loading ? '…' : 'Send';
}

// ── Sidebar helpers ───────────────────────────────────────────────────────────
function updateSidebar(data) {
  if (data.step_count !== undefined) sbSteps.textContent = data.step_count;
  if (data.escalated !== undefined) {
    sbEscalated.textContent = data.escalated ? 'Yes 🚨' : 'No';
    sbEscalated.className   = data.escalated ? 'text-red-400' : 'text-gray-300';
  }
  if (data.guardrail_flags && data.guardrail_flags.length) {
    sbFlags.innerHTML = data.guardrail_flags
      .map((f) => `<span class="flag-badge">${escHtml(f)}</span>`)
      .join('');
  }
}

function clearSidebar() {
  sbSteps.textContent = '0';
  sbEscalated.textContent = 'No';
  sbEscalated.className = 'text-gray-300';
  sbFlags.innerHTML = '<span class="text-gray-500 italic">None triggered</span>';
}

function shortId(id) {
  return id ? id.substring(0, 8) + '…' : '—';
}

function escHtml(str) {
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ── Boot ──────────────────────────────────────────────────────────────────────
init();
