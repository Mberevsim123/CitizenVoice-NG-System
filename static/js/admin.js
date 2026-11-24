/**
 * Officer console: sign in / officer registration, KPIs, triage queue, complaint detail
 * drawer, analytics, agency routing, model performance and (super administrator only)
 * officer account approvals.
 */
import {
  $, $$, ApiError, api, cssVar, debounce, fmt, h, hidePasswords, initHeader, initPasswordToggles, initTabs, kv,
  pill, render, session, setBusy,
} from './common.js';

const CATEGORIES = ['Security', 'Fire', 'Flood/Emergency', 'Health', 'Electricity', 'Water', 'Roads', 'Corruption', 'Environment', 'Education'];
const UPDATE_STATUSES = ['Acknowledged', 'In Progress', 'Resolved', 'Closed'];
const SLA_TARGETS = 'Targets: High 2 h, Medium 24 h, Low 72 h';

const loginView = $('#login-view');
const consoleView = $('#main');
const consoleTabs = $$('.console-tabs [role="tab"]');
const authTabs = $$('.auth-card__tabs [role="tab"]');
const officersTab = $('#tab-officers');
const drawer = $('#drawer');
const drawerPanel = $('.drawer__panel', drawer);
const drawerBody = $('#drawer-body');
let lastFocus = null;
let me = null;

/* ------------------------------------------------------------------ sign in / register */

const selectAuthTab = initTabs(authTabs, () => {
  $$('[data-error]', loginView).forEach((el) => {
    el.textContent = '';
  });
});

async function loadSetupNotice() {
  const notice = $('#setup-notice');
  try {
    const { has_super_admin: hasSuper } = await api('/api/officers/setup', { auth: false });
    notice.textContent = hasSuper
      ? 'New officer accounts must be approved by the super administrator before they can sign in.'
      : 'No officer has registered yet. The first account created becomes the super administrator, '
        + 'who approves every officer that registers after.';
  } catch {
    notice.textContent = 'New officer accounts must be approved by the super administrator.';
  }
}

function showLogin() {
  consoleView.hidden = true;
  $('#sign-out').hidden = true;
  loginView.hidden = false;
  loadSetupNotice();
  $('#login-id').focus();
}

/** Run an officer API call; a 401/403 (expired session, not an officer) returns to sign in. */
async function officerApi(path, options) {
  try {
    return await api(path, options);
  } catch (err) {
    if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
      session.clear();
      showLogin();
    }
    throw err;
  }
}

async function onLogin(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const error = $('[data-error]', form);
  const button = $('[type="submit"]', form);
  const data = new FormData(form);
  error.textContent = '';
  setBusy(button, true);
  try {
    const result = await api('/api/login', {
      method: 'POST',
      auth: false,
      body: { phone: String(data.get('phone')).trim(), password: String(data.get('password')) },
    });
    if (result.role !== 'admin') {
      error.textContent = 'This is a citizen account. Use the citizen page to report a problem.';
      return;
    }
    session.save(result);
    form.reset();
    hidePasswords(form);
    await start();
  } catch (err) {
    error.textContent = err.message;
  } finally {
    setBusy(button, false);
  }
}

async function onRegister(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const error = $('[data-error]', form);
  const button = $('[type="submit"]', form);
  const data = new FormData(form);
  error.textContent = '';
  if (data.get('password') !== data.get('confirm')) {
    error.textContent = 'The two passwords do not match.';
    $('#off-confirm').focus();
    return;
  }
  setBusy(button, true);
  try {
    const result = await api('/api/officers/register', {
      method: 'POST',
      auth: false,
      body: {
        full_name: String(data.get('full_name')).trim(),
        email: String(data.get('email')).trim(),
        phone: String(data.get('phone')).trim(),
        password: String(data.get('password')),
      },
    });
    form.reset();
    hidePasswords(form);
    if (result.status === 'active') {
      // first officer: super administrator, signed in straight away
      session.save(result);
      await start();
      return;
    }
    selectAuthTab('login');
    const loginForm = $('#officer-login');
    const success = h('p', { class: 'alert alert--info form-success', role: 'status' }, result.message);
    $$('.form-success', loginForm).forEach((el) => el.remove());
    loginForm.prepend(success);
  } catch (err) {
    error.textContent = err.message;
  } finally {
    setBusy(button, false);
  }
}

