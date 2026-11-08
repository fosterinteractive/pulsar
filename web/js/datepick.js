/* Date fields: a button with the day in words that opens a small month calendar.
   Each <input type="date"> stays in the page, hidden, and keeps what the views rely on: its value is YYYY-MM-DD, a pick
   sets it and fires input and change, code that sets .value repaints the button, and a disabled field disables it. The keys follow the WAI-ARIA
   date picker dialog: arrows by day and week, PageUp/PageDown by month (with Shift by year), Home/End to the week's
   first and last day, Enter or Space picks, Escape closes and goes back to the button. Weeks start on Monday, or on
   the Mac's first weekday in Pulsar's window (PULSAR_APP.prefs.firstWeekday, 1 = Sunday). */
(() => {
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
const VALUE = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
const DISABLED = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'disabled');
const ICON = '<svg class="dp-ic" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><rect x="2" y="3" width="12" height="11" rx="2.5" '
  + 'fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M2.5 6.5h11M5.5 1.6v2.6M10.5 1.6v2.6" fill="none" stroke="currentColor" '
  + 'stroke-width="1.4" stroke-linecap="round"/></svg>';

/* the words are built from the date's parts, not from a locale's format, which changes between macOS versions */
const parts = d => d.split('-').map(Number);
const weekday = d => (new Date(d + 'T12:00:00Z').getUTCDay() + 6) % 7;  // Monday 0 ... Sunday 6
/* the week's first day as Date.getUTCDay counts (0 Sunday ... 6 Saturday), and a day's column in the calendar */
const FIRST = (f => Number.isInteger(f) && f >= 1 && f <= 7 ? f - 1 : 1)(window.PULSAR_APP?.prefs?.firstWeekday);
const col = d => (new Date(d + 'T12:00:00Z').getUTCDay() - FIRST + 7) % 7;
const ORDER = [0, 1, 2, 3, 4, 5, 6].map(i => (FIRST + i) % 7);  // the columns, as getUTCDay numbers
const WEEKEND = n => n === 0 || n === 6;
const NAME = n => DAYS[(n + 6) % 7];
const shortLabel = d => {
  const [y, m, day] = parts(d);
  return `${DAYS[weekday(d)].slice(0, 3)} ${day} ${MONTHS[m - 1].slice(0, 3)}${y !== +todayLocal().slice(0, 4) ? ' ' + y : ''}`;
};
const longLabel = d => { const [y, m, day] = parts(d); return `${DAYS[weekday(d)]} ${day} ${MONTHS[m - 1]} ${y}`; };
/* the same day n months on, or the month's last day when it has fewer (31 March + 1 month is 30 April) */
function addMonths(d, n) {
  const [y, m, day] = parts(d), t = new Date(Date.UTC(y, m - 1 + n, 1, 12));
  t.setUTCDate(Math.min(day, new Date(Date.UTC(t.getUTCFullYear(), t.getUTCMonth() + 1, 0, 12)).getUTCDate()));
  return t.toISOString().slice(0, 10);
}

/* one calendar for the page, moved next to the field it opens for: inside a modal dialog it has to sit in the dialog,
   or the dialog would make it inert */
const pop = document.createElement('div');
pop.className = 'dp'; pop.hidden = true;
pop.setAttribute('role', 'dialog'); pop.setAttribute('aria-modal', 'true');
pop.innerHTML = `<div class="dp-h"><div class="dp-title" id="dp-title" aria-live="polite"></div>
  <button type="button" class="btn icon" data-step="-1" aria-label="Previous month" title="Previous month (Page Up)">‹</button>
  <button type="button" class="btn icon" data-step="1" aria-label="Next month" title="Next month (Page Down)">›</button></div>
  <table class="dp-grid" role="grid" aria-labelledby="dp-title"><thead><tr>${ORDER.map(n =>
    `<th scope="col" abbr="${NAME(n)}"${WEEKEND(n) ? ' class="we"' : ''}>${NAME(n).slice(0, 2)}</th>`).join('')}</tr></thead><tbody></tbody></table>
  <div class="dp-f"><button type="button" class="btn bordered sm" data-today>Today</button></div>`;
const body = pop.querySelector('tbody'), title = pop.querySelector('.dp-title');
let cur = null;  // the open field: {input, btn, focus: the day with the keyboard focus, month: the month drawn}

/* the month of the focused day, a week a row from the first weekday; other months' days are left blank */
function draw() {
  const month = cur.focus.slice(0, 7);
  if (cur.month !== month) {
    cur.month = month;
    const [y, m] = parts(month + '-01'), first = month + '-01', start = shiftDate(first, -col(first));
    const weeks = Math.ceil((col(first) + new Date(Date.UTC(y, m, 0, 12)).getUTCDate()) / 7);
    const today = todayLocal(), sel = cur.input.value;
    title.textContent = `${MONTHS[m - 1]} ${y}`;
    let rows = '';
    for (let w = 0; w < weeks; w++) {
      rows += '<tr>';
      for (let i = 0; i < 7; i++) {
        const d = shiftDate(start, w * 7 + i);
        rows += d.slice(0, 7) !== month ? '<td class="dp-out"></td>'
          : `<td tabindex="-1" data-date="${d}"${WEEKEND(ORDER[i]) ? ' class="we"' : ''} aria-label="${longLabel(d)}" aria-selected="${d === sel}"`
            + `${d === today ? ' aria-current="date"' : ''}>${+d.slice(8)}</td>`;
      }
      rows += '</tr>';
    }
    body.innerHTML = rows;
    place();  // a month of four, five or six weeks
  }
  body.querySelectorAll('td[tabindex="0"]').forEach(td => { td.tabIndex = -1; });
  body.querySelector(`td[data-date="${cur.focus}"]`).tabIndex = 0;
}
function moveTo(d) {
  if (!validDate(d)) return;
  cur.focus = d; draw();
  body.querySelector(`td[data-date="${d}"]`).focus();
}
/* below the button, or above it when there is no room below; always inside the window */
function place() {
  const r = cur.btn.getBoundingClientRect(), w = pop.offsetWidth, h = pop.offsetHeight, m = 8;
  const left = Math.max(m, Math.min(r.left, innerWidth - w - m));
  const top = r.bottom + 6 + h <= innerHeight - m || r.top - 6 - h < m ? r.bottom + 6 : r.top - 6 - h;
  pop.style.left = `${left}px`; pop.style.top = `${Math.max(m, top)}px`;
}
function open(input, btn) {
  cur = {input, btn, focus: validDate(input.value) ? input.value : todayLocal(), month: null};
  pop.setAttribute('aria-label', `Choose ${btn.dataset.name.toLowerCase()}`);
  const dlg = btn.closest('dialog');
  (dlg || document.body).append(pop);
  dlg?.addEventListener('close', () => close(false), {once: true});
  pop.hidden = false; btn.setAttribute('aria-expanded', 'true');
  draw(); place();
  body.querySelector('td[tabindex="0"]').focus();
}
function close(refocus) {
  if (!cur) return;
  const btn = cur.btn;
  cur = null; pop.hidden = true; btn.setAttribute('aria-expanded', 'false');
  if (refocus) btn.focus();
}
/* a pick sets the field and tells the views, as the browser's own field does: nothing fires when the day is the same */
function pick(d) {
  const input = cur.input;
  close(true);
  if (!validDate(d) || d === input.value) return;
  input.value = d;
  input.dispatchEvent(new Event('input', {bubbles: true}));
  input.dispatchEvent(new Event('change', {bubbles: true}));
}

const KEYS = {
  ArrowLeft: d => shiftDate(d, -1), ArrowRight: d => shiftDate(d, 1), ArrowUp: d => shiftDate(d, -7), ArrowDown: d => shiftDate(d, 7),
  Home: d => shiftDate(d, -col(d)), End: d => shiftDate(d, 6 - col(d)),
  PageUp: (d, ev) => addMonths(d, ev.shiftKey ? -12 : -1), PageDown: (d, ev) => addMonths(d, ev.shiftKey ? 12 : 1),
};
/* every key stays in the calendar: the day view's own arrows and T, and a dialog's Escape, wait until it is closed */
pop.addEventListener('keydown', ev => {
  ev.stopPropagation();
  if (!cur) return;
  if (ev.key === 'Escape') { ev.preventDefault(); close(true); return; }
  if (ev.key === 'Tab') {  // Tab goes round the month arrows, the focused day and Today
    const stops = [...pop.querySelectorAll('button, td[tabindex="0"]')], i = stops.indexOf(document.activeElement);
    if (ev.shiftKey && i <= 0) { ev.preventDefault(); stops.at(-1).focus(); }
    else if (!ev.shiftKey && i === stops.length - 1) { ev.preventDefault(); stops[0].focus(); }
    return;
  }
  const td = ev.target.closest('td[data-date]');
  if (!td || ev.metaKey || ev.ctrlKey || ev.altKey) return;
  if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); pick(td.dataset.date); return; }
  if (KEYS[ev.key]) { ev.preventDefault(); moveTo(KEYS[ev.key](cur.focus, ev)); }
});
pop.addEventListener('click', ev => {
  const td = ev.target.closest('td[data-date]'), step = ev.target.closest('[data-step]');
  if (td) pick(td.dataset.date);
  else if (step) { cur.focus = addMonths(cur.focus, +step.dataset.step); draw(); }
  else if (ev.target.closest('[data-today]')) pick(todayLocal());
});
/* a click anywhere else closes it, leaving the focus where the click put it */
document.addEventListener('pointerdown', ev => {
  const t = /** @type {Node} */ (ev.target);
  if (cur && !pop.contains(t) && !cur.btn.contains(t)) close(false);
}, true);
addEventListener('resize', () => cur && place());
document.addEventListener('scroll', () => cur && place(), true);

