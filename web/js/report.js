/* ================= Report: a range of days ================= */
const RANGES = ['today', 'this-week', 'last-week', 'this-month', 'last-month', '30d'];
const DEFAULT_RANGE = 'this-week';  // what the Report opens on
const MAX_DAYS = 93;
/* the problem with a custom range, or '' when it can load */
function rangeProblem(from, to) {
  if (!validDate(from) || !validDate(to)) return 'Pick both dates.';
  if (from > to) return 'The start date is after the end date.';
  if (daysBetween(from, to) + 1 > MAX_DAYS) return `Pick a range of at most ${MAX_DAYS} days.`;
  return '';
}
/* from the address: a preset, or custom with two good dates; anything else is this week */
const R = (() => {
  const range = params.get('range'), from = params.get('from'), to = params.get('to');
  const ok = range === 'custom' ? !rangeProblem(from, to) : RANGES.includes(range);
  return {range: ok ? range : DEFAULT_RANGE, from: ok && range === 'custom' ? from : '', to: ok && range === 'custom' ? to : '', data: null, days: [], seq: 0, at: 0};
})();
const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const WEEKDAYS_FULL = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
const pct = (a, b) => (b ? Math.round(a / b * 100) : 0);
const hh = h => String(h % 24).padStart(2, '0') + ':00';
/** @type {(d: string, opts?: Intl.DateTimeFormatOptions) => string} */
const dayLabel = (d, opts = {weekday: 'short', day: 'numeric', month: 'short'}) => {
  const t = new Date(d + 'T12:00:00Z');
  return isNaN(t.getTime()) ? String(d ?? '') : new Intl.DateTimeFormat(DATE_LOCALE, {...opts, timeZone: 'UTC'}).format(t);
};
/* sequential: one blue ramp, more is stronger; empty cells stay the neutral well */
const seq = t => (t <= 0 ? 'var(--seg)' : `color-mix(in oklab, var(--seq-hi) ${Math.round(14 + 86 * Math.min(1, t))}%, var(--seq-lo))`);

function rangeDates(key) {
  const t = todayLocal(), base = new Date(t + 'T12:00:00Z'), wd = (base.getUTCDay() + 6) % 7;
  const at = n => { const x = new Date(base); x.setUTCDate(x.getUTCDate() + n); return x.toISOString().slice(0, 10); };
  if (key === 'today') return [t, t];
  if (key === 'this-week') return [at(-wd), t];
  if (key === 'last-week') return [at(-wd - 7), at(-wd - 1)];
  if (key === 'this-month') return [t.slice(0, 8) + '01', t];
  if (key === 'last-month') {
    const y = base.getUTCFullYear(), m = base.getUTCMonth();
    return [new Date(Date.UTC(y, m - 1, 1, 12)).toISOString().slice(0, 10), new Date(Date.UTC(y, m, 0, 12)).toISOString().slice(0, 10)];
  }
  return [at(-29), t];
}
function paintFilters() {
  document.querySelectorAll('#range button').forEach(b => { b.classList.toggle('on', b.dataset.v === R.range); b.setAttribute('aria-pressed', String(b.dataset.v === R.range)); });
  $('#rfrom').value = R.from; $('#rto').value = R.to; $('#rmsg').textContent = '';
  $('#print-title').textContent = R.from && R.to ? `Pulsar report, ${dayLabel(R.from, {day: 'numeric', month: 'short'})} to ${dayLabel(R.to, {day: 'numeric', month: 'short', year: 'numeric'})}` : 'Pulsar report';
  $('#rlabel').textContent = R.from && R.to
    ? `${dayLabel(R.from, {day: 'numeric', month: 'short'})} to ${dayLabel(R.to, {day: 'numeric', month: 'short', year: 'numeric'})}` : '';
}
/* quiet: the report on screen stays as it is (not dimmed) while it is read again, only ↻ turns: going back to the tab */
async function loadReport(note = false, quiet = false) {
  if (R.range !== 'custom' || rangeProblem(R.from, R.to)) { if (!RANGES.includes(R.range)) R.range = DEFAULT_RANGE; [R.from, R.to] = rangeDates(R.range); }
  syncURL(); paintFilters();
  const id = ++R.seq, btn = $('#refresh'), asked = Date.now();
  if (!quiet) $('#report').classList.add('loading');
  btn.classList.add('busy');
  const x = await getJSON(`/api/report?from=${R.from}&to=${R.to}`);
  if (id !== R.seq) return;
  $('#report').classList.remove('loading'); btn.classList.remove('busy');
  R.data = x; R.at = asked; renderReport(); paintUpdated();
  if (note) toast(x.error ? `Report not updated: ${x.error}` : x.warning ? x.warning
    : `Report updated: ${x.entries.length} entries, ${dur(x.entries.reduce((s, e) => s + e.minutes, 0))}`, !!(x.error || x.warning));
}

/* going to the Report reads it again unless it was read in the last minute and nothing was logged from this page
   since; work logged elsewhere (Claude Code, Productive) shows within that minute, or on ↻ */
const REPORT_FRESH = 60000;
const reportStale = (now = Date.now()) => !R.data || now - (R.at || 0) > REPORT_FRESH || wroteAt > (R.at || 0);

