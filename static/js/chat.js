/**
 * Chat page: conversation with the grievance agent, the citizen's complaint list,
 * and the panel that explains how the agent read the latest message.
 */
import { $, ApiError, api, fmt, h, initHeader, kv, pill, render, session, signOut } from './common.js';

if (!session.token) window.location.replace('/');

const AGENT = 'CitizenVoice Agent';
const WELCOME = 'Welcome to CitizenVoice. Describe the problem you want to report, and mention the LGA or community where it is happening.';

const log = $('#chat-log');
const scroller = $('#chat-scroll');
const form = $('#composer');
const input = $('#composer-input');
const sendButton = $('#composer-send');
const complaintList = $('#my-complaints');
const perception = $('#perception');

let busy = false;

/* ------------------------------------------------------------------ conversation */

function scrollToEnd() {
  scroller.scrollTop = scroller.scrollHeight;
}

/** "Amina Yusuf" -> "AY" */
function initials(name) {
  const parts = String(name || '').trim().split(/\s+/).filter(Boolean);
  return (parts.slice(0, 2).map((p) => p[0]).join('') || 'You').toUpperCase();
}

const SENDERS = {
  agent: { name: AGENT, avatar: 'CV' },
  user: { name: session.name || 'You', avatar: initials(session.name) },
};

/**
 * Add one message to the single conversation column. Consecutive messages from the
 * same sender are grouped: only the first shows the avatar and name.
 * @param {string} text
 * @param {{from?: 'agent'|'user', variant?: ''|'alert'|'error'}} options
 */
function addMessage(text, { from = 'agent', variant = '' } = {}) {
  const last = log.lastElementChild;
  const continued = Boolean(last && last.classList.contains('msg') && last.dataset.from === from);
  const sender = SENDERS[from];
  const classes = ['msg', `msg--${from}`, variant && `msg--${variant}`, continued && 'msg--continued']
    .filter(Boolean)
    .join(' ');
  log.append(
    h(
      'div',
      { class: classes, dataset: { from } },
      continued ? null : h('span', { class: 'msg__avatar', 'aria-hidden': 'true' }, sender.avatar),
      h(
        'div',
        { class: 'msg__body' },
        continued ? null : h('span', { class: 'msg__author' }, sender.name),
        h('div', { class: 'msg__bubble' }, text),
      ),
    ),
  );
  scrollToEnd();
}

function showTyping() {
  const last = log.lastElementChild;
  const continued = Boolean(last && last.classList.contains('msg') && last.dataset.from === 'agent');
  const el = h(
    'div',
    { class: `msg msg--agent${continued ? ' msg--continued' : ''}`, dataset: { from: 'agent' }, 'aria-label': `${AGENT} is typing` },
    continued ? null : h('span', { class: 'msg__avatar', 'aria-hidden': 'true' }, 'CV'),
    h(
      'div',
      { class: 'msg__body' },
      continued ? null : h('span', { class: 'msg__author' }, AGENT),
      h('div', { class: 'msg__bubble typing' }, [1, 2, 3].map(() => h('span', { class: 'typing__dot' }))),
    ),
  );
  log.append(el);
  scrollToEnd();
  return el;
}

function showQuickReplies(options) {
  if (!options || !options.length) return;
  const group = h('div', { class: 'quick-replies', role: 'group', 'aria-label': 'Suggested replies' });
  for (const option of options) {
    group.append(h('button', { class: 'chip', type: 'button', onClick: () => send(option) }, option));
  }
  log.append(group);
  scrollToEnd();
}

function clearQuickReplies() {
  log.querySelectorAll('.quick-replies').forEach((el) => el.remove());
}

function updateSendState() {
  sendButton.disabled = busy || !input.value.trim();
}

async function send(text) {
  const message = text.trim();
  if (!message || busy) return;
  busy = true;
  updateSendState();
  clearQuickReplies();
  addMessage(message, { from: 'user' });
  const typing = showTyping();

  try {
    const reply = await api('/api/chat', { method: 'POST', body: { message } });
    typing.remove();
    const emergency = reply.action === 'ESCALATE_EMERGENCY';
    reply.messages.forEach((m, i) => addMessage(m, { variant: emergency && i < 2 ? 'alert' : '' }));
    showQuickReplies(reply.quick_replies);
    renderPerception(reply.analysis, reply.action);
    if (reply.ref) loadComplaints();
  } catch (err) {
    typing.remove();
    if (err instanceof ApiError && err.status === 401) {
      signOut('/');
      return;
    }
    addMessage(`${err.message} Your message was not delivered.`, { variant: 'error' });
  } finally {
    busy = false;
    updateSendState();
    input.focus();
  }
}

