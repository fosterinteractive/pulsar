/* A stretch picked in the Day view (a drag in the Logged column, a rail, an Activity block, + Log, Log and next): what
   it holds, from Log the day's minute ownership (GET /api/range, pulsar/ranges.py). The log dialog then
   - leads with the task Log the day would give the work that holds most of it (the existing groups follow);
   - fills in that work's note: its commits, Claude's draft for that task, a meeting's title or a repeat's usual note,
     then Claude's note from the session with the Claude Code switch on (log.js draftFromSession);
   - when the stretch mixes kinds of work, says so in one line and offers to log the parts as separate entries.
   Never what the person typed or picked, and only in the dialog that asked. log.js calls rangeOpen() from openLog and
   rangeLead() from fillSuggestions; nothing else.

   The split logs its entries one at a time through POST /api/log, each with its own task, service and note and the
   same checks as the dialog (an overlap stops it; nothing is forced). A minimal version: the dialog's own way of
   logging several entries (exp/split-overlap) should replace it once both are in. */

let rangeNow = null;  // {opened (log.js logOpened), date, start, minutes, res, typed, open (the split list shown), busy}
const rgBox = () => $('#log-range');

async function rangeOpen(r) {
  const mine = logOpened;
  rangeNow = r ? {opened: mine, ...r, res: null, typed: false, open: false, busy: false} : null;
  rangeDraw();
  if (!r) return;
  const ignore = typeof LD_IGNORE !== 'undefined' && LD_IGNORE;  // logday.js: a read-only test server's whole-day view
  const res = await getJSON(`/api/range?date=${r.date}&s=${r.start}&e=${r.start + r.minutes}&source=${state.source}`
    + (ignore ? '&ignore_logged=1' : '')).catch(() => null);
  if (rangeNow?.opened !== mine || mine !== logOpened || !logDlg.open) return;  // another dialog by now
  if (!res || res.error) return;  // the dialog works as it did: nothing to add
  rangeNow.res = res;
  const g = rangeLead(r.date);
  if (g) logPicker.lead(g);
  rangeNote(res);
  rangeDraw();
}

/* the dominant work's task first, then the other parts' tasks and its alternatives. Not for work outside Claude: its
   task is only the neighbour's, and on the replay the dialog's own first task was right more often there (29% of
   those minutes against 22%); everywhere else Log the day's was (47% of the minutes of entries logged as one, 45%
   of runs of several, against 44% and 33%) */
function rangeLead(date) {
  const res = rangeNow && rangeNow.opened === logOpened && rangeNow.date === date ? rangeNow.res : null;
  const d = res?.dominant;
  if (!d?.taskId || !d.title || d.kind === 'outside') return null;
  const others = res.groups.filter(g => g !== d && g.taskId && g.title && g.minutes >= 10 && g.taskId !== d.taskId)
    .map(g => ({taskId: g.taskId, title: g.title, project: g.project, why: `also in this time: ${dur(g.minutes)}, ${g.label}`}));
  const of = `${dur(d.minutes)} of ${dur(res.work)}`;
  return {label: d.share >= 0.995 ? `For this time (${d.label})`
            : `For this time (${d.share >= 0.5 ? 'mostly ' : ''}${d.label}, ${of})`,
          tasks: [{taskId: d.taskId, title: d.title, project: d.project, why: `${d.confidenceWords}: ${d.why}`},
                  ...others, ...(d.alts || []).map(a => ({...a, why: a.why || 'another option'}))]};
}

/* the note of the work that leads, while the person has not written one; then Claude's from the session */
function rangeNote(res) {
  const f = logForm.elements, d = res.dominant;
  if (!d || d.kind === 'outside' || rangeNow.typed) return;
  if (f.note.value.trim() && !logDrafted && !logFilled) return;  // a note that is the person's own
  if (d.note) {
    f.note.value = d.note;
    if (d.noteFrom === 'commits') { logDrafted = {note: d.note, from: 'commits'}; logFilled = false; }
    else if (d.noteFrom === 'claude') { logDrafted = {note: d.note, taskId: d.taskId, title: d.title}; logFilled = false; }
    else { logDrafted = null; logFilled = true; }  // a meeting's title, a repeat's usual note
    showDraft();
  }
  if (CONFIG?.likely?.askClaude && d.kind === 'claude' && d.repo && d.noteFrom !== 'claude')
    draftFromSession({date: res.date, s: res.s, e: res.e, project: d.repo});
}

