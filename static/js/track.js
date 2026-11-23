/**
 * Track page: look up a complaint by reference and show its progress timeline.
 * Signed-in owners (and officers) also see the complaint text.
 */
import { $, api, fmt, h, pill, render, setBusy } from './common.js';

const form = $('#track-form');
const input = $('#track-ref');
const result = $('#track-result');

function summaryItem(label, value) {
  return h(
    'div',
    { class: 'summary-grid__item' },
    h('span', { class: 'summary-grid__label' }, label),
    h('span', { class: 'summary-grid__value' }, value),
  );
}

function renderComplaint(c) {
  render(
    result,
    h(
      'article',
      { class: 'card', 'aria-labelledby': 'result-ref' },
      h(
        'header',
        { class: 'result__header' },
        h('h2', { class: 'result__ref', id: 'result-ref' }, c.ref),
        pill(c.status, { large: true }),
      ),
      c.owner_view
        ? h('p', { class: 'result__text' }, c.text)
        : h('p', { class: 'alert alert--info result__note' }, 'Sign in as the citizen who filed this complaint to see its full text.'),
      h(
        'div',
        { class: 'summary-grid' },
        summaryItem('Category', c.category),
        summaryItem('Priority', pill(c.urgency)),
        summaryItem('Location', `${c.lga} LGA`),
        summaryItem('Response target', `${c.sla_hours} hours`),
      ),
      h('p', { class: 'muted' }, 'Handled by ', h('strong', {}, c.agency)),
      h('h3', { class: 'section-title' }, 'Progress'),
      h(
        'ol',
        { class: 'timeline' },
        c.history.map((e) =>
          h(
            'li',
            { class: 'timeline__item' },
            h('div', { class: 'timeline__status' }, e.status),
            h('div', {}, e.note),
            h('div', { class: 'timeline__meta' }, `${fmt.dateTime(e.created_at)} · ${e.actor}`),
          ),
        ),
      ),
    ),
  );
}

async function track(ref) {
  const value = ref.trim().toUpperCase();
  if (!value) return;
  const button = $('[type="submit"]', form);
  setBusy(button, true);
  try {
    const complaint = await api(`/api/track/${encodeURIComponent(value)}`);
    renderComplaint(complaint);
    const url = new URL(window.location.href);
    url.searchParams.set('ref', value);
    window.history.replaceState(null, '', url);
  } catch (err) {
    render(result, h('p', { class: 'alert alert--danger', role: 'alert' }, err.message));
  } finally {
    setBusy(button, false);
  }
}

form.addEventListener('submit', (event) => {
  event.preventDefault();
  track(input.value);
});

const initial = new URLSearchParams(window.location.search).get('ref');
if (initial) {
  input.value = initial;
  track(initial);
}
