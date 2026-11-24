/**
 * Home page: citizen sign in, citizen registration, and continuing an existing session.
 */
import { $, $$, api, h, initHeader, initPasswordToggles, initTabs, render, session, setBusy } from './common.js';

const loginForm = $('#panel-login');
const registerForm = $('#panel-register');
const lgaSelect = $('#reg-lga');

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
  if (data.get('password') !== data.get('confirm')) {
    $('[data-error]', registerForm).textContent = 'The two passwords do not match.';
    $('#reg-confirm').focus();
    return;
  }
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
  const link = $('#continue-link');
  link.href = session.role === 'admin' ? '/admin' : '/chat';
  link.textContent = session.role === 'admin' ? 'Continue to the officer console' : 'Continue to the chatbot';
  $('#signed-in-notice').hidden = false;
}

/* ------------------------------------------------------------------ start */

initTabs($$('[role="tab"]'), () => {
  $$('[data-error]').forEach((el) => {
    el.textContent = '';
  });
});
initPasswordToggles();
loginForm.addEventListener('submit', onLogin);
registerForm.addEventListener('submit', onRegister);
initHeader();
showExistingSession();
loadLgas();
