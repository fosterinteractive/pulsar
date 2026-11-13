/* Log the day with Claude (pulsar/dayreview.py): suggestions on the day's plan, each accepted or rejected here.
   FI's rules come with the plan (GET /api/dayplan, "suggestions"); Claude's come from POST /api/dayplan/review, which
   sends the day's digest to the person's own Claude Code, isolated as Ask Claude is. Accepting changes the row in
   the review only: nothing is written until Log the day's own confirm step, one entry at a time, journalled. */

const SG = {items: [], asking: false, note: ''};
/* each suggestion is one sentence saying what its button does, and the button is named for that (2026-11-12: "Leave
   out not this entry" with Accept and Reject, and a question answered with "Noted", were not understood); a question
   comes with answers that each make their change */
const SG_KIND = {task: 'task', service: 'service', minutes: 'length', split: 'split', create_task: 'new task',
                 question: 'question', untick: "don't log", extend: 'add to an entry'};

/* the plan's own suggestions (FI's rules), when Log the day opens */
function sgFromPlan(plan) {
  SG.items = (plan?.suggestions || []).map((s, i) => ({...s, key: 'p' + i, state: 'open', at: sgAt(s.row)}));
  SG.asking = false; SG.note = '';
}
/* kept with the day's work (logday.js ldStash): the suggestions, each with its state, and the line about them */
function sgSnapshot() { return {items: SG.items.map(s => ({...s})), note: SG.note}; }
/* back on a fresh plan: FI's rules recomputed take the state they had (the same suggestion, ldkeep.js sgItemKey);
   Claude's come back as they were, matched to the rows again by time and task */
function sgRestore(k) {
  const was = new Map((k.items || []).map(s => [sgItemKey(s), s]));
  for (const s of SG.items) if (was.has(sgItemKey(s))) Object.assign(s, {state: was.get(sgItemKey(s)).state, answer: was.get(sgItemKey(s)).answer});
  SG.items = SG.items.concat((k.items || []).filter(s => s.source === 'claude').map((s, i) => ({...s, key: 'k' + i})));
  SG.note = k.note || '';
}
/* the panel opening with nothing kept in the page (after a restart): the review the server kept for this plan, if
   any; never a new run of Claude */
async function sgKept(d) {
  if (!CONFIG?.likely?.askClaude || !CONFIG.likely.claudeFound) return;
  const ans = await postJSON('/api/dayplan/review', {date: d, source: state.source, kept_only: true, ...(LD_IGNORE ? {ignore_logged: true} : {})}).catch(() => ({}));
  if (!ldActive() || LD.date !== d || !ans.kept || !(ans.suggestions || []).length || SG.items.some(s => s.source === 'claude')) return;
  sgTake(ans, true);
  renderReview(); render();
}
function sgTake(ans, kept = false) {
  const rows = ans.rows || [];
  SG.items = SG.items.filter(x => x.source !== 'claude').concat((ans.suggestions || []).map((s, i) => {
    const at = s.row ? (sgAt(s.row, rows) || null) : null;  // matched to the page's rows by time and task, not id
    return {...s, key: 'c' + i, state: 'open', at};
  }));
  const n = (ans.suggestions || []).length;
  SG.note = `${n ? `${n} ${n === 1 ? 'suggestion' : 'suggestions'}` : 'Nothing to change'} from Claude`
    + (kept || ans.kept ? ', kept from the last review of this plan' : ans.cost != null ? ` · $${(+ans.cost).toFixed(3)}` : '')
    + (ans.dropped?.length ? ` · ${ans.dropped.length} left out by Pulsar's checks` : '') + '.';
}
/* the row a suggestion was made for, as it was then: it applies only while the row is still as proposed */
function sgAt(id, rows = null) {
  const r = rows ? rows.find(x => x.id === id) : ldRow(id);
  return r ? {s: r.s, taskId: r.taskId || null} : null;
}
function sgRowFor(s) {
  if (!s.row) return null;
  return LD.rows.find(r => s.at && r.s === s.at.s && (r.taskId || null) === s.at.taskId && ldPending(r)) || null;
}

async function sgAsk() {
  if (SG.asking) return;  // the button stays live while it turns (a disabled one would be dimmed), so a second click waits
  const d = LD.date;
  SG.asking = true; SG.note = ''; renderReview();
  const again = SG.items.some(s => s.source === 'claude');  // "Review again": a new run, not the kept one
  const ans = await postJSON('/api/dayplan/review', {date: d, source: state.source, again, ...(LD_IGNORE ? {ignore_logged: true} : {})});
  SG.asking = false;
  if (!ldActive() || LD.date !== d) return;
  if (ans.error) { SG.note = `Check my day with Claude: ${ans.error}`; renderReview(); return; }
  sgTake(ans);
  renderReview(); render();
}

