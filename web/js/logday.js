/* Log the day: the whole day proposed at once (GET /api/dayplan), reviewed in the side panel, and written one entry
   at a time through POST /api/log, in time order, stopping at the first answer that is not "logged". Nothing is
   written until the person confirms. Each write carries the run's name, so the server's journal can refuse a row
   it already sent, and Undo deletes only what this run created (POST /api/entry/delete with the run). */

const LD = {on: false, date: null, plan: null, rows: [], open: null, run: null, writing: false, stop: false,
            loading: false, undoAsk: false, note: ''};
const ldActive = () => LD.on && LD.date === state.date;
const ldRow = id => LD.rows.find(r => r.id === id);
/* a test server can show the whole proposal of a day already logged (only PULSAR_READ_ONLY accepts it) */
const LD_IGNORE = params.get('ignore_logged') === '1';
const CONF_WORDS = {high: 'likely right', medium: 'probably right', low: 'check'};
const ldPending = r => !['logged', 'writing'].includes(r.status) && r.status !== 'unknown';

/* small line icons for why a row says what it says */
const WI = {
  link: '<path d="M6.5 9.5l3-3M5 7.5 3.8 8.7a2.3 2.3 0 0 0 3.3 3.3L8.3 10.8M11 8.5l1.2-1.2a2.3 2.3 0 0 0-3.3-3.3L7.7 5.2"/>',
  session: '<path d="M3 8a5 5 0 1 0 1.5-3.5M3 3v2.5h2.5"/>',
  meeting: '<rect x="2.5" y="3.5" width="11" height="10" rx="1.5"/><path d="M2.5 6.5h11M5.5 2v3M10.5 2v3"/>',
  repeat: '<path d="M3 6h8.5l-2-2M13 10H4.5l2 2"/>',
  rule: '<path d="M6 4.5h6.5M6 8h6.5M6 11.5h6.5M3.5 4.5h.01M3.5 8h.01M3.5 11.5h.01"/>',
  words: '<path d="M3 4h10M3 8h7M3 12h5"/>',
  outside: '<circle cx="8" cy="8" r="5.5"/><path d="M2.5 8h11M8 2.5c2 2.2 2 8.8 0 11M8 2.5c-2 2.2-2 8.8 0 11"/>',
  agent: '<rect x="3.5" y="5" width="9" height="7" rx="1.5"/><path d="M8 2.5V5M6 8.5h.01M10 8.5h.01"/>',
  none: '<circle cx="8" cy="8" r="5.5"/><path d="M8 5.5v3M8 10.8h.01"/>',
};
function whyIcon(r) {
  if (r.source === 'claude') return CLAUDE_MARK.split('<span')[0];
  const k = r.kind === 'agent' ? 'agent' : r.source === 'repeat' ? 'repeat' : ['meeting', 'call'].includes(r.source) ? 'meeting'
    : r.source === 'link' ? 'link' : r.source === 'rule' ? 'rule' : ['session', 'repo', 'sameday', 'learned'].includes(r.source) ? 'session'
    : ['words', 'scoped', 'ticket'].includes(r.source) ? 'words' : r.source === 'neighbour' ? 'outside' : 'none';
  return `<svg class="wi" viewBox="0 0 16 16" aria-hidden="true">${WI[k]}</svg>`;
}
function meter(c) {
  const n = {high: 3, medium: 2, low: 1}[c] || 1;
  return `<span class="meter" title="Confidence: ${CONF_WORDS[c]}" aria-hidden="true">${[1, 2, 3].map(i => `<i${i <= n ? ' class="f"' : ''}></i>`).join('')}</span>`
    + `<span class="vh">Confidence: ${CONF_WORDS[c]}. </span>`;
}

/* ---------- opening and closing ---------- */
async function openLogDay() {
  if (!CONFIG?.productive.active) { toast('Connect Productive in Settings first.', true); openSettings('t-conn'); return; }
  const d = state.date;
  Object.assign(LD, {on: true, date: d, plan: null, rows: [], open: null, run: null, writing: false, stop: false,
                     loading: true, undoAsk: false, note: '', lastRun: null, undo: [], confirmed: null, claudeLate: null});
  closePop();
  document.body.classList.add('rv-on');
  $('#review').hidden = false;
  renderReview(); render();
  const r = await getJSON(`/api/dayplan?date=${d}&source=${state.source}${LD_IGNORE ? '&ignore_logged=1' : ''}`);
  if (!LD.on || LD.date !== d) return;
  LD.loading = false; LD.plan = r;
  LD.rows = (r.entries || []).map((e, i) => ({...e, id: 'r' + i, opened: false, edited: false,
                                               status: e.sent === 'logged' ? 'logged' : e.sent ? 'unknown' : null,
                                               reason: e.sent ? 'A run of this day already sent it.' : ''}));
  renderReview(); render();
  document.body.classList.toggle('rv-ignore', !!r.ignoreLogged);
  // Ask Claude (when it is on in Settings): the whole day goes to it, its picks fill in unsure rows as they arrive
  const unsure = LD.rows.some(x => x.confidence !== 'high' && ['focused', 'agent', 'outside'].includes(x.kind));
  if (!r.error && !r.claude && unsure && CONFIG?.likely?.askClaude && CONFIG.likely.claudeFound) askClaudeForDay(d);
}

/* Claude's pick replaces only rows the planner is not sure of (medium, low): on high rows it was wrong more often
   than the planner. The planner's guess stays as the first alternative. Rows the person changed are left alone. */