/* ------------------------------------------------------------------ tabs */

const loaders = {
  queue: loadQueue, analytics: loadAnalytics, routing: loadRouting, model: loadModel, officers: loadOfficers,
};
const selectTab = initTabs(consoleTabs, (name) => {
  loaders[name]().catch(() => {});
});

/* ------------------------------------------------------------------ KPIs */

async function loadKpis() {
  const s = await officerApi('/api/admin/stats');
  const kpi = (label, value, alert = false) =>
    h('div', { class: `kpi${alert ? ' kpi--alert' : ''}` }, h('span', { class: 'kpi__label' }, label), h('span', { class: 'kpi__value' }, value));
  render(
    $('#kpis'),
    kpi('Total complaints', s.total),
    kpi('Open complaints', s.open),
    kpi('Open HIGH-urgency', s.high_open, true),
    kpi('Resolved / closed', s.resolved),
    kpi('Negative sentiment', `${s.negative_pct}%`),
  );
  return s;
}

/* ------------------------------------------------------------------ triage queue */

async function loadQueue() {
  const params = new URLSearchParams(new FormData($('#filters')));
  const rows = await officerApi(`/api/admin/complaints?${params}`);
  const body = $('#queue-rows');
  if (!rows.length) {
    render(body, h('tr', {}, h('td', { colspan: 8, class: 'empty' }, 'No complaints match these filters.')));
    return;
  }
  render(
    body,
    rows.map((c) =>
      h(
        'tr',
        {
          tabindex: 0,
          'aria-label': `${c.ref}, ${c.urgency} priority, ${c.status}`,
          onClick: () => openComplaint(c.id),
          onKeydown: (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              openComplaint(c.id);
            }
          },
        },
        h('td', {}, pill(c.urgency, { label: c.urgency.toUpperCase() }), h('div', { class: 'queue__sub' }, `score ${c.score}`)),
        h('td', { class: 'nowrap' }, h('strong', {}, c.ref)),
        h('td', { class: 'queue__text' }, fmt.truncate(c.text, 140)),
        h('td', {}, c.category, h('div', { class: 'queue__sub' }, c.agency)),
        h('td', {}, pill(c.sentiment)),
        h('td', {}, c.lga),
        h('td', {}, pill(c.status)),
        h('td', { class: 'nowrap' }, fmt.age(c.age_hours), c.overdue && h('div', { class: 'sla-breach' }, 'SLA BREACHED')),
      ),
    ),
  );
}

/* ------------------------------------------------------------------ complaint drawer */

function openDrawer() {
  lastFocus = document.activeElement;
  drawer.hidden = false;
  document.body.style.overflow = 'hidden';
  drawerPanel.focus();
}

function closeDrawer() {
  drawer.hidden = true;
  document.body.style.overflow = '';
  if (lastFocus) lastFocus.focus();
}

function tagList(terms, warn = false) {
  if (!terms || !terms.length) return h('p', { class: 'muted small' }, 'None');
  return h('div', { class: 'tags' }, terms.map((t) => h('span', { class: warn ? 'tag tag--warn' : 'tag' }, t)));
}

