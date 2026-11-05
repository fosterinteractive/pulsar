/* Day view: one day on one time axis (activity, likely, logged) */
function load({fresh = false, keepScroll = false} = {}) {
  const d = state.date;
  if (!keepScroll) { state.act = state.likely = state.logged = null; render(); }
  return Promise.all([
    getJSON(`/api/activity?date=${d}&source=${state.source}`).then(x => { if (d === state.date) { state.act = x; render(); } return x; }),
    getJSON(`/api/entries?date=${d}`).then(x => { if (d === state.date) { state.logged = x; render(); loadEstimates(); } return x; }),
    getJSON(`/api/proposal?date=${d}${fresh ? '&fresh=1' : ''}`).then(x => { if (d === state.date) { state.likely = x; render(); } return x; }),
  ]);
}

/* each task's estimate and the time everyone logged on it, for the Tasks box */
async function loadEstimates() {
  const d = state.date, ids = [...new Set((state.logged?.entries || []).map(x => x.taskId).filter(Boolean))];
  if (!ids.length) { state.estimates = {}; render(); return; }
  const r = await getJSON(`/api/tasks/estimates?ids=${ids.join(',')}`);
  if (d === state.date) { state.estimates = r.tasks || {}; render(); }
}
async function refreshLogged() {
  const d = state.date;
  const x = await getJSON(`/api/entries?date=${d}`);
  if (d === state.date) { state.logged = x; render(); loadEstimates(); }
  return x;
}
/* a task against its estimate: the words, and whether it is over */
function estimateLine(t) {
  if (!t || t.worked == null) return null;
  if (!t.estimate) return {text: `${dur(t.worked)} logged by everyone, no estimate`, over: false, pct: null};
  const over = t.worked > t.estimate;
  return {over, pct: Math.min(100, t.worked / t.estimate * 100),
          text: over ? `${dur(t.worked)} of ${dur(t.estimate)}, over by ${dur(t.worked - t.estimate)}`
                     : `${dur(t.worked)} of ${dur(t.estimate)}, ${dur(t.estimate - t.worked)} left`};
}

/* ---------- activity blocks, the MemTime way: dominant window per slot, equal neighbours joined ---------- */
function activityBlocks(items, Z, end) {
  const slots = Array.from({length: Math.ceil(end / Z)}, () => ({tot: 0, keys: new Map()}));
  for (const it of items) {
    const key = it.app + '|' + (it.host || it.title);
    let s = it.s;
    while (s < it.e) {
      const i = Math.floor(s / Z), stop = Math.min(it.e, (i + 1) * Z), sl = slots[i];
      if (!sl) break;
      const d = stop - s;
      sl.tot += d;
      let k = sl.keys.get(key);
      if (!k) sl.keys.set(key, k = {min: 0, titles: new Map(), urls: new Map(), it});
      k.min += d; k.titles.set(it.title, (k.titles.get(it.title) || 0) + d);
      if (it.url) k.urls.set(it.url, (k.urls.get(it.url) || 0) + d);
      s = stop;
    }
  }
  // a slot shows when enough of it was active: a tenth of a long slot, a quarter of a 1 or 5 min one
  const threshold = Z >= 15 ? Math.max(1, Z * 0.1) : Z * 0.25, blocks = [];
  slots.forEach((sl, i) => {
    if (sl.tot < threshold) return;
    let bestKey = null, best = null;
    for (const [k, v] of sl.keys) if (!best || v.min > best.min) { best = v; bestKey = k; }
    const last = blocks[blocks.length - 1];
    if (last && last.key === bestKey && last.end === i) { last.end = i + 1; last.slots.push(sl); }
    else blocks.push({key: bestKey, start: i, end: i + 1, slots: [sl]});
  });
  for (const b of blocks) {
    const agg = new Map(); b.total = 0;
    for (const sl of b.slots) {
      b.total += sl.tot;
      for (const [k, v] of sl.keys) {
        let a = agg.get(k); if (!a) agg.set(k, a = {min: 0, titles: new Map(), urls: new Map(), it: v.it});
        a.min += v.min; for (const [t, m] of v.titles) a.titles.set(t, (a.titles.get(t) || 0) + m);
        for (const [u, m] of v.urls) a.urls.set(u, (a.urls.get(u) || 0) + m);
      }
    }
    b.items = [...agg.values()].sort((x, y) => y.min - x.min);
    const dom = agg.get(b.key);
    b.it = dom.it;
    b.title = [...dom.titles].sort((x, y) => y[1] - x[1])[0][0] || dom.it.app;
  }
  return blocks;
}
/* the window of an app or site that had the most minutes in a block: its busiest page and title, for a new rule */
const busiest = m => [...m].sort((x, y) => y[1] - x[1])[0]?.[0];
const topWindow = a => ({...a.it, title: busiest(a.titles) ?? a.it.title, url: busiest(a.urls) || ''});

