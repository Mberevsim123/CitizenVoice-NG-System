/**
 * Home page: sign in, create an account, and continue an existing session.
 */
import { $, $$, api, h, initHeader, render, session, setBusy } from './common.js';

const tabs = $$('[role="tab"]');
const loginForm = $('#panel-login');
const registerForm = $('#panel-register');
const lgaSelect = $('#reg-lga');

/* ------------------------------------------------------------------ tabs */

function selectTab(name) {
  for (const tab of tabs) {
    const active = tab.dataset.tab === name;
    tab.classList.toggle('is-active', active);
    tab.setAttribute('aria-selected', String(active));
    tab.tabIndex = active ? 0 : -1;
    $(`#${tab.getAttribute('aria-controls')}`).hidden = !active;
  }
}

function onTabKeydown(event) {
  if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return;
  const index = tabs.indexOf(event.currentTarget);
  const next = tabs[(index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length];
  selectTab(next.dataset.tab);
  next.focus();
}

/* ------------------------------------------------------------------ forms */

async function submitAuth(form, path, payload) {
  const error = $('[data-error]', form);
  const button = $('[type="submit"]', form);
  error.textContent = '';
  setBusy(button, true);
  try {
    const result = await api(path, { method: 'POST', body: payload, auth: false });
    session.save(result);
    window.location.assign(result.role === 'admin' ? '/admin' : '/chat');
  } catch (err) {
    error.textContent = err.message;
    setBusy(button, false);
  }
}

function onLogin(event) {
  event.preventDefault();
  const data = new FormData(loginForm);
  submitAuth(loginForm, '/api/login', {
    phone: String(data.get('phone')).trim(),
    password: String(data.get('password')),
  });
}

function onRegister(event) {
  event.preventDefault();
  const data = new FormData(registerForm);
  submitAuth(registerForm, '/api/register', {
    full_name: String(data.get('full_name')).trim(),
    phone: String(data.get('phone')).trim(),
    email: String(data.get('email')).trim(),
    lga: String(data.get('lga') || ''),
    password: String(data.get('password')),
  });
}

async function loadLgas() {
  try {
    const lgas = await api('/api/lgas', { auth: false });
    render(
      lgaSelect,
      h('option', { value: '', disabled: true, selected: true }, 'Select your LGA'),
      lgas.map((lga) => h('option', { value: lga }, lga)),
    );
  } catch {
    render(lgaSelect, h('option', { value: '', disabled: true, selected: true }, 'Could not load LGAs - refresh the page'));
  }
}

function showExistingSession() {
  if (!session.token) return;
  $('#continue-link').href = session.role === 'admin' ? '/admin' : '/chat';
  $('#continue-link').textContent = session.role === 'admin' ? 'Continue to the officer console' : 'Continue to the chatbot';
  $('#signed-in-notice').hidden = false;
}

/* ------------------------------------------------------------------ start */

tabs.forEach((tab) => {
  tab.addEventListener('click', () => selectTab(tab.dataset.tab));
  tab.addEventListener('keydown', onTabKeydown);
});
loginForm.addEventListener('submit', onLogin);
registerForm.addEventListener('submit', onRegister);
initHeader();
showExistingSession();
loadLgas();
