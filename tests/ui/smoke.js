// A smoke test of the page in Pulsar's real window (macos/ui_smoke.sh runs it). The window's test build runs this file
// as the body of an async function once the day has loaded, and writes what it returns to ui.json.
//
// It clicks and reads only: nothing is written to Productive, to the settings or to the day's marks, and whatever it
// changes on the page it puts back. Each check is {name, ok, detail}; a check that cannot run on this day (no entry
// logged, no break) says "skipped" in its detail and counts as passed.

const out = [];
const check = (name, ok, detail = '') => out.push({name, ok: !!ok, detail: String(detail)});
const wait = ms => new Promise(r => setTimeout(r, ms));
const until = async (test, ms = 8000) => {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    try { if (test()) return true; } catch (e) { /* not there yet */ }
    await wait(100);
  }
  return false;
};
const q = s => document.querySelector(s);
const errors = [];
addEventListener('error', ev => errors.push(ev.message));
// a click at a point, as a person makes it: pressed and released there
const clickAt = (el, x, y) => {
  el.dispatchEvent(new PointerEvent('pointerdown', {bubbles: true, cancelable: true, clientX: x, clientY: y}));
  el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, clientX: x, clientY: y}));
};

try {
  check('the day loaded', await until(() => state.act && state.logged && state.likely), JSON.stringify({
    act: !!state.act, logged: !!state.logged, likely: !!state.likely}));
  check('signed in', !q('#signin').open);

  // every dialog shows its ✕ (Pulsar's window used to hide them)
  const hidden = [...document.querySelectorAll('dialog .dh [aria-label="Close"]')]
    .filter(b => getComputedStyle(b).display === 'none').map(b => b.closest('dialog').id);
  check('every dialog has a visible ✕', !hidden.length, hidden.join(', ') || 'all visible');

  // a click on the backdrop closes a dialog; a press inside that ends outside does not
  const keys = q('#keysdlg');
  keys.showModal();
  clickAt(keys, 2, 2);
  check('a click outside a dialog closes it', !keys.open);
  keys.showModal();
  const r = keys.getBoundingClientRect(), inner = keys.querySelector('dl') || keys;
  inner.dispatchEvent(new PointerEvent('pointerdown', {bubbles: true, clientX: r.left + 20, clientY: r.top + 40}));
  keys.dispatchEvent(new MouseEvent('click', {bubbles: true, clientX: 2, clientY: 2}));
  check('a drag from inside a dialog to outside keeps it open', keys.open);
  keys.close();

  // Settings closes from outside through its own ✕ (which asks only when something changed)
  q('#open-settings').click();
  await until(() => q('#settings').open, 3000);
  clickAt(q('#settings'), 2, 2);
  await until(() => !q('#settings').open, 3000);
  check('Settings closes on a click outside', !q('#settings').open);

  // the error log: Mark as seen shows only while there are errors to mark
  openLogs(1);
  await until(() => q('#logsdlg').open, 3000);
  check('Mark as seen shows only with errors', q('#logs-seen').hidden === !CONFIG.errors24h,
    `errors24h ${CONFIG.errors24h}, button ${q('#logs-seen').hidden ? 'hidden' : 'shown'}`);
  q('#logsdlg').close();

  // a pause or time away: named, and a click only says what it is
  const band = q('.awayband');
  if (band) {
    const label = [...document.querySelectorAll('.awaylabel')].map(x => x.textContent);
    check('breaks are named', label.some(t => /^(Pause|Away|Off) · /.test(t)), label.join(' | ') || 'no label shown');
    const b = band.getBoundingClientRect();
    band.dispatchEvent(new MouseEvent('click', {bubbles: true, clientX: b.left + 2, clientY: b.top + 4}));
    await wait(200);
    const pop = q('#pop');
    check('a break offers no logging', pop.classList.contains('on') && /^(Pause|Away|Off)$/.test(pop.querySelector('h3')?.textContent || '')
      && !pop.querySelector('[data-log-a]') && !q('#logdlg').open, pop.querySelector('h3')?.textContent || 'no popover');
    closePop();
  } else check('breaks are named', true, 'skipped: no break on this day');

  // a logged entry offers "Not an example" (only looked at, never clicked): on the latest of the last 7 days with one
  const today = state.date;
  let card = q('#lane-logged .card.logged');
  for (let back = 1; !card && back <= 7; back++) {
    go(shiftDate(today, -back));
    await until(() => state.logged && state.date === shiftDate(today, -back));
    await wait(300);
    card = q('#lane-logged .card.logged');
  }
  if (card) {
    const c = card.getBoundingClientRect();
    card.dispatchEvent(new MouseEvent('click', {bubbles: true, clientX: c.left + 10, clientY: c.top + 10}));
    await wait(200);
    check('a logged entry offers Not an example', !!q('#pop [data-nolearn]'),
      `${state.date}: ${q('#pop [data-nolearn]')?.textContent || 'no button'}`);
    closePop();
  } else check('a logged entry offers Not an example', true, 'skipped: nothing logged in the last 7 days');
  if (state.date !== today) { go(today); await until(() => state.logged && state.date === today); }

  // the log dialog's over-estimate line follows the length typed
  const f = logForm.elements, before = f.minutes.value;
  f.minutes.value = '30';
  showFlags({taskId: '1'}, [], {estimate: 60, worked: 50});
  const over = q('#log-flags').textContent;
  f.minutes.value = '5';
  f.minutes.dispatchEvent(new Event('input'));
  const under = q('#log-flags').hidden;
  showFlags(null);
  f.minutes.value = before;
  check('the over-estimate line follows the length', over.includes('+20 min over the estimate (1 h 20 min of 1 h)') && under, over);

  // Update says it restarts Pulsar when the release brings a newer window (Pulsar's window only)
  if (window.PULSAR_APP && typeof window.paintUpdate === 'function') {
    const saved = CONFIG.update, preview = CONFIG.preview;
    CONFIG.preview = false;
    CONFIG.update = {...saved, installed: true, status: 'available', latest: '9.9.9', app: {version: '9.9.9', min: '9.9.9'}};
    window.paintUpdate(CONFIG.update);
    const text = q('#update-now').textContent;
    CONFIG.update = saved; CONFIG.preview = preview;
    window.paintUpdate(saved);
    check('Update says it restarts when the window is behind', text === 'Update to 9.9.9 and restart', text);
  } else check('Update says it restarts when the window is behind', true, 'skipped: not in Pulsar\'s window');

  // today's Activity is asked for again every minute, so it keeps up with the now line; what one redraw costs
  if (state.view === 'day' && state.date === todayLocal()) {
    const shown = state.act;
    actAt = 0;
    refreshToday();
    const again = await until(() => state.act !== shown, 5000);
    const t = performance.now();
    for (let i = 0; i < 5; i++) render();
    check('today\'s activity is read again', again, `a redraw takes ${((performance.now() - t) / 5).toFixed(1)} ms`);
  } else check('today\'s activity is read again', true, 'skipped: not on today');

  // the theme: ◐ at the top, in Pulsar's window too, and Settings > Appearance set it (put back as it was)
  const themeWas = q('#theme-btn').dataset.v;
  check('the theme button shows', getComputedStyle(q('#theme-btn')).display !== 'none');
  const sel = q('#theme-sel'), other = themeWas === 'dark' ? 'light' : 'dark';
  sel.value = other;
  sel.dispatchEvent(new Event('change'));
  const set = (document.documentElement.dataset.theme || 'system') === other && q('#theme-btn').dataset.v === other;
  applyTheme(themeWas);
  check('Settings > Appearance sets the theme', set && sel.value === themeWas, `${themeWas} → ${other} → ${sel.value}`);

  // the day's entries come from the kept copy first
  const copy = await getJSON(`/api/entries?date=${state.date}&copy=1`);
  check('the server gives the day\'s kept copy', typeof copy.copy === 'boolean', JSON.stringify({copy: copy.copy, n: copy.entries?.length}));

  check('no page errors during the run', !errors.length, errors.join(' | ') || 'none');
} catch (e) {
  check('the script ran to the end', false, `${e.name}: ${e.message}`);
}
return JSON.stringify(out);