async function openComplaint(id) {
  const c = await officerApi(`/api/admin/complaints/${id}`);
  const a = c.analysis || {};
  const location = c.community ? `${c.lga} LGA · ${c.community}` : `${c.lga} LGA`;

  const statusForm = h(
    'form',
    { class: 'status-form', onSubmit: (e) => updateStatus(e, c.id) },
    h('label', { class: 'sr-only', for: 'status-select' }, 'New status'),
    h('select', { class: 'select', id: 'status-select', name: 'status' }, UPDATE_STATUSES.map((s) => h('option', {}, s))),
    h('label', { class: 'sr-only', for: 'status-note' }, 'Note to citizen'),
    h('input', { class: 'input', id: 'status-note', name: 'note', placeholder: 'Note to citizen', maxlength: 500 }),
    h('button', { class: 'btn btn--primary', type: 'submit' }, 'Save'),
  );

  render(
    drawerBody,
    h('header', { class: 'detail__header' }, h('h2', { class: 'detail__title', id: 'drawer-title' }, c.ref), pill(c.urgency, { large: true, label: `${c.urgency.toUpperCase()} PRIORITY` })),
    h('p', { class: 'detail__text' }, c.text),
    kv('Citizen', `${c.citizen} (${c.phone})`),
    kv('Location', location),
    kv('Routed to', c.agency),
    kv('Response target', `${c.sla_hours} hours`),
    kv('Status', pill(c.status)),

    h('h3', { class: 'detail__heading' }, 'Agent analysis (explainable triage)'),
    kv('Category (confidence)', `${a.category} (${fmt.pct(a.category_confidence, 0)})`),
    kv('Sentiment (confidence)', `${a.sentiment} (${fmt.pct(a.sentiment_confidence, 0)})`),
    kv('VADER compound score', a.vader_compound),
    kv('Urgency model P(High) / P(Medium)', `${a.p_high} / ${a.p_medium}`),
    kv('Similar reports in LGA (24h)', a.cluster_count),
    kv('Hybrid urgency score', `${a.score} / 100`),
    a.model_backend && kv('Model backend', a.model_backend),
    h('p', { class: 'label-caps' }, 'Danger signals'),
    tagList(a.critical_terms),
    h('p', { class: 'label-caps' }, 'Vulnerability / immediacy cues'),
    tagList([...(a.vulnerable_terms || []), ...(a.immediacy_terms || [])], true),

    h('h3', { class: 'detail__heading' }, 'Update status'),
    statusForm,
    h('p', { class: 'form-error', role: 'alert', id: 'status-error' }),

    h('h3', { class: 'detail__heading' }, 'Timeline'),
    c.history.map((e) =>
      h(
        'div',
        { class: 'history__item' },
        h('strong', {}, e.status),
        ` — ${e.note}`,
        h('div', { class: 'history__meta' }, `${fmt.dateTime(e.created_at)} · ${e.actor}`),
      ),
    ),

    h('h3', { class: 'detail__heading' }, 'Dispatch'),
    c.dispatch.length
      ? c.dispatch.map((d) => h('p', { class: 'dispatch__item' }, `${fmt.dateTime(d.created_at)} · ${d.channel}: ${d.message}`))
      : h('p', { class: 'muted small' }, 'No dispatch records.'),
  );
  if (drawer.hidden) openDrawer();
}

async function updateStatus(event, id) {
  event.preventDefault();
  const form = event.currentTarget;
  const data = new FormData(form);
  const button = $('[type="submit"]', form);
  setBusy(button, true);
  try {
    await officerApi(`/api/admin/complaints/${id}/status`, {
      method: 'POST',
      body: { status: data.get('status'), note: String(data.get('note') || '') },
    });
    await Promise.all([openComplaint(id), loadQueue(), loadKpis()]);
  } catch (err) {
    const error = $('#status-error');
    if (error) error.textContent = err.message;
    setBusy(button, false);
  }
}

/* ------------------------------------------------------------------ analytics */

function donut(items, colours) {
  const total = items.reduce((sum, x) => sum + x.n, 0) || 1;
  let acc = 0;
  const segments = items.map((x) => {
    const start = (acc / total) * 100;
    acc += x.n;
    return `${colours[x.k] || cssVar('--c-neutral')} ${start}% ${(acc / total) * 100}%`;
  });
  return h(
    'div',
    { class: 'donut' },
    h(
      'div',
      { class: 'donut__ring', role: 'img', 'aria-label': items.map((x) => `${x.k}: ${x.n}`).join(', '), style: { background: `conic-gradient(${segments.join(',')})` } },
      h('div', { class: 'donut__hole' }, total),
    ),
    h(
      'ul',
      { class: 'legend' },
      items.map((x) =>
        h(
          'li',
          { class: 'legend__item' },
          h('span', { class: 'legend__swatch', style: { background: colours[x.k] } }),
          `${x.k}: `,
          h('strong', {}, x.n),
          ` (${Math.round((100 * x.n) / total)}%)`,
        ),
      ),
    ),
  );
}

function hbars(items, alt = false) {
  const max = Math.max(1, ...items.map((x) => x.n));
  return items.map((x) =>
    h(
      'div',
      { class: 'hbar' },
      h('span', {}, x.k),
      h('div', { class: 'hbar__track' }, h('div', { class: `hbar__fill${alt ? ' hbar__fill--alt' : ''}`, style: { width: `${(100 * x.n) / max}%` } })),
      h('span', { class: 'hbar__value' }, x.n),
    ),
  );
}

