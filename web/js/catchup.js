/* Log the week: catching up on several earlier days from Log the day (logday.js). "Earlier days…" lists the last
   workdays before today with their active time that no entry covers (GET /api/report); the days picked are then
   reviewed one after another in the same panel, each exactly as Log the day reviews a day, with a running total.
   Each day is written only through its own confirm step and journal: nothing is ever sent for several days at once.
   logday.js calls cuRender, cuButton, cuStrip and cuClosed. */

const CU = {picking: false, loading: false, days: null, error: '', shown: 7, ticked: new Set(),
            queue: [], i: 0, done: {}, moving: false};
const CU_MAX = 14;           // workdays offered at most
const CU_TICK = 30;          // days with this many minutes not logged start ticked
const cuActive = () => CU.queue.length > 0 && LD.on && LD.date === CU.queue[CU.i];

/* the last CU_MAX workdays (Monday to Friday) before today, oldest first, read once per opening */
async function cuLoad() {
  const today = todayLocal(), want = [];
  for (let d = shiftDate(today, -1); want.length < CU_MAX; d = shiftDate(d, -1))
    if ((new Date(d + 'T12:00:00Z').getUTCDay() + 6) % 7 < 5) want.unshift(d);
  Object.assign(CU, {loading: true, error: '', days: null});
  renderReview();
  const [r, marks] = await Promise.all([getJSON(`/api/report?from=${want[0]}&to=${want[want.length - 1]}`),
                                        getJSON(`/api/daymarks?from=${want[0]}&to=${want[want.length - 1]}`)]);
  CU.loading = false;
  if (r.error) { CU.error = r.error; renderReview(); return; }
  // a day done (said, or its logged time reached the target and nobody said "not done") is not offered; time marked
  // "not work" is not counted (daymarks.py)
  const m = marks.days || {};
  CU.days = (r.days || []).filter(d => want.includes(d.date))
    .filter(d => !(m[d.date]?.done === true || (m[d.date]?.done !== false && d.logged >= TARGET)))
    .map(d => ({...d, notLogged: Math.max(0, d.notLogged - (m[d.date]?.notWork || []).reduce((s, [a, b]) => s + b - a, 0))}));
  CU.ticked = new Set(CU.days.slice(-CU.shown).filter(d => d.notLogged >= CU_TICK).map(d => d.date));
  // no activity read for the range (ActivityWatch down, say): said, or every day would read "nothing left"
  CU.warning = [r.warning, r.trackerWarning].filter(Boolean).join(' ');
  CU.noActivity = !!r.trackerWarning;
  renderReview();
}

/* the title row's button, while no catch-up runs and nothing is being written */
function cuButton() {
  return CU.queue.length || LD.writing ? '' : '<button type="button" class="btn bordered sm" id="cu-open" title="Catch up on earlier days">Earlier days…</button>';
}

/* the list of earlier days in place of the review, while it is open; false otherwise */
function cuRender(el) {
  if (!CU.picking) return false;
  const head = `<div class="rv-h"><div class="rv-t"><h2>Earlier days</h2>
      <button type="button" class="btn icon" id="ld-close" aria-label="Close Log the day">✕</button></div>
    <div class="rv-sum">Tick the days to catch up on. Each opens as Log the day; nothing is written until you confirm it.</div>
    ${CU.warning ? `<p class="rv-test" role="status">${esc(CU.warning)}</p>` : ''}</div>`;
  if (CU.loading || (!CU.days && !CU.error)) { el.innerHTML = `${head}<div class="rv-empty"><p>Reading your last workdays…</p></div>`; return true; }
  if (CU.error) {
    el.innerHTML = `${head}<div class="rv-empty"><div class="big-ic" aria-hidden="true">!</div><p>${esc(CU.error)}</p>
      <div class="btnrow"><button type="button" class="btn bordered" id="cu-back">Back to the day</button></div></div>`;
    return true;
  }
  const shown = CU.days.slice(-CU.shown), more = CU.days.length > shown.length;
  const picked = shown.filter(d => CU.ticked.has(d.date)), sum = picked.reduce((s, d) => s + d.notLogged, 0);
  const line = d => {
    const none = d.notLogged < 5;
    return `<li class="cu-d${none ? ' none' : ''}"><label><input type="checkbox" data-cu-day="${esc(d.date)}"${CU.ticked.has(d.date) && !none ? ' checked' : ''}${none ? ' disabled' : ''}>
      <span class="cu-n">${esc(dayLabel(d.date, {weekday: 'short', day: 'numeric', month: 'short'}))}</span>
      <span class="cu-m num">${none ? (CU.noActivity ? 'no activity read' : 'nothing left') : `<b>${dur(d.notLogged)}</b> not logged`}</span>
      <span class="cu-l num">${dur(d.logged)} logged of ${dur(d.active)} active</span></label></li>`;
  };
  el.innerHTML = `${head}
    <ul class="rv-list cu-list" aria-label="Earlier workdays">
      ${more ? `<li class="cu-more"><button type="button" class="btn bordered sm" id="cu-more">Show ${CU.days.length - shown.length} earlier days</button></li>` : ''}
      ${shown.map(line).join('')}</ul>
    <div class="rv-f"><button type="button" class="btn bordered" id="cu-back">Back to the day</button>
      <p class="hint">${picked.length ? `${dur(sum)} not logged on these` : 'Tick a day to review it.'}</p>
      <button type="button" class="btn primary big" id="cu-go"${picked.length ? '' : ' disabled'}>Review ${picked.length} ${picked.length === 1 ? 'day' : 'days'}</button></div>`;
  return true;
}