/* ------------------------------------------------------------------ perception panel */

function tagList(terms, warn = false) {
  if (!terms.length) return h('p', { class: 'muted small' }, 'None detected');
  return h('div', { class: 'tags' }, terms.map((t) => h('span', { class: warn ? 'tag tag--warn' : 'tag' }, t)));
}

function renderPerception(analysis, action) {
  const actionBadge = h(
    'div',
    { class: `action-badge${action === 'ESCALATE_EMERGENCY' ? ' action-badge--emergency' : ''}` },
    `Action: ${fmt.action(action)}`,
  );
  if (!analysis) {
    render(perception, actionBadge);
    return;
  }
  const a = analysis;
  const score = Math.max(0, Math.min(100, Number(a.score) || 0));
  const thresholds = a.thresholds || { medium: 22, high: 45 };

  const gauge = h(
    'div',
    {
      class: 'gauge',
      role: 'meter',
      'aria-label': 'Urgency score',
      'aria-valuemin': 0,
      'aria-valuemax': 100,
      'aria-valuenow': score,
      style: { '--t-medium': `${thresholds.medium}%`, '--t-high': `${thresholds.high}%` },
    },
    h('span', { class: 'gauge__marker', style: { left: `${score}%` } }),
  );

  render(
    perception,
    kv('Category', a.category),
    kv('Category confidence', fmt.pct(a.category_confidence, 0)),
    kv('Sentiment', pill(a.sentiment)),
    kv('Sentiment confidence', fmt.pct(a.sentiment_confidence, 0)),
    kv('VADER compound', a.vader_compound),
    kv('P(High urgency)', a.p_high),
    kv('Similar reports (24h)', a.cluster_count),
    h('p', { class: 'label-caps' }, `Urgency score ${a.score} / 100`),
    gauge,
    h('div', { class: 'gauge__legend', 'aria-hidden': 'true' }, h('span', {}, 'Low'), h('span', {}, 'Medium'), h('span', {}, 'High')),
    kv('Triage decision', pill(a.urgency, { label: a.urgency.toUpperCase() })),
    h('p', { class: 'label-caps' }, 'Danger signals perceived'),
    tagList(a.critical_terms),
    h('p', { class: 'label-caps' }, 'Vulnerable groups / immediacy'),
    tagList([...a.vulnerable_terms, ...a.immediacy_terms], true),
    actionBadge,
  );
}

/* ------------------------------------------------------------------ complaint list */

async function loadComplaints() {
  try {
    const complaints = await api('/api/my/complaints');
    if (!complaints.length) {
      render(complaintList, h('li', { class: 'empty' }, 'No complaints yet.'));
      return;
    }
    render(
      complaintList,
      complaints.map((c) =>
        h(
          'li',
          { class: 'complaint' },
          h('a', { class: 'complaint__ref', href: `/track?ref=${encodeURIComponent(c.ref)}` }, c.ref),
          h('div', { class: 'complaint__meta' }, h('span', {}, c.category), pill(c.urgency)),
          h('div', { class: 'complaint__status' }, `${c.status} · ${fmt.dateTime(c.created_at)}`),
        ),
      ),
    );
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) signOut('/');
  }
}

/* ------------------------------------------------------------------ composer */

const COMPOSER_MAX_HEIGHT = 200; // keep in sync with .composer__input max-height

function autoGrow() {
  input.style.height = 'auto';
  input.style.height = `${Math.min(input.scrollHeight, COMPOSER_MAX_HEIGHT)}px`;
  input.classList.toggle('is-scrollable', input.scrollHeight > COMPOSER_MAX_HEIGHT);
}

function submitComposer() {
  const text = input.value;
  if (!text.trim() || busy) return;
  input.value = '';
  autoGrow();
  send(text);
}

form.addEventListener('submit', (event) => {
  event.preventDefault();
  submitComposer();
});

input.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    submitComposer();
  }
});

input.addEventListener('input', () => {
  autoGrow();
  updateSendState();
});

/* ------------------------------------------------------------------ start */

initHeader({ signOutTo: '/' });
addMessage(WELCOME);
loadComplaints();
input.focus();
