/* Log the day with Claude (pulsar/dayreview.py): suggestions on the day's plan, each accepted or rejected here.
   FI's rules come with the plan (GET /api/dayplan, "suggestions"); Claude's come from POST /api/dayplan/review, which
   sends the day's digest to the person's own Claude Code, isolated as Ask Claude is. Accepting changes the row in
   the review only: nothing is written until Log the day's own confirm step, one entry at a time, journalled. */

const SG = {items: [], asking: false, note: ''};
const SG_KIND = {task: 'Task', service: 'Service', minutes: 'Minutes', split: 'Split', create_task: 'New task',
                 question: 'Question', untick: 'Leave out'};

/* the plan's own suggestions (FI's rules), when Log the day opens */
function sgFromPlan(plan) {
  SG.items = (plan?.suggestions || []).map((s, i) => ({...s, key: 'p' + i, state: 'open', at: sgAt(s.row)}));
  SG.asking = false; SG.note = '';
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
  const ans = await postJSON('/api/dayplan/review', {date: d, source: state.source, ...(LD_IGNORE ? {ignore_logged: true} : {})});
  SG.asking = false;
  if (!ldActive() || LD.date !== d) return;
  if (ans.error) { SG.note = `Review with Claude: ${ans.error}`; renderReview(); return; }
  const rows = ans.rows || [];
  SG.items = SG.items.filter(x => x.source !== 'claude').concat((ans.suggestions || []).map((s, i) => {
    const at = s.row ? (sgAt(s.row, rows) || null) : null;  // matched to the page's rows by time and task, not id
    return {...s, key: 'c' + i, state: 'open', at};
  }));
  const n = (ans.suggestions || []).length;
  SG.note = `${n ? `${n} ${n === 1 ? 'suggestion' : 'suggestions'}` : 'Nothing to change'} from Claude`
    + (ans.cost != null ? ` · $${(+ans.cost).toFixed(3)}` : '') + (ans.dropped?.length ? ` · ${ans.dropped.length} left out by Pulsar's checks` : '') + '.';
  renderReview(); render();
}

