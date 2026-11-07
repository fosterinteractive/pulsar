/* What Log the day keeps of the person's work on a day, while the page is open (logday.js): ticks, tasks, services,
   times, notes, the rows opened and the rows deleted from the proposal. Closing the panel or going to another day and
   back puts them back on a fresh plan. Pure functions, no page state: tests/test_ux.py runs them under node.

   A row is known by what it was proposed for, never by its place in the list (r0, r1… shift when the plan changes):
   the day, its start as proposed, the work it came from (the repository, the meeting, else its kind) and its task as
   proposed. An edit goes back on the row with the same key; it is dropped when that row is gone, or when the time it
   covers (as edited) is logged in Productive now. A row a run already wrote stays as the journal says. */

/* the fields the person can change, and the marks of having looked */
const LD_KEEP_FIELDS = ['checked', 'opened', 'edited', 'taskId', 'title', 'project', 'serviceId', 'service', 'services',
  'billable', 'budget', 's', 'e', 'minutes', 'note', 'noteFrom', 'noteEdited', 'noteTried', 'noteError', 'flags',
  'source', 'why', 'confidence', 'confidenceWords', 'ticket', 'alts', 'claudeNone', 'serviceProblem', 'choice'];

/* A shared row: a client's agent ran on a request the person typed while internal work was in front, so the client got
   the stretch first (pulsar/dayplan.py). The person decides who it goes to, at its real time: Client (as proposed),
   Half each (the first half the client's, the second the internal work's: two rows), Internal (all of it the
   internal work's). r.client holds the client's fields as proposed, r.sharedWith the internal work's, r.whole the
   stretch [s, e]. Pure, as dayplan.apply_choice: the rows it returns replace r (and its second half, if any). */
const LD_SHARED_TEXT = {client: 'Client work went first: its agent ran while you did internal work. Check it.',
                        half: 'Half each: the client first, then your internal work.',
                        internal: 'Given to the internal work that was on screen.'};
function ldInner(w) {
  return {taskId: w.taskId ? String(w.taskId) : null, title: w.title || '', project: w.project || '',
          serviceId: w.serviceId || null, service: w.service || '', services: w.services || [], billable: w.billable,
          budget: w.budget || '', why: w.why || '', source: w.source || '', confidence: w.confidence || 'low',
          alts: [], repo: w.repo || '', note: '', noteFrom: ''};
}
function ldChoice(r, choice, step) {
  const [s, e] = r.whole || [r.s, r.e ?? r.s + r.minutes];
  const base = {...r, ...(r.client || {}), s, e, minutes: e - s, choice};
  const ready = x => ({...x, checked: !!(x.taskId && x.serviceId)});
  if (choice === 'internal') return [ready({...base, ...ldInner(r.sharedWith || {})})];
  if (choice !== 'half') return [ready({...base, choice: 'client'})];
  const g = Math.max(1, step || 5), half = Math.round((e - s) / 2 / g) * g;
  if (!(half >= g && half <= e - s - g)) return [ready({...base, choice: 'client'})];  // too short to halve on the grid
  return [ready({...base, e: s + half, minutes: half}),
          ready({...base, ...ldInner(r.sharedWith || {}), id: r.id + 'h', key: r.key + '|half', s: s + half,
                 minutes: e - s - half, halfOf: r.id})];
}
/* after ldReapply: a shared row kept as Half each gets its second half back */
function ldWithHalves(rows, step) {
  const out = [];
  for (const r of rows) {
    out.push(r);
    if (r.shared && r.choice === 'half' && !r.halfOf && !rows.some(x => x.halfOf === r.id)) {
      const pair = ldChoice(r, 'half', step);
      if (pair.length === 2) out.push(pair[1]);
    }
  }
  return out;
}

function ldRowKey(date, r) {
  const work = r.repo || (r.meeting ? 'meeting:' + r.meeting : r.kind || '');
  return [date, r.s, work, r.taskId || ''].join('|');
}

/* rows: the panel's rows (each with .key from ldRowKey on the plan as it came); open: the open row's id; deleted: the
   keys of rows deleted from the proposal. Only rows the person touched or opened are kept. */
function ldSnapshot(rows, open, deleted) {
  const kept = {};
  for (const r of rows) {
    if (!r.key || r.status === 'logged' || r.status === 'writing' || r.status === 'unknown') continue;
    if (!(r.edited || r.opened || r.noteTried)) continue;
    kept[r.key] = Object.fromEntries(LD_KEEP_FIELDS.filter(k => k in r).map(k => [k, JSON.parse(JSON.stringify(r[k] ?? null))]));
  }
  return {rows: kept, deleted: [...(deleted || [])], open: rows.find(r => r.id === open)?.key || null};
}

/* minutes [s, e) of a row inside an entry logged now: [{s, e}] */
function ldOverLogged(r, logged) {
  const e = r.e ?? r.s + r.minutes;
  return (logged || []).some(x => x.s != null && x.e != null && Math.min(e, x.e) - Math.max(r.s, x.s) > 0);
}

/* fresh: the rows of a new plan (with .key, .id, .status from the journal); kept: an ldSnapshot; logged: the day's
   entries now. {rows, open (an id or null), dropped (keys whose edits went)} */
function ldReapply(fresh, kept, logged) {
  if (!kept) return {rows: fresh, open: null, dropped: []};
  const gone = new Set(kept.deleted || []), dropped = [];
  const rows = [];
  for (const f of fresh) {
    if (f.status) { rows.push(f); continue; }  // a run wrote it, or tried: the journal's word stands
    if (gone.has(f.key)) continue;  // deleted from the proposal by the person
    const k = kept.rows?.[f.key];
    if (!k) { rows.push(f); continue; }
    if (ldOverLogged(k, logged)) { dropped.push(f.key); rows.push(f); continue; }  // its time is logged now: the plan's row
    rows.push({...f, ...k, id: f.id, key: f.key, status: f.status || null, reason: f.reason || ''});
  }
  for (const key of Object.keys(kept.rows || {})) if (!fresh.some(f => f.key === key)) dropped.push(key);  // the row is gone
  const open = kept.open ? rows.find(r => r.key === kept.open)?.id || null : null;
  return {rows, open, dropped};
}

/* the review's suggestions (ldreview.js SG.items): one is known by its kind, the row it was for as it was then, and
   what it says; its state (accepted, rejected) goes back on the same suggestion */
function sgItemKey(s) {
  const {key, state, at, ...what} = s;
  return JSON.stringify([s.source || '', s.kind, at ? [at.s, at.taskId] : null, what]);
}