async function askClaudeForDay(d) {
  LD.asking = true; LD.askNote = ''; renderReview();
  const ans = await postJSON('/api/likely/ask', {date: d});
  LD.asking = false;
  if (!ldActive() || LD.date !== d) return;
  if (ans.error) { LD.askNote = `Ask Claude: ${ans.error}`; renderReview(); return; }
  state.claude = {date: d, picks: ans.picks || {}};
  if (LD.writing) { LD.askNote = "Claude answered while writing; Start over to see its picks."; renderReview(); return; }
  const r = await getJSON(`/api/dayplan?date=${d}&source=${state.source}${LD_IGNORE ? '&ignore_logged=1' : ''}`);
  if (!ldActive() || LD.date !== d || r.error) return;
  ldClaudePlan(d, r, ans);
}
/* Claude's plan goes in only while nothing is being confirmed or written: what the person confirmed is what is sent.
   It waits for the confirm dialog to close, and after a run has started it is only mentioned. */
function ldClaudePlan(d, r, ans) {
  if (!ldActive() || LD.date !== d) return;
  if (ldDlg.open) { LD.claudeLate = [d, r, ans]; return; }
  LD.claudeLate = null;
  if (LD.writing) { LD.askNote = "Claude answered while writing; Start over to see its picks."; renderReview(); return; }
  const fresh = (r.entries || []).map((e, i) => ({...e, id: 'c' + i, opened: false, edited: false, status: null, reason: ''}));
  let changed = 0;
  if (!LD.rows.some(x => x.edited || x.status)) {  // nothing touched yet: the new plan as a whole
    changed = fresh.filter(n => !LD.rows.some(o => o.s === n.s && o.taskId === n.taskId)).length;
    LD.rows = fresh; LD.open = null; LD.plan = r;
  } else {  // only rows still as proposed, at the same time
    for (const n of fresh.filter(n => n.source === 'claude' || n.claudeNone)) {
      const o = LD.rows.find(o => o.s === n.s && o.e === n.e && !o.edited && !o.status);
      if (!o) continue;
      if (o.taskId !== n.taskId) changed++;
      Object.assign(o, {taskId: n.taskId, title: n.title, project: n.project, serviceId: n.serviceId, service: n.service,
                        services: n.services, billable: n.billable, budget: n.budget, why: n.why, source: n.source,
                        confidence: n.confidence, confidenceWords: n.confidenceWords, alts: n.alts, checked: n.checked,
                        flags: n.flags, claudeNone: n.claudeNone, note: n.note, noteFrom: n.noteFrom});
    }
  }
  const none = LD.rows.filter(x => x.claudeNone).length;
  LD.askNote = `Claude looked at the day: ${changed ? `${changed} ${changed === 1 ? 'row' : 'rows'} changed` : 'no row changed'}`
    + (none ? `, ${none} with no task found (Pulsar's guess kept)` : '') + '.' + (ans.warning ? ` ${ans.warning}` : '');
  renderReview(); render();
}
function closeLogDay() {
  if (LD.writing) { LD.stop = true; return; }
  LD.on = false; LD.open = null;
  document.body.classList.remove('rv-on', 'rv-ignore');
  $('#review').hidden = true; $('#ld-pk').hidden = true;
  render();
}
$('#log-day').onclick = () => (ldActive() ? closeLogDay() : openLogDay());

/* ---------- what is drawn on the axis ---------- */
function ldCoverSpans() { return LD.rows.filter(r => r.checked && r.status !== 'logged').map(r => [r.s, r.e]); }
function ldHead(loggedSum) {
  if (LD.loading) return 'Working out the day…';
  const n = LD.rows.filter(r => r.status !== 'logged').length;
  return `${n} proposed, dashed · ${dur(LD.plan?.ignoreLogged ? 0 : loggedSum)} logged`;
}
function ldLaneItems() { return LD.rows.filter(r => r.status !== 'logged').map(r => ({...r, prop: true})); }
function ldCards(ppm, list) {
  return list.map(x => {
    const h = (x.e - x.s) * ppm, lines = Math.max(0, Math.floor((h - 36) / 15));
    const cls = ['card', 'prop', LD.open === x.id ? 'on' : '', x.checked ? '' : 'off', x.taskId ? '' : 'notask',
                 ['refused', 'unknown'].includes(x.status) ? 'fail' : '', x.status === 'writing' ? 'writing' : ''].filter(Boolean).join(' ');
    const right = x.status === 'refused' || x.status === 'unknown' ? '<span class="m fl">not written</span>'
      : x.confidence === 'low' ? `<span class="m chk">check · ${dur(x.minutes)}</span>` : `<span class="m num">${dur(x.minutes)}</span>`;
    return `<div class="${cls}" data-k="prop" data-row="${x.id}" data-i="0" style="--c:${color(x.project)};${place(x, ppm, 18)}"
      tabindex="0" role="button" aria-label="${esc(`Proposed ${hm(x.s)}–${hm(x.e)}: ${x.title || 'no task yet'}, ${dur(x.minutes)}${x.checked ? '' : ', unticked'}. Open it in the review`)}">
      <div class="row"><span class="t">${esc(x.title || 'Pick a task')}</span>${right}</div>
      ${h > 34 ? `<div class="d">${esc([x.project, x.service].filter(Boolean).join(' · '))}</div>` : ''}
      ${lines && x.note ? `<div class="n" style="-webkit-line-clamp:${lines}">${esc(x.note)}</div>` : ''}</div>`;
  }).join('');
}

