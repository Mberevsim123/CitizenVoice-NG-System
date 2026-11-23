/**
 * CitizenVoice NG - shared front-end module.
 *
 * - h():        builds DOM elements. Text is always inserted as text nodes, never parsed
 *               as HTML, so citizen-supplied content can never inject markup or script.
 * - api():      JSON fetch wrapper that adds the session token and raises ApiError.
 * - session:    sign-in state kept in localStorage.
 * - formatters: percentages, ages, dates and labels used across pages.
 */

const STORAGE = { token: 'cv_token', name: 'cv_name', role: 'cv_role' };

/* ------------------------------------------------------------------ DOM */

/**
 * Create an element.
 *   h('a', { class: 'link', href: '/x', onClick: fn }, 'text', childNode)
 * Props: class, text, style (object; keys starting with "--" set custom properties),
 * dataset (object), on<Event> (listener), boolean attributes (true/false), others as attributes.
 */
export function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') {
      el.className = value;
    } else if (key === 'text') {
      el.textContent = value;
    } else if (key === 'style') {
      for (const [prop, v] of Object.entries(value)) {
        if (prop.startsWith('--')) el.style.setProperty(prop, v);
        else el.style[prop] = v;
      }
    } else if (key === 'dataset') {
      Object.assign(el.dataset, value);
    } else if (key.startsWith('on') && typeof value === 'function') {
      el.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (value === true) {
      el.setAttribute(key, '');
    } else {
      el.setAttribute(key, String(value));
    }
  }
  append(el, children);
  return el;
}

function append(parent, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    parent.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

/** Replace all children of `parent`. */
export function render(parent, ...children) {
  parent.replaceChildren();
  append(parent, children);
}

export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

/* ------------------------------------------------------------------ session */

export const session = {
  get token() {
    return localStorage.getItem(STORAGE.token);
  },
  get name() {
    return localStorage.getItem(STORAGE.name) || '';
  },
  get role() {
    return localStorage.getItem(STORAGE.role) || '';
  },
  save({ token, name, role }) {
    localStorage.setItem(STORAGE.token, token);
    localStorage.setItem(STORAGE.name, name || '');
    localStorage.setItem(STORAGE.role, role || '');
  },
  clear() {
    Object.values(STORAGE).forEach((k) => localStorage.removeItem(k));
  },
};

/* ------------------------------------------------------------------ API */

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

/**
 * Call the CitizenVoice API.
 * @param {string} path      e.g. '/api/chat'
 * @param {object} options   { method, body, auth=true }
 * @returns parsed JSON body
 * @throws ApiError (status 0 for network failures)
 */
export async function api(path, { method = 'GET', body, auth = true } = {}) {
  const headers = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (auth && session.token) headers.Authorization = `Bearer ${session.token}`;

  let response;
  try {
    response = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, 'Network problem. Please check your connection and try again.');
  }

  let data = null;
  try {
    data = await response.json();
  } catch {
    /* empty or non-JSON body */
  }
  if (!response.ok) {
    const detail = data && typeof data.detail === 'string' ? data.detail : 'Something went wrong. Please try again.';
    throw new ApiError(response.status, detail);
  }
  return data;
}

/** Revoke the session on the server, clear it locally and leave the page. */
export async function signOut(redirectTo = '/') {
  try {
    if (session.token) await api('/api/logout', { method: 'POST' });
  } catch {
    /* sign out locally even if the server cannot be reached */
  }
  session.clear();
  window.location.assign(redirectTo);
}

/** Wire the shared header: user name and every [data-action="sign-out"] button. */
export function initHeader({ signOutTo = '/' } = {}) {
  $$('[data-user-name]').forEach((el) => {
    el.textContent = session.name;
  });
  $$('[data-action="sign-out"]').forEach((btn) => {
    btn.addEventListener('click', () => signOut(signOutTo));
  });
}

/** Disable a button while a request runs and show a spinner. */
export function setBusy(button, busy) {
  if (!button) return;
  if (busy) {
    button.dataset.label = button.textContent;
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    render(button, h('span', { class: 'spinner', 'aria-hidden': 'true' }), ' Please wait');
  } else {
    button.disabled = false;
    button.removeAttribute('aria-busy');
    if (button.dataset.label) button.textContent = button.dataset.label;
  }
}

/* ------------------------------------------------------------------ formatting */

export const fmt = {
  /** 0.8734 -> "87.3%" */
  pct: (value, digits = 1) => `${(Number(value || 0) * 100).toFixed(digits)}%`,
  /** hours -> "35 min", "5 h", "3 d" */
  age: (hours) => {
    if (hours < 1) return `${Math.round(hours * 60)} min`;
    if (hours < 48) return `${Math.round(hours)} h`;
    return `${Math.round(hours / 24)} d`;
  },
  /** "2026-11-23 09:12:45" -> "23 Nov 2026, 09:12" */
  dateTime: (value) => {
    if (!value) return '';
    const d = new Date(value.replace(' ', 'T'));
    if (Number.isNaN(d.getTime())) return value;
    return d.toLocaleString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  },
  /** "ESCALATE_EMERGENCY" -> "Escalate emergency" */
  action: (value) => {
    const s = String(value || '').toLowerCase().replace(/_/g, ' ');
    return s.charAt(0).toUpperCase() + s.slice(1);
  },
  capitalize: (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : ''),
  truncate: (s, n) => (s && s.length > n ? `${s.slice(0, n).trimEnd()}…` : s || ''),
};

/** A coloured pill for a priority, sentiment or status value. */
export function pill(value, { large = false, label } = {}) {
  const slug = String(value || '').toLowerCase().replace(/[^a-z]+/g, '-');
  return h('span', { class: `pill pill--${slug}${large ? ' pill--lg' : ''}` }, label ?? value);
}

/** A key / value row. `value` may be text or a node. */
export function kv(key, value) {
  return h('div', { class: 'kv' }, h('span', { class: 'kv__key' }, key), h('span', { class: 'kv__value' }, value));
}

/** Read a CSS custom property from :root (keeps chart colours in the stylesheet). */
export function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** Delay calls until input pauses (search boxes). */
export function debounce(fn, wait = 250) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}