function dailyChart(days) {
  const max = Math.max(1, ...days.map((d) => d.high + d.medium + d.low));
  const seg = (level, n) => h('div', { class: `daily__seg--${level}`, style: { height: `${(150 * n) / max}px` } });
  return h(
    'div',
    { class: 'daily', role: 'img', 'aria-label': 'Daily complaints by urgency' },
    days.map((d) =>
      h(
        'div',
        { class: 'daily__day', title: `${d.d}: ${d.high} high, ${d.medium} medium, ${d.low} low` },
        h('div', { class: 'daily__stack' }, seg('low', d.low), seg('medium', d.medium), seg('high', d.high)),
        h('span', { class: 'daily__label' }, d.d.slice(5)),
      ),
    ),
  );
}

function card(title, ...content) {
  return h('div', { class: 'card' }, h('h3', { class: 'card__title' }, title), content);
}

async function loadAnalytics() {
  const s = await loadKpis();
  const urgencyColours = { High: cssVar('--c-high'), Medium: cssVar('--c-medium'), Low: cssVar('--c-low') };
  const sentimentColours = { Negative: cssVar('--c-negative'), Neutral: cssVar('--c-neutral'), Positive: cssVar('--c-positive') };
  const ack = s.ack_hours.length
    ? s.ack_hours.map((x) => kv(pill(x.urgency), `${Number(x.hrs).toFixed(1)} hours`))
    : h('p', { class: 'empty' }, 'No acknowledgements yet.');

  render(
    $('#panel-analytics'),
    h(
      'div',
      { class: 'grid-3' },
      card('Complaints by urgency', donut(s.by_urgency, urgencyColours)),
      card('Complaints by sentiment', donut(s.by_sentiment, sentimentColours)),
      card('Average time to acknowledgement', ack, h('p', { class: 'muted small mt' }, SLA_TARGETS)),
    ),
    h('div', { class: 'mt' }, card('Daily complaints by urgency (last 14 days)', dailyChart(s.daily))),
    h('div', { class: 'grid-2 mt' }, card('Complaints by category', hbars(s.by_category)), card('Complaints by LGA', hbars(s.by_lga, true))),
  );
}

/* ------------------------------------------------------------------ routing */

function table(headers, rows) {
  return h(
    'div',
    { class: 'table-wrap' },
    h(
      'table',
      { class: 'table' },
      h('thead', {}, h('tr', {}, headers.map((t) => h('th', { scope: 'col' }, t)))),
      h('tbody', {}, rows),
    ),
  );
}

async function loadRouting() {
  const d = await officerApi('/api/admin/dispatch');
  render(
    $('#panel-routing'),
    h(
      'div',
      { class: 'card card--flush' },
      table(
        ['Agency', 'Handles', 'Total routed', 'High urgency', 'Open'],
        d.agencies.map((a) =>
          h('tr', {}, h('td', {}, h('strong', {}, a.code), ` — ${a.name}`), h('td', {}, a.category), h('td', {}, a.total), h('td', {}, pill('High', { label: a.high || 0 })), h('td', {}, a.open || 0)),
        ),
      ),
    ),
    h('h3', { class: 'section-title' }, 'Recent dispatches'),
    h(
      'div',
      { class: 'card card--flush' },
      table(
        ['Time', 'Reference', 'Agency', 'Channel', 'Dispatch message'],
        d.log.slice(0, 12).map((l) =>
          h('tr', {}, h('td', { class: 'nowrap' }, fmt.dateTime(l.created_at)), h('td', { class: 'nowrap' }, h('strong', {}, l.ref)), h('td', {}, l.code), h('td', {}, l.channel), h('td', { class: 'queue__text' }, l.message)),
        ),
      ),
    ),
  );
}

/* ------------------------------------------------------------------ model performance */

function metricRow(name, metrics, highlight, f1Key = 'f1') {
  return h(
    'tr',
    { class: highlight ? 'is-highlight' : null },
    h('td', {}, name),
    h('td', {}, fmt.pct(metrics.accuracy)),
    h('td', {}, fmt.pct(metrics[f1Key])),
    h('td', {}, fmt.pct(metrics.high_precision)),
    h('td', {}, fmt.pct(metrics.high_recall)),
  );
}