/* an entry already in the Logged column, by its id (extend) */
const sgLogged = id => (state.logged?.entries || []).find(x => String(x.id) === String(id) && x.s != null) || null;
/* extend: that entry's time once the row's minutes are added to it */
function sgSpan(x, r) { return {a: Math.min(x.s, r.s), b: Math.max(x.e ?? x.s + x.minutes, r.e ?? r.s + r.minutes)}; }
/* {text, ok}: the sentence (HTML) and the label of the button that does it (none: nothing to do but read it) */
function sgSay(s, r) {
  const t = (title, id, project) => `<b>${esc(title || 'task ' + id)}</b>${project ? ` <span class="sg-p">· ${esc(project)}</span>` : ''}`;
  if (s.kind === 'task') return {text: `${r?.taskId ? 'Change the task to' : 'Log it to'} ${t(s.title, s.taskId, s.project)}`, ok: 'Use this task'};
  if (s.kind === 'service') return {text: `Change the service to <b>${esc(s.service)}</b>`, ok: 'Use this service'};
  if (s.kind === 'minutes') return {text: `Make it <b>${dur(s.minutes)}</b>${r ? ` instead of ${dur(r.minutes)}` : ''}`, ok: `Make it ${dur(s.minutes)}`};
  if (s.kind === 'split') return {text: `Split it: ${s.split.map(p => `${t(p.title, p.taskId)} ${dur(p.minutes)}`).join(' + ')}`, ok: 'Split it'};
  if (s.kind === 'untick') return {text: "Don't log this entry (it stays in the list, unticked)", ok: "Don't log it"};
  if (s.kind === 'extend') {
    const x = sgLogged(s.entryId), name = esc(x?.task || s.entryTask || 'the entry next to it');
    if (!x || !r) return {text: `Add these minutes to <b>${name}</b>, already logged`, ok: null};
    const {a, b} = sgSpan(x, r);
    return {text: `Add these ${dur(r.minutes)} to <b>${name}</b>, already logged: ${hm(x.s)}–${hm(x.e ?? x.s + x.minutes)} becomes <b>${hm(a)}–${hm(b)}</b>, with no new entry`, ok: 'Add to that entry…'};
  }
  if (s.kind === 'create_task') return {text: `No task fits: create ${t(s.title, '', s.project)}${s.parentTitle ? ` under ${esc(s.parentTitle)}` : ''}`, ok: r ? 'Create it…' : null};
  if (s.kind === 'question') return {text: `<b>${esc(s.question)}</b>`, ok: null};
  return {text: '', ok: null};
}
function sgItemHTML(s, row) {
  const mark = s.source === 'claude' ? CLAUDE_MARK.split('<span')[0] : '<span class="sg-rule" aria-hidden="true">FI</span>';
  const who = s.source === 'claude' ? 'Claude' : "FI's rule";
  const gone = s.row && !row, say = sgSay(s, row), answers = s.kind === 'question' ? s.answers || [] : [];
  const done = s.state === 'accepted' ? `<span class="sg-done">${s.answer ? `Answered: ${esc(s.answer)}` : 'Done'}</span>`
    : s.state === 'rejected' ? `<span class="sg-done">${s.kind === 'question' ? (answers.length ? 'Skipped' : 'Seen') : s.kind === 'create_task' ? 'Not now' : 'Kept as it is'}</span>` : '';
  const acts = done || gone ? ''
    : answers.length ? answers.map((a, i) => `<button type="button" class="btn sm" data-sg-ans="${s.key}" data-i="${i}">${esc(a.label)}</button>`).join('')
      + `<button type="button" class="btn sm bordered" data-sg-no="${s.key}">Skip</button>`
    : say.ok ? `<button type="button" class="btn sm" data-sg-ok="${s.key}">${esc(say.ok)}</button><button type="button" class="btn sm bordered" data-sg-no="${s.key}">${s.kind === 'create_task' ? 'Not now' : 'Keep as it is'}</button>`
    : `<button type="button" class="btn sm bordered" data-sg-no="${s.key}">Got it</button>`;
  return `<li class="sg ${s.state}${gone ? ' gone' : ''}" data-sg="${s.key}" aria-label="${esc(`${who} ${s.kind === 'question' ? 'asks' : 'suggests'}: ${SG_KIND[s.kind]}`)}">${mark}
    <div class="sg-b"><span class="sg-k">${who} ${s.kind === 'question' ? 'asks' : 'suggests'}:</span> <span class="sg-t">${say.text}</span>
      <span class="sg-w">${esc(s.why)}${gone ? ' · the row changed since' : ''}</span></div>
    ${done || (acts ? `<span class="sg-a">${acts}</span>` : '')}</li>`;
}
/* under a row: its open suggestions (and the ones just decided, so the click shows) */
function sgRowHTML(r) {
  const mine = SG.items.filter(s => s.row && sgRowFor(s) === r && s.state !== 'hidden');
  return mine.length ? `<ul class="sg-list" aria-label="Suggestions for this entry">${mine.map(s => sgItemHTML(s, r)).join('')}</ul>` : '';
}
/* above the list: the button and what Claude said; the suggestions for the whole day are the list's first item */
/* Claude's one way in, here in Log the day: "Check my day with Claude" asks for task picks for the rows Pulsar is
   unsure of when the day has none yet (Ask Claude), then reviews the whole day with FI's rules (Review). The button
   says which models run and the most each run may cost; nothing runs by itself. */