/* ---------- overlaps, totals, what to check ---------- */
function ldOverlaps() {
  const out = new Map();  // row id -> [{with, minutes}]
  const add = (id, w, m) => { if (!out.has(id)) out.set(id, []); out.get(id).push({with: w, minutes: m}); };
  const live = LD.rows.filter(r => r.checked && ldPending(r));
  const logged = LD.plan?.ignoreLogged ? [] : (state.logged?.entries || []).filter(x => x.s != null);
  for (let i = 0; i < live.length; i++) {
    const a = live[i];
    for (let j = i + 1; j < live.length; j++) {
      const b = live[j], m = Math.min(a.e, b.e) - Math.max(a.s, b.s);
      if (m > 0) { add(a.id, b.title || 'another entry', m); add(b.id, a.title || 'another entry', m); }
    }
    for (const x of logged) {
      const m = Math.min(a.e, x.e) - Math.max(a.s, x.s);
      if (m > 0) add(a.id, `${x.task || x.project} (logged)`, m);
    }
  }
  return out;
}
function ldTotals() {
  const live = LD.rows.filter(r => r.checked && ldPending(r));
  const t = LD.plan?.totals || {};
  const logged = LD.plan?.ignoreLogged ? 0 : (state.logged?.entries || []).reduce((s, x) => s + x.minutes, 0);
  return {n: live.length, minutes: live.reduce((s, r) => s + r.minutes, 0),
          billable: live.filter(r => r.billable).reduce((s, r) => s + r.minutes, 0), logged,
          notCovered: t.notCovered || 0, over: t.over || 0, cap: t.cap || 0};
}
const ldCheck = r => ldPending(r) && (r.confidence === 'low' || !r.taskId || !r.serviceId || r.claudeNone);