function sgText(s) {
  if (s.kind === 'task') return `${esc(s.title || 'task ' + s.taskId)}${s.project ? ` <span class="sg-p">· ${esc(s.project)}</span>` : ''}`;
  if (s.kind === 'service') return esc(s.service);
  if (s.kind === 'minutes') return `${dur(s.minutes)}`;
  if (s.kind === 'split') return s.split.map(p => `${esc(p.title || 'task ' + p.taskId)} ${dur(p.minutes)}`).join(' + ');
  if (s.kind === 'create_task') return `“${esc(s.title)}”${s.parentTitle ? ` under ${esc(s.parentTitle)}` : ''} <span class="sg-p">· ${esc(s.project)}</span>`;
  if (s.kind === 'question') return esc(s.question);
  return 'not this entry';
}
function sgItemHTML(s, row) {
  const mark = s.source === 'claude' ? CLAUDE_MARK.split('<span')[0] : '<span class="sg-rule" aria-hidden="true">FI</span>';
  const who = s.source === 'claude' ? 'Claude' : "FI's rule";
  const gone = s.row && !row;
  const ok = s.kind === 'question' ? 'Noted' : s.kind === 'create_task' ? 'Keep as a to-do' : 'Accept';
  const done = s.state === 'accepted' ? `<span class="sg-done">${s.kind === 'question' ? 'Noted' : 'Accepted'}</span>`
    : s.state === 'rejected' ? '<span class="sg-done">Rejected</span>' : '';
  return `<li class="sg ${s.state}${gone ? ' gone' : ''}" data-sg="${s.key}">${mark}
    <div class="sg-b"><span class="sg-k">${SG_KIND[s.kind]}</span> <span class="sg-t">${sgText(s)}</span>
      <span class="sg-w"><span class="vh">${who}: </span>${esc(s.why)}${gone ? ' · the row changed since' : ''}</span></div>
    ${done || (gone ? '' : `<span class="sg-a"><button type="button" class="btn sm" data-sg-ok="${s.key}">${ok}</button>
      <button type="button" class="btn sm bordered" data-sg-no="${s.key}" aria-label="Reject: ${esc(SG_KIND[s.kind])}">Reject</button></span>`)}</li>`;
}
/* under a row: its open suggestions (and the ones just decided, so the click shows) */
function sgRowHTML(r) {
  const mine = SG.items.filter(s => s.row && sgRowFor(s) === r && s.state !== 'hidden');
  return mine.length ? `<ul class="sg-list" aria-label="Suggestions for this entry">${mine.map(s => sgItemHTML(s, r)).join('')}</ul>` : '';
}
/* above the list: the button and what Claude said; the suggestions for the whole day are the list's first item */
function sgHead() {
  const can = CONFIG?.likely?.askClaude && CONFIG.likely.claudeFound;
  const model = {haiku: 'Haiku', sonnet: 'Sonnet', opus: 'Opus'}[(CONFIG?.claude?.uses || []).find(u => u.id === 'review')?.model] || 'Claude';
  // while Claude reviews, the button wears Ask Claude's ring, beams and glow (ask.css)
  const anim = SG.asking ? '<span class="ask-ring" aria-hidden="true"><span class="ask-beam"></span><span class="ask-flash"></span></span><span class="ask-glow" aria-hidden="true"></span>' : '';
  const btn = can && !LD.writing ? `<button type="button" class="btn sm bordered${SG.asking ? ' asking' : ''}" id="sg-ask"${SG.asking ? ' aria-disabled="true"' : ''}>${anim}${CLAUDE_MARK.split('<span')[0]}${SG.asking ? 'Reviewing…' : SG.items.some(s => s.source === 'claude') ? 'Review again' : 'Review with Claude'}</button>` : '';
  const line = SG.asking ? `${model} is reviewing the day with FI's rules (about a minute). You can review meanwhile.` : SG.note;
  const open = SG.items.filter(s => s.state === 'open' && (!s.row || sgRowFor(s))).length;
  return `<div class="sg-head">${btn}${line ? `<p class="rv-ask" role="status">${esc(line)}</p>` : ''}
    ${open ? `<span class="iss ok">${open} ${open === 1 ? 'suggestion' : 'suggestions'} to decide</span>` : ''}</div>`;
}
/* questions for the whole day, and suggestions whose row has changed since: first in the list, scrolling with it */
function sgDayHTML() {
  const day = SG.items.filter(s => (!s.row || !sgRowFor(s)) && s.state !== 'hidden');
  return day.length ? `<li class="sg-day"><ul class="sg-list day" aria-label="For the whole day">${day.map(s => sgItemHTML(s, null)).join('')}</ul></li>` : '';
}

/* accepting: the row changes in the review, as if changed by hand; still nothing is written */
async function sgAccept(s) {
  const r = sgRowFor(s);
  s.state = 'accepted';
  if (s.kind === 'task' && r) await ldSetTask(r, {taskId: s.taskId, title: s.title, project: s.project});
  else if (s.kind === 'service' && r) {
    const x = (r.services || []).find(v => v.id === s.serviceId);
    if (x) Object.assign(r, {serviceId: x.id, service: x.name, billable: x.billable, budget: x.budget || ''});
    ldChanged(r);
  } else if (s.kind === 'minutes' && r) { r.minutes = s.minutes; r.e = r.s + s.minutes; ldChanged(r); }
  else if (s.kind === 'untick' && r) { r.checked = false; ldChanged(r); }
  else if (s.kind === 'split' && r) {
    let at = r.s;
    const parts = s.split.map((p, j) => {
      const n = {...r, id: `${r.id}.${j}`, s: at, e: at + p.minutes, minutes: p.minutes, alts: [], edited: true};
      at += p.minutes;
      return [n, p];
    });
    LD.rows.splice(LD.rows.indexOf(r), 1, ...parts.map(x => x[0]));
    for (const [n, p] of parts) await ldSetTask(n, {taskId: p.taskId, title: p.title, project: r.project});
  }
  renderReview(); render();
}

$('#review').addEventListener('click', ev => {
  const t = ev.target.closest('button'); if (!t || LD.writing) return;
  if (t.id === 'sg-ask') { ev.stopPropagation(); sgAsk(); return; }
  const k = t.dataset.sgOk || t.dataset.sgNo; if (!k) return;
  ev.stopPropagation();
  const s = SG.items.find(x => x.key === k); if (!s || s.state !== 'open') return;
  if (t.dataset.sgNo) { s.state = 'rejected'; renderReview(); return; }
  sgAccept(s);
});
