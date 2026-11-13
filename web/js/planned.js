/* Planned: your bookings in Productive's Resource Planner on the day, in a column beside Meetings (the owner,
   2026-11-13). Folded to a narrow strip by default; a click opens it, and this Mac remembers. A booking has dates and
   an amount (hours a day, a share of the day, or hours over its period), never a time of day, so the day's bookings are
   laid one after another from 10:00, each as long as its share of the day; an absence is a line under the title. Each
   says how much you logged to its task that day. day.js calls loadBookings() from load(), renderPlanned() from
   render(), and openBooking() for a click. Read only. */
let planCache = [];
const PLAN_FROM = 600;  // 10:00: bookings have no time of day
const planOpen = () => { try { return localStorage.getItem('pulsar-planned-open') === '1'; } catch (e) { return false; } };

function loadBookings(d, fresh = false) {
  if (CONFIG?.planned === false) { state.booked = null; return Promise.resolve(null); }  // switched off in Settings
  return getJSON(`/api/bookings?date=${d}${fresh ? '&fresh=1' : ''}`).then(x => {
    if (d === state.date) { state.booked = {...x, date: d}; drawSoon(); }
    return x;
  }).catch(() => null);
}

/* minutes logged to a task on the shown day, from the Logged column */
const loggedOn = taskId => (state.logged?.entries || []).filter(e => String(e.taskId || '') === String(taskId || ''))
  .reduce((n, e) => n + (e.minutes || 0), 0);

function renderPlanned(ppm) {
  const open = planOpen();
  document.body.classList.toggle('plan-open', open);
  const b = state.booked?.date === state.date ? state.booked : null;
  const lane = $('#lane-plan'), head = $('#h-plan'), allDay = $('#plan-allday'), tg = $('#plan-tg');
  const list = b?.bookings || [];
  const work = list.filter(x => x.kind === 'work' && x.minutes > 0), away = list.filter(x => x.kind === 'absence');
  const total = work.reduce((n, x) => n + x.minutes, 0);
  tg.setAttribute('aria-expanded', String(open));
  tg.title = `Planned in Productive's Resource Planner${work.length ? `: ${dur(total)}` : ''}. Click to ${open ? 'fold' : 'show'} it.`;
  setText(tg, open ? 'Planned' : (work.length || away.length ? String(work.length + away.length) : '·'));
  setText(head, !open ? '' : !b ? '…' : b.error ? 'error' : work.length ? dur(total) : 'nothing');
  head.classList.toggle('warn', !!b?.error);
  head.title = b?.error || '';
  if (!open) { setHTML(allDay, ''); setHTML(lane, ''); planCache = []; return; }
  setHTML(allDay, away.map(x => `<div title="${esc(`Absence: ${x.title}${x.draft ? ', tentative' : ''}`)}">${esc(x.title)}${x.draft ? ' · tentative' : ''}</div>`).join(''));
  let at = PLAN_FROM;
  planCache = work.map(x => { const s = at; at += x.minutes; return {...x, s, e: at}; });
  setHTML(lane, planCache.map((x, i) => {
    const h = (x.e - x.s) * ppm, done = x.taskId ? loggedOn(x.taskId) : 0;
    return `<div class="card plan${x.draft ? ' draft' : ''}" data-k="plan" data-i="${i}" style="${place(x, ppm, 18)}"
      tabindex="0" role="button" aria-label="${esc(`Planned ${dur(x.minutes)}: ${x.title}${x.draft ? ', tentative' : ''}`)}">
      <div class="row"><span class="t">${esc(x.title)}</span></div>
      ${h > 34 ? `<div class="d num">${dur(x.minutes)} planned · ${done ? `${dur(done)} logged` : 'none logged'}</div>` : ''}</div>`;
  }).join('') || (b && !b.error ? '<div class="state">Nothing planned.</div>' : b?.error ? `<div class="state err">${esc(b.error)}</div>` : ''));
}

/* the same popover as the other cards: the booking, its period and how much is logged to its task that day */
function openBooking(i, ev) {
  const x = planCache[i];
  if (!x) return;
  const done = x.taskId ? loggedOn(x.taskId) : 0;
  const url = x.taskId && typeof productiveTaskURL === 'function' ? productiveTaskURL(x.taskId) : '';
  openPop(`<div class="ph"><div><h3>${esc(x.title)}</h3>
      <div class="sub">${dur(x.minutes)} planned on this day${x.draft ? ' · tentative' : ''}${x.start ? ` · booked ${x.start === x.end ? x.start : `${x.start} to ${x.end}`}` : ''}</div></div></div>
    <p>${esc(x.service || '')}${x.service ? ' · ' : ''}${done ? `${dur(done)} logged to it today` : 'Nothing logged to it today'}.</p>
    ${x.note ? `<p>${esc(x.note)}</p>` : ''}
    <p class="sub">Productive gives a booking no time of day: Pulsar shows the day's bookings one after another from 10:00.</p>
    ${url ? `<p class="links"><a href="${esc(url)}" target="_blank" rel="noopener noreferrer">Open the task in Productive</a></p>` : ''}`, ev);
}

$('#plan-tg').onclick = () => {
  try { localStorage.setItem('pulsar-planned-open', planOpen() ? '0' : '1'); } catch (e) { /* this Mac forgets: fine */ }
  render();
};
