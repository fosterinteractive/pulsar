/* The flow of logging a day, kept apart from day.js and log.js: Log and next, the start as 24-hour HH:MM, links to
   Productive, the first-run checklist, the week in the header, report days that open in the Day view, and where a
   day opens. day.js and log.js call into it at a few places, each marked "flow.js". */

/* ---------- Log and next ---------- */
/* entries logged from this page in the last ten minutes: Productive can take a moment to show a new entry, and the
   next stretch offered must never be the one just logged */
const loggedHere = [];
/* the active stretches with no entry (the yellow rails, as drawn), less what was just logged and less [skipA, skipB):
   [{a, b}] in minutes, 3 minutes or more each, earliest first */
function openStretches(skipA = 0, skipB = 0) {
  while (loggedHere.length && Date.now() - loggedHere[0].at > 10 * 60 * 1000) loggedHere.shift();
  const cuts = [...loggedHere.filter(x => x.date === state.date), {a: skipA, b: skipB}].filter(x => x.b > x.a);
  let out = [...document.querySelectorAll('#lane-logged .gaprail')].map(g => ({a: +g.dataset.a, b: +g.dataset.b}));
  for (const c of cuts)
    out = out.flatMap(s => (c.b <= s.a || c.a >= s.b ? [s] : [{a: s.a, b: c.a}, {a: c.b, b: s.b}].filter(p => p.b - p.a >= 3)));
  return out.sort((p, q) => p.a - q.a);
}
/* the dialog opens: "Log and next" is there for new entries in the day on screen (the rails are that day's) */
function prepareNext(date, edit) {
  logForm.elements.start.removeAttribute('aria-invalid');  // a start the last dialog refused is not this one's
  $('#log-next').hidden = !!edit || date !== state.date;
  $('#log-next-hint').textContent = '';
}
logForm.elements.date.addEventListener('change', () => prepareNext(logForm.elements.date.value, logEdit));
/* after a successful log (log.js): remember it, and with Log and next open the next stretch after it (else the day's
   first one left). True when it took care of saying so; false leaves the toast to log.js. */
function afterLog(date, start, minutes, said, andNext) {
  const a = +start.slice(0, 2) * 60 + +start.slice(3, 5);
  loggedHere.push({date, a, b: a + minutes, at: Date.now()});
  if (!andNext || date !== state.date) return false;
  const left = openStretches(), s = left.find(x => x.a >= a + minutes) || left[0];
  if (!s) { toast(`${said} No active time is left without an entry.`); return true; }
  openLog({start: s.a, minutes: s.b - s.a, hint: rulesInRange(s.a, s.b), lead: leadFor(s.a, s.b)});
  const more = left.length - 1;
  $('#log-next-hint').textContent = `${said} Next active stretch with no entry: ${hm(s.a)}–${hm(s.b)}`
    + (more ? `, then ${more} more.` : ', the last one.');
  return true;
}

/* ---------- the start: 24-hour HH:MM, as everywhere else in Pulsar ---------- */
/* 09:30, and also 9:30, 930 or 9.30; '' when it is not a time of day */
function startHHMM(v) {
  const m = String(v ?? '').trim().match(/^(\d{1,2})[:.h]?(\d{2})$/);
  return m && +m[1] < 24 && +m[2] < 60 ? `${m[1].padStart(2, '0')}:${m[2]}` : '';
}
logForm.elements.start.addEventListener('blur', ev => {
  const v = startHHMM(ev.target.value);
  if (v) ev.target.value = v;
  if (ev.target.value) ev.target.setAttribute('aria-invalid', String(!v)); else ev.target.removeAttribute('aria-invalid');
});
logForm.elements.start.addEventListener('input', ev => ev.target.removeAttribute('aria-invalid'));

/* ---------- links to Productive ---------- */
/* a task's page in Productive. The organization's address comes from links seen in your Claude sessions (the API
   does not give it), so without one there is no link rather than a guessed one. */