const useOf = id => (CONFIG?.claude?.uses || []).find(u => u.id === id);
const MODEL_NAME = m => ({haiku: 'Haiku', sonnet: 'Sonnet', opus: 'Opus'}[m] || 'Claude');
function sgNeedsPicks() {
  return !LD.plan?.claude && !LD.picked && LD.rows.some(x => ldPending(x) && x.confidence !== 'high' && ['focused', 'agent', 'outside'].includes(x.kind));
}
function sgCheckTitle() {
  const a = useOf('ask'), r = useOf('review'), cap = u => u?.caps?.[u.model] != null ? `, at most $${u.caps[u.model]}` : '';
  return (sgNeedsPicks() ? `Asks ${MODEL_NAME(a?.model)} for a task for the rows Pulsar is unsure of${cap(a)}, then ` : '')
    + `${sgNeedsPicks() ? 'reviews' : 'Reviews'} the whole day against the team's logging rules (${MODEL_NAME(r?.model)}${cap(r)}). Nothing is written until you press a suggestion's button.`;
}
async function sgCheck() {
  if (SG.asking || LD.asking) return;
  const d = LD.date;
  if (sgNeedsPicks()) { await askClaudeForDay(d); if (!ldActive() || LD.date !== d) return; }
  await sgAsk();
}
function sgHead() {
  const can = CONFIG?.likely?.askClaude && CONFIG.likely.claudeFound, busy = SG.asking || LD.asking;
  // while Claude works, the button wears Ask Claude's ring, beams and glow (ask.css)
  const anim = busy ? '<span class="ask-ring" aria-hidden="true"><span class="ask-beam"></span><span class="ask-flash"></span></span><span class="ask-glow" aria-hidden="true"></span>' : '';
  const label = busy ? 'Checking…' : SG.items.some(s => s.source === 'claude') ? 'Check again' : 'Check my day with Claude';
  const btn = can && !LD.writing ? `<button type="button" class="btn sm bordered${busy ? ' asking' : ''}" id="sg-ask" title="${esc(sgCheckTitle())}"${busy ? ' aria-disabled="true"' : ''}>${anim}${CLAUDE_MARK.split('<span')[0]}${label}</button>` : '';
  const line = SG.asking ? `${MODEL_NAME(useOf('review')?.model)} is reviewing the day (a minute or two)…` : SG.note;
  const open = SG.items.filter(s => s.state === 'open' && (!s.row || sgRowFor(s))).length;
  return `<div class="sg-head">${btn}${line ? `<p class="rv-ask" role="status">${esc(line)}</p>` : ''}
    ${open ? `<span class="iss ok">${open} ${open === 1 ? 'suggestion' : 'suggestions'} to decide</span>` : ''}</div>`;
}
/* questions for the whole day, and suggestions whose row has changed since: first in the list, scrolling with it */
function sgDayHTML() {
  const day = SG.items.filter(s => (!s.row || !sgRowFor(s)) && s.state !== 'hidden');
  return day.length ? `<li class="sg-day"><ul class="sg-list day" aria-label="For the whole day">${day.map(s => sgItemHTML(s, null)).join('')}</ul></li>` : '';
}

/* a change to a row (a suggestion, or the answer given to a question): the row changes in the review, as if changed by
   hand; still nothing is written */