/* hover: one tooltip, values first; every value is also in a table */
let tips = [];
const tipAttr = (title, rows) => `data-tip="${tips.push({title, rows}) - 1}"`;
const tipEl = $('#tip');
document.addEventListener('pointermove', ev => {
  const t = state.view === 'report' && ev.target.closest ? ev.target.closest('[data-tip]') : null;
  if (!t) { tipEl.classList.remove('on'); return; }
  const d = tips[+t.dataset.tip]; if (!d) return;
  if (tipEl.dataset.cur !== t.dataset.tip) {
    tipEl.dataset.cur = t.dataset.tip; tipEl.replaceChildren();
    const h = document.createElement('div'); h.className = 'tt'; h.textContent = d.title; tipEl.append(h);
    for (const [value, label, color] of d.rows) {
      const r = document.createElement('div'); r.className = 'tr';
      if (color) { const k = document.createElement('i'); k.style.background = color; r.append(k); }
      const b = document.createElement('b'); b.textContent = value;
      const sp = document.createElement('span'); sp.textContent = label;
      r.append(b, sp); tipEl.append(r);
    }
  }
  tipEl.classList.add('on');
  const w = tipEl.offsetWidth, h = tipEl.offsetHeight;
  tipEl.style.left = Math.max(8, Math.min(ev.clientX + 14, innerWidth - w - 8)) + 'px';
  tipEl.style.top = (ev.clientY + 16 + h > innerHeight ? ev.clientY - h - 12 : ev.clientY + 16) + 'px';
});