function finetuneSection(m) {
  const f = m.finetune;
  if (!f) return null;
  const variants = ['Baseline TF-IDF', 'Tuned TF-IDF', 'Fine-tuned MiniLM', 'Served (blend)'];
  const tr = f.transformer;
  return h(
    'div',
    { class: 'card' },
    h(
      'h3',
      { class: 'card__title' },
      'Fine-tuned models ',
      h('span', { class: 'card__subtitle' }, `(${f.budget_minutes}-minute budget: ${tr.epochs} epochs of ${tr.base_model}, best epoch ${tr.best_epoch}; selection on ${f.n_dev} dev complaints)`),
    ),
    table(
      ['Task (test macro F1)', ...variants, 'Now serving'],
      Object.entries(f.test).map(([task, v]) =>
        h(
          'tr',
          {},
          h('td', {}, h('strong', {}, fmt.capitalize(task))),
          variants.map((name) => h('td', { class: name === 'Served (blend)' ? 'kv__value' : null }, fmt.pct(v[name].f1))),
          h('td', { class: 'serving-cell' }, m.selected[task] || ''),
        ),
      ),
    ),
    h(
      'h3',
      { class: 'card__title mt' },
      'Urgency triage after fine-tuning ',
      h('span', { class: 'card__subtitle' }, `(High if score ≥ ${f.thresholds.high}, Medium if ≥ ${f.thresholds.medium}; ${f.triage_ms_per_message} ms per message)`),
    ),
    table(
      ['Strategy', 'Accuracy', 'Macro F1', 'High precision', 'High recall'],
      Object.entries(f.triage).map(([name, v]) => metricRow(name, v, name.includes('fine-tuned'))),
    ),
  );
}

async function loadModel() {
  const m = await officerApi('/api/admin/model');
  const algorithms = ['Naive Bayes', 'Logistic Regression', 'Linear SVM'];
  const baselineCards = Object.entries(m.tasks).map(([task, v]) =>
    h(
      'div',
      { class: 'card card--flush model-card' },
      h('h3', { class: 'card__title model-card__title' }, `${fmt.capitalize(task)} classifier `, h('span', { class: 'card__subtitle' }, `(selected: ${v.selected})`)),
      table(
        ['Algorithm', 'Accuracy', 'Precision', 'Recall', 'F1'],
        algorithms.map((name) =>
          h(
            'tr',
            { class: name === v.selected ? 'is-highlight' : null },
            h('td', {}, name),
            h('td', {}, fmt.pct(v[name].accuracy)),
            h('td', {}, fmt.pct(v[name].precision)),
            h('td', {}, fmt.pct(v[name].recall)),
            h('td', {}, fmt.pct(v[name].f1)),
          ),
        ),
      ),
    ),
  );

  render(
    $('#panel-model'),
    h('p', { class: 'muted' }, `Trained on ${m.n_train} annotated complaints, evaluated on ${m.n_test} held-out complaints. Serving backend: `, h('strong', {}, m.backend), '.'),
    h('div', { class: 'mt' }, finetuneSection(m)),
    h('h3', { class: 'section-title' }, 'Baseline classical models'),
    h('div', { class: 'grid-3' }, baselineCards),
    h(
      'div',
      { class: 'card mt' },
      h('h3', { class: 'card__title' }, 'Urgency triage strategies (baseline models)'),
      table(
        ['Strategy', 'Accuracy', 'Macro F1', 'High precision', 'High recall'],
        Object.entries(m.triage).map(([name, v]) => metricRow(name, v, name.startsWith('Hybrid'), 'macro_f1')),
      ),
    ),
  );
}

/* ------------------------------------------------------------------ officer accounts */