/* ---------- the review panel ---------- */
function dayTitle(d) {
  return new Intl.DateTimeFormat('en-GB', {weekday: 'long', day: 'numeric', month: 'long', timeZone: 'UTC'}).format(new Date(d + 'T12:00:00Z'));
}
function renderReview() {
  const el = $('#review');
  if (!LD.on) { el.hidden = true; return; }
  const head = `<div class="rv-t"><h2>Log the day <span class="sub">· ${esc(dayTitle(LD.date))}</span></h2>
    <button type="button" class="btn icon" id="ld-close" aria-label="Close Log the day">✕</button></div>`;
  if (LD.loading) { el.innerHTML = `<div class="rv-h">${head}<div class="rv-sum">Reading the day: activity, Claude sessions, meetings and what is logged…</div></div>`; return; }
  const p = LD.plan;
  if (p?.error) {
    el.innerHTML = `<div class="rv-h">${head}</div><div class="rv-empty"><div class="big-ic" aria-hidden="true">!</div>
      <h3>Nothing can be proposed</h3><p>${esc(p.error)}</p><div class="btnrow"><button class="btn bordered" id="ld-redo">Try again</button></div></div>`;
    return;
  }
  const T = ldTotals(), ov = ldOverlaps(), toCheck = LD.rows.filter(ldCheck);
  const ovMin = [...ov.values()].flat().reduce((s, o) => s + o.minutes, 0) / 2;
  const target = TARGET, withThese = T.logged + T.minutes;
  const pct = x => Math.min(100, x / Math.max(target, withThese) * 100);
  const chips = [
    toCheck.length ? `<button type="button" class="iss" data-ld-goto="${toCheck[0].id}"><span class="g" aria-hidden="true">!</span>${toCheck.length} to check</button>` : '',
    ov.size ? `<button type="button" class="iss" data-ld-goto="${[...ov.keys()][0]}"><span class="g" aria-hidden="true">!</span>${Math.ceil(ov.size / 2)} ${ov.size > 2 ? 'overlaps' : 'overlap'}, ${dur(Math.round(ovMin))}</button>` : '',
    T.notCovered ? `<span class="iss ok" title="Active time no entry covers, logged or proposed">${dur(T.notCovered)} not covered</span>` : '',
    T.over ? `<span class="iss ok" title="More than your active time and meetings: shown, never placed">${dur(T.over)} over the day</span>` : '',
  ].join('');
  const model = {haiku: 'Haiku', sonnet: 'Sonnet', opus: 'Opus'}[CONFIG?.likely?.askClaudeModel] || 'Claude';
  const askLine = LD.asking ? `<p class="rv-ask" role="status">${CLAUDE_MARK.split('<span')[0]}Asking ${model} about the rows Pulsar is unsure of (about 30 seconds). You can review meanwhile.</p>`
    : LD.askNote ? `<p class="rv-ask" role="status">${CLAUDE_MARK.split('<span')[0]}${esc(LD.askNote)}</p>` : '';
  const testNote = p.ignoreLogged ? '<p class="rv-test">Test view: worked out as if nothing were logged this day. Nothing can be written from a read-only test server.</p>' : '';
  const historyNote = p.historyProblem ? `<p class="rv-test" role="status">${esc(p.historyProblem)}</p>` : '';
  const results = ldResultsBox();
  // the list: logged entries (quiet), time not covered, and the proposals, in time order
  const items = [];
  if (!p.ignoreLogged) for (const x of state.logged?.entries || []) items.push({at: x.s ?? -1, html: loggedLine(x)});
  for (const u of p.uncovered || []) items.push({at: u.s, html: `<li class="gr"><span class="e-time num">${hm(u.s)}</span><span class="gt"><b>${dur(u.minutes)}</b> active, not covered</span><span></span></li>`});
  for (const r of LD.rows) items.push({at: r.s, html: rowHTML(r, ov.get(r.id))});
  items.sort((a, b) => a.at - b.at);
  const skipped = (p.skipped || []).length ? `<li class="gr quiet"><span class="e-time num">·</span><span class="gt">Left out: ${esc(p.skipped.map(s => `${s.title || s.project} ${dur(s.minutes)} (${s.why})`).join('; '))}</span><span></span></li>` : '';
  const empty = !LD.rows.length ? `<div class="rv-empty"><div class="big-ic" aria-hidden="true">✓</div><h3>${(state.logged?.entries || []).length ? 'Everything is logged' : 'Nothing to log'}</h3>
    <p>${(state.logged?.entries || []).length ? 'Every active stretch of this day already has an entry, or is too short to log.' : 'No activity, Claude sessions or meetings were found for this day.'}</p></div>` : '';
  const n = T.n;
  el.innerHTML = `<div class="rv-h">${head}
      <div class="rv-sum"><b>${dur(T.minutes)}</b> ticked in ${n} ${n === 1 ? 'entry' : 'entries'} · ${dur(T.billable)} billable · ${dur(T.logged)} logged</div>
      <div class="rv-bar" role="img" aria-label="${esc(`${dur(withThese)} with these, against a target of ${dur(target)}`)}"><i class="lg" style="width:${pct(T.logged)}%"></i><i class="pp" style="width:${pct(T.minutes)}%"></i></div>
      <div class="rv-bl"><span>${dur(withThese)} with these</span><span>target ${dur(target)}</span></div>
      <div class="rv-iss">${chips}</div>${askLine}${testNote}${historyNote}</div>
    ${results}
    ${empty || `<ul class="rv-list" aria-label="Proposed entries">${items.map(x => x.html).join('')}${skipped}</ul>`}
    <div class="rv-f">${LD.writing
      ? `<span class="hint" role="status">Writing ${LD.rows.filter(r => r.status === 'logged' && r.thisRun).length + 1} of ${LD.writeCount}…</span><button type="button" class="btn bordered" id="ld-stop"${LD.stop ? ' disabled' : ''}>${LD.stop ? 'Stopping after this one' : 'Stop after this one'}</button>`
      : `<button type="button" class="btn bordered" id="ld-redo" title="Work the day out again; your changes go">Start over</button>
         <p class="hint">Nothing is written until you confirm.</p>
         <button type="button" class="btn primary big" id="ld-go"${n ? '' : ' disabled'}>Log ${n} ${n === 1 ? 'entry' : 'entries'}</button>`}</div>`;
  if (LD.open) el.querySelector(`[data-row="${LD.open}"] .ed`) && wireEditor(ldRow(LD.open));
}
function loggedLine(x) {
  return `<li class="er done"><div class="eb"><span class="e-time num">${x.s == null ? 'no start' : hm(x.s)}</span>
    <span class="e-task"><i class="dot full" style="--c:${color(x.project)}"></i><span>${esc(x.task || x.project)}</span></span>
    <span class="e-st">in Productive${x.s == null ? ', no start time' : ''} · ${dur(x.minutes)}</span></div></li>`;
}
function rowHTML(r, overlaps) {
  if (r.status === 'logged') {
    return `<li class="er done" data-row="${r.id}"><div class="eb"><span class="e-time num">${hm(r.s)}</span>
      <span class="e-task"><i class="dot full" style="--c:${color(r.project)}"></i><span>${esc(r.title)}</span></span>
      <span class="e-st">✓ ${r.thisRun ? 'logged now' : 'logged'} · ${dur(r.minutes)}</span></div></li>`;
  }
  const warn = [];
  if (r.status === 'refused' || r.status === 'unknown') warn.push(`<b>Not written.</b> ${esc(r.reason || '')}`);
  for (const o of overlaps || []) warn.push(`Overlaps ${esc(o.with)} for ${dur(o.minutes)}`);
  const quiet = [];
  if (!r.taskId) quiet.push('No task: pick one to log it.');
  else if (!r.serviceId) quiet.push(r.serviceProblem ? `No service: ${r.serviceProblem}` : 'No service that takes time on this date: pick one.');
  if (r.flags.includes('agent')) quiet.push("Claude's agent ran while you did something else: unticked.");
  if (r.flags.includes('unattended')) quiet.push('No call was seen: unticked.');
  if (r.claudeNone) quiet.push("Claude found no task for this work: Pulsar's guess stays. Check it.");
  if (r.flags.includes('untimed')) quiet.push(`${dur(r.untimed)} of this task ${r.untimed === 1 ? 'is' : 'are'} logged with no start time: unticked, in case it is this work.`);
  const open = LD.open === r.id;
  return `<li class="er${open ? ' on' : ''}" data-row="${r.id}">
    <label class="ck"><input type="checkbox" data-ld-tick="${r.id}"${r.checked ? ' checked' : ''}${r.taskId && r.serviceId && !LD.writing ? '' : ' disabled'}
      aria-label="${esc(`Log ${hm(r.s)} ${r.title || 'this entry'}`)}"></label>
    <button type="button" class="eb" data-ld-open="${r.id}" aria-expanded="${open}">
      <span class="e-time num">${hm(r.s)}<small>${dur(r.minutes)}</small></span>
      <span class="e-task${r.taskId ? '' : ' none'}"><i class="dot" style="--c:${color(r.project)}"></i><span>${esc(r.title || 'No task yet')}</span></span>
      <span class="e-conf">${r.confidence === 'low' ? '<span class="chk">check</span>' : ''}${meter(r.confidence)}</span>
      <span class="e-meta">${esc([r.project, r.service].filter(Boolean).join(' · ') || r.repo || '')}${r.billable ? ' <span class="bill" title="Billable">$</span>' : ''}</span>
      <span class="e-why">${whyIcon(r)}<span>${esc(r.why)}</span></span>
      ${warn.map(w => `<span class="e-warn">! ${w}</span>`).join('')}
      ${quiet.map(w => `<span class="e-warn neutral">${esc(w)}</span>`).join('')}
    </button>
    ${open ? editorHTML(r) : ''}</li>`;
}

