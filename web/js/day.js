/* The newest request of each kind wins: an answer that arrives after a newer request of the same kind went out (a
   slow tracker after switching, the entries before a refresh that followed a log or a delete) is dropped. A fresh()
   check also stands for "still the same day", as each new day asks again. */
const asked = {};
function newest(kind) {
  const n = (asked[kind] = (asked[kind] || 0) + 1);
  return () => asked[kind] === n;
}

/* Day view: one day on one time axis (activity, likely, logged) */
function load({fresh = false, keepScroll = false} = {}) {
  const d = state.date, act = newest('activity'), logged = newest('entries'), likely = newest('proposal');
  if (!keepScroll) { state.act = state.likely = state.logged = state.repeats = null; render(); }
  loadRepeats();
  return Promise.all([
    getJSON(`/api/activity?date=${d}&source=${state.source}`).then(x => { if (act()) { state.act = x; drawSoon(); } return x; }),
    getJSON(`/api/entries?date=${d}`).then(x => { if (logged()) { state.logged = x; drawSoon(); loadEstimates(); } return x; }),
    getJSON(`/api/proposal?date=${d}&quick=1${fresh ? '&fresh=1' : ''}`).then(x => {
      if (likely()) { state.likely = x; drawSoon(); if (x.learnPending || x.hintsPending) setTimeout(() => loadLearned(d), 3000); }
      return x;
    }),
    loadMeetings(d, fresh),  // meetings.js
    // Ask Claude's picks for this day, kept from an earlier ask (this run or on disk): shown again, never asked again
    CONFIG?.likely?.askClaude && state.claude?.date !== d ? getJSON(`/api/likely/picks?date=${d}`).then(x => {
      if (d === state.date && x.picks && Object.keys(x.picks).length && state.claude?.date !== d) {
        state.claude = {date: d, picks: x.picks}; drawSoon();
      }
      return x;
    }) : null,
  ]);
}

/* the Likely rows came before your Productive history was read, or before their sessions' tasks and commits were
   (the server works them out after the rows): ask once more for them */
async function loadLearned(d) {
  if (d !== state.date) return;
  const mine = newest('proposal');
  const x = await getJSON(`/api/proposal?date=${d}&wait=1`);
  if (mine() && !x.error && !x.disabled) { state.likely = x; drawSoon(); }
}

/* entries you log at the same time most days (the daily meeting), offered when the tracker saw the call: a hint,
   so a failure shows nothing rather than an error */
async function loadRepeats() {
  const d = state.date, mine = newest('repeats');
  const x = await getJSON(`/api/repeats?date=${d}&source=${state.source}`);
  if (mine()) { state.repeats = x.repeats || []; drawSoon(); }
}