/* the line under the note: what the stretch mixes, and the split, when it is worth it */
const rgSpan = x => `${x.start}–${hm(x.s + x.minutes)}`;
const rgLoggable = x => x.taskId && x.serviceId && !x.done;
function rangeDraw() {
  const el = rgBox(), res = rangeNow?.res;
  const show = !!res?.mixed && !rangeNow.stale && res.split.length >= 2;
  el.hidden = !show;
  if (!show) { el.innerHTML = ''; return; }
  const kinds = res.groups.filter(g => g.split > 0).sort((p, q) => q.split - p.split);
  const can = res.split.filter(rgLoggable);
  const line = `This range mixes ${kinds.length} kinds of work: ${kinds.map(g => `${esc(g.label)} ${dur(g.split)}`).join(', ')}.`;
  if (!rangeNow.open) {
    el.innerHTML = `<p class="rg-line">${line}${can.length >= 2 ? ` <button type="button" class="rg-link" data-rg-open>Split into ${res.split.length} entries?</button>` : ''}</p>`;
    return;
  }
  el.innerHTML = `<p class="rg-line">${line}</p>
    <ol class="rg-list">${res.split.map((x, i) => `<li class="rg-row${x.done ? ' done' : ''}">
      <div class="rg-top"><span class="num">${rgSpan(x)}</span><span class="num rg-m">${dur(x.minutes)}</span>
        <span class="rg-task">${x.taskId && x.title ? esc(x.title) : `<span class="rg-none">No task found (${esc(x.label)})</span>`}</span>
        <span class="rg-proj">${x.done ? 'logged' : !x.taskId ? 'log it on its own afterwards'
          : x.serviceId ? esc([x.project, x.service].filter(Boolean).join(' · ')) : esc(x.serviceProblem || 'no service')}</span></div>
      ${rgLoggable(x) ? `<input class="rg-note" data-rg-note="${i}" value="${esc(x.note || '')}" placeholder="Note"
        aria-label="Note for ${rgSpan(x)}" autocomplete="off" spellcheck="true">` : ''}</li>`).join('')}</ol>
    <p class="rg-small">Each is checked like any entry: one that overlaps another stops the rest, nothing is forced.
      Notes filled in for you may reach clients; check them.</p>
    <div class="rg-act"><button type="button" class="btn bordered sm" data-rg-log${rangeNow.busy || !can.length ? ' disabled' : ''}>Log ${
      can.length === 1 ? 'this entry' : `these ${can.length} entries`}</button>
      <button type="button" class="btn sm" data-rg-close>Keep one entry</button></div>`;
}

rgBox().addEventListener('click', ev => {
  if (!rangeNow?.res) return;
  if (ev.target.closest('[data-rg-open]')) { rangeNow.open = true; rangeDraw(); rgBox().querySelector('.rg-note')?.focus(); return; }
  if (ev.target.closest('[data-rg-close]')) { rangeNow.open = false; rangeDraw(); rgBox().querySelector('[data-rg-open]')?.focus(); return; }
  if (ev.target.closest('[data-rg-log]')) rangeLogSplit();
});
rgBox().addEventListener('input', ev => {
  const i = ev.target.dataset?.rgNote;
  if (i != null && rangeNow?.res) rangeNow.res.split[+i].note = ev.target.value;
});
/* Enter in a split note must not log the one entry of the dialog */
rgBox().addEventListener('keydown', ev => { if (ev.key === 'Enter' && ev.target.matches('.rg-note') && !ev.metaKey && !ev.ctrlKey) ev.preventDefault(); });
/* the person writes the note: Pulsar never replaces it. A new time or day: the line was about the old one. */
logForm.elements.note.addEventListener('input', () => { if (rangeNow) rangeNow.typed = true; });
for (const name of ['date', 'start', 'minutes'])
  for (const type of ['input', 'change'])
    logForm.elements[name].addEventListener(type, () => { if (rangeNow && !rangeNow.busy) { rangeNow.stale = true; rangeDraw(); } });

async function rangeLogSplit() {
  const res = rangeNow.res, mine = rangeNow.opened, todo = res.split.filter(rgLoggable);
  if (!todo.length || rangeNow.busy) return;
  rangeNow.busy = true; rangeDraw();
  const main = [$('#log-save'), $('#log-next')];  // one way of logging at a time
  main.forEach(b => { b.disabled = true; });
  let stop = null, unclear = false;
  for (const [k, x] of todo.entries()) {
    setLogMsg(`Logging ${k + 1} of ${todo.length}: ${rgSpan(x)}…`);
    const r = await postJSON('/api/log', {date: res.date, start: x.start, minutes: x.minutes, taskId: x.taskId,
                                          serviceId: x.serviceId, note: (x.note || '').trim()}).catch(e => ({error: String(e)}));
    if (r.overlap) { stop = `${rgSpan(x)} overlaps ${r.overlap.map(o => `${o.start} ${dur(o.minutes)} ${o.project}`).join('; ')}`; break; }
    if (r.error || !r.created) { stop = `${rgSpan(x)} was not logged: ${r.error || 'no answer'}`; unclear = !!r.unclear; break; }
    x.done = true;
    afterLog(res.date, x.start, x.minutes, '', false);  // flow.js: Log and next leaves it out
  }
  if (rangeNow?.opened === mine) rangeNow.busy = false;
  main.forEach(b => { b.disabled = false; });
  const done = res.split.filter(x => x.done), left = res.split.filter(x => !x.done);
  if (res.date === state.date) refreshLogged();
  suggestCache = null;
  const said = `Logged ${done.length} of ${res.split.length}. ${stop}.${unclear ? ' Pulsar did not hear back, so check the Logged column first.' : ''}`;
  if (mine !== logOpened || !logDlg.open) return toast(stop ? said : `Logged ${done.length} entries.`, !!stop);  // closed meanwhile
  if (!stop && !left.some(rgLoggable)) {
    logDlg.close();
    const rest = left.length ? ` Still to log, with no task found: ${left.map(rgSpan).join(', ')}.` : '';
    return toast(`Logged ${done.length} entries: ${done.map(x => `${rgSpan(x)} ${x.title}`).join('; ')}.${rest}`);
  }
  rangeDraw();
  setLogMsg(said, true);
}