/* ---------- the editor of the open row ---------- */
function noteHint(r) {
  if (r.noteFrom === 'claude' && !r.noteEdited) return `${CLAUDE_MARK.split('<span')[0]}Drafted by Claude: check it before logging; clients may see notes.`;
  if (r.noteFrom === 'meeting' && !r.noteEdited) return "The meeting's title. Clients may see notes.";
  if (r.noteFrom === 'repeat' && !r.noteEdited) return 'Your usual note for this. Clients may see notes.';
  if (r.noteFrom === 'commits' && !r.noteEdited) return 'From your commits. Clients may see notes.';
  return 'Clients may see notes: the outcome, in a sentence.';
}
const altName = a => a.title || 'task ' + a.taskId;
const whyLine = r => `${whyIcon(r)}<span>${esc(r.why)}${r.parts > 1 ? ` (${r.parts} stretches joined)` : ''}</span>`;
function editorHTML(r) {
  const svcs = r.services || [];
  return `<div class="ed" role="group" aria-label="Change this entry">
    <div class="field"><span>Task</span><button type="button" class="taskbtn" data-ld-task="${r.id}">
      <span class="tb-t">${esc(r.title || 'Pick a task')}</span><span class="tb-p">${esc(r.project || 'Search every task in Productive')}</span></button></div>
    ${r.alts?.length ? `<div class="alts"><span>Or</span><div>${r.alts.map((a, i) => `<button type="button" class="alt" data-ld-alt="${i}"><span>${esc(altName(a))}</span>${a.why ? `<span class="vh">, ${esc(a.why)}</span>` : ''}</button>`).join('')}</div></div>` : ''}
    <p class="whyd">${whyLine(r)}</p>
    <label class="field"><span>Service</span><select data-ld-svc="${r.id}"${svcs.length ? '' : ' disabled'}>
      ${r.serviceId ? '' : '<option value="">' + (svcs.length ? 'Pick a service' : r.taskId ? 'Loading or none on this date' : 'Pick a task first') + '</option>'}
      ${svcs.map(x => `<option value="${esc(x.id)}"${x.id === r.serviceId ? ' selected' : ''}>${esc(x.name)}${x.budget ? ' · ' + esc(x.budget) : ''}${x.billable ? ' · billable' : x.billable === false ? ' · not billable' : ''}</option>`).join('')}
    </select></label>
    <div class="tl"><label class="field"><span>Start</span><input type="text" inputmode="numeric" autocomplete="off" spellcheck="false" maxlength="5" placeholder="HH:MM" title="24-hour time, HH:MM" data-ld-start value="${hm(r.s)}"></label>
      <label class="field"><span>Minutes</span><input type="number" min="5" max="720" step="5" data-ld-min value="${r.minutes}"></label>
      <span class="endt">until ${hm(r.s + r.minutes)}</span></div>
    <label class="field"><span>Note</span><textarea rows="2" data-ld-note placeholder="What was done: the outcome, in a sentence">${esc(r.note || '')}</textarea></label>
    <p class="hint">${noteHint(r)}</p>
    <div class="acts"><button type="button" class="btn bordered" data-ld-del="${r.id}">Delete from the proposal</button></div></div>`;
}
function wireEditor(r) {
  const li = $(`#review [data-row="${r.id}"]`); if (!li) return;
  // an alternative: its full name as a tooltip only when the chip cuts it, and why it is offered on the why line
  // under the chips while it is hovered or focused (a tooltip of its own would cover the entry it is on)
  const why = li.querySelector('.ed .whyd');
  for (const b of li.querySelectorAll('[data-ld-alt]')) {
    const a = r.alts[+b.dataset.ldAlt], name = b.firstElementChild;
    const fit = () => { if (name.scrollWidth > name.clientWidth) b.title = altName(a); else b.removeAttribute('title'); };
    const show = () => { fit(); why.classList.add('alt-why');
      why.innerHTML = `<span><b>${esc(altName(a))}</b>${a.why ? ` · ${esc(a.why)}` : ''}</span>`; };
    const back = () => { why.classList.remove('alt-why'); why.innerHTML = whyLine(r); };
    fit();
    b.onpointerenter = b.onfocus = show; b.onpointerleave = b.onblur = back;
  }
  li.querySelector('[data-ld-start]').onchange = ev => {  // 24-hour HH:MM as everywhere in Pulsar (930, 9.30, 9:30 too)
    const t = startHHMM(ev.target.value), m = t && /^(\d\d):(\d\d)$/.exec(t);
    if (!m) { ev.target.value = hm(r.s); ev.target.setAttribute('aria-invalid', 'true'); return; }
    ev.target.removeAttribute('aria-invalid'); ev.target.value = t;
    const s = +m[1] * 60 + +m[2]; r.s = s; r.e = Math.min(1440, s + r.minutes); r.minutes = r.e - r.s; ldChanged(r);
  };
  li.querySelector('[data-ld-min]').onchange = ev => {
    const v = Math.round(+ev.target.value); if (!(v >= 1 && v <= 1440 - r.s)) { ev.target.value = r.minutes; return; }
    r.minutes = v; r.e = r.s + v; ldChanged(r);
  };
  li.querySelector('[data-ld-note]').oninput = ev => { r.note = ev.target.value; r.noteEdited = true; r.edited = true;
    const h = li.querySelector('.ed .hint'); if (h) h.innerHTML = noteHint(r); };
  li.querySelector('[data-ld-svc]').onchange = ev => {
    const x = (r.services || []).find(s => s.id === ev.target.value);
    r.serviceId = x?.id || null; r.service = x?.name || ''; r.billable = x?.billable; r.budget = x?.budget || '';
    if (r.serviceId && r.taskId && !r.flags.includes('agent') && !r.flags.includes('unattended')) r.checked = true;
    ldChanged(r);
  };
}
function ldChanged(r) { r.edited = true; if (r.status === 'refused') { r.status = null; r.reason = ''; } renderReview(); render(); }