/* the proposal, by slot too: the project with most minutes in a slot, equal neighbours joined */
function likelyBlocks(entries, Z, end) {
  const slots = Array.from({length: Math.ceil(end / Z)}, () => ({tot: 0, projects: new Map(), entries: new Set()}));
  entries.forEach((x, idx) => {
    let s = x.s;
    while (s < x.e) {
      const i = Math.floor(s / Z), stop = Math.min(x.e, (i + 1) * Z), sl = slots[i];
      if (!sl) break;
      sl.tot += stop - s; sl.projects.set(x.project, (sl.projects.get(x.project) || 0) + stop - s); sl.entries.add(idx);
      s = stop;
    }
  });
  const blocks = [];
  slots.forEach((sl, i) => {
    if (!sl.tot) return;
    const project = [...sl.projects].sort((a, b) => b[1] - a[1])[0][0];
    const last = blocks[blocks.length - 1];
    if (last && last.project === project && last.end === i) { last.end = i + 1; last.slots.push(sl); }
    else blocks.push({project, start: i, end: i + 1, slots: [sl]});
  });
  for (const b of blocks) {
    const idx = new Set(); b.total = 0;
    for (const sl of b.slots) { b.total += sl.tot; sl.entries.forEach(j => idx.add(j)); }
    b.entries = [...idx].sort((p, q) => p - q).map(j => entries[j]);
    const titles = new Map();
    for (const e of b.entries) if (e.project === b.project) for (const t of e.titles) titles.set(t, (titles.get(t) || 0) + e.minutes);
    b.title = [...titles].sort((p, q) => q[1] - p[1])[0]?.[0] || '';
    b.parallel = b.entries.filter(e => e.project === b.project).every(e => e.kind === 'parallel');
  }
  return blocks;
}

/* side-by-side lanes for overlapping cards (by their drawn height, not just their minutes) */
function lanes(list, ppm, minPx) {
  const sorted = list.filter(x => x.s != null).sort((a, b) => a.s - b.s);
  let cluster = [], clusterEnd = -1, ends = [];
  const flush = () => { const n = ends.length; cluster.forEach(x => x.n = n); cluster = []; ends = []; };
  for (const x of sorted) {
    const vEnd = Math.max(x.e, x.s + minPx / ppm);
    if (x.s >= clusterEnd) { flush(); clusterEnd = -1; }
    let lane = ends.findIndex(e => e <= x.s);
    if (lane < 0) { lane = ends.length; ends.push(0); }
    ends[lane] = vEnd; x.lane = lane; cluster.push(x); clusterEnd = Math.max(clusterEnd, vEnd);
  }
  flush();
  return sorted;
}
function place(x, ppm, minPx, inset = 3) {
  const top = x.s * ppm + 1, h = Math.max((x.e - x.s) * ppm - 2, minPx);
  const w = `calc((100% - ${inset * 2}px) / ${x.n || 1})`;
  return `top:${top}px;height:${h}px;left:calc(${inset}px + ${w} * ${x.lane || 0});width:calc(${w} - 3px)`;
}