/* each task's estimate and the time everyone logged on it, for the Tasks box */
async function loadEstimates() {
  const mine = newest('estimates'), ids = [...new Set((state.logged?.entries || []).map(x => x.taskId).filter(Boolean))];
  if (!ids.length) { state.estimates = {}; drawSoon(); return; }
  const r = await getJSON(`/api/tasks/estimates?ids=${ids.join(',')}`);
  if (mine()) { state.estimates = r.tasks || {}; state.estimatesError = r.error || ''; drawSoon(); }
}
async function refreshLogged() {
  const d = state.date, mine = newest('entries');
  const x = await getJSON(`/api/entries?date=${d}`);
  if (mine()) { state.logged = x; render(); loadEstimates(); }
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

/* the Likely rows inside [a, b), by the minutes each project has there: the project with most, its rows (most
   minutes inside first), those minutes, and the other projects. null when no Likely row is there. */
function likelyIn(a, b) {
  const L = state.likely, by = new Map();
  for (const e of L && !L.disabled && !L.error ? L.entries || [] : []) {
    const m = Math.min(b, e.e) - Math.max(a, e.s);
    if (m <= 0) continue;
    const p = by.get(e.project) || {project: e.project, minutes: 0, rows: []};
    p.minutes += m; p.rows.push([e, m]); by.set(e.project, p);
  }
  const [top, ...others] = [...by.values()].sort((p, q) => q.minutes - p.minutes);
  return top ? {project: top.project, minutes: top.minutes, rows: top.rows.sort((p, q) => q[1] - p[1]).map(r => r[0]),
                others: others.map(p => p.project)} : null;
}

/* a stretch picked some other way (the yellow rail, the hatched rail, a drag, an Activity block, + Log, Log and next):
   work, the Likely project with most minutes in it (its rows, not the blocks, which change with the zoom), when it has
   half the stretch, or less when no meeting covers half and no rule (hint, rulesInRange) has more of it; meet, the
   meeting that covers most of it, if half. */
function stretchWork(a, b, hint = []) {
  const cover = (s, e) => Math.max(0, Math.min(b, e) - Math.max(a, s));
  const mt = (typeof meetCache !== 'undefined' ? meetCache : []).filter(x => !x.declined).map(x => [x, cover(x.s, x.e)]).sort((p, q) => q[1] - p[1])[0];
  const meet = mt && mt[1] >= (b - a) / 2 ? mt[0] : null, work = likelyIn(a, b);
  if (work) work.half = work.minutes >= (b - a) / 2;
  return {work: work && (work.half || (!meet && !hint.some(h => h.minutes > work.minutes))) ? work : null, meet};
}

/* what to offer first for such a stretch: that project's tasks, as for its Likely block (saying how much of the
   stretch it had when not half), else the meeting's */
function leadFor(a, b, hint = []) {
  const {work, meet} = stretchWork(a, b, hint), t = work ? rowTasks(work.rows) : null;
  if (t && (t.ids.length || t.learned.length || t.claude.length))
    return {label: `For this work (${work.project}${work.half ? '' : `, ${dur(Math.round(work.minutes))} of ${dur(Math.round(b - a))}`})`,
            ids: t.ids, idsWhy: 'linked in the Claude session', tasks: [...t.claude, ...t.learned]};
  if (meet)
    return {label: `For “${meet.title || 'the meeting'}”`, url: `/api/meeting-tasks?date=${state.date}&uid=${encodeURIComponent(meet.uid || '')}&title=${encodeURIComponent(meet.title || '')}`};
  return null;
}

/* the log dialog for such a stretch: leadFor's tasks first, and the note drafted as for a Likely block, from that
   project's rows in it: Claude's pick's note, else your commits; then, with Claude Code on, Claude's note from the
   sessions, saying which project when others worked in the stretch too */
function logStretch(a, b, exact = false) {  // exact: a drag picked these minutes, they are not rounded
  // the note at once from what the page knows (Claude's pick, the commits); range.js then works out what the stretch
  // holds (GET /api/range: meetings and work outside Claude too), leads with its main task and asks Claude for the note
  const hint = rulesInRange(a, b), {work} = stretchWork(a, b, hint), draft = work ? rowTasks(work.rows).draft : null;
  openLog({start: a, minutes: b - a, hint, lead: leadFor(a, b, hint), note: draft?.note || '', drafted: draft, range: true, exact});
}

/* the active minutes inside [a, b): from the first active minute, as long as the activity was (not the slot) */
function activeSpan(a, b) {
  let first = null, total = 0;
  for (const it of state.act?.items || []) {
    if (it.cat === 'personal') continue;
    const s = Math.max(a, it.s), e = Math.min(b, it.e);
    if (e > s) { total += e - s; first = first == null ? s : Math.min(first, s); }
  }
  return first == null ? {start: a, minutes: b - a} : {start: Math.floor(first), minutes: Math.max(1, Math.round(total))};
}

/* a Likely block's rows of its own project, longest first */
function blockRows(b) { return [...(b?.entries || [])].filter(e => e.project === b.project).sort((p, q) => q.minutes - p.minutes); }
/* Claude's answer (the Ask Claude button) for some Likely rows: the pick of the first row that has one */
function rowsPick(rows) {
  const picks = state.claude?.date === state.date ? state.claude.picks : null;
  return picks ? rows.map(e => picks[`${e.s}|${e.project}`]).find(p => p?.tasks?.length) || null : null;
}
/* for a Likely block: the pick of its longest row that has one */
function claudePick(b) { return b ? rowsPick(blockRows(b)) : null; }

/* a note from your commits in a block, as commits.note() makes it: the first three subjects, a shared "Area: " said
   once, stopping at 160 characters */
function commitNote(subjects) {
  let out = '', last = null;
  for (const s of [...new Set(subjects)].slice(0, 3)) {
    const i = s.indexOf(': '), head = i > 0 ? s.slice(0, i) : null;
    const next = out ? `${out}; ${head && head === last && s.length > i + 2 ? s.slice(i + 2) : s}` : s;
    if (out && next.length > 160) break;
    out = next; last = head;
  }
  return /^[a-z]+:?(\s|$)/.test(out) ? out.charAt(0).toUpperCase() + out.slice(1) : out;  // a word, not qa:full
}

/* for some Likely rows of one project, the most telling first (a block's longest, a stretch's most minutes in it): the
   Productive tasks linked in their sessions (ids), the tasks learned from your logging in the same session or
   repository (with why), and Claude's pick with its drafted note, else a note from your commits */
function likelyTasks(b) { return rowTasks(blockRows(b)); }
function rowTasks(rows) {
  const seen = new Set(), learned = [];
  for (const t of rows.flatMap(e => e.learned || [])) if (!seen.has(t.taskId) && seen.add(t.taskId)) learned.push(t);
  const pick = rowsPick(rows);
  const claude = (pick?.tasks || []).map((t, i) => ({...t, claude: true, why: i ? 'another option' : pick.why}));
  const made = commitNote([...rows].sort((p, q) => p.s - q.s).flatMap(e => e.commits || []));
  const draft = pick?.note && pick.tasks[0] ? {note: pick.note, taskId: pick.tasks[0].taskId, title: pick.tasks[0].title}
    : made ? {note: made, from: 'commits'} : null;
  return {ids: [...new Set(rows.flatMap(e => e.tasks || []))].slice(0, 4), learned: learned.slice(0, 5), claude, draft};
}

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
let blocksCache = [], likelyCache = [], loggedCache = [], repeatCache = [];
function render() {
  const Z = state.zoom, ppm = ppmOf(Z);
  const act = state.act, likely = state.likely, logged = state.logged;
  const ends = [1440];
  for (const e of logged?.entries || []) if (e.e) ends.push(e.e);
  for (const e of likely?.entries || []) ends.push(e.e);
  const end = Math.ceil(Math.max(...ends) / 60) * 60, height = end * ppm;

  // header
  const noon = new Date(state.date + 'T12:00:00Z');  // a date it cannot read shows as written, instead of stopping the page
  const label = isNaN(noon) ? String(state.date || '') : new Intl.DateTimeFormat('en-GB', {weekday: 'long', day: 'numeric',
    month: 'long', year: 'numeric', timeZone: 'UTC'}).format(noon);
  setText($('#dayname'), label.charAt(0).toUpperCase() + label.slice(1));
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
  setHTML($('#axis'), axis);
  let now = grid.querySelector('.now');
  if (grid._rules !== rules) {  // the rules change with the zoom and the day's length only
    grid.querySelectorAll('.rule').forEach(n => n.remove());
    grid.insertAdjacentHTML('beforeend', rules); grid._rules = rules;
    now?.remove(); now = null;  // after the rules, so it is drawn over them
  }
  if (state.date !== todayLocal()) now?.remove();
  else {
    if (!now) { now = document.createElement('div'); now.className = 'now'; grid.append(now); }
    now.style.top = `${nowMin() * ppm}px`;
  }
  renderMeetings(ppm);  // meetings.js: the Meetings column, only while Google Calendar is connected

  assignHues([...(logged?.entries || []).map(x => x.project), ...(CONFIG?.rules || []).map(r => r.project),
              ...(state.repeats || []).map(r => r.project)]);
  // activity
  const laneAct = $('#lane-act');
  if (!act) { setHTML(laneAct, '<div class="state">loading…</div>'); setText($('#h-act'), '…'); }
  else if (act.error) { setHTML(laneAct, `<div class="state err">${esc(act.error)}</div>`); setText($('#h-act'), 'error'); }
  else {
    blocksCache = activityBlocks(act.items, Z, end);
    const src = act.source === 'aw' ? 'ActivityWatch' : 'MemTime';
    const total = act.items.reduce((s, x) => s + x.e - x.s, 0);
    const other = (act.source === 'aw' ? act.minutes?.memtime : act.minutes?.aw) || 0;
    const hint = other === 0 ? ` · ${act.source === 'aw' ? 'MemTime' : 'ActivityWatch'} is empty for this day` : '';
    const problem = (act.problems || {})[act.source] || (!total ? Object.values(act.problems || {})[0] : '');
    setText($('#h-act'), problem ? problem : !total && !other ? 'No activity recorded' : `${src} · ${dur(total)}${hint}`);
    $('#h-act').classList.toggle('warn', !!problem);
    // away from the computer: a hatched rail on the column's left edge, above the cards (a 30-minute card can hide a
    // 20-minute break), and its length in words where no card covers it. A click logs it (a call away from the desk).
    const covered = (a, b) => blocksCache.some(k => k.start * Z < b && k.end * Z > a);
    const awayHTML = (act.away || []).map(x => {
      const h = (x.e - x.s) * ppm, len = dur(x.e - x.s), word = x.off ? 'Off' : 'Away';
      return `<div class="awayband${x.off ? ' off' : ''}" data-a="${Math.round(x.s)}" data-b="${Math.round(x.e)}" style="top:${x.s * ppm}px;height:${h}px"
        title="${word} from the computer ${hm(x.s)}–${hm(x.e)}, ${len}. Click to log it." tabindex="0" role="button"
        aria-label="${word} ${hm(x.s)}–${hm(x.e)}, ${len}: log this time"></div>`
        + (h >= 18 && !covered(x.s, x.e) ? `<div class="awaylabel" style="top:${x.s * ppm + 4}px">${word} · ${len}</div>` : '');
    }).join('');
    setHTML(laneAct, (blocksCache.length ? blocksCache.map((b, i) => {
      const x = {s: b.start * Z, e: b.end * Z};
      const byRule = new Map();  // the block belongs to a rule's project when most of its time matches it
      for (const a of b.items) { const r = ruleOf(a.it); if (r) byRule.set(r.project, (byRule.get(r.project) || 0) + a.min); }
      const top = [...byRule].sort((p, q) => q[1] - p[1])[0];
      b.rule = top && top[1] >= b.total * 0.4 ? top[0] : null;
      return `<div class="card act ${b.it.cat}${b.rule ? ' ruled' : ''}" data-k="act" data-i="${i}" style="${b.rule ? `--c:${color(b.rule)};` : ''}${place(x, ppm, 18)}"${b.rule ? ` title="${esc(b.rule)}"` : ''}
        tabindex="0" role="button" aria-label="${esc(`Activity ${hm(x.s)}–${hm(x.e)}: ${b.title}, ${dur(b.total)}`)}">
        <div class="row">${icon(b.it.icon, b.it.app)}<span class="t">${esc(b.title)}</span><span class="m num">${dur(b.total)}</span></div></div>`;
    }).join('') : '<div class="state">No activity on this day.</div>') + awayHTML);
  }

  // likely
  const laneLikely = $('#lane-likely');
  if (!likely) { setHTML(laneLikely, '<div class="state">reading Claude sessions…</div>'); setText($('#h-likely'), '…'); }
  else if (likely.disabled) { setHTML(laneLikely, ''); setText($('#h-likely'), ''); }
  else if (likely.error) { setHTML(laneLikely, `<div class="state err">${esc(likely.error)}</div>`); setText($('#h-likely'), 'error'); }
  else {
    likelyCache = likelyBlocks(likely.entries, Z, end);
    setText($('#h-likely'), `Claude sessions · ${dur(likely.proposed || 0)}`);
    const ask = $('#ask-claude');
    ask.hidden = !(CONFIG?.likely?.askClaude && CONFIG.likely.claudeFound && likely.entries.length);
    setHTML(laneLikely, likelyCache.length ? likelyCache.map((b, i) => {
      const x = {s: b.start * Z, e: b.end * Z}, h = (x.e - x.s) * ppm;
      return `<div class="card likely${b.parallel ? ' parallel' : ''}" data-k="likely" data-i="${i}" style="${place(x, ppm, 18)}"
        tabindex="0" role="button" aria-label="${esc(`Likely ${hm(x.s)}–${hm(x.e)}: ${b.project}${b.title ? ', ' + b.title : ''}, ${dur(b.total)}`)}">
        <div class="row"><span class="t">${esc(b.project)}</span><span class="m num">${dur(b.total)}</span></div>
        ${h > 34 && claudePick(b) ? `<div class="d pick" title="${esc(`Claude: ${claudePick(b).why}`)}">${CLAUDE_MARK}${esc(claudePick(b).tasks[0].title)}</div>`
          : h > 34 && b.title ? `<div class="d">${esc(b.title)}</div>` : ''}</div>`;
    }).join('') : '<div class="state">No Claude sessions on this day.</div>');
  }

  // logged
  const laneLogged = $('#lane-logged');
  let gapMin = 0;
  if (!logged) { setHTML(laneLogged, '<div class="state">reading Productive…</div>'); setText($('#h-logged'), '…'); }
  else if (logged.error) { setHTML(laneLogged, `<div class="state err">${esc(logged.error)}${logged.needsSetup ? '<div><button class="btn bordered" data-open-settings>Open Settings</button></div>' : ''}</div>`); setText($('#h-logged'), 'error'); }
  else {
    // a repeat is offered until its task has an entry that day
    const done = new Set(logged.entries.map(x => x.taskId));
    // in Log the day the proposals share the lanes (logday.js), and stand in for the repeats
    const review = ldActive();
    const laid = lanes([...(review && LD.plan?.ignoreLogged ? [] : logged.entries).map(x => ({...x})),
                        ...(review ? [] : (state.repeats || []).filter(r => !done.has(r.taskId)).map(r => ({...r, repeat: true}))),
                        ...(review ? ldLaneItems() : [])], ppm, 18);
    loggedCache = laid.filter(x => !x.repeat && !x.prop); repeatCache = laid.filter(x => x.repeat);
    const sum = logged.entries.reduce((s, x) => s + x.minutes, 0);
    const head = $('#h-logged');
    setText(head, logged.warning ? logged.warning : `Productive · ${dur(sum)} · updated ${logged.fetched.split(' ')[1]}`);
    head.classList.toggle('warn', !!logged.warning);
    setText(head.previousElementSibling, ldActive() ? 'Logged and proposed' : 'Logged');
    if (ldActive() && !logged.warning) setText(head, ldHead(sum));
    let html = loggedCache.map((x, i) => {
      const h = (x.e - x.s) * ppm, lines = Math.max(0, Math.floor((h - 36) / 15));
      const rz = x.s == null ? '' : '<i class="rz rz-t" data-rz="t" title="Drag to change the start"></i><i class="rz rz-b" data-rz="b" title="Drag to change the end"></i>';
      return `<div class="card logged" data-k="logged" data-i="${i}" style="--c:${color(x.project)};${place(x, ppm, 18)}"
        tabindex="0" role="button" aria-label="${esc(`Logged ${hm(x.s)}–${hm(x.e)}${x.fromClaudeCode ? ' from Claude Code' : ''}: ${x.project}${x.task ? ', ' + x.task : ''}, ${dur(x.minutes)}`)}"${x.fromClaudeCode ? ' title="Logged from Claude Code (Pulsar in Claude Code)"' : ''}>
        <div class="row"><span class="t">${esc(x.project)}</span>${x.billable ? `<span class="bill" title="Billable${x.billing ? ': ' + esc(x.billing) : ''}">$</span>` : ''}<span class="m num">${dur(x.minutes)}</span></div>
        ${h > 34 ? `<div class="d">${esc(x.task)}</div>` : ''}
        ${lines ? `<div class="n" style="-webkit-line-clamp:${lines}">${esc(x.note)}</div>` : ''}${rz}</div>`;
    }).join('') + repeatCache.map((x, i) => {
      const h = (x.e - x.s) * ppm, why = `Logged at about ${hm(x.at)} on ${x.days} days; ${x.seen} seen today. Click to log it.`;
      return `<div class="card repeat" data-k="repeat" data-i="${i}" style="--c:${color(x.project)};${place(x, ppm, 18)}" title="${esc(why)}"
        tabindex="0" role="button" aria-label="${esc(`Not logged yet, usually logged at this time: ${x.title}, ${hm(x.s)}–${hm(x.e)}, ${dur(x.minutes)}. Log it`)}">
        <div class="row"><span class="t">${esc(x.title)}</span><span class="m num">${dur(x.minutes)}</span></div>
        ${h > 34 ? `<div class="d">Usual · ${esc(x.seen)} seen · click to log</div>` : ''}</div>`;
    }).join('');
    // active minutes (not personal) with no entry on them. The number counts each minute's active share, as the
    // report does; the amber rails mark stretches of at least 3 mostly-active minutes, for the eye and the click.
    if (act && !act.error) {
      const active = new Float32Array(end + 1), covered = new Uint8Array(end + 1);
      for (const it of act.items) {
        if (it.cat === 'personal') continue;
        for (let m = it.s; m < it.e && m < end;) { const next = Math.min(it.e, Math.floor(m) + 1); active[Math.floor(m)] += next - m; m = next; }
      }
      for (const e of ldActive() && LD.plan?.ignoreLogged ? [] : logged.entries) if (e.s != null) for (let m = Math.floor(e.s); m < Math.floor(e.s) + e.minutes && m < end; m++) covered[m] = 1;
      for (const [a, b] of ldActive() ? ldCoverSpans() : []) for (let m = Math.floor(a); m < b && m < end; m++) covered[m] = 1;  // ticked proposals
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
    if (review) html += ldCards(ppm, laid.filter(x => x.prop));  // logday.js: the proposed entries, dashed
    setHTML(laneLogged, html || '<div class="state">No entries on this day.</div>');
  }

  renderAside(gapMin);
  renderFlow();  // flow.js: the first-run checklist and the week in the header

  // first view of a day: today opens with now in the lower third, another day where its work begins
  if (state.scrolledFor !== state.date && act && logged) {
    if (state.date === todayLocal()) scrollToNow(false);
    else {
      const first = firstWorkRun();  // flow.js
      if (first != null) $('#scroll').scrollTop = Math.max(0, first - 30) * ppm;
    }
    state.scrolledFor = state.date;
  }
}

/* now in the lower third, the hours before it in view */
function scrollToNow(smooth = true) {
  const sc = $('#scroll');
  sc.scrollTo({top: Math.max(0, nowMin() * ppmOf(state.zoom) - sc.clientHeight * 0.7), behavior: smooth ? 'smooth' : 'auto'});
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
  setHTML($('#worktime'), `<h2>Work time</h2>
    <div class="kv big"><span>Logged</span><span class="num">${v(sum)}</span></div>
    ${bill ? `<div class="kv"><span>Billable</span><span class="num">${dur(bill.yes)}</span></div><div class="kv"><span>Not billable</span><span class="num">${dur(bill.no)}</span></div>` : ''}
    ${sum && sum > (bill ? bill.yes + bill.no : 0) && bill ? `<div class="kv"><span>Billing unknown</span><span class="num">${dur(sum - bill.yes - bill.no)}</span></div>` : ''}
    <div class="kv"><span>Active, work</span><span class="num">${v(work)}</span></div>
    ${personal ? `<div class="kv"><span>Personal</span><span class="num">${dur(personal)}</span></div>` : ''}
    ${logged && act && !logged.error && !act.error ? `<div class="kv gap"><span>Active, not logged</span><span class="num">${dur(gapMin)}</span></div>` : ''}
    ${act?.away?.some(x => !x.off) ? `<div class="kv"><span>Away, breaks</span><span class="num">${dur(act.away.filter(x => !x.off).reduce((s, x) => s + x.e - x.s, 0))}</span></div>` : ''}
    <div class="bar"><i style="width:${Math.min(100, (sum || 0) / TARGET * 100)}%"></i></div>
    <div class="kv"><span>Daily target</span><span class="num">${dur(TARGET)}</span></div>`);

  if (!logged || logged.error) { setHTML($('#projects'), `<h2>Total project time</h2><div class="note">${logged ? esc(logged.error) : '…'}</div>`); }
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
    setHTML($('#projects'), `<h2>Total project time<span class="total num">${dur(total)}</span></h2>` +
      ([...byProject].sort((a, b) => b[1].min - a[1].min).map(([name, p]) =>
        `<details class="pg" style="--c:${color(name)}" data-p="${esc(name)}"${folded.has(name) ? '' : ' open'}>
          <summary><b>${esc(name)}</b><span class="num">${dur(p.min)}</span></summary>
          ${[...p.rows.values()].sort((a, b) => b.min - a.min).map(r =>
            `<div class="ti" title="${esc([...r.tasks].join('\n'))}"><span>${esc(r.budget)}${r.service ? ` <small>· ${esc(r.service)}</small>` : ''}${r.billable ? `<span class="bill" title="Billable: ${esc(r.billing)}">$</span>` : ''}</span><span class="num">${dur(r.min)}</span></div>`).join('')}
        </details>`
      ).join('') || '<div class="note">Nothing logged.</div>')
      + (noStart.length ? `<div class="note">No start time: ${noStart.length} (${dur(noStart.reduce((s, x) => s + x.minutes, 0))}), not on the timeline.</div>` : ''));
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
    setHTML(box, '<h2>Tasks against estimates</h2>' + (state.estimatesError ? `<div class="note">${esc(state.estimatesError)}</div>` : '') + [...byTask.values()].sort((a, b) => b.today - a.today).map(t => {
      const line = est ? estimateLine(est[t.taskId]) : null;
      return `<div class="tk"><div class="tk-h"><span class="dot" style="background:${color(t.project)}"></span><b title="${esc(t.project)}">${esc(t.title)}</b><span class="num">${dur(t.today)} today</span></div>
        ${line ? `<div class="tk-l${line.over ? ' over' : ''}">${line.over ? '<span aria-hidden="true">⚠ </span>' : ''}${esc(line.text)}</div>
          ${line.pct != null ? `<div class="bar"><i style="width:${line.pct}%${line.over ? ';background:var(--gap);opacity:1' : ''}"></i></div>` : ''}`
               : `<div class="tk-l">${est ? 'no data from Productive' : 'reading estimates…'}</div>`}</div>`;
    }).join('') + '<div class="note">Totals include everyone\'s time on the task.</div>');
  }

  if (likely?.disabled) { setHTML($('#likelybox'), ''); }
  else if (!likely || likely.error) { setHTML($('#likelybox'), `<h2>Likely, from Claude sessions</h2><div class="note">${likely ? esc(likely.error) : 'reading…'}</div>`); }
  else {
    const byProject = new Map();
    for (const e of likely.entries) byProject.set(e.project, (byProject.get(e.project) || 0) + e.minutes);
    const unplaced = (likely.unplaced || []).reduce((s, u) => s + u.minutes, 0);
    setHTML($('#likelybox'), `<h2>Likely, from Claude sessions</h2>` +
      [...byProject].sort((a, b) => b[1] - a[1]).map(([p, m]) => `<div class="kv"><span>${esc(p)}</span><span class="num">${dur(m)}</span></div>`).join('') +
      (unplaced ? `<div class="note">Not in the column: ${dur(unplaced)} of sessions that ran at the same time as other work.</div>` : '') +
      (likely.logging ? `<div class="note">Time logging: ${dur(likely.logging)}, never logged.</div>` : '') +
      (likely.learning ? `<div class="note">Learning your tasks from past days: ${likely.learning.done} of ${likely.learning.of}.</div>` : '') +
      `<div class="note">Tracker: ${esc(likely.tracker || 'none')}.</div>`);
  }
}

/* ---------- popover ---------- */
const pop = $('#pop');
let popFrom = null;  // the card the keyboard opened the popover from: Escape returns there
let popEntry = null;  // the logged entry the popover shows, as it showed it
function openPop(html, ev) {
  pop.innerHTML = html; pop.classList.add('on');
  pop.setAttribute('aria-label', pop.querySelector('h3')?.textContent || 'Details');
  const r = pop.getBoundingClientRect();
  pop.style.left = Math.max(12, Math.min(ev.clientX + 14, innerWidth - r.width - 12)) + 'px';
  pop.style.top = Math.max(12, Math.min(ev.clientY - 20, innerHeight - r.height - 12)) + 'px';
}
document.addEventListener('click', ev => {
  const logBtn = ev.target.closest('[data-log-a], .gaprail, .awayband');
  if (logBtn) {
    let a = +(logBtn.dataset.logA ?? logBtn.dataset.a), b = +(logBtn.dataset.logB ?? logBtn.dataset.b);
    if (logBtn.dataset.likely != null) {  // a Likely block: its own minutes, not the slots it spans at this zoom
      const rows = (likelyCache[+logBtn.dataset.likely]?.entries || []).filter(e => e.project === likelyCache[+logBtn.dataset.likely].project);
      if (rows.length) { a = Math.min(...rows.map(e => e.s)); b = a + rows.reduce((s, e) => s + e.minutes, 0); }
    } else if (logBtn.dataset.act != null) {  // an Activity block: the active minutes in it
      const sp = activeSpan(a, b); a = sp.start; b = sp.start + sp.minutes;
    }
    const meet = logBtn.dataset.meet != null ? meetCache[+logBtn.dataset.meet] : null;
    pop.classList.remove('on');
    if (!meet && logBtn.dataset.likely == null) { logStretch(a, b); return; }  // a rail or an Activity block
    const linked = logBtn.dataset.likely != null ? likelyTasks(likelyCache[+logBtn.dataset.likely]) : {ids: [], learned: [], claude: [], draft: null};
    openLog({start: a, minutes: b - a, hint: rulesInRange(a, b), note: (meet ? meet.note : linked.draft?.note) || '',
             drafted: meet ? null : linked.draft,
             lead: meet ? {label: 'For this meeting', url: `/api/meeting-tasks?date=${state.date}&uid=${
                             encodeURIComponent(meet.uid || '')}&title=${encodeURIComponent(meet.title || '')}`}
                 : linked.ids.length || linked.learned.length || linked.claude.length
                   ? {label: 'For this work', ids: linked.ids, idsWhy: 'linked in the Claude session',
                      tasks: [...linked.claude, ...linked.learned]} : null});
    // with Claude Code on, a note from the block's session and commits, unless Claude's pick already brought one
    if (!meet && logDlg.open && CONFIG?.likely?.askClaude && (!linked.draft || linked.draft.from === 'commits'))
      draftFromSession({date: state.date, s: a, e: b, project: likelyCache[+logBtn.dataset.likely].project});
    return;
  }
  // Edit, Delete and Change act on the entry the popover shows (popEntry), never on a place in a list that a refresh
  // may have filled with another one; the server also refuses it if it changed since (seen)
  if (ev.target.closest('[data-edit]') && popEntry) { const x = popEntry; closePop(); editEntry(x); return; }
  if (ev.target.closest('[data-del]') && popEntry) { askDelete(popEntry, ev); return; }
  const delYes = ev.target.closest('[data-del-yes]');
  if (delYes && popEntry) { deleteEntry(popEntry, delYes); return; }
  const rzYes = ev.target.closest('[data-rz-yes]');
  if (rzYes) { saveResize(rzYes, rzYes.dataset.force === '1'); return; }
  if (ev.target.closest('[data-rz-no]')) { closePop(); forgetHTML($('#lane-logged')); render(); return; }
  const ruleBtn = ev.target.closest('[data-rule-text]');
  if (ruleBtn) { pop.classList.remove('on'); openRule(ruleBtn.dataset.ruleText); return; }
  const card = ev.target.closest('.card');
  if (!card) { if (!ev.target.closest('.pop')) closePop(); return; }
  const i = +card.dataset.i, Z = state.zoom;
  if (card.dataset.k === 'prop') { closePop(); ldOpen(card.dataset.row, true); return; }  // logday.js
  if (card.dataset.k === 'meet') return openMeeting(i, ev);  // meetings.js
  if (card.dataset.k === 'repeat') {
    const x = repeatCache[i];
    closePop();
    openLog({start: x.s, minutes: x.minutes, note: x.note,
             lead: {label: `Usually at this time (${x.days} days)`, tasks: [{taskId: x.taskId, title: x.title, project: x.project}]}});
    return;
  }
  if (card.dataset.k === 'act') {
    const b = blocksCache[i];
    openPop(`<div class="ph"><div><h3>${hm(b.start * Z)}–${hm(b.end * Z)}</h3><div class="sub">${dur(b.total)} active</div></div>
      <button class="btn bordered sm" data-log-a="${b.start * Z}" data-log-b="${b.end * Z}" data-act="${i}">Log this time</button></div>` +
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
      <button class="btn bordered sm" data-log-a="${b.start * Z}" data-log-b="${b.end * Z}" data-likely="${i}">Log this time</button></div>` +
      b.entries.map(x => `<div class="li"><div class="t"><div><b>${esc(x.project)}</b> · ${esc(x.titles.join(', ') || '(untitled)')}</div>
        <small>${hm(x.s)}–${hm(x.e)}${x.kind === 'parallel' ? ' · at the same time as other work, so shown after it' : ''} · ${esc(x.messages)} messages${x.first_message ? ' · ' + esc(x.first_message) : ''}</small></div>
        <span class="num">${dur(x.minutes)}</span></div>`).join(''), ev);
  } else {
    const x = loggedCache[i];
    popEntry = x;
    openPop(`<h3>${esc(x.project)}</h3><div class="sub">${hm(x.s)}–${hm(x.e)} · ${dur(x.minutes)}${x.approved ? ' · approved' : ''}${x.fromClaudeCode ? ' · from Claude Code' : ''}</div>
      <p>${esc(x.note)}</p>
      <dl><dt>Task</dt><dd>${esc(x.task)}</dd><dt>Budget</dt><dd>${esc(x.budget)}</dd><dt>Service</dt><dd>${esc(x.service)}</dd><dt>Billable</dt><dd>${x.billable == null ? 'Unknown' : x.billable ? 'Yes' : 'No'}${x.billing ? ' · ' + esc(x.billing) : ''}</dd></dl>
      <div class="btnrow"><button class="btn bordered sm" data-edit>Edit</button><button class="btn bordered sm" data-del>Delete</button>${
        productiveTaskURL(x.taskId) ? `<a class="btn bordered sm" href="${esc(productiveTaskURL(x.taskId))}" target="_blank" rel="noopener noreferrer">Open in Productive</a>` : ''}</div>
      ${x.approved ? '<p class="sub">Approved. Where your budget has an approver, Productive may refuse a change and say why.</p>' : ''}`, ev);
  }
});
function closePop(refocus = false) {
  const was = pop.classList.contains('on');
  pop.classList.remove('on'); popEntry = null;
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
  if ((ev.key === 'Enter' || ev.key === ' ') && ev.target.matches?.('.card[data-k], .gaprail, .awayband')) {
    ev.preventDefault(); activateByKey(ev.target); return;
  }
  if (ev.key === 'Escape') closePop(true);
  if (ev.metaKey || ev.ctrlKey || ev.altKey || ev.shiftKey) return;  // Cmd+← and friends belong to the browser
  if (ev.key === 'ArrowLeft') go(shiftDate(state.date, -1));
  if (ev.key === 'ArrowRight') go(shiftDate(state.date, 1));
  if (ev.code === 'KeyT') go(todayLocal());  // the T key on any layout
});

function go(date) { if (!validDate(date)) return; if (ldActive()) closeLogDay(); state.date = date; closePop(); syncURL(); load(); }
$('#prev').onclick = () => go(shiftDate(state.date, -1));
$('#next').onclick = () => go(shiftDate(state.date, 1));
$('#today').onclick = () => go(todayLocal());
$('#date').onchange = e => {
  if (validDate(e.target.value)) go(e.target.value);
  else if (!e.target.value) e.target.value = state.date;  // cleared: show the day again
};
$('#date').addEventListener('blur', e => { if (e.target.value !== state.date) e.target.value = state.date; });
$('#refresh').onclick = async () => {
  warp();  // one short warp while the reload starts; holding it until the data is back made redrawing lag
  if (state.view === 'report') return loadReport(true);
  const btn = $('#refresh'), before = state.logged, d = state.date;
  btn.classList.add('busy');
  const [, logged] = await load({fresh: true, keepScroll: true});
  btn.classList.remove('busy');
  if (d !== state.date) return;
  const c = loggedChange(before, logged);
  toast(c.warn ? c.text : `Everything updated. Logs: ${c.text}`, c.warn);
};
/* Ask Claude: a task for each Likely block of the day, shown on the cards and first when logging one */
$('#ask-claude').onclick = async () => {
  const d = state.date;
  if (askBusy()) return;  // ask.js: one question at a time, and its working state
  askStart();
  const r = await postJSON('/api/likely/ask', {date: d});
  if (r.error) { askEnd('error'); toast(r.error, true); return; }
  if (d !== state.date) { askEnd('stale'); return; }
  state.claude = {date: d, picks: r.picks || {}};
  render();
  // the blocks on screen that show a pick: the picks are per row, and a block joins several rows
  const n = likelyCache.filter(b => claudePick(b)).length;
  const said = n ? `Claude picked a task for ${n} ${n === 1 ? 'block' : 'blocks'}. Log one to see them first.` : 'Claude found no task for these blocks.';
  toast(r.warning ? `${said} ${r.warning}` : said, !!r.warning);
  askEnd(n ? 'picked' : 'none');
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
  const d = state.date, mine = newest('activity');
  getJSON(`/api/activity?date=${d}&source=${state.source}`).then(x => { if (mine()) { state.act = x; render(); } });
  loadRepeats();  // the call is looked for in the tracker shown
});
// today: refresh everything once an hour; the ↻ buttons do it on demand
setInterval(async () => {
  if (state.view !== 'day' || state.date !== todayLocal() || document.visibilityState !== 'visible') return;
  const before = state.logged, [, logged] = await load({keepScroll: true});
  const c = loggedChange(before, logged);
  if (c.changed || c.warn) toast(c.warn ? c.text : `Logs updated automatically: ${c.text}`, c.warn);  // quiet when nothing changed
}, 60 * 60 * 1000);


/* ---------- changing a logged entry: Edit (the log dialog), Delete (asked first), drag an edge (asked first) ---------- */
/* the entry as the page shows it: the server changes nothing if Productive has it otherwise now. The start is the
   minute it falls in, as the server reads it. */
const seenOf = x => ({start: x.s == null ? null : hm(Math.floor(x.s)), minutes: x.minutes, taskId: x.taskId || '',
                      serviceId: x.serviceId || '', note: x.note || ''});
function editEntry(x) {
  openLog({date: state.date, start: x.s, minutes: x.minutes, note: x.note || '',
           edit: {id: x.id, taskId: x.taskId, serviceId: x.serviceId, service: x.service, budget: x.budget, title: x.task,
                  noteRich: !!x.noteRich, seen: seenOf(x)},
           lead: {label: 'This entry', tasks: x.taskId ? [{taskId: x.taskId, title: x.task, project: x.project}] : []}});
}
function askDelete(x, ev) {
  popEntry = x;
  openPop(`<h3>Delete this entry?</h3><div class="sub">${hm(x.s)}–${hm(x.e)} · ${dur(x.minutes)} · ${esc(x.project)}</div>
    <p>${esc(x.task || '')}</p><p class="sub">It goes to Productive's Recycle Bin, where it can be restored.</p>
    <div class="btnrow"><button class="btn primary sm" data-del-yes>Delete</button><button class="btn bordered sm" data-rz-no>Cancel</button></div>`, ev);
}
async function deleteEntry(x, btn) {
  btn.disabled = true;
  const r = await postJSON('/api/entry/delete', {id: x.id, date: state.date, seen: seenOf(x)});
  closePop();
  if (r.error) { toast(r.error, true); refreshLogged(); return; }
  toast(`Deleted ${dur(x.minutes)} of ${x.task || x.project}.`);
  refreshLogged();
}
/* drag the top or bottom edge of a logged card: 1-minute steps, the card follows, then "Change to …?" */
(function resizeEntries() {
  const lane = $('#lane-logged');
  let drag = null, justDone = 0;
  lane.addEventListener('pointerdown', ev => {
    const h = ev.target.closest('.rz'); if (!h || ev.button !== 0) return;
    const card = h.closest('.card'), x = loggedCache[+card.dataset.i];
    ev.preventDefault(); ev.stopPropagation();
    // the edge moves by as much as the pointer does: grabbed anywhere on the handle (or on a short card drawn taller
    // than its minutes), it does not jump to the pointer first
    drag = {card, x, edge: h.dataset.rz, s: x.s, e: x.e, y0: ev.clientY - lane.getBoundingClientRect().top};
    lane.setPointerCapture(ev.pointerId);
  });
  lane.addEventListener('pointermove', ev => {
    if (!drag) return;
    const ppm = ppmOf(state.zoom), d = Math.round((ev.clientY - lane.getBoundingClientRect().top - drag.y0) / ppm);
    if (drag.edge === 't') drag.s = Math.max(0, Math.min(drag.e - 1, Math.round(drag.x.s) + d));
    else drag.e = Math.min(1440, Math.max(drag.s + 1, Math.round(drag.x.e) + d));
    drag.card.style.top = (drag.s * ppm + 1) + 'px'; drag.card.style.height = Math.max((drag.e - drag.s) * ppm - 2, 18) + 'px';
    drag.card.title = `${hm(drag.s)}–${hm(drag.e)} · ${dur(drag.e - drag.s)}`;
  });
  lane.addEventListener('pointerup', ev => {
    if (!drag) return;
    const d = drag; drag = null; justDone = Date.now();
    forgetHTML(lane);  // the card was moved by hand: the next redraw puts it where its entry is
    if (Math.round(d.s) === Math.round(d.x.s) && Math.round(d.e) === Math.round(d.x.e)) { forgetHTML(lane); render(); return; }
    const r = d.card.getBoundingClientRect();
    // only the edge that moved changes: the other keeps its exact time (an entry's start can hold seconds)
    askResize(d.x, d.s === d.x.s ? null : d.s, Math.round(d.e - d.s), {clientX: r.right - 8, clientY: r.top + 10});
  });
  lane.addEventListener('pointercancel', () => { if (drag) { drag = null; forgetHTML(lane); render(); } });
  // the click that ends a drag must not open the card's popover over the question
  lane.addEventListener('click', ev => { if (Date.now() - justDone < 400) { ev.stopPropagation(); ev.preventDefault(); } }, true);
})();
/* start: the new start, or null when only the end moved (the start stays exactly as it is) */
function askResize(x, start, minutes, at, overlap = null) {
  popEntry = x;
  const s = start ?? x.s;
  openPop(`<h3>Change this entry?</h3><div class="sub">${hm(x.s)}–${hm(x.e)} → <b>${hm(s)}–${hm(s + minutes)}</b> · ${dur(minutes)}</div>
    <p>${esc(x.task || x.project)}</p>
    ${overlap ? `<p class="sub warnish">It would overlap ${esc(overlap.map(o => `${o.start} ${dur(o.minutes)} ${o.project}`).join('; '))}.</p>` : ''}
    <div class="btnrow"><button class="btn primary sm" data-rz-yes data-start="${start ?? ''}" data-minutes="${minutes}" data-force="${overlap ? 1 : 0}">${overlap ? 'Change anyway' : 'Change'}</button>
      <button class="btn bordered sm" data-rz-no>Cancel</button></div>`, at);
}
async function saveResize(btn, force) {
  const x = popEntry, start = btn.dataset.start === '' ? null : +btn.dataset.start, minutes = +btn.dataset.minutes;
  if (!x) return;
  btn.disabled = true;
  const r = await postJSON('/api/entry/update', {id: x.id, date: state.date, ...(start == null ? {} : {start: hm(start)}),
                                                 minutes, force, seen: seenOf(x)});
  if (r.overlap) { const at = btn.getBoundingClientRect(); askResize(x, start, minutes, {clientX: at.left, clientY: at.top - 60}, r.overlap); return; }
  closePop();
  if (r.error) { toast(r.error, true); refreshLogged(); return; }
  const s = start ?? x.s;
  toast(`Changed to ${hm(s)}–${hm(s + minutes)}, ${dur(minutes)}.`);
  refreshLogged();
}