function ldOpen(id, scroll = false) {
  const r = ldRow(id); if (!r || LD.writing) return;
  LD.open = LD.open === id && !scroll ? null : id; r.opened = true;
  renderReview(); render();
  if (scroll) {
    const li = $(`#review [data-row="${id}"]`);
    li?.scrollIntoView({block: 'nearest', behavior: 'smooth'});
    li?.querySelector('.eb')?.focus({preventScroll: true});
  }
}

/* a new task: its services on that date, and the one used last on it */
async function ldSetTask(r, t) {
  if (LD.writing) return;
  Object.assign(r, {taskId: String(t.taskId), title: t.title || '', project: t.project || '', serviceId: null, service: '',
                    services: [], billable: null, edited: true, opened: true, source: 'you', why: 'picked by you', confidence: 'high'});
  r.flags = r.flags.filter(f => !['notask', 'low', 'noservice'].includes(f));
  if (r.noteFrom === 'claude' && !r.noteEdited) { r.note = ''; r.noteFrom = ''; }  // Claude wrote it for another task
  renderReview(); render();
  const [list, sug] = await Promise.all([getJSON(`/api/services?task=${r.taskId}&date=${LD.date}`), suggestions(LD.date)]);
  if (r.taskId !== String(t.taskId)) return;
  if (list.error) { r.serviceProblem = list.error; renderReview(); return; }
  r.services = (list.services || []).map(x => ({id: x.id, name: (x.name || '').trim(), budget: x.budget || '', billable: x.billable}));
  const recent = (sug.recent || []).find(x => x.taskId === r.taskId);
  const pick = r.services.find(x => x.id === recent?.serviceId)
    || r.services.find(x => recent?.service && x.name === recent.service.trim())
    || r.services.find(x => x.id === list.task?.serviceId) || (r.services.length === 1 ? r.services[0] : null);
  if (pick) Object.assign(r, {serviceId: pick.id, service: pick.name, billable: pick.billable, budget: pick.budget});
  r.checked = !!(r.serviceId && !r.flags.includes('agent') && !r.flags.includes('unattended'));
  renderReview(); render();
}

/* the task picker beside the open row: the same groups as the log dialog */
const ldPk = makePicker($('#ld-pk-list'), $('#ld-pk-q'), t => {
  const r = ldRow(LD.pkFor); $('#ld-pk').hidden = true;
  if (r) { ldSetTask(r, t); $(`#review [data-row="${r.id}"] .taskbtn`)?.focus(); }
});
async function ldPickTask(r, from) {
  LD.pkFor = r.id;
  const pk = $('#ld-pk');
  $('#ld-pk-title').textContent = `Task for ${hm(r.s)}–${hm(r.e)}`; $('#ld-pk-sub').textContent = dur(r.minutes);
  pk.hidden = false;
  const box = from.getBoundingClientRect(), w = pk.offsetWidth, h = Math.min(520, innerHeight - 24);  // its full height, once filled
  pk.style.left = Math.max(12, Math.min(box.left - w - 12, innerWidth - w - 12)) + 'px';
  pk.style.top = Math.max(12, Math.min(box.top - 40, innerHeight - h - 12)) + 'px';
  ldPk.loading();
  $('#ld-pk-q').focus();
  const s = await suggestions(LD.date);
  const here = [...(r.taskId ? [{taskId: r.taskId, title: r.title, project: r.project, why: 'on the entry now'}] : []),
                ...(r.alts || []).map(a => ({...a, why: a.why || 'another option'}))];
  ldPk.set(dedupe([{label: 'For this work', tasks: here},
                   {label: 'You logged to these lately', tasks: (s.recent || []).slice(0, 10)},
                   {label: 'Assigned to you', tasks: (s.assigned || []).slice(0, 10)}]), null);  // opening it picks nothing
}
$('#ld-pk-x').onclick = () => { $('#ld-pk').hidden = true; };
$('#ld-pk').addEventListener('keydown', ev => { if (ev.key === 'Escape') { ev.stopPropagation(); $('#ld-pk').hidden = true; } });

/* ---------- clicks in the panel ---------- */
$('#review').addEventListener('click', ev => {
  const t = ev.target.closest('button, input'); if (!t) return;
  if (t.id === 'ld-close') return closeLogDay();
  if (LD.writing && t.id !== 'ld-stop') { ev.preventDefault(); return; }  // a run (or Undo) is writing: rows stay as confirmed
  if (t.id === 'ld-redo') return openLogDay();
  if (t.id === 'ld-go') return openConfirm();
  if (t.id === 'ld-stop') { LD.stop = true; renderReview(); return; }
  if (t.dataset.ldGoto) return ldOpen(t.dataset.ldGoto, true);
  if (t.dataset.ldOpen) return ldOpen(t.dataset.ldOpen);
  if (t.dataset.ldTick) { const r = ldRow(t.dataset.ldTick); r.checked = t.checked; r.edited = true; renderReview(); render(); return; }
  if (t.dataset.ldTask) return ldPickTask(ldRow(t.dataset.ldTask), t);
  if (t.dataset.ldAlt != null) { const r = ldRow(LD.open); return ldSetTask(r, r.alts[+t.dataset.ldAlt]); }
  if (t.dataset.ldDel) { LD.rows = LD.rows.filter(r => r.id !== t.dataset.ldDel); LD.open = null; renderReview(); render(); return; }
  if (t.id === 'ld-undo') { LD.undoAsk = true; renderReview(); return; }
  if (t.id === 'ld-undo-no') { LD.undoAsk = false; renderReview(); return; }
  if (t.id === 'ld-undo-yes') return undoRun();
  if (t.id === 'ld-retry') return openConfirm();
});