/* ---------- render ---------- */
let blocksCache = [], likelyCache = [], loggedCache = [];
function render() {
  const Z = state.zoom, ppm = ppmOf(Z);
  const act = state.act, likely = state.likely, logged = state.logged;
  const ends = [1440];
  for (const e of logged?.entries || []) if (e.e) ends.push(e.e);
  for (const e of likely?.entries || []) ends.push(e.e);
  const end = Math.ceil(Math.max(...ends) / 60) * 60, height = end * ppm;

  // header
  const label = new Intl.DateTimeFormat('en-GB', {weekday: 'long', day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC'})
    .format(new Date(state.date + 'T12:00:00Z'));
  $('#dayname').textContent = label.charAt(0).toUpperCase() + label.slice(1);
  $('#date').value = state.date;
  document.querySelectorAll('#zoom button').forEach(b => b.classList.toggle('on', +b.dataset.v === Z));
  $('#source-select').value = state.source;

  // axis and rules
  const grid = $('#grid'); grid.style.height = height + 'px';
  let axis = '', rules = '';
  const labelEvery = LABEL_EVERY[Z] || 30, ruleEvery = Z === 1 ? 5 : Z;
  for (let m = 0; m <= end; m += ruleEvery) {
    const hour = m % 60 === 0;
    if (m % labelEvery === 0) axis += `<div class="${hour ? 'h' : ''} num" style="top:${m * ppm}px">${m ? hm(m) : ''}</div>`;
    rules += `<div class="rule${hour ? ' h' : ''}" style="top:${m * ppm}px"></div>`;
  }
  $('#axis').innerHTML = axis;
  grid.querySelectorAll('.rule,.now').forEach(n => n.remove());
  grid.insertAdjacentHTML('beforeend', rules);
  if (state.date === todayLocal()) grid.insertAdjacentHTML('beforeend', `<div class="now" style="top:${nowMin() * ppm}px"></div>`);

  assignHues([...(logged?.entries || []).map(x => x.project), ...(CONFIG?.rules || []).map(r => r.project)]);
  // activity
  const laneAct = $('#lane-act');
  if (!act) { laneAct.innerHTML = '<div class="state">loading…</div>'; $('#h-act').textContent = '…'; }
  else if (act.error) { laneAct.innerHTML = `<div class="state err">${esc(act.error)}</div>`; $('#h-act').textContent = 'error'; }
  else {
    blocksCache = activityBlocks(act.items, Z, end);
    const src = act.source === 'aw' ? 'ActivityWatch' : 'MemTime';
    const total = act.items.reduce((s, x) => s + x.e - x.s, 0);
    const other = (act.source === 'aw' ? act.minutes?.memtime : act.minutes?.aw) || 0;
    const hint = other === 0 ? ` · ${act.source === 'aw' ? 'MemTime' : 'ActivityWatch'} is empty for this day` : '';
    const problem = (act.problems || {})[act.source] || (!total ? Object.values(act.problems || {})[0] : '');
    $('#h-act').textContent = problem ? problem : !total && !other ? 'No activity recorded' : `${src} · ${dur(total)}${hint}`;
    $('#h-act').classList.toggle('warn', !!problem);
    laneAct.innerHTML = blocksCache.length ? blocksCache.map((b, i) => {
      const x = {s: b.start * Z, e: b.end * Z};
      const byRule = new Map();  // the block belongs to a rule's project when most of its time matches it
      for (const a of b.items) { const r = ruleOf(a.it); if (r) byRule.set(r.project, (byRule.get(r.project) || 0) + a.min); }
      const top = [...byRule].sort((p, q) => q[1] - p[1])[0];
      b.rule = top && top[1] >= b.total * 0.4 ? top[0] : null;
      return `<div class="card act ${b.it.cat}${b.rule ? ' ruled' : ''}" data-k="act" data-i="${i}" style="${b.rule ? `--c:${color(b.rule)};` : ''}${place(x, ppm, 18)}"${b.rule ? ` title="${esc(b.rule)}"` : ''}
        tabindex="0" role="button" aria-label="${esc(`Activity ${hm(x.s)}–${hm(x.e)}: ${b.title}, ${dur(b.total)}`)}">
        <div class="row">${icon(b.it.icon, b.it.app)}<span class="t">${esc(b.title)}</span><span class="m num">${dur(b.total)}</span></div></div>`;
    }).join('') : '<div class="state">No activity on this day.</div>';
  }

  // likely
  const laneLikely = $('#lane-likely');
  if (!likely) { laneLikely.innerHTML = '<div class="state">reading Claude sessions…</div>'; $('#h-likely').textContent = '…'; }
  else if (likely.disabled) { laneLikely.innerHTML = ''; $('#h-likely').textContent = ''; }
  else if (likely.error) { laneLikely.innerHTML = `<div class="state err">${esc(likely.error)}</div>`; $('#h-likely').textContent = 'error'; }
  else {
    likelyCache = likelyBlocks(likely.entries, Z, end);
    $('#h-likely').textContent = `Claude sessions · ${dur(likely.proposed || 0)}`;
    laneLikely.innerHTML = likelyCache.length ? likelyCache.map((b, i) => {
      const x = {s: b.start * Z, e: b.end * Z}, h = (x.e - x.s) * ppm;
      return `<div class="card likely${b.parallel ? ' parallel' : ''}" data-k="likely" data-i="${i}" style="${place(x, ppm, 18)}"
        tabindex="0" role="button" aria-label="${esc(`Likely ${hm(x.s)}–${hm(x.e)}: ${b.project}${b.title ? ', ' + b.title : ''}, ${dur(b.total)}`)}">
        <div class="row"><span class="t">${esc(b.project)}</span><span class="m num">${dur(b.total)}</span></div>
        ${h > 34 && b.title ? `<div class="d">${esc(b.title)}</div>` : ''}</div>`;
    }).join('') : '<div class="state">No Claude sessions on this day.</div>';
  }

  // logged
  const laneLogged = $('#lane-logged');
  let gapMin = 0;
  if (!logged) { laneLogged.innerHTML = '<div class="state">reading Productive…</div>'; $('#h-logged').textContent = '…'; }
  else if (logged.error) { laneLogged.innerHTML = `<div class="state err">${esc(logged.error)}${logged.needsSetup ? '<div><button class="btn bordered" data-open-settings>Open Settings</button></div>' : ''}</div>`; $('#h-logged').textContent = 'error'; }
  else {
    loggedCache = lanes(logged.entries.map(x => ({...x})), ppm, 18);
    const sum = logged.entries.reduce((s, x) => s + x.minutes, 0);
    const head = $('#h-logged');
    head.textContent = logged.warning ? logged.warning : `Productive · ${dur(sum)} · updated ${logged.fetched.split(' ')[1]}`;
    head.classList.toggle('warn', !!logged.warning);
    let html = loggedCache.map((x, i) => {
      const h = (x.e - x.s) * ppm, lines = Math.max(0, Math.floor((h - 36) / 15));
      return `<div class="card logged" data-k="logged" data-i="${i}" style="--c:${color(x.project)};${place(x, ppm, 18)}"
        tabindex="0" role="button" aria-label="${esc(`Logged ${hm(x.s)}–${hm(x.e)}: ${x.project}${x.task ? ', ' + x.task : ''}, ${dur(x.minutes)}`)}">
        <div class="row"><span class="t">${esc(x.project)}</span>${x.billable ? `<span class="bill" title="Billable${x.billing ? ': ' + esc(x.billing) : ''}">$</span>` : ''}<span class="m num">${dur(x.minutes)}</span></div>
        ${h > 34 ? `<div class="d">${esc(x.task)}</div>` : ''}
        ${lines ? `<div class="n" style="-webkit-line-clamp:${lines}">${esc(x.note)}</div>` : ''}</div>`;
    }).join('');
    // active minutes (not personal) with no entry on them. The number counts each minute's active share, as the
    // report does; the amber rails mark stretches of at least 3 mostly-active minutes, for the eye and the click.
    if (act && !act.error) {
      const active = new Float32Array(end + 1), covered = new Uint8Array(end + 1);
      for (const it of act.items) {
        if (it.cat === 'personal') continue;
        for (let m = it.s; m < it.e && m < end;) { const next = Math.min(it.e, Math.floor(m) + 1); active[Math.floor(m)] += next - m; m = next; }
      }
      for (const e of logged.entries) if (e.s != null) for (let m = Math.floor(e.s); m < Math.floor(e.s) + e.minutes && m < end; m++) covered[m] = 1;
      for (let m = 0; m < end; m++) if (!covered[m]) gapMin += Math.min(1, active[m]);
      let runStart = -1;
      for (let m = 0; m <= end; m++) {
        const gap = m < end && active[m] >= 0.5 && !covered[m];
        if (gap && runStart < 0) runStart = m;
        if (!gap && runStart >= 0) {
          if (m - runStart >= 3) html += `<div class="gaprail" data-a="${runStart}" data-b="${m}" style="top:${runStart * ppm}px;height:${(m - runStart) * ppm}px"
            title="Active, not logged: ${hm(runStart)}–${hm(m)}. Click to log it." tabindex="0" role="button"
            aria-label="Active, not logged ${hm(runStart)}–${hm(m)}, ${dur(m - runStart)}: log this time"></div>`;
          runStart = -1;
        }
      }
    }
    laneLogged.innerHTML = html || '<div class="state">No entries on this day.</div>';
  }

  renderAside(gapMin);

  // first view of a day: scroll to the first thing that happened
  // first view of a day: today opens at the current hour, another day at the first thing that happened
  if (state.scrolledFor !== state.date && act && logged) {
    if (state.date === todayLocal()) scrollToNow(false);
    else {
      const firsts = [...(act.items || []).map(x => x.s), ...(logged.entries || []).filter(x => x.s != null).map(x => x.s)];
      if (firsts.length) $('#scroll').scrollTop = Math.max(0, Math.min(...firsts) - 30) * ppm;
    }
    state.scrolledFor = state.date;
  }
}

/* the current hour near the top, with the hour before it above for context */
function scrollToNow(smooth = true) {
  $('#scroll').scrollTo({top: Math.max(0, (nowMin() - 60) * ppmOf(state.zoom)), behavior: smooth ? 'smooth' : 'auto'});
}

function renderAside(gapMin) {
  const act = state.act, logged = state.logged, likely = state.likely;
  const work = act && !act.error ? act.items.filter(x => x.cat !== 'personal').reduce((s, x) => s + x.e - x.s, 0) : null;
  const personal = act && !act.error ? act.items.filter(x => x.cat === 'personal').reduce((s, x) => s + x.e - x.s, 0) : null;
  const sum = logged && !logged.error ? logged.entries.reduce((s, x) => s + x.minutes, 0) : null;
  const known = sum ? logged.entries.filter(x => x.billable != null) : [];
  const bill = known.length ? {yes: known.filter(x => x.billable).reduce((s, x) => s + x.minutes, 0),
                               no: known.filter(x => !x.billable).reduce((s, x) => s + x.minutes, 0)} : null;
  const v = x => x == null ? '…' : dur(x);
  $('#worktime').innerHTML = `<h2>Work time</h2>
    <div class="kv big"><span>Logged</span><span class="num">${v(sum)}</span></div>
    ${bill ? `<div class="kv"><span>Billable</span><span class="num">${dur(bill.yes)}</span></div><div class="kv"><span>Not billable</span><span class="num">${dur(bill.no)}</span></div>` : ''}
    ${sum && sum > (bill ? bill.yes + bill.no : 0) && bill ? `<div class="kv"><span>Billing unknown</span><span class="num">${dur(sum - bill.yes - bill.no)}</span></div>` : ''}
    <div class="kv"><span>Active, work</span><span class="num">${v(work)}</span></div>
    ${personal ? `<div class="kv"><span>Personal</span><span class="num">${dur(personal)}</span></div>` : ''}
    ${logged && act && !logged.error && !act.error ? `<div class="kv gap"><span>Active, not logged</span><span class="num">${dur(gapMin)}</span></div>` : ''}
    <div class="bar"><i style="width:${Math.min(100, (sum || 0) / TARGET * 100)}%"></i></div>
    <div class="kv"><span>Daily target</span><span class="num">${dur(TARGET)}</span></div>`;

  if (!logged || logged.error) { $('#projects').innerHTML = `<h2>Total project time</h2><div class="note">${logged ? esc(logged.error) : '…'}</div>`; }
  else {
    // project -> budget + service, as MemTime lists them; the tasks go in the row's tooltip
    const byProject = new Map();
    for (const e of logged.entries) {
      let p = byProject.get(e.project); if (!p) byProject.set(e.project, p = {min: 0, rows: new Map()});
      const key = (e.budget || e.task) + '|' + e.service;
      let r = p.rows.get(key); if (!r) p.rows.set(key, r = {budget: e.budget || e.task, service: e.service, billable: e.billable, billing: e.billing, min: 0, tasks: new Set()});
      p.min += e.minutes; r.min += e.minutes; if (e.task) r.tasks.add(e.task);
    }
    const noStart = logged.entries.filter(e => e.s == null);
    const total = logged.entries.reduce((s, x) => s + x.minutes, 0);
    $('#projects').innerHTML = `<h2>Total project time<span class="total num">${dur(total)}</span></h2>` +
      ([...byProject].sort((a, b) => b[1].min - a[1].min).map(([name, p]) =>
        `<details class="pg" style="--c:${color(name)}" data-p="${esc(name)}"${folded.has(name) ? '' : ' open'}>
          <summary><b>${esc(name)}</b><span class="num">${dur(p.min)}</span></summary>
          ${[...p.rows.values()].sort((a, b) => b.min - a.min).map(r =>
            `<div class="ti" title="${esc([...r.tasks].join('\n'))}"><span>${esc(r.budget)}${r.service ? ` <small>· ${esc(r.service)}</small>` : ''}${r.billable ? `<span class="bill" title="Billable: ${esc(r.billing)}">$</span>` : ''}</span><span class="num">${dur(r.min)}</span></div>`).join('')}
        </details>`
      ).join('') || '<div class="note">Nothing logged.</div>')
      + (noStart.length ? `<div class="note">No start time: ${noStart.length} (${dur(noStart.reduce((s, x) => s + x.minutes, 0))}), not on the timeline.</div>` : '');
  }

  const box = $('#tasksbox');
  const byTask = new Map();
  for (const e of logged && !logged.error ? logged.entries : []) {
    if (!e.taskId) continue;
    const t = byTask.get(e.taskId) || {taskId: e.taskId, title: e.task, project: e.project, today: 0};
    t.today += e.minutes; byTask.set(e.taskId, t);
  }
  box.hidden = !byTask.size;
  if (byTask.size) {
    const est = state.estimates;
    box.innerHTML = '<h2>Tasks against estimates</h2>' + [...byTask.values()].sort((a, b) => b.today - a.today).map(t => {
      const line = est ? estimateLine(est[t.taskId]) : null;
      return `<div class="tk"><div class="tk-h"><span class="dot" style="background:${color(t.project)}"></span><b title="${esc(t.project)}">${esc(t.title)}</b><span class="num">${dur(t.today)} today</span></div>
        ${line ? `<div class="tk-l${line.over ? ' over' : ''}">${line.over ? '<span aria-hidden="true">⚠ </span>' : ''}${esc(line.text)}</div>
          ${line.pct != null ? `<div class="bar"><i style="width:${line.pct}%${line.over ? ';background:var(--gap);opacity:1' : ''}"></i></div>` : ''}`
               : `<div class="tk-l">${est ? 'no data from Productive' : 'reading estimates…'}</div>`}</div>`;
    }).join('') + '<div class="note">Totals include everyone\'s time on the task.</div>';
  }

  if (likely?.disabled) { $('#likelybox').innerHTML = ''; }
  else if (!likely || likely.error) { $('#likelybox').innerHTML = `<h2>Likely, from Claude sessions</h2><div class="note">${likely ? esc(likely.error) : 'reading…'}</div>`; }
  else {
    const byProject = new Map();
    for (const e of likely.entries) byProject.set(e.project, (byProject.get(e.project) || 0) + e.minutes);
    const unplaced = (likely.unplaced || []).reduce((s, u) => s + u.minutes, 0);
    $('#likelybox').innerHTML = `<h2>Likely, from Claude sessions</h2>` +
      [...byProject].sort((a, b) => b[1] - a[1]).map(([p, m]) => `<div class="kv"><span>${esc(p)}</span><span class="num">${dur(m)}</span></div>`).join('') +
      (unplaced ? `<div class="note">Parallel time that did not fit: ${dur(unplaced)}.</div>` : '') +
      (likely.logging ? `<div class="note">Time logging: ${dur(likely.logging)}, never logged.</div>` : '') +
      `<div class="note">Tracker: ${esc(likely.tracker || 'none')}.</div>`;
  }
}

/* ---------- popover ---------- */
const pop = $('#pop');
let popFrom = null;  // the card the keyboard opened the popover from: Escape returns there
function openPop(html, ev) {
  pop.innerHTML = html; pop.classList.add('on');
  pop.setAttribute('aria-label', pop.querySelector('h3')?.textContent || 'Details');
  const r = pop.getBoundingClientRect();
  pop.style.left = Math.max(12, Math.min(ev.clientX + 14, innerWidth - r.width - 12)) + 'px';
  pop.style.top = Math.max(12, Math.min(ev.clientY - 20, innerHeight - r.height - 12)) + 'px';
}
document.addEventListener('click', ev => {
  const logBtn = ev.target.closest('[data-log-a], .gaprail');
  if (logBtn) {
    const a = +(logBtn.dataset.logA ?? logBtn.dataset.a), b = +(logBtn.dataset.logB ?? logBtn.dataset.b);
    pop.classList.remove('on'); openLog({start: a, minutes: b - a, hint: rulesInRange(a, b)}); return;
  }
  const ruleBtn = ev.target.closest('[data-rule-text]');
  if (ruleBtn) { pop.classList.remove('on'); openRule(ruleBtn.dataset.ruleText); return; }
  const card = ev.target.closest('.card');
  if (!card) { if (!ev.target.closest('.pop')) closePop(); return; }
  const i = +card.dataset.i, Z = state.zoom;
  if (card.dataset.k === 'act') {
    const b = blocksCache[i];
    openPop(`<div class="ph"><div><h3>${hm(b.start * Z)}–${hm(b.end * Z)}</h3><div class="sub">${dur(b.total)} active</div></div>
      <button class="btn bordered sm" data-log-a="${b.start * Z}" data-log-b="${b.end * Z}">Log this time</button></div>` +
      b.items.filter(a => a.min >= 0.5).slice(0, 14).map(a => {
        const top = [...a.titles].sort((x, y) => y[1] - x[1]).slice(0, 3), r = ruleOf(a.it);
        return `<div class="li">${icon(a.it.icon, a.it.app)}<div class="t"><div>${esc(top[0][0] || a.it.app)}</div>
          <small>${esc([a.it.app, a.it.host].filter(Boolean).join(' · '))}${top.length > 1 ? ' · ' + (a.titles.size - 1) + ' more' : ''}</small></div>
          ${r ? `<span class="tag" style="--c:${color(r.project)}" title="Rule “${esc(r.match)}”: ${esc(r.title)}">${esc(r.project.split(' ')[0])}</span>`
              : `<button class="btn sm" data-rule-text="${esc(ruleSuggestion(topWindow(a)))}" title="Make a rule: this window belongs to a task">Rule</button>`}
          <span class="num">${dur(a.min)}</span></div>`;
      }).join(''), ev);
  } else if (card.dataset.k === 'likely') {
    const b = likelyCache[i];
    openPop(`<div class="ph"><div><h3>${hm(b.start * Z)}–${hm(b.end * Z)}</h3><div class="sub">${dur(b.total)} from Claude sessions</div></div>
      <button class="btn bordered sm" data-log-a="${b.start * Z}" data-log-b="${b.end * Z}">Log this time</button></div>` +
      b.entries.map(x => `<div class="li"><div class="t"><div><b>${esc(x.project)}</b> · ${esc(x.titles.join(', ') || '(untitled)')}</div>
        <small>${hm(x.s)}–${hm(x.e)} · ${x.kind === 'parallel' ? 'parallel, moved down' : 'focused'} · ${esc(x.messages)} messages${x.first_message ? ' · ' + esc(x.first_message) : ''}</small></div>
        <span class="num">${dur(x.minutes)}</span></div>`).join(''), ev);
  } else {
    const x = loggedCache[i];
    openPop(`<h3>${esc(x.project)}</h3><div class="sub">${hm(x.s)}–${hm(x.e)} · ${dur(x.minutes)}${x.approved ? ' · approved' : ''}</div>
      <p>${esc(x.note)}</p>
      <dl><dt>Task</dt><dd>${esc(x.task)}</dd><dt>Budget</dt><dd>${esc(x.budget)}</dd><dt>Service</dt><dd>${esc(x.service)}</dd><dt>Billable</dt><dd>${x.billable == null ? 'Unknown' : x.billable ? 'Yes' : 'No'}${x.billing ? ' · ' + esc(x.billing) : ''}</dd><dt>Entry</dt><dd class="num">${esc(x.id)}</dd></dl>`, ev);
  }
});
function closePop(refocus = false) {
  const was = pop.classList.contains('on');
  pop.classList.remove('on');
  if (refocus && was && popFrom) {  // the card may have been redrawn since: find it again by its place
    const el = popFrom.isConnected ? popFrom : document.querySelector(`.card[data-k="${popFrom.dataset.k}"][data-i="${popFrom.dataset.i}"]`);
    el?.focus();
  }
  popFrom = null;
}
/* Enter or Space on a card or an amber rail: the same as a click, with the popover opened beside the card */
function activateByKey(el) {
  const r = el.getBoundingClientRect();
  el.dispatchEvent(new MouseEvent('click', {bubbles: true, clientX: r.left + Math.min(r.width / 2, 60), clientY: r.top + 20}));
  if (el.classList.contains('card') && pop.classList.contains('on')) {
    popFrom = el;
    (pop.querySelector('button') || pop).focus();
  }
}
document.addEventListener('keydown', ev => {
  if (/^(INPUT|TEXTAREA|SELECT)$/.test(ev.target.tagName) || ev.target.closest('dialog')) return;
  if (state.view === 'report') { if (ev.key === 'Escape') tipEl.classList.remove('on'); return; }
  if ((ev.key === 'Enter' || ev.key === ' ') && ev.target.matches?.('.card[data-k], .gaprail')) {
    ev.preventDefault(); activateByKey(ev.target); return;
  }
  if (ev.key === 'Escape') closePop(true);
  if (ev.metaKey || ev.ctrlKey || ev.altKey || ev.shiftKey) return;  // Cmd+← and friends belong to the browser
  if (ev.key === 'ArrowLeft') go(shiftDate(state.date, -1));
  if (ev.key === 'ArrowRight') go(shiftDate(state.date, 1));
  if (ev.code === 'KeyT') go(todayLocal());  // the T key on any layout
});

function go(date) { if (!validDate(date)) return; state.date = date; closePop(); syncURL(); load(); }
$('#prev').onclick = () => go(shiftDate(state.date, -1));
$('#next').onclick = () => go(shiftDate(state.date, 1));
$('#today').onclick = () => go(todayLocal());
$('#date').onchange = e => {
  if (validDate(e.target.value)) go(e.target.value);
  else if (!e.target.value) e.target.value = state.date;  // cleared: show the day again
};
$('#date').addEventListener('blur', e => { if (e.target.value !== state.date) e.target.value = state.date; });
$('#refresh').onclick = async () => {
  if (state.view === 'report') return loadReport(true);
  const btn = $('#refresh'), before = state.logged, d = state.date;
  btn.classList.add('busy');
  const [, logged] = await load({fresh: true, keepScroll: true});
  btn.classList.remove('busy');
  if (d !== state.date) return;
  const c = loggedChange(before, logged);
  toast(c.warn ? c.text : `Everything updated. Logs: ${c.text}`, c.warn);
};
/* logged time only: one Productive read, the other columns stay as they are */
$('#refresh-logged').onclick = async () => {
  const btn = $('#refresh-logged'), d = state.date, before = state.logged;
  btn.classList.add('busy');
  const x = await refreshLogged();
  btn.classList.remove('busy');
  if (d !== state.date) return;
  const c = loggedChange(before, x);
  toast(c.warn ? c.text : `Logs updated: ${c.text}`, c.warn);
};
/* folded projects stay folded when the page redraws */
const folded = new Set();
document.addEventListener('toggle', ev => {
  const p = ev.target.dataset?.p; if (p == null) return;
  ev.target.open ? folded.delete(p) : folded.add(p);
}, true);
document.querySelectorAll('#zoom button').forEach(b => b.onclick = () => {
  const sc = $('#scroll'), top = sc.scrollTop / ppmOf(state.zoom);  // keep the same minute at the top
  state.zoom = +b.dataset.v; syncURL(); render(); sc.scrollTop = top * ppmOf(state.zoom);
});
$('#source-select').addEventListener('change', ev => {
  state.source = ev.target.value; syncURL(); state.act = null; render();
  const d = state.date;
  getJSON(`/api/activity?date=${d}&source=${state.source}`).then(x => { if (d === state.date) { state.act = x; render(); } });
});
// today: refresh everything once an hour; the ↻ buttons do it on demand
setInterval(async () => {
  if (state.view !== 'day' || state.date !== todayLocal() || document.visibilityState !== 'visible') return;
  const before = state.logged, [, logged] = await load({keepScroll: true});
  const c = loggedChange(before, logged);
  if (c.changed || c.warn) toast(c.warn ? c.text : `Logs updated automatically: ${c.text}`, c.warn);  // quiet when nothing changed
}, 60 * 60 * 1000);