function renderReport() {
  const x = R.data, body = $('#rbody');
  tips = []; tipEl.dataset.cur = ''; tipEl.classList.remove('on');
  if (!x) return;
  if (x.error) { body.innerHTML = `<div class="banner">${esc(x.error)}</div>`; return; }
  const days = R.days = x.days.filter(d => d.date <= x.today);
  const work = days.filter(d => d.weekday < 5);
  const sum = (a, k) => a.reduce((s, d) => s + d[k], 0);
  const logged = sum(days, 'logged'), active = sum(days, 'active'), notLogged = sum(days, 'notLogged');
  const billable = sum(days, 'billable'), unknownBill = Math.max(0, logged - billable - sum(days, 'nonBillable'));
  const knownBill = logged - unknownBill;  // billing Productive does not show this person counts as neither
  const approved = sum(days, 'approved'), target = work.length * TARGET, workLogged = sum(work, 'logged');
  const n8 = work.filter(d => d.logged >= TARGET).length, activeDays = days.filter(d => d.active > 0).length;

  const projects = new Map();
  for (const e of x.entries) {
    let p = projects.get(e.project);
    if (!p) projects.set(e.project, p = {name: e.project, minutes: 0, rows: new Map(), byDay: new Map()});
    p.minutes += e.minutes; p.billable = (p.billable || 0) + (e.billable ? e.minutes : 0);
    p.nonBillable = (p.nonBillable || 0) + (e.billable === false ? e.minutes : 0); p.byDay.set(e.date, (p.byDay.get(e.date) || 0) + e.minutes);
    const k = (e.budget || e.task) + '|' + e.service;
    let r = p.rows.get(k); if (!r) p.rows.set(k, r = {budget: e.budget || e.task, service: e.service, billable: e.billable, billing: e.billing, minutes: 0});
    r.minutes += e.minutes;
  }
  const plist = [...projects.values()].sort((a, b) => b.minutes - a.minutes);
  assignHues(plist.map(p => p.name));
  const rowsOf = p => [...p.rows.values()].sort((a, b) => b.minutes - a.minutes);

  let html = x.trackerWarning ? `<div class="banner">${esc(x.trackerWarning)}</div>` : '';
  html += x.warning ? `<div class="banner">${esc(x.warning)}${x.fromCopy ? '' : ' Logged time is missing from this report.'}`
    + `${x.needsSetup ? ' <button class="btn bordered" data-open-settings>Open Settings</button>' : ''}</div>` : '';
  html += `<div class="tiles">
    ${tile('Logged', dur(logged), target ? `${pct(logged, target)}% of the ${dur(target)} target` : 'no workdays in this range')}
    ${tile('Active, work', dur(active), `${activeDays} ${activeDays === 1 ? 'day' : 'days'} with activity`)}
    ${tile('Active, not logged', dur(notLogged), active ? `${pct(notLogged, active)}% of active work time` : 'no activity recorded')}
    ${tile('Logged per workday', work.length ? dur(workLogged / work.length) : '–', `${n8} of ${work.length} workdays reached ${dur(TARGET)}`)}
    ${tile('Billable', knownBill ? `${pct(billable, knownBill)}%` : '–', !unknownBill ? `${dur(billable)} of ${dur(logged)} logged`
      : knownBill ? `${dur(billable)} of ${dur(knownBill)} with known billing; ${dur(unknownBill)} unknown` : 'Productive does not show your billing')}
    ${tile('Approved', `${pct(approved, logged)}%`, `${dur(approved)} of ${dur(logged)}`)}
  </div>`;
  html += `<section class="chart"><div class="ch"><h2>Logged and active, by day</h2>
      <div class="legend"><span><i style="background:var(--accent)"></i>Logged, billable</span><span><i style="background:var(--accent2)"></i>Logged, not billable</span>${unknownBill ? '<span><i style="background:var(--unknown)"></i>Logged, billing unknown</span>' : ''}<span><i style="background:var(--deemph)"></i>Active, work</span><span><i class="line"></i>${dur(TARGET)} target</span></div></div>
    <div class="take">${esc(work.length ? `${n8} of ${work.length} workdays reached ${dur(TARGET)} logged.${active ? ` Logged time is ${pct(logged, active)}% of active work time.` : ''}` : 'No workdays in this range.')}</div>
    <div class="plot" id="p-daily"></div></section>`;
  html += `<div class="two">
    <section class="chart"><div class="ch"><h2>Logged by project</h2></div>
      <div class="take">${esc(plist.length ? `${plist[0].name}: ${pct(plist[0].minutes, logged)}% of logged time, across ${plist.length} ${plist.length === 1 ? 'project' : 'projects'}.` : 'Nothing logged in this range.')}</div>
      ${rankBars(plist.map(p => ({name: p.name, minutes: p.minutes, billable: p.billable, nonBillable: p.nonBillable,
          more: rowsOf(p).slice(0, 6).map(r => [dur(r.minutes), r.budget + (r.service ? ' · ' + r.service : '')])})), logged)}
      ${plist.length ? `<details class="more"><summary>Budgets and services</summary><div class="tblwrap">${budgetTable(plist, rowsOf)}</div></details>` : ''}</section>
    <section class="chart"><div class="ch"><h2>Top apps and sites</h2></div>
      <div class="take">${esc(x.apps.length ? `${x.apps[0].name}: ${pct(x.apps[0].minutes, x.activeTotal)}% of active work time.` : 'No activity recorded in this range.')}</div>
      ${rankBars(x.apps.map(a => ({name: a.name, minutes: a.minutes, icon: a.icon})), x.activeTotal)}</section>
  </div>`;
  const busiest = days.reduce((b, d) => (!b || d.logged > b.logged ? d : b), null);
  html += `<section class="chart"><div class="ch"><h2>Projects by day</h2><div class="legend">Less<span class="ramp"></span>More</div></div>
    <div class="take">${esc(busiest && busiest.logged ? `Busiest day: ${dayLabel(busiest.date)}, ${dur(busiest.logged)} logged.` : 'Nothing logged in this range.')}</div>
    <div class="tblwrap">${projectMatrix(plist, days)}</div>
    ${plist.length ? `<details class="more astable"><summary>Show as a table</summary><div class="tblwrap">${projectTable(plist, days)}</div></details>` : ''}</section>`;
  html += `<section class="chart"><div class="ch"><h2>When you work</h2><div class="legend">Less<span class="ramp"></span>More</div></div>
    <div class="take">${esc(hoursTake(x.hours))}</div><div class="tblwrap">${hourMatrix(x.hours, days)}</div></section>`;
  const taskRows = new Map();
  for (const e of x.entries) {
    if (!e.taskId) continue;
    const t = taskRows.get(e.taskId) || {taskId: e.taskId, title: e.task, project: e.project, minutes: 0};
    t.minutes += e.minutes; taskRows.set(e.taskId, t);
  }
  R.tasks = [...taskRows.values()].sort((a, b) => b.minutes - a.minutes).slice(0, 40);
  html += `<section class="chart"><div class="ch"><h2>Tasks against estimates</h2></div>
    <div class="take" id="tasks-take">Reading estimates from Productive…</div><div class="tblwrap" id="tasks-table"></div></section>`;
  html += `<section class="chart"><div class="ch"><h2>Days</h2></div><div class="tblwrap">${daysTable(days)}</div></section>`;
  body.innerHTML = html;
  dailyChart(days);
  loadReportEstimates();
}

async function loadReportEstimates() {
  const seq = R.seq, rows = R.tasks || [];
  if (!rows.length) { $('#tasks-take').textContent = 'Nothing logged in this range.'; return; }
  const r = await getJSON(`/api/tasks/estimates?ids=${rows.map(t => t.taskId).join(',')}`);
  if (seq !== R.seq || !$('#tasks-table')) return;
  const est = r.tasks || {};
  const over = rows.filter(t => est[t.taskId]?.estimate && est[t.taskId].worked > est[t.taskId].estimate);
  const none = rows.filter(t => est[t.taskId] && !est[t.taskId].estimate);
  $('#tasks-take').textContent = r.error ? r.error
    : `${over.length} of ${rows.length} tasks are over their estimate; ${none.length} have none. Totals include everyone's time.`;
  $('#tasks-table').innerHTML = `<table class="tbl rowhover"><thead><tr><th>Task</th><th>Logged here</th><th>Logged by everyone</th><th>Estimate</th><th>Left</th></tr></thead><tbody>`
    + rows.map(t => {
      const d = est[t.taskId], isOver = d?.estimate && d.worked > d.estimate;
      const url = productiveTaskURL(t.taskId);  // its page in Productive, when the organization's address is known
      const name = url ? `<a class="tasklink" href="${esc(url)}" target="_blank" rel="noopener noreferrer"
          aria-label="${esc(`Open “${t.title}” in Productive`)}">${esc(t.title)}</a>` : esc(t.title);
      return `<tr><td><span class="dot" style="background:${color(t.project)};display:inline-block;margin-right:6px"></span>${name}
          <small style="color:var(--muted)"> · ${esc(t.project)}</small></td><td>${dur(t.minutes)}</td>
        <td>${d?.worked != null ? dur(d.worked) : '–'}</td><td>${d?.estimate ? dur(d.estimate) : '–'}</td>
        <td${isOver ? ' style="color:var(--gap)"' : ''}>${!d?.estimate ? '–' : isOver ? `⚠ over by ${dur(d.worked - d.estimate)}` : dur(d.estimate - d.worked)}</td></tr>`;
    }).join('') + '</tbody></table>';
}
const tile = (l, v, s) => `<div class="tile"><div class="l">${esc(l)}</div><div class="v">${esc(v)}</div><div class="s">${esc(s)}</div></div>`;