/* ---------- the confirm step ---------- */
const ldDlg = $('#lddlg');
function openConfirm(dry = null) {
  const live = LD.rows.filter(r => r.checked && ldPending(r)).sort((a, b) => a.s - b.s);
  if (!live.length) return;
  LD.run = LD.run || newRun();
  LD.confirmed = live.map(r => ({id: r.id, body: rowBody(r)}));  // what this dialog shows is what Log sends
  const T = ldTotals(), ov = ldOverlaps();
  const unopened = live.filter(r => r.confidence === 'low' && !r.opened);
  const warns = [];
  if (ov.size) warns.push(`${[...ov.entries()].filter(([id]) => live.some(r => r.id === id)).map(([id, os]) => `${hm(ldRow(id).s)} ${ldRow(id).title} overlaps ${os.map(o => o.with).join(', ')}`).join('; ')}. Pulsar checks the day before each one and stops at an overlap.`);
  const untimed = LD.plan?.ignoreLogged ? [] : (state.logged?.entries || []).filter(x => x.s == null && live.some(r => r.taskId === String(x.taskId)));
  if (untimed.length) warns.push(`Already logged with no start time, on a task below: ${untimed.map(x => `${x.task || x.project} ${dur(x.minutes)}`).join(', ')}. Check it is not the same work.`);
  if (unopened.length) warns.push(`${unopened.length} ${unopened.length === 1 ? 'entry' : 'entries'} marked “check” ${unopened.length === 1 ? 'was' : 'were'} not opened: ${unopened.map(r => `${hm(r.s)} ${r.title}`).join(', ')}.`);
  const quiet = T.notCovered ? [`${dur(T.notCovered)} of active time stays unlogged.`] : [];
  const lines = live.map(r => `<li class="cl-r"><span class="num">${hm(r.s)}</span><span><b>${esc(r.title)}</b>
      <small>${esc([r.project, r.service, r.budget].filter(Boolean).join(' · '))}${r.billable ? ' <span class="bill">$</span>' : ''}</small>
      ${r.note ? `<small class="cl-n">${esc(r.note)}</small>` : ''}
      ${dry?.[r.id] ? `<small class="cl-d">${esc(dry[r.id])}</small>` : ''}</span><span class="num">${dur(r.minutes)}</span></li>`).join('');
  ldDlg.innerHTML = `<div class="dh"><h2 id="ld-title">Log ${live.length} ${live.length === 1 ? 'entry' : 'entries'} in Productive?</h2>
      <button type="button" class="btn icon" data-ld-x aria-label="Close">✕</button></div>
    <div class="scrollarea">
      <p class="cf-sum">${esc(dayTitle(LD.date))} · <b>${dur(T.minutes)}</b> · ${dur(T.billable)} billable · target ${dur(TARGET)}</p>
      ${warns.map(w => `<div class="cf-w"><span class="g" aria-hidden="true">!</span><span>${esc(w)}</span></div>`).join('')}
      ${quiet.map(w => `<div class="cf-w quiet"><span class="g" aria-hidden="true">·</span><span>${esc(w)}</span></div>`).join('')}
      ${dry ? `<div class="cf-w quiet"><span class="g" aria-hidden="true">·</span><span>Dry run: each entry below shows what would be sent. Nothing was written.</span></div>` : ''}
      <ul class="cl">${lines}</ul></div>
    <div class="df"><p class="hint">One at a time, in time order. Pulsar reads the day from Productive before each one, so nothing is logged twice.</p>
      <button type="button" class="btn bordered" data-ld-dry>Dry run</button>
      <button type="button" class="btn bordered" data-ld-x>Back to the review</button>
      <button type="button" class="btn primary" data-ld-write autofocus>Log ${live.length} ${live.length === 1 ? 'entry' : 'entries'}</button></div>`;
  if (!ldDlg.open) ldDlg.showModal();
}
ldDlg.addEventListener('click', ev => {
  const t = ev.target.closest('button'); if (!t) return;
  if (t.hasAttribute('data-ld-x')) ldDlg.close();
  if (t.hasAttribute('data-ld-dry')) dryRun(t);
  if (t.hasAttribute('data-ld-write')) { writeRun(); ldDlg.close(); }
});
ldDlg.addEventListener('close', () => { if (LD.claudeLate) ldClaudePlan(...LD.claudeLate); });

const rowBody = r => ({date: LD.date, start: hm(r.s), minutes: r.minutes, taskId: r.taskId, serviceId: r.serviceId,
                       note: (r.note || '').trim(), run: LD.run, row: r.id});
function newRun() {
  const hex = [...crypto.getRandomValues(new Uint8Array(8))].map(b => b.toString(16).padStart(2, '0')).join('');
  return `ld-${LD.date}-${hex}`;
}
/* exactly what would be sent, row by row, with "dry": nothing reaches Productive and nothing goes in the journal */
async function dryRun(btn) {
  btn.disabled = true; btn.textContent = 'Checking…';
  LD.run = LD.run || newRun();
  const out = {};
  for (const r of LD.rows.filter(r => r.checked && ldPending(r)).sort((a, b) => a.s - b.s)) {
    const res = await postJSON('/api/log', {...rowBody(r), dry: true});
    const w = res.wouldWrite;
    out[r.id] = res.error ? `Would be refused: ${res.error}`
      : `Would send: ${w.date} ${w.started_at.slice(11, 16)}, ${w.minutes} min, task ${w.task}, service ${w.service}, note “${rowBody(r).note}”`
        + (res.overlap?.length ? `; overlaps ${res.overlap.map(o => `${o.start} ${o.project}`).join(', ')}` : '');
  }
  openConfirm(out);
}

