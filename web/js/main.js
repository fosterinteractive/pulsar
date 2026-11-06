/* Views and start-up: settings from the server first, then the view in the address */
let dayLoaded = false;

function applyConfig(c) {
  CONFIG = c;
  TZ = c.timezone || undefined;  // undefined: the browser's own zone, the computer's
  TARGET = c.targetMinutes || 480;
  document.body.classList.toggle('no-likely', !c.likely.enabled);
  const errs = c.errors24h || 0;
  $('#open-logs').classList.toggle('has-errors', errs > 0);
  $('#open-logs').title = errs ? `${errs} error${errs === 1 ? '' : 's'} in the last 24 hours` : 'Error log';
  // Pulsar Preview: which code this is, at all times, so nobody mistakes it for the release
  const pv = c.preview, chip = $('#preview-chip');
  chip.hidden = !pv;
  if (pv) {
    chip.textContent = `Preview · ${pv.branch || '?'} · ${pv.commit || '?'}${pv.changed ? ' +' : ''}`;
    chip.title = `Pulsar Preview runs the code of branch ${pv.branch} (${pv.commit}${pv.changed ? ', with changes not committed' : ''}): ${pv.subject}. `
      + (pv.writes ? 'It WRITES to Productive (allowed in Settings).' : 'It does not write to Productive.');
    chip.classList.toggle('writes', !!pv.writes);
  }
}

function setView(v) {
  state.view = v === 'report' ? 'report' : 'day';
  document.body.dataset.view = state.view;
  document.querySelectorAll('#view button').forEach(b => b.classList.toggle('on', b.dataset.v === state.view));
  pop.classList.remove('on'); tipEl.classList.remove('on');
  syncURL();
  if (state.view === 'report' && !R.data) return loadReport();
  if (state.view === 'day' && !dayLoaded) { dayLoaded = true; return load(); }
  return null;  // what it loads, if anything: the warp after an update waits for it
}

/* after a settings change: everything shown may have moved (time zone, target, connection) */
function reloadView() {
  R.data = null;
  dayLoaded = state.view === 'day';
  if (state.view === 'report') loadReport(); else load({keepScroll: true});
}

document.querySelectorAll('#view button').forEach(b => b.onclick = () => {
  setView(b.dataset.v);
  if (b.dataset.v === 'day') requestAnimationFrame(() => scrollToNow());  // Day: straight to the current hour
});
/* the logo goes home: the Day view */
$('#home').onclick = ev => {  // home: today, at the current hour
  ev.preventDefault();
  setView('day');
  if (state.date !== todayLocal()) go(todayLocal());  // opens at the current hour once the day has loaded
  else requestAnimationFrame(() => scrollToNow());
};
/* the favicon is the header's pulsar (still: the page's styles do not reach it) */
$('#favicon').href = 'data:image/svg+xml,' + encodeURIComponent($('#logo').outerHTML);

/* a restart into a new version (by itself, or from Settings): reload the page into it and say so */
setInterval(async () => {
  if (signedOut) return;
  try {
    const r = await fetch('/api/version');
    if (!r.ok) return;  // signed out (401) or restarting: quiet, the next minute tries again
    const v = (await r.json()).version;
    if (CONFIG && v && v !== CONFIG.version && !document.querySelector('dialog[open]')) {
      sessionStorage.setItem('pulsar-updated', v); await warpIn(); location.reload();
    }
  } catch (e) { /* restarting: try again in a minute */ }
}, 60 * 1000);

/* opened by the app or pulsar.py with ?code=...: trade the one-time code for this browser's session token, then
   take the code out of the address */
async function signInFromAddress() {
  const code = params.get('code');
  if (!code) return;
  params.delete('code');
  history.replaceState(null, '', location.pathname + (params.toString() ? '?' + params : ''));
  try {
    const r = await fetch('/api/session', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Pulsar': '1'},
                                            body: JSON.stringify({code})});
    const d = await r.json();
    if (d.token) localStorage.setItem(SESSION, d.token);
  } catch (e) { /* the API calls that follow show the sign-in notice */ }
}

(async () => {
  await signInFromAddress();
  const c = await getJSON('/api/config');
  if (c.error) toast(c.error, true); else applyConfig(c);
  if (!validDate(params.get('date'))) state.date = todayLocal();  // today in the configured time zone
  const first = setView(params.get('view'));
  // the Day view says so in its checklist (flow.js); the report only in its banner
  if (CONFIG && !CONFIG.productive.active && state.view !== 'day') toast(CONFIG.productive.keychainStuck ? CONFIG.productive.problem
    : 'Productive is not connected yet: open Settings, top right, to add your token.', true);
  try {
    const was = sessionStorage.getItem('pulsar-updated');
    if (was) {  // core.js opened the page inside the lens: let go once this version has drawn its data
      sessionStorage.removeItem('pulsar-updated');
      Promise.race([Promise.resolve(first).catch(() => {}), new Promise(r => setTimeout(r, 6000))])
        .then(warpRelease).then(() => toast(`Pulsar updated to ${was}.`));
    }
  } catch (e) {}
  if (CONFIG) { paintUpdate(CONFIG.update); setTimeout(() => maybeRemind(), 1500); }  // after the page has settled
})();
