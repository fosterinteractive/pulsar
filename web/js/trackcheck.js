/* The trackers notice: when a tracker the person uses stops (ActivityWatch or MemTime not running, ActivityWatch's
   window watcher silent, or no tracker at all), a notice like the update's says so and offers to open it, and Settings'
   Login Items so ActivityWatch opens by itself after a restart (trackhealth.py). Asked every TRACK_EVERY while the page
   is open; a problem shows once two checks in a row agree, so a Mac that just woke or an ActivityWatch restarting is not
   one. Later, ✕ or Escape quiet it for an hour; it asks again sooner when the problem clears and comes back. */
const TRACK_EVERY = 30 * 1000, TRACK_LATER = 60 * 60 * 1000;
const TRACK_NAMES = {aw: 'ActivityWatch', memtime: 'MemTime'};
const trackSeen = {}, trackQuiet = {};  // issue key -> when it was first seen in a row; -> quiet until
let trackShown = [], trackActed = false, trackBusy = false;
const trackDlg = $('#trackprompt');

/* the notice for `issues`, with the buttons that fit them; `login`: whether ActivityWatch opens at login, as last asked */
function paintTrackers(r, issues) {
  trackShown = issues;
  const aw = issues.some(x => x.tracker === 'aw'), mem = issues.some(x => x.tracker === 'memtime');
  const none = issues.some(x => x.key === 'none');
  $('#trackprompt-title').textContent = none ? 'No activity tracker is recording'
    : `${[aw && 'ActivityWatch', mem && 'MemTime'].filter(Boolean).join(' and ')} stopped recording`;
  $('#trackprompt-text').innerHTML = issues.map(x => `<p>${esc(x.text)}</p>`).join('');
  const opens = issues.filter(x => x.action === 'open' && x.tracker).map(x => TRACK_NAMES[x.tracker]);
  const restart = issues.some(x => x.action === 'restart');
  const go = $('#trackprompt-go');
  go.textContent = [restart && 'Restart ActivityWatch', opens.length && `Open ${opens.join(' and ')}`].filter(Boolean).join(', ');
  go.hidden = !go.textContent; go.disabled = false;
  const get = $('#trackprompt-get');
  get.hidden = !issues.some(x => x.action === 'get'); get.href = r.getUrl || 'https://activitywatch.net/';
  // so that it opens by itself after a restart: ActivityWatch from Settings, MemTime from its own settings
  const awOpen = issues.some(x => x.action === 'open' && x.tracker === 'aw'), memOpen = issues.some(x => x.action === 'open' && x.tracker === 'memtime');
  const login = [awOpen && r.login !== true && 'To have ActivityWatch open by itself when you log in, add it to your Login Items: Open at login… takes you there.',
    memOpen && 'MemTime opens at login from its own settings.'].filter(Boolean);
  $('#trackprompt-login').textContent = login.join(' ');
  $('#trackprompt-login').hidden = !login.length;
  $('#trackprompt-settings').hidden = !(awOpen && r.login !== true);
}

async function checkTrackers() {
  if (!CONFIG || signedOut || trackBusy) return;
  const r = await getJSON('/api/trackers');
  if (!r || r.error || !Array.isArray(r.issues)) return;
  const now = Date.now(), keys = new Set(r.issues.map(x => x.key));
  for (const k of Object.keys(trackSeen)) if (!keys.has(k)) { delete trackSeen[k]; delete trackQuiet[k]; }
  for (const k of keys) trackSeen[k] ??= now;
  if (trackDlg.open) {  // fixed by hand while the notice is up: it goes, and says so
    if (!trackShown.some(x => keys.has(x.key))) {
      trackActed = true; trackDlg.close();
      toast(`${trackShown.map(x => TRACK_NAMES[x.tracker] || 'The tracker').filter((v, i, a) => a.indexOf(v) === i).join(' and ')} records again.`);
    }
    return;
  }
  const due = r.issues.filter(x => now - trackSeen[x.key] >= TRACK_EVERY - 5000 && !(trackQuiet[x.key] > now));
  if (!due.length || document.querySelector('dialog[open]')) return;
  paintTrackers(r, due);
  trackActed = false;
  trackDlg.showModal();
}

/* closed without its button (Later, ✕, Escape, a click outside): quiet for an hour */
trackDlg.addEventListener('close', () => {
  if (!trackActed) for (const x of trackShown) trackQuiet[x.key] = Date.now() + TRACK_LATER;
});
$('#trackprompt-later').onclick = $('#trackprompt-x').onclick = () => trackDlg.close();
$('#trackprompt-get').addEventListener('click', () => { trackDlg.close(); });
$('#trackprompt-settings').onclick = () => {
  trackDlg.close();
  openSettings('t-day');
  setTimeout(() => $('#away-sec')?.scrollIntoView({block: 'start'}), 50);
};
$('#trackprompt-go').onclick = async () => {
  const issues = trackShown, go = $('#trackprompt-go');
  go.disabled = true; trackActed = true; trackBusy = true;
  const asks = [];
  if (issues.some(x => x.action === 'restart')) asks.push({tracker: 'aw', restart: true});
  for (const t of new Set(issues.filter(x => x.action === 'open' && x.tracker).map(x => x.tracker))) asks.push({tracker: t});
  trackDlg.close();
  toast(asks.map(a => `${a.restart ? 'Restarting' : 'Opening'} ${TRACK_NAMES[a.tracker]}`).join(', ') + '…');
  const failed = [];
  for (const a of asks) {
    const res = await postJSON('/api/trackers/open', a);
    if (res.error || !res.result?.ok) failed.push(res.error || res.result?.message || `${TRACK_NAMES[a.tracker]} did not start.`);
  }
  if (failed.length) { trackBusy = false; toast(failed.join(' '), true); return; }
  setTimeout(async () => {  // a tracker takes a few seconds to answer
    trackBusy = false;
    const r = await getJSON('/api/trackers');
    const left = (r.issues || []).filter(x => issues.some(y => y.key === x.key));
    if (r.error) return;
    if (left.length) toast(`${left.map(x => TRACK_NAMES[x.tracker] || 'The tracker').join(' and ')} still records nothing. Open it from Applications.`, true);
    else toast(`${asks.map(a => TRACK_NAMES[a.tracker]).join(' and ')} records again.`);
  }, 10000);
};

setTimeout(checkTrackers, 3000);  // after the page has settled
setInterval(checkTrackers, TRACK_EVERY);