async function sgApply(r, c) {
  if (c.kind === 'task') await ldSetTask(r, {taskId: c.taskId, title: c.title, project: c.project});
  else if (c.kind === 'service') {
    const x = (r.services || []).find(v => v.id === c.serviceId);
    if (x) Object.assign(r, {serviceId: x.id, service: x.name, billable: x.billable, budget: x.budget || ''});
    ldChanged(r);
  } else if (c.kind === 'minutes') { r.minutes = c.minutes; r.e = r.s + c.minutes; ldChanged(r); }
  else if (c.kind === 'untick') { r.checked = false; ldChanged(r); }
  else if (c.kind === 'split') {
    let at = r.s;
    const parts = c.split.map((p, j) => {
      // the server's journal takes row names of letters, digits, "-" and "_" only (dayplan.ROW): "c3.0" was refused,
      // so no part of a split was ever written (2026-11-12)
      const n = {...r, id: `${r.id}-${j}`, key: `${r.key}#${j}`, splitOf: r.splitOf || r.key, s: at, e: at + p.minutes,
                 minutes: p.minutes, alts: [], edited: true};
      at += p.minutes;
      return [n, p];
    });
    LD.rows.splice(LD.rows.indexOf(r), 1, ...parts.map(x => x[0]));
    for (const [n, p] of parts) {
      const same = String(p.taskId) === String(r.taskId || '');
      await ldSetTask(n, {taskId: p.taskId, title: p.title, project: r.project});
      // each part its own note (2026-11-12: three parts logged with the row's one note), set after its task: setting a
      // task drops a note Claude wrote for another task, which had dropped these too. Claude's for that part, a draft
      // like any of Claude's notes (never ticked by itself); else the row's note only on the part of the row's own
      // task; else empty, to write
      if (p.note) Object.assign(n, {note: p.note, noteFrom: 'claude', noteEdited: false, checked: false,
                                     flags: [...new Set([...(n.flags || []), 'claudenote'])]});
      else if (same) Object.assign(n, {note: r.note || '', noteFrom: r.noteFrom || ''});
      else Object.assign(n, {note: '', noteFrom: ''});
    }
    renderReview(); render();
  }
}
async function sgAccept(s) {
  const r = sgRowFor(s);
  // a split or a length under the shortest entry, from a review made before that rule: never applied
  if ((s.kind === 'split' && s.split.some(p => p.minutes < minEntry())) || (s.kind === 'minutes' && s.minutes < minEntry())) {
    toast(`That would make an entry under ${minEntry()} minutes, which Pulsar does not log: nothing changed.`, true); return;
  }
  if (s.kind === 'extend') return sgExtend(s, r, s);
  if (s.kind === 'create_task') return sgCreate(s, r);
  s.state = 'accepted';
  if (r) await sgApply(r, s);
  renderReview(); render();
}
async function sgAnswer(s, i) {
  const a = (s.answers || [])[i], r = sgRowFor(s);
  if (!a) return;
  if (a.kind === 'extend') return sgExtend(s, r, a, a.label);
  s.state = 'accepted'; s.answer = a.label;
  if (r && a.kind !== 'keep') await sgApply(r, a);
  renderReview(); render();
}
/* extend: the entry already logged gets the row's minutes, asked as a dragged edge is ("Change this entry?", with
   Undo); once changed, the row is left out of this run */
function sgExtend(s, r, c, label = null) {
  const x = sgLogged(c.entryId);
  if (!x || !r) { toast('That entry is not in the Logged column any more: nothing changed.', true); return; }
  const {a, b} = sgSpan(x, r), at = document.querySelector(`[data-sg="${s.key}"] .sg-a`)?.getBoundingClientRect();
  resizeDone = () => {  // day.js: after "Change" wrote it
    s.state = 'accepted'; if (label) s.answer = label;
    const rr = ldRow(r.id); if (rr) { rr.checked = false; ldChanged(rr); }
    renderReview(); render();
  };
  askResize(x, a < x.s ? a : null, realMinutes(state.date, a, b), {clientX: at?.left ?? 240, clientY: at?.top ?? 200});
}
/* create_task: the new-task form, filled with the suggested title (newtask.js); the task created is the row's */
function sgCreate(s, r) {
  if (!r) { s.state = 'accepted'; renderReview(); return; }
  return openNewTask({date: LD.date, s: r.s, e: r.s + r.minutes, project: s.project || r.project, title: s.title,
                      description: s.description || '',
                      onCreated: (task, serviceHint) => {
                        s.state = 'accepted';
                        if (ldRow(r.id)) ldSetTask(ldRow(r.id), {...task, serviceHint});
                        renderReview(); render();
                      }});
}

$('#review').addEventListener('click', ev => {
  const t = ev.target.closest('button'); if (!t || LD.writing) return;
  if (t.id === 'sg-ask') { ev.stopPropagation(); sgCheck(); return; }
  const k = t.dataset.sgOk || t.dataset.sgNo || t.dataset.sgAns; if (!k) return;
  ev.stopPropagation();
  const s = SG.items.find(x => x.key === k); if (!s || s.state !== 'open') return;
  if (t.dataset.sgNo) { s.state = 'rejected'; renderReview(); return; }
  if (t.dataset.sgAns) { sgAnswer(s, +t.dataset.i); return; }
  sgAccept(s);
});
