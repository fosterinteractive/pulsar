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

  // a pause or time away: named; a click says what it is, and only its own button logs it (from where it began)
  const band = q('.awayband');
  if (band) {
    const label = [...document.querySelectorAll('.awaylabel')].map(x => x.textContent);
    check('breaks are named', label.some(t => /^(Pause|Away|Off) · /.test(t)), label.join(' | ') || 'no label shown');
    const b = band.getBoundingClientRect();
    band.dispatchEvent(new MouseEvent('click', {bubbles: true, clientX: b.left + 2, clientY: b.top + 4}));
    await wait(200);
    const pop = q('#pop'), word = pop.querySelector('h3')?.textContent || '', btn = pop.querySelector('[data-away-a]');
    const shown = pop.classList.contains('on'), dialog = q('#logdlg').open;
    check('a break is logged only from its button', shown && /^(Pause|Away|Off)$/.test(word) && !!btn && !pop.querySelector('[data-log-a]') && !dialog,
      `${word || 'no title'}${shown ? '' : ', popover not shown'}${btn ? ', Log this time…' : ', no button'}${dialog ? ', log dialog open' : ''}`);
    if (btn) {
      btn.click();
      const opened = await until(() => q('#logdlg').open, 3000), start = logForm.elements.start?.value || '';
      check('Log this time… opens the log dialog where the break began', opened && start === hm24(+band.dataset.a),
        `${opened ? 'open' : 'not open'} at ${start}, the break from ${hm24(+band.dataset.a)}`);
      q('#logdlg').close();
    }
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

  // Working days: Settings shows the saved ones (Monday to Friday unless changed), and Earlier days and the week line
  // count only those
  const boxes = [...document.querySelectorAll('#working-days input')];
  const ticked = boxes.filter(x => x.checked).map(x => +x.value);
  check('Settings shows the working days', boxes.length === 7 && ticked.join() === (CONFIG.workingDays || [0, 1, 2, 3, 4]).join(),
    ticked.join(','));
  check('a day outside the working days is not one', typeof workingDay === 'function'
    && workingDay('2026-11-09') === (CONFIG.workingDays || [0, 1, 2, 3, 4]).includes(0), `Mon ${workingDay('2026-11-09')}, Sun ${workingDay('2026-11-08')}`);

  // Settings > Advanced: closed, and showing the saved choices (the usual ones unless changed)
  const adv = CONFIG.advanced || {}, fe = form.elements;
  const shown = ADV_KEYS.map(k => fe[`adv_${k}`].value).join() + ',' + fe.adv_no_estimate.checked;
  const saved = ADV_KEYS.map(k => String(adv[k])).join() + ',' + (adv.no_estimate !== false);
  check('Settings shows the advanced choices', !q('#adv-more').open && shown === saved, shown);

  // Log the day drafts the note of every row of a repository's work by itself: an empty one or one from the commits
  const dr = x => ldDraftable({repo: 'site', note: '', noteFrom: '', ...x});
  const drafts = [dr({}), dr({note: 'Fix', noteFrom: 'commits'}), !dr({note: 'Fix', noteFrom: 'commits', noteEdited: true}),
    !dr({note: 'Mine', noteFrom: ''}), !dr({repo: ''}), !dr({noteTried: true}), !dr({status: 'logged'})];
  check('Log the day drafts the notes by itself', drafts.every(Boolean), drafts.join(','));

  // the day's entries come from the kept copy first
  const copy = await getJSON(`/api/entries?date=${state.date}&copy=1`);
  check('the server gives the day\'s kept copy', typeof copy.copy === 'boolean', JSON.stringify({copy: copy.copy, n: copy.entries?.length}));

  // beside ↻, how long ago the day's entries were read from Productive
  const dayAge = q('#updated').textContent;
  check('↻ says when the day was read', /^(just now|\d+ min ago|\d+ h ago)$/.test(dayAge) && /at \d/.test(q('#refresh').title),
    `${dayAge} · ${q('#refresh').title}`);

  // going to the Report reads it, and again only when older than a minute or after a write from this page
  setView('report');
  const read = await until(() => R.data && R.at && !$('#refresh').classList.contains('busy'), 60000);
  const at = R.at, w = wroteAt;
  setView('day'); setView('report');
  const kept = R.at === at && !$('#refresh').classList.contains('busy');
  const reportAge = q('#updated').textContent;
  const old = reportStale(at + 61000), wrote = (wroteAt = at + 1, reportStale(at + 1000));
  wroteAt = w;
  setView('day');
  check('↻ says when the Report was read', reportAge === 'just now', reportAge);
  check('the Report is read again when it is stale', read && kept && old && wrote,
    `read ${read}, kept within the minute ${kept}, stale after a minute ${old}, after a write ${wrote}`);

  // the trackers notice: what the server says of them, and the notice it makes (painted only, never shown)
  const th = await getJSON('/api/trackers');
  check('the server says how the trackers stand', Array.isArray(th.issues) && 'running' in (th.aw || {}),
    th.issues ? th.issues.map(x => x.key).join(', ') || 'all recording' : JSON.stringify(th));
  const shownBefore = trackShown;
  paintTrackers({login: null}, [{key: 'aw-down', tracker: 'aw', action: 'open', text: 'ActivityWatch is not running.'},
    {key: 'memtime-down', tracker: 'memtime', action: 'open', text: 'MemTime is not running.'}]);
  const tp = [q('#trackprompt-title').textContent, q('#trackprompt-go').textContent, !q('#trackprompt-settings').hidden,
    q('#trackprompt-get').hidden];
  paintTrackers({getUrl: 'https://activitywatch.net/downloads/'}, [{key: 'none', tracker: null, action: 'get', text: 'None.'}]);
  const tn = [q('#trackprompt-title').textContent, q('#trackprompt-go').hidden, !q('#trackprompt-get').hidden];
  trackShown = shownBefore;
  check('the trackers notice offers to open them and Login Items',
    tp.join() === 'ActivityWatch and MemTime stopped recording,Open ActivityWatch and MemTime,true,true'
    && tn.join() === 'No activity tracker is recording,true,true', `${tp.join(' | ')} / ${tn.join(' | ')}`);

  // Pause at the top: painted from a pause made up here (the server's is never started or ended by this script), and
  // the paused stretch on today
  const st = await getJSON('/api/pause'), pWas = PAUSE;
  PAUSE = {paused: true, since: Date.now() / 1000 - 3600, sinceText: '12:00', minutes: 60};
  paintPause();
  const pb = q('#pause-btn'), pOn = [pb.classList.contains('on'), q('#pause-label').textContent, pb.getAttribute('aria-pressed')];
  const sp = state.date === todayLocal() ? pausedSpans() : null;
  PAUSE = pWas; paintPause();
  check('Pause shows when Pulsar is paused', typeof st.paused === 'boolean' && pOn.join() === 'true,Paused since 12:00,true'
    && pb.classList.contains('on') === !!pWas.paused && (sp === null || (sp.length === 1 && Math.abs(sp[0][1] - sp[0][0] - Math.min(60, nowMin())) <= 1)),
    `${pOn.join(' | ')}; today ${JSON.stringify(sp)}; server paused ${st.paused}`);

  // text contrast: every text on the day view at 4.5:1 or more (3:1 when large), in the theme shown
  {
    const parse = c => { const m = c.match(/[\d.]+/g); return m ? [+m[0], +m[1], +m[2], m.length > 3 ? +m[3] : 1] : null; };
    const over = (top, bot) => [0, 1, 2].map(i => top[i] * top[3] + bot[i] * (1 - top[3]));
    const ground = el => {
      const layers = [];
      for (let e = el; e; e = e.parentElement) { const c = parse(getComputedStyle(e).backgroundColor); if (c && c[3] > 0) { layers.push(c); if (c[3] >= 1) break; } }
      let bg = parse(getComputedStyle(document.body).backgroundColor).slice(0, 3);
      for (const l of layers.reverse()) bg = over(l, bg);
      return bg;
    };
    const low = [];
    let seen = 0;
    for (const el of document.querySelectorAll('body *')) {
      if (![...el.childNodes].some(x => x.nodeType === 3 && x.textContent.trim()) || el.closest('[hidden], svg, dialog:not([open])')) continue;
      const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
      if (!r.width || !r.height || cs.visibility === 'hidden' || el.matches(':disabled') || el.closest(':disabled')) continue;
      let op = 1; for (let e = el; e; e = e.parentElement) op *= +getComputedStyle(e).opacity;
      const c = parse(cs.color); c[3] *= op;
      const bg = ground(el), ratio = contrast(over(c, bg), bg), size = parseFloat(cs.fontSize);
      seen++;
      if (ratio < (size >= 24 || (size >= 18.66 && +cs.fontWeight >= 700) ? 3 : 4.5)) low.push(`${el.id || el.className || el.tagName} ${ratio.toFixed(2)}:1 "${el.textContent.trim().slice(0, 20)}"`);
    }
    check('text reads at 4.5:1', !low.length, low.slice(0, 8).join(' | ') || `${seen} texts`);
  }
  // the accent under white text and as text, for the Mac's accents that fail as they are (put back afterwards)
  {
    const root = document.documentElement, was = root.style.getPropertyValue('--mac-accent'), theme = root.dataset.theme || 'system';
    const bad = [];
    for (const t of ['light', 'dark']) {
      applyTheme(t);
      for (const a of ['#007AFF', '#FFC600', '#F7821B', '#62BA46', '#F74F9E']) {
        root.style.setProperty('--mac-accent', a);
        paintAccent();
        const fill = rgbOf('var(--accent-fill)'), text = rgbOf('var(--accent-text)');
        const worst = Math.min(...['--bg', '--panel', '--card'].map(v => contrast(text, rgbOf(`var(${v})`))));
        if (contrast(fill, [255, 255, 255]) < 4.5 || worst < 4.5) bad.push(`${t} ${a}: fill ${contrast(fill, [255, 255, 255]).toFixed(2)}, text ${worst.toFixed(2)}`);
      }
    }
    if (was) root.style.setProperty('--mac-accent', was); else root.style.removeProperty('--mac-accent');
    applyTheme(theme);
    check('the accent carries text at 4.5:1 in every Mac accent', !bad.length, bad.join(' | ') || 'blue, yellow, orange, green, pink, both themes');
  }

  check('no page errors during the run', !errors.length, errors.join(' | ') || 'none');
} catch (e) {
  check('the script ran to the end', false, `${e.name}: ${e.message}`);
}
return JSON.stringify(out);