/* ---------- writing: one at a time, stop at the first answer that is not "logged" ---------- */
async function writeRun() {
  const todo = LD.confirmed || []; LD.confirmed = null;  // exactly what the confirm step showed, frozen there
  if (!todo.length || LD.writing) return;
  Object.assign(LD, {writing: true, stop: false, writeCount: todo.length, undoAsk: false});
  LD.open = null; $('#ld-pk').hidden = true;
  let stopped = null;
  for (const {id, body} of todo) {
    if (LD.stop || LD.date !== state.date) { stopped = 'you stopped'; break; }
    const r = ldRow(id) || {id};  // the row on screen: nothing in the panel can change or remove it while writing
    r.status = 'writing'; renderReview(); render();
    const res = await postJSON('/api/log', body);
    if (res.created) { Object.assign(r, {status: 'logged', entryId: res.created.id, thisRun: true, reason: res.warning || ''}); continue; }
    r.status = res.unclear ? 'unknown' : 'refused';  // unclear: it may be in Productive, and is never sent again
    r.reason = res.overlap ? `It would overlap ${res.overlap.map(o => `${o.start} ${o.project}`).join(', ')}.` : res.error || 'No answer.';
    stopped = r; break;
  }
  // what the server recorded is what counts (an answer lost on the way says "unknown"; "sending" for one the page saw
  // created means the journal could not take its "logged", and it stays logged here)
  await syncJournal();
  LD.writing = false; LD.stop = false;
  LD.lastRun = {stopped, total: todo.length};
  renderReview(); render();
  refreshLogged().then(() => { renderReview(); });
  suggestCache = null;
}
async function syncJournal() {
  const j = await getJSON(`/api/dayplan/journal?date=${LD.date}`);
  const run = (j.runs || []).find(x => x.run === LD.run);
  for (const x of run?.rows || []) {
    const r = ldRow(x.row); if (!r) continue;
    if (x.status === 'logged') Object.assign(r, {status: 'logged', entryId: x.id, thisRun: true, reason: ''});
    else if (x.status === 'unknown' || (x.status === 'sending' && !(r.thisRun && r.status === 'logged'))) Object.assign(r, {status: 'unknown', reason: x.reason || 'No answer came. Check the Logged column; Pulsar will not send it again.'});
    else if (x.status === 'refused') Object.assign(r, {status: 'refused', reason: x.reason || r.reason});
    else if (x.status === 'undone') Object.assign(r, {status: null, thisRun: false, checked: false, reason: 'Undone: deleted from Productive.'});
    else if (x.status === 'gone') Object.assign(r, {status: null, thisRun: false, checked: false, entryId: null, reason: 'Not in Productive any more: deleted or moved by hand.'});
  }
  LD.undo = run?.undo || [];
}
function ldResultsBox() {
  if (!LD.lastRun || LD.writing) return '';
  const mine = LD.rows.filter(r => r.thisRun && r.status === 'logged').length;
  const failed = LD.rows.filter(r => ['refused', 'unknown'].includes(r.status));
  const left = LD.rows.filter(r => r.checked && ldPending(r) && !failed.includes(r)).length;
  const n = LD.undo?.length || 0;
  const head = `<b>${mine} of ${LD.lastRun.total} logged.</b>` + (failed.length ? ` ${failed.length} ${failed.length === 1 ? 'was' : 'were'} not written and ${failed.length === 1 ? 'is' : 'are'} still here, with the reason.` : '')
    + (left ? ` ${left} not sent yet.` : '');
  return `<div class="rv-res${failed.length ? ' warn' : ''}" tabindex="-1" role="status"><p>${head}</p>
    <p>${n ? 'Undo deletes only what this run wrote, and only as it wrote it.' : ''}</p>
    ${LD.undoAsk ? `<p>Delete the ${n} ${n === 1 ? 'entry' : 'entries'} this run logged? ${n === 1 ? 'It goes' : 'They go'} to Productive's Recycle Bin.</p>
      <div class="btnrow"><button type="button" class="btn primary sm" id="ld-undo-yes">Delete the ${n}</button><button type="button" class="btn bordered sm" id="ld-undo-no">Cancel</button></div>`
    : `<div class="btnrow">${failed.length + left ? `<button type="button" class="btn bordered sm" id="ld-retry">Try the ${failed.filter(r => r.status === 'refused').length + left} again</button>` : ''}
      ${n ? `<button type="button" class="btn bordered sm" id="ld-undo">Undo this run (${n})</button>` : ''}</div>`}</div>`;
}
async function undoRun() {
  LD.undoAsk = false; LD.writing = true; LD.writeCount = LD.undo.length; renderReview();
  let deleted = 0;
  const left = [];  // each one is tried: one deleted or changed by hand, or refused, never holds up the rest
  for (const x of LD.undo) {
    const res = await postJSON('/api/entry/delete', {id: x.id, date: LD.date, run: LD.run});
    if (!res.deleted) { left.push(`${x.start || ''} ${res.why || res.error || 'no answer'}`.trim()); continue; }
    deleted++;
    const r = LD.rows.find(r => r.entryId === x.id);  // back to a proposal, unticked
    if (r) Object.assign(r, {status: null, thisRun: false, checked: false, entryId: null, reason: 'Undone: deleted from Productive.'});
  }
  await syncJournal();  // the journal says what Undo still offers
  LD.writing = false;
  renderReview(); render();
  refreshLogged().then(() => renderReview());
  toast(left.length ? `Undo deleted ${deleted} and left ${left.length}: ${left.join('; ')}.`
                    : `Undone: the ${deleted} ${deleted === 1 ? 'entry' : 'entries'} this run logged ${deleted === 1 ? 'was' : 'were'} deleted.`, !!left.length);
}
