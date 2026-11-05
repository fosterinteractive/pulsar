/* Meetings: the day's events from Google Calendar, in a narrow column on the day's time axis, and the Google
   Calendar part of Settings. The column is there only while Google Calendar is connected; otherwise nothing of it
   shows. day.js calls loadMeetings() from load(), renderMeetings() from render(), and openMeeting() for a click. */
let meetCache = [];
const meetOn = () => !!CONFIG?.calendar?.connected;
const httpsOnly = u => (typeof u === 'string' && /^https:\/\//i.test(u) ? u : '');

/* fresh: the ↻ button, which asks Google at once; a normal load uses the copy the server keeps for five minutes */
function loadMeetings(d, fresh = false) {
  if (!meetOn()) { state.meet = null; return Promise.resolve(null); }
  return getJSON(`/api/calendar?date=${d}${fresh ? '&fresh=1' : ''}`).then(x => {
    if (x.connected === false && CONFIG.calendar?.connected) {  // the access lapsed or was withdrawn: say so once
      CONFIG.calendar = {...CONFIG.calendar, connected: false, account: ''};
      if (x.problem) toast(x.problem, true);
    }
    if (d === state.date) { state.meet = {...x, date: d}; render(); }
    return x;
  });
}

function renderMeetings(ppm) {
  document.body.classList.toggle('has-meetings', meetOn());
  if (!meetOn()) { meetCache = []; return; }
  const m = state.meet?.date === state.date ? state.meet : null;
  const lane = $('#lane-meet'), head = $('#h-meet'), allDay = $('#meet-allday');
  head.classList.remove('warn');
  if (!m) { lane.innerHTML = '<div class="state">reading Google Calendar…</div>'; head.textContent = '…'; allDay.innerHTML = ''; meetCache = []; return; }
  if (m.error) { lane.innerHTML = `<div class="state err">${esc(m.error)}</div>`; head.textContent = 'error'; allDay.innerHTML = ''; meetCache = []; return; }
  const events = m.events || [];
  allDay.innerHTML = events.filter(x => x.all_day).map(x =>
    `<div class="${x.declined ? 'declined' : ''}" title="${esc(`All day: ${x.title || '(no title)'}${x.declined ? ', declined' : ''}`)}">${esc(x.title || '(no title)')}${x.declined ? ' · declined' : ''}</div>`).join('');
  meetCache = lanes(events.filter(x => !x.all_day).map(x => ({...x})), ppm, 18);
  const going = meetCache.filter(x => !x.declined);
  head.textContent = m.problem || (meetCache.length
    ? `${going.length} ${going.length === 1 ? 'meeting' : 'meetings'}${meetCache.length > going.length ? `, ${meetCache.length - going.length} declined` : ''}`
    : 'Google Calendar');
  head.classList.toggle('warn', !!m.problem);
  head.title = CONFIG.calendar.account ? `Google Calendar: ${CONFIG.calendar.account}` : 'Google Calendar';
  lane.innerHTML = meetCache.map((x, i) => {
    const h = (x.e - x.s) * ppm, title = x.title || '(no title)';
    return `<div class="card meet${x.declined ? ' declined' : ''}" data-k="meet" data-i="${i}" style="${place(x, ppm, 18)}"
      tabindex="0" role="button" aria-label="${esc(`Meeting ${hm(x.s)}–${hm(x.e)}: ${title}${x.declined ? ', declined' : ''}`)}">
      <div class="row"><span class="t">${esc(title)}</span>${x.declined ? '<span class="dec">declined</span>' : ''}</div>
      ${h > 34 ? `<div class="d num">${hm(x.s)}–${hm(x.e)}</div>` : ''}</div>`;
  }).join('') || (events.length ? '' : '<div class="state">No meetings on this day.</div>');
}

/* the same popover as the other cards: when, what, a link to join or open it, and Log this time */
function openMeeting(i, ev) {
  const x = meetCache[i];
  if (!x) return;
  const join = httpsOnly(x.url), open = httpsOnly(x.link);
  const links = [join ? `<a href="${esc(join)}" target="_blank" rel="noopener noreferrer">Join the meeting</a>` : '',
                 open ? `<a href="${esc(open)}" target="_blank" rel="noopener noreferrer">Open in Google Calendar</a>` : '']
    .filter(Boolean).join('');
  openPop(`<div class="ph"><div><h3>${esc(x.title || '(no title)')}</h3>
      <div class="sub">${hm(x.s)}–${hm(x.e)} · ${dur(x.e - x.s)}${x.declined ? ' · you declined it' : ''}${x.recurring ? ' · repeats' : ''}</div></div>
    <button class="btn bordered sm" data-log-a="${x.s}" data-log-b="${x.e}" data-meet="${i}">Log this time</button></div>
    ${links ? `<p class="links">${links}</p>` : ''}`, ev);
}

/* ---------- Settings: connect in the browser (Google's own sign-in), disconnect here ---------- */
function paintCalendar(c) {
  const g = c?.calendar || {}, st = $('#gcal-status'), connect = $('#gcal-connect');
  st.textContent = !g.configured ? 'Not set up in this copy of Pulsar yet: it needs the organization\'s Google sign-in first.'
    : g.connected ? `Connected as ${g.account || 'your Google account'}.` : 'Not connected.';
  st.classList.toggle('warn', !g.configured);
  connect.hidden = !!g.connected;
  connect.disabled = !g.configured;
  connect.title = g.configured ? '' : 'Not set up in this copy of Pulsar yet';
  $('#gcal-disconnect').hidden = !g.connected;
  if (g.connected) $('#gcal-link').hidden = true;
}

let gcalWait = null;
$('#gcal-connect').onclick = async () => {
  const st = $('#gcal-status'), link = $('#gcal-link'), btn = $('#gcal-connect');
  btn.disabled = true;
  const r = await postJSON('/api/calendar/connect', {});
  btn.disabled = false;
  if (r.error) { st.textContent = r.error; st.classList.add('warn'); return; }
  st.classList.remove('warn');
  st.textContent = 'Sign in to Google in your browser and click Allow. Pulsar is waiting.';
  link.hidden = false;
  link.innerHTML = `${r.opened ? 'No browser window?' : 'Your browser did not open by itself.'} `
    + `<a href="${esc(httpsOnly(r.url))}" target="_blank" rel="noopener noreferrer">Open Google's sign-in page</a>.`;
  clearInterval(gcalWait);
  const t0 = Date.now();
  gcalWait = setInterval(async () => {  // the answer arrives in the browser; this page learns of it here
    const s = await getJSON('/api/calendar/status');
    if (s.connected) {
      clearInterval(gcalWait);
      CONFIG.calendar = s; paintCalendar(CONFIG);
      toast(`Google Calendar connected${s.account ? ': ' + s.account : ''}.`);
      reloadView();
    } else if (s.error || Date.now() - t0 > 10 * 60 * 1000) {  // a sign-in is valid for 10 minutes
      clearInterval(gcalWait); link.hidden = true; paintCalendar(CONFIG);
    }
  }, 2000);
};
$('#gcal-disconnect').onclick = async () => {
  const btn = $('#gcal-disconnect');
  btn.disabled = true;
  const r = await postJSON('/api/calendar/disconnect', {});
  btn.disabled = false;
  if (r.error) { toast(r.error, true); return; }
  CONFIG.calendar = r.calendar; paintCalendar(CONFIG);
  toast(r.message, !r.told);
  reloadView();
};