function productiveTaskURL(id) {
  const base = CONFIG?.productive?.web || '';
  return base.startsWith('https://app.productive.io/') && /^\d+$/.test(String(id ?? '')) ? `${base}/tasks/${id}` : '';
}
function showTaskLink(t) {
  const a = $('#log-task-open'), url = productiveTaskURL(t?.taskId);
  a.hidden = !url;
  if (url) { a.href = url; a.setAttribute('aria-label', `Open “${t.title}” in Productive`); } else a.removeAttribute('href');
}

/* ---------- first run: what is missing, above the board, and the one thing that fixes it ---------- */
function renderChecklist() {
  const el = $('#checklist'), act = state.act, logged = state.logged, items = [];
  if (!CONFIG || !el) return;
  const link = (label, url) => `<a class="btn bordered sm" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>`;
  if (!CONFIG.productive.active)
    items.push(['Productive is not connected', 'Pulsar logs your time there and shows what you logged.',
                '<button type="button" class="btn primary sm" data-ck="t-conn">Connect Productive</button>']);
  else if (logged?.needsSetup)
    items.push(['Productive does not know who you are yet', logged.error || 'It needs your organization and person.',
                '<button type="button" class="btn primary sm" data-ck="t-conn">Open Connections</button>']);
  if (act && !act.error) {
    const seen = act.items.length || act.minutes?.aw || act.minutes?.memtime;
    const problem = Object.values(act.problems || {})[0] || '';
    const aw = /^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?\/?$/.test(CONFIG.activitywatchUrl || '') ? CONFIG.activitywatchUrl : 'http://localhost:5600';
    if (!seen && problem)
      items.push(['No activity tracker answers', problem, /not answering/.test(problem)
        ? link('Get ActivityWatch', 'https://activitywatch.net/downloads/') : link('Open ActivityWatch', aw)]);
    else if (!seen && state.date === todayLocal() && !logged?.entries?.length)
      items.push(['No activity yet today', 'Your work shows here within a minute, while ActivityWatch runs.',
                  act.source === 'aw' ? link('Open ActivityWatch', aw) : '']);
  }
  el.hidden = !items.length;
  setHTML(el, items.length ? `<h2>Before Pulsar can fill your day</h2>` + items.map(([what, why, action]) =>
    `<div class="ck"><span class="ck-dot" aria-hidden="true"></span><div class="ck-t"><b>${esc(what)}</b><span>${esc(why)}</span></div>${action}</div>`).join('') : '');
}
$('#checklist').addEventListener('click', ev => { const b = ev.target.closest('[data-ck]'); if (b) openSettings(b.dataset.ck); });

/* ---------- the week, in the Day header: logged against active, Monday to Friday ---------- */
const week = {key: '', at: 0, days: null, today: '', busy: false};
const mondayOf = d => shiftDate(d, -((new Date(d + 'T12:00:00Z').getUTCDay() + 6) % 7));
const hours1 = m => `${Math.round(m / 6) / 10} h`;
async function loadWeek(mon) {
  week.busy = true;
  const x = await getJSON(`/api/report?from=${mon}&to=${shiftDate(mon, 4)}`);
  Object.assign(week, {key: mon, at: Date.now(), busy: false, days: x.error ? null : x.days, today: x.today || ''});
  renderWeek();
}
/* Monday to Friday up to today; the day on screen counts what its Logged column shows now, so an entry just logged
   counts at once */