/* name: what the field is for, said before its day ("From: Mon 7 Oct"); the input's aria-label, else its <label> */
function enhance(input, name) {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = `dp-btn ${input.className}`.trim();  // dayonly and the like still apply
  btn.dataset.name = name;
  btn.setAttribute('aria-haspopup', 'dialog'); btn.setAttribute('aria-expanded', 'false');
  btn.innerHTML = `${ICON}<span></span>`;
  input.before(btn);  // first in a <label>, so clicking the label's text opens the calendar
  input.hidden = true;
  const paint = () => {
    const v = VALUE.get.call(input), text = validDate(v) ? shortLabel(v) : 'Pick a date';
    btn.lastChild.textContent = text;
    btn.setAttribute('aria-label', `${name}: ${text}`);
    btn.title = validDate(v) ? longLabel(v) : '';
  };
  Object.defineProperty(input, 'value', {
    configurable: true,
    get() { return VALUE.get.call(this); },
    set(v) { VALUE.set.call(this, v); paint(); },
  });
  // a disabled field (the date of an entry being edited, which stays on its day) cannot be changed from its calendar
  Object.defineProperty(input, 'disabled', {
    configurable: true,
    get() { return DISABLED.get.call(this); },
    set(v) { DISABLED.set.call(this, v); btn.disabled = !!v; if (v && cur?.input === input) close(false); },
  });
  btn.addEventListener('click', () => {
    if (input.disabled) return;
    const was = cur?.input === input;
    close(was);  // a second click closes it
    if (!was) open(input, btn);
  });
  paint();
  btn.disabled = input.disabled;
}
enhance($('#date'), 'Day');
enhance($('#rfrom'), 'From');
enhance($('#rto'), 'To');
enhance($('#log-form').elements.date, 'Date');
})();