function officerActions(o) {
  if (o.is_super) return h('span', { class: 'muted small' }, 'Super administrator');
  const decide = async (action, button) => {
    const verb = action === 'approve' ? 'Approve' : 'Reject';
    if (!window.confirm(`${verb} ${o.full_name}'s officer account?`)) return;
    setBusy(button, true);
    try {
      await officerApi(`/api/admin/officers/${o.id}`, { method: 'POST', body: { action } });
      await loadOfficers();
    } catch (err) {
      setBusy(button, false);
      window.alert(err.message);
    }
  };
  const approve = h('button', { class: 'btn btn--primary btn--sm', type: 'button' }, 'Approve');
  const reject = h('button', { class: 'btn btn--ghost btn--danger btn--sm', type: 'button' }, 'Reject');
  approve.addEventListener('click', () => decide('approve', approve));
  reject.addEventListener('click', () => decide('reject', reject));
  if (o.status === 'pending') return h('div', { class: 'officer-actions' }, approve, reject);
  if (o.status === 'active') return h('div', { class: 'officer-actions' }, reject);
  return h('div', { class: 'officer-actions' }, approve);
}

function updatePendingBadge(officers) {
  const pending = officers.filter((o) => o.status === 'pending').length;
  const badge = $('#pending-badge');
  badge.hidden = pending === 0;
  badge.textContent = pending;
  badge.setAttribute('aria-label', `${pending} awaiting approval`);
}

async function loadOfficers() {
  const officers = await officerApi('/api/admin/officers');
  updatePendingBadge(officers);
  const pending = officers.filter((o) => o.status === 'pending');
  const statusLabel = { active: 'Active', pending: 'Awaiting approval', rejected: 'Rejected' };
  render(
    $('#panel-officers'),
    pending.length
      ? h('p', { class: 'alert alert--info' }, `${pending.length} officer account(s) awaiting your approval.`)
      : h('p', { class: 'muted' }, 'No officer accounts are awaiting approval.'),
    h(
      'div',
      { class: 'card card--flush mt' },
      h(
        'div',
        { class: 'table-wrap' },
        h(
          'table',
          { class: 'table' },
          h('thead', {}, h('tr', {}, ['Officer', 'Email', 'Phone', 'Registered', 'Status', 'Decision'].map((t) => h('th', { scope: 'col' }, t)))),
          h(
            'tbody',
            {},
            officers.map((o) =>
              h(
                'tr',
                { class: o.status === 'pending' ? 'is-pending' : null },
                h('td', {}, h('strong', {}, o.full_name), o.is_super && h('div', { class: 'queue__sub' }, 'Super administrator')),
                h('td', {}, o.email),
                h('td', { class: 'nowrap' }, o.phone),
                h('td', { class: 'nowrap' }, fmt.dateTime(o.created_at)),
                h(
                  'td',
                  {},
                  pill(o.status === 'active' ? 'Resolved' : o.status === 'pending' ? 'Medium' : 'High', { label: statusLabel[o.status] }),
                  o.approved_by_name && o.status !== 'pending'
                    && h('div', { class: 'queue__sub' }, `by ${o.approved_by_name}`),
                ),
                h('td', {}, officerActions(o)),
              ),
            ),
          ),
        ),
      ),
    ),
  );
}

/* ------------------------------------------------------------------ start */

async function start() {
  try {
    me = await officerApi('/api/me');
    if (me.role !== 'admin') throw new Error('not an officer');
    await loadKpis();
  } catch {
    session.clear();
    showLogin();
    return;
  }
  loginView.hidden = true;
  consoleView.hidden = false;
  $('#sign-out').hidden = false;
  initHeader({ signOutTo: '/admin' });
  officersTab.hidden = !me.is_super;
  if (me.is_super) officerApi('/api/admin/officers').then(updatePendingBadge).catch(() => {});
  selectTab('queue');
}

function bindEvents() {
  $('#officer-login').addEventListener('submit', onLogin);
  $('#officer-register').addEventListener('submit', onRegister);
  initPasswordToggles(loginView);
  const filters = $('#filters');
  const reload = () => loadQueue().catch(() => {});
  filters.addEventListener('change', reload);
  $('#filter-q').addEventListener('input', debounce(reload, 250));
  filters.addEventListener('submit', (e) => {
    e.preventDefault();
    reload();
  });
  $$('[data-close-drawer]', drawer).forEach((el) => el.addEventListener('click', closeDrawer));
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !drawer.hidden) closeDrawer();
  });
  $('#filter-category').append(...CATEGORIES.map((c) => h('option', {}, c)));
}

bindEvents();
if (session.token) start();
else showLogin();