function weekDays() {
  const live = state.logged && !state.logged.error ? state.logged.entries.reduce((s, x) => s + x.minutes, 0) : null;
  return (week.days || []).filter(d => d.weekday < 5 && d.date <= week.today)
    .map(d => (d.date === state.date && live != null ? {...d, logged: live} : d));
}
function renderWeek() {
  const btn = $('#weekline'), mon = mondayOf(state.date);
  if (!btn || !CONFIG?.productive?.active) { if (btn) btn.hidden = true; return; }
  if ((week.key !== mon || Date.now() - week.at > 15 * 60 * 1000) && !week.busy) loadWeek(mon);
  const days = week.key === mon ? weekDays() : [];
  btn.hidden = !days.length;
  if (!days.length) return;
  const sum = k => days.reduce((s, d) => s + d[k], 0);
  btn.dataset.label = mon === mondayOf(todayLocal()) ? 'This week' : `Week of ${dayLabel(mon, {day: 'numeric', month: 'short'})}`;
  const L = hours1(sum('logged')), A = hours1(sum('active'));
  setHTML(btn, `<span class="wl-long">${esc(btn.dataset.label)}: ${L} logged of ${A} active</span>`
    + `<span class="wl-short">${esc(btn.dataset.label === 'This week' ? 'Week' : btn.dataset.label)}: ${L} of ${A}</span>`);
  btn.title = `${btn.dataset.label}, Monday to Friday: ${L} logged of ${A} active. Click for each day.`;
}
$('#weekline').addEventListener('click', ev => {
  ev.stopPropagation();  // the page's click handler would close the popover it opens
  const r = ev.currentTarget.getBoundingClientRect();
  openPop(`<h3>${esc(ev.currentTarget.dataset.label)}</h3><div class="sub">Logged of active work time. A day opens it.</div>`
    + weekDays().map(d => `<button type="button" class="wk-day${d.date === state.date ? ' on' : ''}" data-go="${esc(d.date)}"${d.date === state.date ? ' aria-current="date"' : ''}>
        <span>${esc(dayLabel(d.date, {weekday: 'long', day: 'numeric', month: 'short'}))}</span><span class="num">${hours1(d.logged)} of ${hours1(d.active)}</span></button>`).join(''),
    {clientX: r.left - 14, clientY: r.bottom + 26});
  pop.querySelector('.wk-day')?.focus();
});
pop.addEventListener('click', ev => { const b = ev.target.closest('[data-go]'); if (b) go(b.dataset.go); });

/* ---------- the report's days open in the Day view ---------- */
function openDay(date) {
  if (!validDate(date)) return;
  state.date = date; state.scrolledFor = null;
  if (dayLoaded) { setView('day'); go(date); } else setView('day');
}
document.addEventListener('click', ev => {
  const el = ev.target.closest?.('#rbody [data-day]');
  if (!el || ev.metaKey || ev.ctrlKey || ev.shiftKey) return;  // a modified click opens the link in a new tab
  ev.preventDefault(); tipEl.classList.remove('on'); openDay(el.dataset.day);
});
const dayLink = d => `<a class="daylink" href="?view=day&amp;date=${esc(d)}" data-day="${esc(d)}">${esc(dayLabel(d))}</a>`;

/* ---------- where a day opens ---------- */
/* the start of its first long run of work: activity, entries and meetings less than 15 minutes apart, 30 minutes or
   more of them, so a stray minute at 00:30 does not decide it. Else the longest run. null on an empty day. */
function firstWorkRun() {
  const spans = [...(state.act?.items || []).filter(x => x.cat !== 'personal'),
                 ...(state.logged?.entries || []).filter(x => x.s != null),
                 ...(typeof meetCache !== 'undefined' ? meetCache : []).filter(x => !x.declined)]
    .filter(x => x.e > x.s).map(x => ({s: x.s, e: x.e})).sort((p, q) => p.s - q.s);
  const runs = [];
  for (const x of spans) {
    const run = runs[runs.length - 1];
    if (run && x.s - run.e <= 15) { run.busy += Math.max(0, x.e - Math.max(x.s, run.e)); run.e = Math.max(run.e, x.e); }
    else runs.push({s: x.s, e: x.e, busy: x.e - x.s});
  }
  return (runs.find(r => r.busy >= 30) || runs.sort((p, q) => q.busy - p.busy)[0])?.s ?? null;
}

/* called by render() (day.js) after each redraw */
function renderFlow() { renderChecklist(); renderWeek(); }