function colPath(x, y, w, h, fill, ring) {  // 4px rounded data end, square at the baseline
  const r = Math.min(4, w / 2, h);
  return `<path d="M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z" fill="${fill}"`
    + (ring ? ' stroke="var(--panel)" stroke-width="2" paint-order="stroke"' : '') + '/>';
}
function dailyChart(days) {
  const el = $('#p-daily'); if (!el) return;
  const W = Math.max(200, el.clientWidth), H = 240, ml = 40, mr = 34, mt = 8, mb = 24;
  const n = Math.max(1, days.length), pw = W - ml - mr, ph = H - mt - mb, band = pw / n;
  const maxV = Math.max(TARGET, ...days.map(d => Math.max(d.logged, d.active)));
  const step = maxV > 960 ? 240 : 120, yMax = Math.ceil(maxV / step) * step, y = v => mt + ph - v / yMax * ph;
  const gw = Math.min(22, band * 0.72), bw = Math.max(3, Math.min(10, gw * 0.5));
  const every = band >= 24 ? 1 : band >= 13 ? 2 : band >= 7 ? 4 : 7;
  let s = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Logged and active work time per day. The Days table below lists every value.">`;
  days.forEach((d, i) => { if (d.weekday >= 5) s += `<rect x="${ml + i * band}" y="${mt}" width="${band}" height="${ph}" fill="var(--weekend)"/>`; });
  for (let v = 0; v <= yMax; v += step)
    s += `<line x1="${ml}" x2="${W - mr}" y1="${y(v)}" y2="${y(v)}" stroke="${v ? 'var(--grid)' : 'var(--axis)'}" shape-rendering="crispEdges"/>`
      + `<text class="tick" x="${ml - 8}" y="${y(v) + 4}" text-anchor="end">${v / 60} h</text>`;
  days.forEach((d, i) => {
    const cx = ml + i * band + band / 2;
    if (d.active) s += colPath(cx - gw / 2, y(d.active), gw, y(0) - y(d.active), 'var(--deemph)');
    if (d.logged) {  // stacked from the baseline: billable, not billable, billing unknown; 2px of surface between
      const parts = billParts(d.logged, d.billable, d.nonBillable);
      let base = 0;
      parts.forEach(([m, fill], k) => {
        const y0 = y(base), y1 = y(base + m);
        s += k === parts.length - 1 ? colPath(cx - bw / 2, y1, bw, Math.max(0, y0 - y1 - (k ? 2 : 0)), fill, true)
          : `<rect x="${cx - bw / 2}" y="${y1}" width="${bw}" height="${Math.max(0, y0 - y1 - (k ? 2 : 0))}" fill="${fill}" stroke="var(--panel)" stroke-width="2" paint-order="stroke"/>`;
        base += m;
      });
    }
    if (i % every === 0) s += `<text class="tick" x="${cx}" y="${H - 6}" text-anchor="middle">${+d.date.slice(8)}</text>`;
  });
  s += `<line x1="${ml}" x2="${W - mr}" y1="${y(TARGET)}" y2="${y(TARGET)}" stroke="var(--muted)" stroke-opacity=".6" shape-rendering="crispEdges"/>`
    + `<text class="tick" x="${W - mr + 6}" y="${y(TARGET) + 4}">${dur(TARGET)}</text>`;
  days.forEach((d, i) => {
    s += `<rect class="hit" data-day="${d.date}" x="${ml + i * band}" y="${mt}" width="${band}" height="${ph}" ${tipAttr(dayLabel(d.date) + ' · click to open', [
      [dur(d.logged), 'logged'], [dur(d.billable), 'billable', 'var(--accent)'], [dur(d.nonBillable), 'not billable', 'var(--accent2)'],
      ...(d.logged - d.billable - d.nonBillable > 0 ? [[dur(d.logged - d.billable - d.nonBillable), 'billing unknown', 'var(--unknown)']] : []),
      [dur(d.active), 'active, work', 'var(--deemph)'],
      [dur(d.notLogged), 'active, not logged'], [String(d.entries), d.entries === 1 ? 'entry' : 'entries']])}/>`;
  });
  el.innerHTML = s + '</svg>';
}
/* [minutes, colour] for billable, not billable and billing unknown, the empty ones left out */
/** @returns {[number, string][]} */
function billParts(total, billable, nonBillable) {
  const b = Math.min(billable || 0, total), n = Math.min(nonBillable || 0, total - b);
  /** @type {[number, string][]} */
  const parts = [[b, 'var(--accent)'], [n, 'var(--accent2)'], [total - b - n, 'var(--unknown)']];
  return parts.filter(p => p[0] > 0);
}
function barOf(it, max) {
  const width = `width:${(it.minutes / max * 100).toFixed(1)}%`;
  if (it.billable == null) return `<i style="${width}"></i>`;  // not about billing (apps and sites)
  const parts = billParts(it.minutes, it.billable, it.nonBillable);
  if (parts.length < 2) return `<i style="${width};background:${(parts[0] || [0, 'var(--unknown)'])[1]}"></i>`;
  return `<span class="seg2" style="${width}">${parts.map(([m, fill]) => `<i style="flex:${m};background:${fill}"></i>`).join('')}</span>`;
}
function rankBars(items, total) {
  if (!items.length) return '';
  const max = Math.max(1, ...items.map(i => i.minutes));
  return items.map(it => `<div class="rb" ${tipAttr(it.name, [[dur(it.minutes), `${pct(it.minutes, total)}% of the total`],
    ...(it.billable != null ? [[dur(it.billable), 'billable', 'var(--accent)']] : []), ...(it.more || [])])}>
    <span class="rn">${'icon' in it ? icon(it.icon, it.name) : ''}<span>${esc(it.name)}</span></span>
    <span class="rt">${barOf(it, max)}</span>
    <span class="rbv num">${dur(it.minutes)}<small>${pct(it.minutes, total)}%</small></span></div>`).join('');
}
function budgetTable(plist, rowsOf) {
  return `<table class="tbl wrap"><thead><tr><th>Project and budget</th><th class="txt">Service</th><th class="txt">Billing</th><th>Logged</th></tr></thead><tbody>`
    + plist.map(p => `<tr><td class="txt"><b>${esc(p.name)}</b></td><td></td><td></td><td><b>${dur(p.minutes)}</b></td></tr>`
      + rowsOf(p).map(r => `<tr><td class="txt sub1">${esc(r.budget)}</td><td class="txt">${esc(r.service)}</td><td class="txt">${esc(r.billing || 'Unknown')}</td><td>${dur(r.minutes)}</td></tr>`).join('')).join('')
    + '</tbody></table>';
}
function matrixRows(plist) {
  const rows = plist.slice(0, 8).map(p => ({name: p.name, byDay: p.byDay, minutes: p.minutes}));
  const rest = plist.slice(8);
  if (rest.length) {  // a ninth row would need a ninth colour nobody can tell apart; fold the tail
    const byDay = new Map(); let m = 0;
    for (const p of rest) for (const [d, v] of p.byDay) { byDay.set(d, (byDay.get(d) || 0) + v); m += v; }
    rows.push({name: `Other (${rest.length} projects)`, byDay, minutes: m});
  }
  return rows;
}
function projectMatrix(plist, days) {
  if (!plist.length) return '';
  const rows = matrixRows(plist);
  const max = Math.max(1, ...rows.flatMap(r => [...r.byDay.values()]));
  const every = days.length > 45 ? 7 : days.length > 16 ? 2 : 1;
  let h = `<div class="hm" style="grid-template-columns: minmax(90px, 210px) repeat(${days.length}, minmax(6px, 1fr)) auto">`;
  h += '<span></span>' + days.map((d, i) => `<span class="col">${i % every === 0 ? +d.date.slice(8) : ''}</span>`).join('') + '<span class="tot">Total</span>';
  for (const r of rows) {
    h += `<span class="lab pname" title="${esc(r.name)}">${esc(r.name)}</span>`;
    h += days.map(d => { const v = r.byDay.get(d.date) || 0;
      return `<span class="c" style="background:${seq(v / max)}" ${tipAttr(`${r.name} · ${dayLabel(d.date)}`, [[dur(v), 'logged']])}></span>`; }).join('');
    h += `<span class="tot">${dur(r.minutes)}</span>`;
  }
  return h + '</div>';
}
/* the heatmap's numbers, a row per day (the cells themselves only answer the mouse) */
function projectTable(plist, days) {
  const rows = matrixRows(plist);
  return `<table class="tbl wrap"><thead><tr><th>Day</th>${rows.map(r => `<th class="pcol">${esc(r.name)}</th>`).join('')}<th>Total</th></tr></thead><tbody>`
    + days.map(d => {
      const vals = rows.map(r => r.byDay.get(d.date) || 0), tot = vals.reduce((a, b) => a + b, 0);
      return `<tr class="${d.weekday >= 5 ? 'we' : ''}"><td>${dayLink(d.date)}</td>${vals.map(v => `<td>${v ? dur(v) : '–'}</td>`).join('')}<td>${tot ? dur(tot) : '–'}</td></tr>`;
    }).join('')
    + `</tbody><tfoot><tr><td>Total</td>${rows.map(r => `<td>${dur(r.minutes)}</td>`).join('')}<td>${dur(rows.reduce((a, r) => a + r.minutes, 0))}</td></tr></tfoot></table>`;
}
function hoursTake(hours) {
  const byHour = Array.from({length: 24}, (_, h) => hours.reduce((s, row) => s + row[h], 0));
  if (!byHour.some(Boolean)) return 'No activity recorded in this range.';
  let best = 0; for (let h = 0; h < 22; h++) if (byHour[h] + byHour[h + 1] + byHour[h + 2] > byHour[best] + byHour[best + 1] + byHour[best + 2]) best = h;
  const totals = hours.map(row => row.reduce((a, b) => a + b, 0)), wd = totals.indexOf(Math.max(...totals));
  return `Most active hours: ${hh(best)} to ${hh(best + 3)}. Busiest weekday: ${WEEKDAYS_FULL[wd]}.`;
}
function hourMatrix(hours, days) {
  const counts = [0, 0, 0, 0, 0, 0, 0]; for (const d of days) counts[d.weekday]++;
  const max = Math.max(1, ...hours.flat());
  let h = `<div class="hm" style="grid-template-columns: 44px repeat(24, minmax(8px, 1fr))">`;
  h += '<span></span>' + Array.from({length: 24}, (_, i) => `<span class="col">${i % 3 === 0 ? String(i).padStart(2, '0') : ''}</span>`).join('');
  hours.forEach((row, w) => {
    h += `<span class="lab">${WEEKDAYS[w]}</span>`;
    h += row.map((v, hr) => `<span class="c" style="background:${seq(v / max)}" ${tipAttr(`${WEEKDAYS_FULL[w]}s, ${hh(hr)} to ${hh(hr + 1)}`, [
      [dur(v), `active over ${counts[w]} ${WEEKDAYS_FULL[w]}${counts[w] === 1 ? '' : 's'}`], [dur(counts[w] ? v / counts[w] : 0), 'on average']])}></span>`).join('');
  });
  return h + '</div>';
}
function daysTable(days) {
  const s = k => days.reduce((a, d) => a + d[k], 0);
  const src = d => (d.source === 'aw' ? 'ActivityWatch' : d.source === 'memtime' ? 'MemTime' : '–');
  return `<table class="tbl"><thead><tr><th>Day</th><th>Logged</th><th>Billable</th><th>Active, work</th><th>Not logged</th><th>Personal</th><th>Entries</th><th>Approved</th><th style="text-align:left">Tracker</th></tr></thead><tbody>`
    + [...days].reverse().map(d => `<tr class="${d.weekday >= 5 ? 'we' : ''}"><td>${dayLink(d.date)}</td><td>${dur(d.logged)}</td><td>${dur(d.billable)}</td><td>${dur(d.active)}</td>
      <td>${dur(d.notLogged)}</td><td>${dur(d.personal)}</td><td>${d.entries}</td><td>${d.logged ? pct(d.approved, d.logged) + '%' : '–'}</td><td style="text-align:left">${src(d)}</td></tr>`).join('')
    + `</tbody><tfoot><tr><td>Total</td><td>${dur(s('logged'))}</td><td>${dur(s('billable'))}</td><td>${dur(s('active'))}</td><td>${dur(s('notLogged'))}</td><td>${dur(s('personal'))}</td>
      <td>${s('entries')}</td><td>${pct(s('approved'), s('logged'))}%</td><td></td></tr></tfoot></table>`;
}
let resizeTimer;
addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { if (state.view === 'report' && R.data) dailyChart(R.days); }, 150); });
document.querySelectorAll('#range button').forEach(b => b.onclick = () => { R.range = b.dataset.v; loadReport(); });
/* a day picked in the calendar (datepick.js) fires one change: load at once */
function checkCustomRange() {
  const a = $('#rfrom').value, b = $('#rto').value, problem = rangeProblem(a, b);
  $('#rmsg').textContent = problem;
  if (problem || (a === R.from && b === R.to)) return;  // nothing changed: nothing to reload
  R.range = 'custom'; R.from = a; R.to = b; loadReport();
}
for (const el of [$('#rfrom'), $('#rto')]) el.addEventListener('change', checkCustomRange);