/* while a catch-up runs: which day of how many, what it has logged so far, and the way on */
function cuLogged() {
  const here = LD.rows.filter(r => r.thisRun && r.status === 'logged').reduce((s, r) => s + r.minutes, 0);
  return Object.entries(CU.done).filter(([d]) => d !== LD.date).reduce((s, [, m]) => s + m, 0) + here;
}
function cuStrip() {
  if (!cuActive()) return '';
  const last = CU.i === CU.queue.length - 1;
  return `<div class="cu-strip" role="status"><span>Catching up · day ${CU.i + 1} of ${CU.queue.length} · <b>${dur(cuLogged())}</b> logged so far</span>
    <button type="button" class="btn bordered sm" id="cu-next"${LD.writing ? ' disabled' : ''}>${last ? 'Finish' : 'Next day ›'}</button></div>`;
}

/* a day of the catch-up: the Day view moves to it and Log the day opens on it, as if opened there by hand */
function cuOpenDay(d) {
  CU.moving = true;
  if (ldActive()) closeLogDay();
  CU.moving = false;
  state.date = d; state.scrolledFor = null; closePop(); syncURL();
  const loaded = load();
  openLogDay();
  loaded.then(() => { if (ldActive()) renderReview(); });  // its entries came after the panel was drawn
}
function cuNext() {
  if (LD.writing) return;
  CU.done[LD.date] = LD.rows.filter(r => r.thisRun && r.status === 'logged').reduce((s, r) => s + r.minutes, 0);
  if (CU.i < CU.queue.length - 1) { CU.i++; cuOpenDay(CU.queue[CU.i]); return; }
  const total = Object.values(CU.done).reduce((s, m) => s + m, 0), n = CU.queue.length;
  closeLogDay();
  toast(`Catch-up done: ${dur(total)} logged over ${n} ${n === 1 ? 'day' : 'days'}.`);
}
/* Log the day closed (logday.js): the catch-up ends, unless it is moving on to its next day */
function cuClosed() {
  if (CU.moving) return;
  Object.assign(CU, {picking: false, queue: [], i: 0, done: {}});
}

$('#review').addEventListener('click', ev => {
  const t = ev.target.closest('button, input'); if (!t) return;
  if (t.id === 'cu-open') { Object.assign(CU, {picking: true, shown: 7}); LD.open = null; $('#ld-pk').hidden = true; cuLoad(); return; }
  if (t.id === 'cu-back') { CU.picking = false; renderReview(); return; }
  if (t.id === 'cu-more') { CU.shown = CU_MAX; renderReview(); $('#review .cu-d input:not([disabled])')?.focus(); return; }
  if (t.dataset.cuDay) { t.checked ? CU.ticked.add(t.dataset.cuDay) : CU.ticked.delete(t.dataset.cuDay); renderReview(); $(`#review [data-cu-day="${t.dataset.cuDay}"]`)?.focus(); return; }
  if (t.id === 'cu-go') {
    const shown = new Set(CU.days.slice(-CU.shown).map(d => d.date));
    Object.assign(CU, {picking: false, queue: [...CU.ticked].filter(d => shown.has(d)).sort(), i: 0, done: {}});
    if (CU.queue.length) cuOpenDay(CU.queue[0]);
    return;
  }
  if (t.id === 'cu-next') cuNext();
});