/* ---------- download: CSV for spreadsheets, PDF for reading and invoices, print for paper ---------- */
function toCSV(rows) {  // the BOM makes Excel read the file as UTF-8
  const cell = v => {
    let t = v == null ? '' : String(v);
    if (typeof v === 'string' && /^[=+\-@\t\r]/.test(t)) t = "'" + t;  // a task named =HYPERLINK(...) stays text in Excel
    return /[",\r\n;]/.test(t) ? `"${t.replace(/"/g, '""')}"` : t;
  };
  return '\ufeff' + rows.map(r => r.map(cell).join(',')).join('\r\n') + '\r\n';
}
function saveFile(name, text, type = 'text/csv;charset=utf-8') {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([text], {type})); a.download = name;
  document.body.append(a); a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
}
const hours = m => (m / 60).toFixed(2);
document.addEventListener('click', ev => { if (!ev.target.closest('#report-dl')) $('#report-dl').open = false; });
$('#report-dl').addEventListener('keydown', ev => {
  if (ev.key === 'Escape' && $('#report-dl').open) { ev.preventDefault(); $('#report-dl').open = false; $('#report-dl summary').focus(); }
});
/* ---------- the invoice summary: every task logged in the range, by project, with its time and billable time, and the
   totals (report.invoice on the server: the Billable tile's rule). Printed alone: nothing of the activity. ---------- */
/* a line's billable time (with its share on a subtotal), "Not billable", and the time whose billing Productive does not
   show, as unknown: never guessed */
function billOf(t, share = false) {
  const parts = [t.billable ? dur(t.billable) + (share && t.billablePct != null ? ` (${t.billablePct}%)` : '') : t.nonBillable ? 'Not billable' : ''];
  if (t.unknown) parts.push(t.unknown === t.minutes ? 'Unknown' : `${dur(t.unknown)} unknown`);
  return parts.filter(Boolean).join(', ') || '–';
}
const hoursOf = m => `${hours(m)} h`;
/* the projects in sections, the most time first in each: billable, then of unknown billing (Productive does not show
   it), then not billable. The document and the spreadsheet list them in this order. */
function invoiceSections(inv) {
  const most = list => [...list].sort((a, b) => b.minutes - a.minutes);
  return [{label: 'Billable', ps: most(inv.projects.filter(p => p.billable > 0))},
          {label: 'Billing unknown', ps: most(inv.projects.filter(p => !(p.billable > 0) && p.unknown > 0))},
          {label: 'Not billable', ps: most(inv.projects.filter(p => !(p.billable > 0) && !(p.unknown > 0)))}].filter(x => x.ps.length);
}
/* the invoice summary as a document: who and when, the totals first, then one table where each project opens with its
   subtotal and lists its tasks; numbers right-aligned, what is not billable quieter (style.css, @media print) */
function renderInvoice(x) {
  const inv = x.invoice, sameYear = x.from.slice(0, 4) === x.to.slice(0, 4);
  const range = `${dayLabel(x.from, {day: 'numeric', month: 'long', ...(sameYear ? {} : {year: 'numeric'})})} to ${dayLabel(x.to, {day: 'numeric', month: 'long', year: 'numeric'})}`;
  const pr = CONFIG?.productive || {}, who = [pr.personName, pr.orgName].filter(Boolean).join(' · ');
  const known = inv.billable + inv.nonBillable;
  const stat = (label, value, sub) => `<div class="inv-stat"><span class="l">${esc(label)}</span><span class="v">${esc(value)}</span>${sub ? `<span class="s">${esc(sub)}</span>` : ''}</div>`;
  const bill = t => `<td${t.billable ? '' : ' class="mut"'}>${esc(billOf(t))}</td>`;
  // a project's line: its billable time, then quieter its share of the known billing and the time of unknown billing
  const projBill = p => {
    const quiet = x => ` <span class="inv-pct">${esc(x)}</span>`;
    if (!p.billable) return bill(p);
    return `<td>${esc(dur(p.billable))}${p.billablePct != null && p.billablePct < 100 ? quiet(`${p.billablePct}%`) : ''}${
      p.unknown ? `<span class="inv-pct inv-unknown">${esc(dur(p.unknown))} unknown</span>` : ''}</td>`;
  };
  // a div, not a header: the print styles hide the page's header elements
  $('#invoice-doc').innerHTML = `<div class="inv-head"><div><h1>Invoice summary</h1>${who ? `<p class="inv-who">${esc(who)}</p>` : ''}</div>
      <div class="inv-meta"><p>${esc(range)}</p><p>Prepared ${esc(dayLabel(todayLocal(), {day: 'numeric', month: 'long', year: 'numeric'}))}</p></div></div>
    <section class="inv-stats">${stat('Total time', dur(inv.minutes), hoursOf(inv.minutes))}${stat('Billable', dur(inv.billable), hoursOf(inv.billable))}${
      stat('Billable share', inv.billablePct == null ? 'Unknown' : `${inv.billablePct}%`, inv.unknown && known ? 'of the known billing' : 'of all time')}${
      inv.unknown ? stat('Billing unknown', dur(inv.unknown), 'not shown in Productive') : ''}</section>
    <table class="inv"><colgroup><col><col class="c-share"><col class="c-time"><col class="c-bill"></colgroup>
      <thead><tr><th scope="col">Task</th><th scope="col" class="inv-sh">Share</th><th scope="col">Time</th><th scope="col">Billable</th></tr></thead>`
    + invoiceSections(inv).map(({label, ps}) => {
      const sum = k => ps.reduce((n, p) => n + (p[k] || 0), 0);
      return `<tbody class="inv-section"><tr><th scope="rowgroup" colspan="2">${esc(label)}</th><td>${esc(dur(sum('minutes')))}</td>`
        + `<td>${sum('billable') ? esc(dur(sum('billable'))) : ''}</td></tr></tbody>` + ps.map(p => {
      const share = inv.minutes ? Math.round(100 * p.minutes / inv.minutes) : 0;
      return `<tbody class="inv-group"><tr class="inv-proj"><th scope="rowgroup">${esc(p.project)}</th>`
        + `<td class="inv-sh"><i style="--w:${Math.max(2, share)}%"></i>${share}%</td><td>${esc(dur(p.minutes))}</td>${projBill(p)}</tr>`
        + p.tasks.map(t => `<tr><td>${esc(t.task)}${t.noTask ? ' <small>no task</small>' : ''}</td><td></td><td>${esc(dur(t.minutes))}</td>${bill(t)}</tr>`).join('')
        + '</tbody>';
    }).join('');
    }).join('')
    + `</table><div class="inv-foot"><span>From ${who ? 'the' : 'your'} Productive time entries, ${esc(range)}. Share: each project's part of the time.</span><span class="inv-mark">Pulsar</span></div>`;
}
/* the same lines for a spreadsheet: one row per task, a subtotal per project, the total */
function invoiceRows(inv) {
  const row = (project, task, x) => [project, task, hours(x.minutes), x.minutes, hours(x.billable), x.billable, x.nonBillable, x.unknown,
    'billablePct' in x ? x.billablePct ?? '' : ''];
  return [['Project', 'Task', 'Hours', 'Minutes', 'Billable (h)', 'Billable (min)', 'Not billable (min)', 'Billing unknown (min)', 'Billable %'],
    ...invoiceSections(inv).flatMap(x => x.ps).flatMap(p => [...p.tasks.map(t => row(p.project, t.task, t)), row(p.project, 'Subtotal', p)]),
    row('Total', '', inv)];
}

/* ---------- printing and PDF: the report, or the invoice summary alone (style.css, @media print) ----------
   Pulsar's window writes a PDF straight to a file where it lists savePDF (native.js pulsarNative.savePDF). An older
   window, which the updates never replace, and a browser open their print dialog instead, where Save as PDF does it. */
const printOnly = what => document.documentElement.classList.toggle('print-invoice', what === 'invoice');
let pdfBusy = false;
/* the print dialog. In the window the page keeps what it prints until the next click or key: the dialog lays it out
   again while it is open (page setup, paper size). The title is the name Save as PDF offers. */
function printDialog(what, title) {
  printOnly(what);
  const before = document.title;
  document.title = title;
  let done = false;
  const back = () => {
    if (done) return;
    done = true; printOnly(''); document.title = before;
    removeEventListener('pointerdown', back, true); removeEventListener('keydown', back, true);
  };
  // after this click: its own pointerdown has gone by
  setTimeout(() => { if (!done) { addEventListener('pointerdown', back, true); addEventListener('keydown', back, true); } }, 0);
  if (APP) { toApp('print'); return; }
  addEventListener('afterprint', back, {once: true});
  window.print();
}
async function savePDF(what, name) {
  const save = window.pulsarNative?.savePDF;
  if (typeof save !== 'function') {  // a browser, or a window from before Save as PDF
    if (APP) toast('Choose PDF, then Save as PDF, in the print dialog.');
    printDialog(what, name.replace(/\.pdf$/, ''));
    return;
  }
  if (pdfBusy) return;
  pdfBusy = true; printOnly(what);
  const r = await save(name);
  pdfBusy = false; printOnly('');
  if (r.error) toast(`The PDF was not saved: ${r.error}`, true);
  else if (r.name) toast(`Saved ${r.name}.`);
}
$('#report-dl').addEventListener('click', ev => {
  const b = ev.target.closest('[data-dl]'); if (!b) return;
  $('#report-dl').open = false;
  const x = R.data, dl = b.dataset.dl;
  if (!x || x.error) { toast('Load a report first.', true); return; }
  const base = `pulsar-report-${x.from}-to-${x.to}`, invoice = `pulsar-invoice-${x.from}-to-${x.to}`;
  if (dl === 'print') { printDialog('report', base); return; }
  if (dl === 'pdf') { savePDF('report', `${base}.pdf`); return; }
  if (dl === 'invoice' || dl === 'invoice-csv') {
    // an invoice from part of the entries would be wrong: only from a full read of Productive
    if (x.warning) { toast(`${x.warning} The invoice summary needs every entry: try again once Productive answers (↻).`, true); return; }
    if (!x.invoice?.projects.length) { toast('Nothing logged in this range.', true); return; }
    if (dl === 'invoice') { renderInvoice(x); savePDF('invoice', `${invoice}.pdf`); return; }
    saveFile(`${invoice}.csv`, toCSV(invoiceRows(x.invoice)));
  } else if (dl === 'days') {
    saveFile(`${base}-days.csv`, toCSV([
      ['Date', 'Weekday', 'Logged (h)', 'Logged (min)', 'Billable (min)', 'Not billable (min)', 'Active, work (min)',
       'Active, not logged (min)', 'Personal (min)', 'Entries', 'Approved (min)', 'Tracker'],
      ...R.days.map(d => [d.date, WEEKDAYS_FULL[d.weekday], hours(d.logged), d.logged, d.billable, d.nonBillable, d.active,
                          d.notLogged, d.personal, d.entries, d.approved, d.source === 'aw' ? 'ActivityWatch' : d.source === 'memtime' ? 'MemTime' : ''])]));
  } else {
    saveFile(`${base}-entries.csv`, toCSV([
      ['Date', 'Project', 'Budget', 'Service', 'Task', 'Hours', 'Minutes', 'Billable', 'Billing type', 'Approved'],
      ...[...x.entries].sort((p, q) => p.date.localeCompare(q.date)).map(e => [e.date, e.project, e.budget, e.service, e.task,
          hours(e.minutes), e.minutes, e.billable == null ? '' : e.billable ? 'yes' : 'no', e.billing, e.approved ? 'yes' : 'no'])]));
  }
  toast('Downloaded.');
});
