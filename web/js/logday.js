/* Log the day: the whole day proposed at once (GET /api/dayplan), reviewed in the side panel, and written one entry
   at a time through POST /api/log, in time order, stopping at the first answer that is not "logged". Nothing is
   written until the person confirms. Each write carries the run's name, so the server's journal can refuse a row
   it already sent, and Undo deletes only what this run created (POST /api/entry/delete with the run). */

const LD = {on: false, date: null, plan: null, rows: [], open: null, run: null, writing: false, stop: false,
            loading: false, undoAsk: false, note: '', view: 'review', deleted: new Set()};
try { if (localStorage.getItem('pulsar.ldView') === 'all') LD.view = 'all'; } catch {}  // this viewer's last choice
/* what the person did in Log the day, per day, while the page is open (ldkeep.js): put back on the next fresh plan */
const LD_KEPT = new Map();
function ldStash() {
  if (!LD.date || !LD.plan || LD.plan.error || LD.loading) return;
  LD_KEPT.set(LD.date, {...ldSnapshot(LD.rows, LD.open, LD.deleted), sg: sgSnapshot()});
}
const ldActive = () => LD.on && LD.date === state.date;
const ldRow = id => LD.rows.find(r => r.id === id);
/* a test server can show the whole proposal of a day already logged (only PULSAR_READ_ONLY accepts it) */
const LD_IGNORE = params.get('ignore_logged') === '1';
const CONF_WORDS = {high: 'likely right', medium: 'probably right', low: 'check'};
const ldPending = r => !['logged', 'writing'].includes(r.status) && r.status !== 'unknown';
/* a row as the plan proposed it, before anything was changed: sent with the row, so the journal keeps it beside what
   was logged (Settings, How well Pulsar picks for you: logged as proposed, with another task or another time) */
const ldOrig = e => ({taskId: e.taskId || null, serviceId: e.serviceId || null, start: hm24(e.s), minutes: e.minutes,
                      source: e.source || '', confidence: e.confidence || null, ticked: !!e.checked});

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
  const k = ['agent', 'shared'].includes(r.kind) ? 'agent' : r.source === 'repeat' ? 'repeat' : ['meeting', 'call'].includes(r.source) ? 'meeting'
    : ['link', 'seen'].includes(r.source) ? 'link' : r.source === 'rule' ? 'rule' : ['session', 'repo', 'sameday', 'learned'].includes(r.source) ? 'session'
    : ['words', 'work', 'ticket'].includes(r.source) ? 'words' : ['neighbour', 'context'].includes(r.source) ? 'outside' : 'none';
  return `<svg class="wi" viewBox="0 0 16 16" aria-hidden="true">${WI[k]}</svg>`;
}
function meter(c) {
  const n = {high: 3, medium: 2, low: 1}[c] || 1;
  return `<span class="meter" title="Confidence: ${CONF_WORDS[c]}" aria-hidden="true">${[1, 2, 3].map(i => `<i${i <= n ? ' class="f"' : ''}></i>`).join('')}</span>`
    + `<span class="vh">Confidence: ${CONF_WORDS[c]}. </span>`;
}

/* ---------- opening and closing ---------- */
/* fresh: Start over, which forgets what was done on the day; else what was done comes back (ldkeep.js) */
async function openLogDay({fresh = false} = {}) {
  if (!CONFIG?.productive.active) { toast('Connect Productive in Settings first.', true); openSettings('t-conn'); return; }
  if (LD.on) ldStash();  // the day open until now (a catch-up moving on, Start over) keeps its work
  const d = state.date;
  if (fresh) LD_KEPT.delete(d);
  const kept = LD_KEPT.get(d) || null;
  Object.assign(LD, {on: true, date: d, plan: null, rows: [], open: null, run: null, writing: false, stop: false,
                     loading: true, undoAsk: false, undoing: false, note: '', lastRun: null, undo: [], confirmed: null, claudeLate: null, picked: false,
                     deleted: new Set(kept?.deleted || [])});
  closePop();
  document.body.classList.add('rv-on');
  $('#review').hidden = false;
  renderReview(); render();
  const r = await getJSON(`/api/dayplan?date=${d}&source=${state.source}${LD_IGNORE ? '&ignore_logged=1' : ''}`);
  if (!LD.on || LD.date !== d) return;
  LD.loading = false; LD.plan = r;
  LD.rows = (r.entries || []).map((e, i) => ({...e, id: 'r' + i, key: ldRowKey(d, e), orig: ldOrig(e), opened: false, edited: false,
                                               status: e.sent === 'logged' ? 'logged' : e.sent ? 'unknown' : null,
                                               reason: e.sent ? 'A run of this day already sent it.' : '',
                                               ...(e.shared ? ldSharedFrom(e) : {})}));
  if (kept) { const got = ldReapply(LD.rows, kept, r.ignoreLogged ? [] : state.logged?.entries); LD.rows = ldWithHalves(got.rows, CONFIG?.roundTo || 5); LD.open = got.open; }
  LD.rows = LD.rows.filter(x => x.status || !ldNotWork(x));  // time marked "not work" (day.js) is not proposed
  sgFromPlan(r);  // ldreview.js: FI's rules on this plan
  if (kept?.sg) sgRestore(kept.sg); else sgKept(d);  // the review's suggestions as they were, or the one kept for the day
  renderReview(); render();
  document.body.classList.toggle('rv-ignore', !!r.ignoreLogged);
  // Claude runs only from "Check my day with Claude" (ldreview.js sgCheck), never by itself on opening
}

/* a shared row as the plan proposed it: the client's fields, kept to switch back to, and the whole stretch */
function ldSharedFrom(e) {
  const keys = ['taskId', 'title', 'project', 'serviceId', 'service', 'services', 'billable', 'budget', 'why', 'source',
                'confidence', 'confidenceWords', 'alts', 'repo', 'note', 'noteFrom'];
  return {client: Object.fromEntries(keys.map(k => [k, e[k]])), whole: [e.s, e.e], choice: e.choice || 'client'};
}
function ldChoose(id, choice) {
  const r = ldRow(id); if (!r || LD.writing || !r.shared || r.halfOf) return;
  const rows = ldChoice(r, choice, CONFIG?.roundTo || 5).map(x => ({...x, edited: true, opened: true}));
  LD.rows = LD.rows.filter(x => x.halfOf !== r.id);
  LD.rows.splice(LD.rows.indexOf(r), 1, ...rows);
  renderReview(); render();
}

/* Claude's pick replaces only rows the planner is not sure of (medium, low): on high rows it was wrong more often
   than the planner. The planner's guess stays as the first alternative. Rows the person changed are left alone. */
async function askClaudeForDay(d) {
  LD.asking = true; LD.askNote = ''; renderReview();
  const ans = await postJSON('/api/likely/ask', {date: d});
  LD.asking = false;
  if (!ldActive() || LD.date !== d) return;
  if (ans.error) { LD.askNote = `Check my day with Claude: ${ans.error}`; renderReview(); return; }
  LD.picked = true;  // this day has Claude's picks now: "Check again" reviews, it does not pick again
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
  // each row keeps Pulsar's own first plan as "proposed" (asking Claude is the person's step), where one was at that time
  const firstAt = e => LD.rows.find(o => o.s === e.s && o.e === e.e)?.orig;
  const fresh = (r.entries || []).map((e, i) => ({...e, id: 'c' + i, key: ldRowKey(d, e), orig: firstAt(e) || ldOrig(e), opened: false, edited: false, status: null, reason: ''}));
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
                        flags: n.flags, claudeNone: n.claudeNone, note: n.note, noteFrom: n.noteFrom, ticket: n.ticket});
    }
  }
  const none = LD.rows.filter(x => x.claudeNone).length;
  LD.askNote = `Claude looked at the day: ${changed ? `${changed} ${changed === 1 ? 'row' : 'rows'} changed` : 'no row changed'}`
    + (none ? `, ${none} with no task found (Pulsar's guess kept)` : '') + '.' + (ans.warning ? ` ${ans.warning}` : '');
  renderReview(); render();
}
/* the drafts, one row at a time in time order, while this day's review is open and nothing is being written */
async function ldDraftAll() {
  if (LD.drafting || !CONFIG?.likely?.askClaude) return;
  const d = LD.date;
  LD.drafting = true;
  try {
    for (let r; LD.on && LD.date === d && !LD.writing && (r = LD.rows.filter(ldDraftable).sort((a, b) => a.s - b.s)[0]);) await ldDraftNote(r);
  } finally { LD.drafting = false; }
}
function closeLogDay() {
  if (LD.writing) { LD.stop = true; return; }
  ldStash();
  cuClosed();  // catchup.js: a catch-up ends here, unless it is moving on to its next day
  LD.on = false; LD.open = null;
  document.body.classList.remove('rv-on', 'rv-ignore');
  $('#review').hidden = true; $('#ld-pk').hidden = true;
  render();
}
$('#log-day').onclick = () => (ldActive() ? closeLogDay() : openLogDay());
$('#worktime').addEventListener('click', ev => { if (ev.target.closest('#wt-logday')) openLogDay(); });

/* ---------- what is drawn on the axis ---------- */
function ldCoverSpans() { return LD.rows.filter(r => r.checked && r.status !== 'logged').map(r => [r.s, r.e]); }
function ldHead(loggedSum) {
  if (LD.loading) return 'Working out the day…';
  const n = LD.rows.filter(r => r.status !== 'logged').length;
  return `${n} proposed · ${dur(LD.plan?.ignoreLogged ? 0 : loggedSum)} logged`;
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
          notCovered: t.notCovered || 0, over: t.over || 0, cap: t.cap || 0,
          parallel: t.parallel || 0, shared: t.shared || 0, leftOut: t.leftOut || 0};
}
/* a row that needs a look: unsure, no task or service, or something Claude did. It counts as checked once the person
   opened it and it is ticked; one with no task or service cannot be ticked, so it stays until it has them */
const ldNeedsLook = r => r.confidence === 'low' || !r.taskId || !r.serviceId || r.claudeNone
  || r.flags.includes('claudefound') || (r.flags.includes('claudenote') && r.noteFrom === 'claude' && !r.noteEdited);
const ldCheck = r => ldPending(r) && ldNeedsLook(r) && !(r.opened && r.checked);
/* a row or a stretch inside time marked "not work" (day.js notWorkSpans) or paused (pause.js pausedSpans) */
const ldNotWork = x => [...notWorkSpans(), ...pausedSpans()].some(([a, b]) => a <= x.s && (x.e ?? x.s + x.minutes) <= b);
/* "Tick all with a task": every row that has a task and a service and is not ticked yet, except a task Claude found by
   searching Productive (not one you logged to lately) that nobody opened: what Claude read could have steered it
   there, so it waits for the person, as it always has */
const ldTickable = r => ldPending(r) && !r.checked && !!r.taskId && !!r.serviceId;
const ldTickAllRows = () => LD.rows.filter(r => ldTickable(r) && !(r.flags.includes('claudefound') && !r.opened));
/* the next row to check after the open one (in time order), back to the first after the last */
function ldNextCheck(list) {
  const at = ldRow(LD.open)?.s ?? -1;
  return list.find(r => r.s > at && r.id !== LD.open) || list.find(r => r.id !== LD.open) || list[0];
}

/* ---------- the review panel ---------- */
function dayTitle(d) {
  return new Intl.DateTimeFormat(DATE_LOCALE, {weekday: 'long', day: 'numeric', month: 'long', timeZone: 'UTC'}).format(new Date(d + 'T12:00:00Z'));
}
function renderReview() {
  const el = $('#review');
  if (!LD.on) { el.hidden = true; return; }
  if (cuRender(el)) return;  // catchup.js: the list of earlier days, while it is open
  // a tick, an edit or a late answer redraws the list: it stays scrolled where the person was, in the field they were in
  const was = {list: el.querySelector('.rv-list')?.scrollTop || 0, panel: el.scrollTop, focus: ldFocus(el)};
  const head = `<div class="rv-t"><h2>Log the day <span class="sub">· ${esc(dayTitle(LD.date))}</span></h2>${cuButton()}
    <button type="button" class="btn icon" id="ld-close" aria-label="Close Log the day">✕</button></div>`;
  if (LD.loading) { el.innerHTML = `<div class="rv-h">${head}<div class="rv-sum">Reading the day…</div></div>`; return; }
  const p = LD.plan;
  if (p?.error) {
    el.innerHTML = `<div class="rv-h">${head}</div><div class="rv-empty"><div class="big-ic" aria-hidden="true">!</div>
      <h3>Nothing can be proposed</h3><p>${esc(p.error)}</p><div class="btnrow"><button class="btn bordered" id="ld-redo">Try again</button></div></div>`;
    return;
  }
  const T = ldTotals(), ov = ldOverlaps(), toCheck = LD.rows.filter(ldCheck);
  const fixes = r => (r.ticket || []).filter(f => !LD_NEAR.has(f.kind));  // near an estimate or budget: said on the row, nothing to fix
  const flagged = LD.rows.filter(r => ldPending(r) && fixes(r).length), nFlags = flagged.reduce((s, r) => s + fixes(r).length, 0);
  const ovMin = [...ov.values()].flat().reduce((s, o) => s + o.minutes, 0) / 2;
  const target = TARGET, withThese = T.logged + T.minutes;
  const pct = x => Math.min(100, x / Math.max(target, withThese) * 100);
  const chips = [
    toCheck.length ? `<button type="button" class="iss" data-ld-goto="${ldNextCheck(toCheck).id}" title="Open the next row to check"><span class="g" aria-hidden="true">!</span>${toCheck.length} to check</button>`
      : LD.rows.some(ldPending) ? '<span class="iss ok all" role="status"><span class="g" aria-hidden="true">✓</span>All checked</span>' : '',
    ov.size ? `<button type="button" class="iss" data-ld-goto="${[...ov.keys()][0]}"><span class="g" aria-hidden="true">!</span>${Math.ceil(ov.size / 2)} ${ov.size > 2 ? 'overlaps' : 'overlap'}, ${dur(Math.round(ovMin))}</button>` : '',
    nFlags ? `<button type="button" class="iss" data-ld-goto="${flagged[0].id}" title="A closed task, no budget on this date, over or without an estimate"><span class="g" aria-hidden="true">!</span>${nFlags} to fix in Productive</button>` : '',
    T.notCovered ? `<span class="iss ok" title="Active time no entry covers, logged or proposed">${dur(T.notCovered)} not logged</span>` : '',
    T.over ? `<span class="iss ok" title="More than your active time and meetings: shown, never placed">${dur(T.over)} over the day</span>` : '',
    T.parallel ? `<span class="iss ok" title="${esc(`Agents ran while you did other work: ${dur(T.shared)} went to a client first, ${dur(T.leftOut)} left out`)}">Parallel ${dur(T.parallel)}</span>` : '',
  ].join('');
  const model = {haiku: 'Haiku', sonnet: 'Sonnet', opus: 'Opus'}[CONFIG?.likely?.askClaudeModel] || 'Claude';
  const askLine = LD.asking ? `<p class="rv-ask" role="status">${CLAUDE_MARK.split('<span')[0]}Asking ${model} about the unsure rows (up to a minute)…</p>`
    : LD.askNote ? `<p class="rv-ask" role="status">${CLAUDE_MARK.split('<span')[0]}${esc(LD.askNote)}</p>` : '';
  const testNote = p.ignoreLogged ? '<p class="rv-test">Test view: worked out as if nothing were logged this day. Nothing can be written from a read-only test server.</p>' : '';
  const historyNote = p.historyProblem ? `<p class="rv-test" role="status">${esc(p.historyProblem)}</p>` : '';
  const results = ldResultsBox();
  // the list: time not covered and the proposals, in time order; what is logged (in Productive, or by this run) only
  // in "All", else folded into one line at the top. The timeline shows all of it either way.
  const items = [], all = LD.view === 'all', step = CONFIG?.roundTo || 5;
  const inProductive = p.ignoreLogged ? [] : state.logged?.entries || [], loggedRows = LD.rows.filter(r => r.status === 'logged');
  if (all) for (const x of inProductive) items.push({at: x.s ?? -1, html: loggedLine(x)});
  for (const u of (p.uncovered || []).filter(u => u.minutes >= step && !ldNotWork(u))) items.push({at: u.s, html: `<li class="gr"><span class="e-time num">${hm(u.s)}</span><span class="gt"><b>${dur(u.minutes)}</b> not logged</span><span></span></li>`});
  for (const r of LD.rows) if (all || r.status !== 'logged') items.push({at: r.s, html: rowHTML(r, ov.get(r.id))});
  items.sort((a, b) => a.at - b.at);
  const folded = inProductive.length + loggedRows.length;
  if (!all && folded) {
    const mins = inProductive.reduce((t, x) => t + x.minutes, 0) + loggedRows.reduce((t, r) => t + r.minutes, 0);
    items.unshift({at: -2, html: `<li class="gr fold"><span class="e-time num" aria-hidden="true">✓</span><span class="gt"><b>${dur(mins)}</b> logged,
      ${folded} ${folded === 1 ? 'entry' : 'entries'}${loggedRows.length ? ` (${loggedRows.length} just now)` : ''}</span>
      <button type="button" class="btn link sm" data-ld-view="all">Show</button></li>`});
  }
  const viewSwitch = folded ? `<span class="seg ld-view" role="group" aria-label="Which entries the list shows">
      <button type="button" data-ld-view="review" aria-pressed="${!all}">To review</button><button type="button" data-ld-view="all" aria-pressed="${all}">All</button></span>` : '';
  const skipped = (p.skipped || []).length ? `<li class="gr quiet"><span class="e-time num">·</span><span class="gt">Left out: ${esc(p.skipped.map(s => `${s.title || s.project} ${dur(s.minutes)} (${s.why})`).join('; '))}</span><span></span></li>` : '';
  const empty = !LD.rows.length ? `<div class="rv-empty"><div class="big-ic" aria-hidden="true">✓</div><h3>${(state.logged?.entries || []).length ? 'Everything is logged' : 'Nothing to log'}</h3>
    ${(state.logged?.entries || []).length ? '' : '<p>No activity, Claude sessions or meetings were found for this day.</p>'}</div>` : '';
  const n = T.n, tickRows = LD.writing ? [] : ldTickAllRows(), held = LD.rows.filter(r => ldTickable(r) && !tickRows.includes(r)).length;
  const tickAll = tickRows.length ? `<div class="rv-tickall"><button type="button" class="btn bordered sm" id="ld-tickall"
      title="Ticks every row that has a task and a service. The next step lists each entry and its note before anything is written.">Tick all ${tickRows.length} with a task</button>${held
      ? `<span class="hint">${held} found by Claude's search ${held === 1 ? 'stays' : 'stay'} unticked until opened</span>` : ''}</div>` : '';
  el.innerHTML = `<div class="rv-h">${head}
      <div class="rv-sum"><b>${dur(T.minutes)}</b> ticked in ${n} ${n === 1 ? 'entry' : 'entries'} · ${dur(T.billable)} billable</div>
      <div class="rv-bar" role="img" aria-label="${esc(`${dur(withThese)} with these, against a target of ${dur(target)}`)}"><i class="lg" style="width:${pct(T.logged)}%"></i><i class="pp" style="width:${pct(T.minutes)}%"></i></div>
      <div class="rv-bl"><span>${dur(withThese)} with these</span><span>target ${dur(target)}</span></div>
      <div class="rv-iss">${chips}${viewSwitch}</div>${tickAll}${askLine}${sgHead()}${testNote}${historyNote}${cuStrip()}</div>
    ${results}
    ${empty || `<ul class="rv-list" aria-label="Proposed entries">${sgDayHTML()}${items.map(x => x.html).join('')}${skipped}</ul>`}
    <div class="rv-f">${LD.writing && LD.undoing
      ? '<span class="hint" role="status">Deleting what this run logged…</span>'
      : LD.writing
      ? `<span class="hint" role="status">Writing ${LD.rows.filter(r => r.status === 'logged' && r.thisRun).length + 1} of ${LD.writeCount}…</span><button type="button" class="btn bordered" id="ld-stop"${LD.stop ? ' disabled' : ''}>${LD.stop ? 'Stopping after this one' : 'Stop after this one'}</button>`
      : `<button type="button" class="btn bordered" id="ld-redo" title="Work the day out again; your changes go">Start over</button>
         <button type="button" class="btn primary big" id="ld-go"${n ? '' : ' disabled'}>Log ${n} ${n === 1 ? 'entry' : 'entries'}…</button>`}</div>`;
  const list = el.querySelector('.rv-list');
  if (list) list.scrollTop = was.list;
  el.scrollTop = was.panel;
  if (LD.open) el.querySelector(`[data-row="${LD.open}"] .ed`) && wireEditor(ldRow(LD.open));
  ldRefocus(el, was.focus);
  if (!LD.drafting && !LD.writing && LD.rows.some(ldDraftable)) setTimeout(ldDraftAll, 0);
}
/* the field of the open row the person is in, so a redraw (a note Claude drafted for another row) leaves them there:
   a start or minutes still being typed (they apply on change) and where the caret was */
function ldFocus(el) {
  const a = /** @type {HTMLInputElement} */ (document.activeElement);
  if (!a || !el.contains(a) || !/^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName)) return null;
  const row = /** @type {HTMLElement} */ (a.closest('[data-row]'))?.dataset.row;
  const attr = [...a.attributes].map(x => x.name).find(n => /^data-ld-(note|start|min|svc)$/.test(n));
  let at = null; try { at = [a.selectionStart, a.selectionEnd]; } catch (e) { /* a number field has no caret */ }
  return row && attr ? {row, attr, value: a.value, at} : null;
}
function ldRefocus(el, f) {
  if (!f || el.contains(document.activeElement) && document.activeElement !== document.body) return;
  const a = /** @type {HTMLInputElement} */ (el.querySelector(`[data-row="${f.row}"] [${f.attr}]`)); if (!a) return;
  const typing = f.attr === 'data-ld-start' || f.attr === 'data-ld-min';
  if (typing) a.value = f.value;
  a.focus({preventScroll: true});
  try {
    if (a.value === f.value && f.at?.[0] != null) a.setSelectionRange(f.at[0], f.at[1]);
    else if (a.tagName === 'TEXTAREA') a.setSelectionRange(a.value.length, a.value.length);  // Claude's draft came in
  } catch (e) { /* a number field has no caret */ }
}
function loggedLine(x) {
  return `<li class="er done"><div class="eb"><span class="e-time num">${x.s == null ? 'no start' : hm(x.s)}</span>
    <span class="e-task"><i class="dot full" style="--c:${color(x.project)}"></i><span>${esc(x.task || x.project)}</span></span>
    <span class="e-st">✓ ${x.s == null ? 'no start · ' : ''}${dur(x.minutes)}</span></div></li>`;
}
function rowHTML(r, overlaps) {
  if (r.status === 'logged') {
    return `<li class="er done" data-row="${r.id}"><div class="eb"><span class="e-time num">${hm(r.s)}</span>
      <span class="e-task"><i class="dot full" style="--c:${color(r.project)}"></i><span>${esc(r.title)}</span></span>
      <span class="e-st">✓ ${r.thisRun ? 'logged now' : 'logged'} · ${dur(r.minutes)}</span></div></li>`;
  }
  const warn = [];
  if (r.status === 'refused' || r.status === 'unknown') warn.push(`<b>Not written.</b> ${esc(r.reason || '')}`);
  else if (r.minutes < minEntry()) warn.push(`Under ${minEntry()} minutes: Pulsar does not log it. Join it to a row of the same task, or make it ${minEntry()}.`);
  for (const o of overlaps || []) warn.push(`Overlaps ${esc(o.with)} for ${dur(o.minutes)}`);
  const quiet = [];
  if (!r.taskId) quiet.push('No task yet.');
  else if (!r.serviceId && !r.ticket?.some(f => f.kind === 'nobudget')) quiet.push(r.serviceProblem ? `No service: ${r.serviceProblem}` : 'No service that takes time on this date: pick one.');
  if (r.flags.includes('agent')) quiet.push('Agent ran while you did other work.');
  if (r.flags.includes('unattended')) quiet.push('No call seen.');
  if (r.claudeNone) quiet.push("Claude found no task; Pulsar's guess stays.");
  if (r.flags.includes('claudefound')) quiet.push('Claude found this task by search; not one of yours lately.');
  if (r.flags.includes('claudenote') && r.noteFrom === 'claude' && !r.noteEdited) quiet.push('Claude drafted the note: read it first.');
  if (r.flags.includes('meetingtitle')) quiet.push('Meeting title not used as the note.');
  if (r.flags.includes('renamed')) quiet.push('Meeting renamed since you last logged it: check the task.');
  if (r.flags.includes('untimed')) quiet.push(`${dur(r.untimed)} of this task has no start time: is it this work?`);
  if (r.halfOf) quiet.push('The second half of a shared stretch.');
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
    ${sharedLine(r)}
    ${ticketLine(r)}
    ${sgRowHTML(r)}
    ${joinLine(r)}
    ${open ? editorHTML(r) : ''}</li>`;
}

/* the row right before this one when it is the same task and ends where this one starts: two stretches of one task
   that the layout put back to back, or a row whose task was just changed to its neighbour's (2026-11-12: two Pulsar
   rows of 25 minutes with the same note); null otherwise */
function ldJoinable(r) {
  if (!r.taskId || r.halfOf || !ldPending(r)) return null;
  return LD.rows.find(p => p !== r && p.taskId === r.taskId && !p.halfOf && ldPending(p)
                      && Math.abs((p.e ?? p.s + p.minutes) - r.s) < 0.5) || null;
}
function joinLine(r) {
  const p = ldJoinable(r);
  return p && !LD.writing ? `<div class="ld-join">Same task as the row before it, right after it.
    <button type="button" class="btn sm" data-ld-join="${r.id}">Join into one entry (${dur(p.minutes + r.minutes)})</button></div>` : '';
}
/* one entry from the two: the first takes the second's minutes, its note when it says something else, and its doubts */
function ldJoin(r) {
  const p = ldJoinable(r);
  if (!p) return;
  p.minutes += r.minutes; p.e = r.e ?? r.s + r.minutes;
  p.note = [...new Set([p.note, r.note].map(n => (n || '').trim()).filter(Boolean))].join(' ');
  if (r.noteFrom === 'claude') p.noteFrom = 'claude';
  p.flags = [...new Set([...(p.flags || []), ...(r.flags || [])])];
  p.checked = p.checked && r.checked;
  LD.rows = LD.rows.filter(x => x !== r);
  if (LD.open === r.id) LD.open = p.id;
  ldChanged(p);
}

/* a shared row's note and its choice (ldkeep.js ldChoice), outside the row's button */
function sharedLine(r) {
  if (!r.shared || r.halfOf) return '';
  const c = r.choice || 'client', b = (k, label) => `<button type="button" data-ld-choice="${r.id}|${k}" aria-pressed="${c === k}"${LD.writing ? ' disabled' : ''}>${label}</button>`;
  return `<div class="e-shared"><span>${esc(LD_SHARED_TEXT[c] || LD_SHARED_TEXT.client)}</span>
    <span class="seg ld-choice" role="group" aria-label="Who this stretch goes to">${b('client', 'Client')}${b('half', 'Half each')}${b('internal', 'Internal')}</span></div>`;
}

/* the row's ticket flags (pulsar/flags.py), read only: what to fix in Productive, and the link to the task there.
   Outside the row's button, since a link cannot sit inside one. */
const LD_NEAR = new Set(['nearestimate', 'nearbudget']);  // flags.py: how much is left, amber, nothing to fix
function ticketLine(r) {
  const fix = (r.ticket || []).filter(f => !LD_NEAR.has(f.kind)), near = (r.ticket || []).filter(f => LD_NEAR.has(f.kind));
  if (!fix.length && !near.length) return '';
  const url = productiveTaskURL(r.taskId), link = url ? ` · <a href="${esc(url)}" target="_blank" rel="noopener noreferrer">Open in Productive</a>` : '';
  return (near.length ? `<p class="e-flags near">${near.map(f => esc(f.text)).join(' · ')}${fix.length ? '' : link}</p>` : '')
    + (fix.length ? `<p class="e-flags">! ${fix.map(f => esc(f.text)).join(' · ')}${link}</p>` : '');
}

/* ---------- the editor of the open row ---------- */
function noteHint(r) {
  if (r.noteDrafting) return `${CLAUDE_MARK.split('<span')[0]}Claude is drafting a note…`;
  if (r.noteFrom === 'session' && !r.noteEdited) return `${CLAUDE_MARK.split('<span')[0]}Drafted by Claude. Clients may see notes.`;
  if (r.noteError && !(r.note || '').trim()) return `No note from Claude: ${esc(r.noteError)} Clients may see notes.`;
  if (r.noteFrom === 'claude' && !r.noteEdited) return `${CLAUDE_MARK.split('<span')[0]}Drafted by Claude. Clients may see notes.`;
  if (r.noteFrom === 'meeting' && !r.noteEdited) return "The meeting's title. Clients may see notes.";
  if (r.noteFrom === 'repeat' && !r.noteEdited) return 'Your usual note for this. Clients may see notes.';
  if (r.noteFrom === 'commits' && !r.noteEdited) return 'From your commits' + (CONFIG?.likely?.askClaude && ldDraftable(r) ? '; Claude drafts one in a moment.' : '. Clients may see notes.');
  if (CONFIG?.likely?.askClaude && ldDraftable(r)) return `${CLAUDE_MARK.split('<span')[0]}Claude drafts this note in a moment. Clients may see notes.`;
  return 'Clients may see notes: the outcome, in a sentence.';
}
const altName = a => a.title || 'task ' + a.taskId;
/* where an entry's minutes were seen, when it stands mostly elsewhere: with work by turns Log the day lays a task's
   minutes out together, from where it began (dayplan._entry "own") */
function ldSeenAt(r) {
  const own = r.own || [], total = own.reduce((n, [a, b]) => n + b - a, 0);
  const inside = own.reduce((n, [a, b]) => n + Math.max(0, Math.min(b, r.e) - Math.max(a, r.s)), 0);
  if (!total || inside >= total / 2) return '';
  return ` · seen at ${own.slice(0, 3).map(([a, b]) => `${hm(a)}–${hm(b)}`).join(', ')}${own.length > 3 ? ', …' : ''}`;
}
const whyLine = r => `${whyIcon(r)}<span>${esc(r.why)}${r.parts > 1 ? ` (${r.parts} stretches joined)` : ''}${esc(ldSeenAt(r))}</span>`;
function editorHTML(r) {
  const svcs = r.services || [];
  return `<div class="ed" role="group" aria-label="Change this entry">
    <div class="field"><span>Task</span><button type="button" class="taskbtn" data-ld-task="${r.id}">
      <span class="tb-t">${esc(r.title || 'Pick a task')}</span><span class="tb-p">${esc(r.project || 'Search tasks')}</span></button></div>
    ${r.alts?.length ? `<div class="alts"><span>Or</span><div>${r.alts.map((a, i) => `<button type="button" class="alt" data-ld-alt="${i}"><span>${esc(altName(a))}</span>${a.why ? `<span class="vh">, ${esc(a.why)}</span>` : ''}</button>`).join('')}</div></div>` : ''}
    <p class="whyd">${whyLine(r)}</p>
    <button type="button" class="newtask-link" data-ld-newtask="${r.id}">Create a task…</button>
    <label class="field"><span>Service</span><select data-ld-svc="${r.id}"${svcs.length ? '' : ' disabled'}>
      ${r.serviceId ? '' : '<option value="">' + (svcs.length ? 'Pick a service' : r.taskId ? 'Loading or none on this date' : 'Pick a task first') + '</option>'}
      ${svcs.map(x => `<option value="${esc(x.id)}"${x.id === r.serviceId ? ' selected' : ''}>${esc(x.name)}${x.budget ? ' · ' + esc(x.budget) : ''}${x.billable ? ' · billable' : x.billable === false ? ' · not billable' : ''}</option>`).join('')}
    </select></label>
    <div class="tl"><label class="field"><span>Start</span><input type="text" inputmode="numeric" autocomplete="off" spellcheck="false" maxlength="9" placeholder="HH:MM" title="A time: 14:30, or 2:30 pm" data-ld-start value="${hm24(r.s)}"></label>
      <label class="field"><span>Minutes</span><input type="number" min="${Math.max(CONFIG?.roundTo || 5, minEntry())}" max="720" step="${CONFIG?.roundTo || 5}" data-ld-min value="${r.minutes}"></label>
      <span class="endt">until ${hm(r.s + r.minutes)}</span></div>
    <label class="field"><span>Note</span><textarea rows="2" data-ld-note placeholder="What was done: the outcome, in a sentence">${esc(r.note || '')}</textarea></label>
    <p class="hint ld-note-hint">${noteHint(r)}</p>
    <div class="acts"><button type="button" class="btn bordered" data-ld-del="${r.id}">Remove</button>
      <button type="button" class="btn bordered" data-ld-notwork="${r.id}" title="Not work: not proposed again, not shown as to log (this Mac only)">Not work</button>
      <span class="hint ld-keys"><kbd>↩</kbd> on the row ${r.taskId && r.serviceId ? 'ticks it and opens the next' : 'picks its task'}</span></div></div>`;
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
    if (!m) { ev.target.value = hm24(r.s); ev.target.setAttribute('aria-invalid', 'true'); return; }
    ev.target.removeAttribute('aria-invalid'); ev.target.value = t;
    const s = +m[1] * 60 + +m[2]; r.s = s; r.e = Math.min(1440, s + r.minutes); r.minutes = r.e - r.s; ldChanged(r);
  };
  li.querySelector('[data-ld-min]').onchange = ev => {
    const v = Math.round(+ev.target.value); if (!(v >= 1 && v <= 1440 - r.s)) { ev.target.value = r.minutes; return; }
    r.minutes = v; r.e = r.s + v; ldChanged(r);
  };
  li.querySelector('[data-ld-note]').onfocus = () => ldDraftNote(r);
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

/* With the Claude Code switch on, Claude drafts the note of every row with work of a repository, by itself, one row
   at a time, as the log dialog drafts one on opening: an empty note, or one from the commits nobody edited (Claude's
   says what was done, the commits only how). Focusing a row's note drafts that one first. Each row is asked once
   (kept with the day's work, ldkeep.js; the server keeps the draft too, so opening the day again asks nothing new);
   what the person typed is never replaced. A note already drafted for the same work in the Likely column is taken as
   it is (reuse: askclaude.kept_note), with no new run. The note is about the work, not the task, so picking another
   task keeps it. Before 1.6.11 a note was drafted only on focus. */
const ldDraftable = r => !r.status && !!r.repo && !r.noteTried && !r.noteDrafting
  && (!(r.note || '').trim() || (r.noteFrom === 'commits' && !r.noteEdited));
async function ldDraftNote(r) {
  if (!CONFIG?.likely?.askClaude || !ldDraftable(r) || LD.writing) return;
  const was = r.note || '';
  r.noteDrafting = true; r.noteTried = true; renderReview();
  const got = await postJSON('/api/likely/note', {date: LD.date, s: r.s, e: r.e ?? r.s + r.minutes, project: r.repo, reuse: 1}).catch(() => ({}));
  const row = ldRow(r.id); r.noteDrafting = false;
  if (!row) return;
  row.noteDrafting = false;
  if ((row.note || '') === was && !row.noteEdited) {  // nothing typed: the draft goes in, and the panel is drawn again
    if (got.note) Object.assign(row, {note: got.note, noteFrom: 'session', noteEdited: false});
    else row.noteError = got.error || got.why || '';
    renderReview();  // keeps the field the person is in (ldFocus)
    if (ldDlg.open && LD.confirmed?.some(x => x.id === row.id)) openConfirm();  // what the dialog shows is what is sent
    return;
  }
  // typed meanwhile: only the hint changes, so the field keeps its focus
  const hint = $(`#review [data-row="${row.id}"] .ld-note-hint`); if (hint) hint.innerHTML = noteHint(row);
}
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
                    services: [], billable: null, edited: true, opened: true, source: 'you', why: 'picked by you', confidence: 'high',
                    ticket: []});  // the new task's flags come with its services
  r.flags = r.flags.filter(f => !['notask', 'low', 'noservice', 'claudefound'].includes(f));
  if (r.noteFrom === 'claude' && !r.noteEdited) { r.note = ''; r.noteFrom = ''; r.flags = r.flags.filter(f => f !== 'claudenote'); }  // Claude wrote it for another task
  renderReview(); render();  // its note, if Claude's for the old task, went: a new draft when the note field is focused
  const [list, sug] = await Promise.all([getJSON(`/api/services?task=${r.taskId}&date=${LD.date}&flags=1&minutes=${Math.round(r.minutes || 0)}`), suggestions(LD.date)]);
  if (r.taskId !== String(t.taskId)) return;
  if (list.error) { r.serviceProblem = list.error; renderReview(); return; }
  r.ticket = list.flags || [];
  r.services = (list.services || []).map(x => ({id: x.id, name: (x.name || '').trim(), budget: x.budget || '', billable: x.billable}));
  const recent = (sug.recent || []).find(x => x.taskId === r.taskId);
  const pick = r.services.find(x => t.serviceHint && x.id === t.serviceHint) || r.services.find(x => x.id === recent?.serviceId)
    || r.services.find(x => recent?.service && x.name === recent.service.trim())
    || r.services.find(x => x.id === list.task?.serviceId)
    || (recent ? null : r.services.find(x => x.id === (sug.recent || []).find(y => y.project && y.project === r.project)?.serviceId))
    || (r.services.length === 1 ? r.services[0] : null);
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
  if (t.id === 'ld-redo') return openLogDay({fresh: true});
  if (t.id === 'ld-go') return openConfirm();
  if (t.dataset.ldView) { LD.view = t.dataset.ldView; try { localStorage.setItem('pulsar.ldView', LD.view); } catch {} renderReview(); return; }
  if (t.id === 'ld-tickall') { for (const r of ldTickAllRows()) { r.checked = true; r.edited = true; } renderReview(); render(); return; }
  if (t.id === 'ld-stop') { LD.stop = true; renderReview(); return; }
  if (t.dataset.ldChoice) { const [id, c] = t.dataset.ldChoice.split('|'); return ldChoose(id, c); }
  if (t.dataset.ldGoto) return ldOpen(t.dataset.ldGoto, true);
  if (t.dataset.ldOpen) return ldOpen(t.dataset.ldOpen);
  if (t.dataset.ldTick) { const r = ldRow(t.dataset.ldTick); r.checked = t.checked; r.edited = true; renderReview(); render(); return; }
  if (t.dataset.ldTask) return ldPickTask(ldRow(t.dataset.ldTask), t);
  if (t.dataset.ldJoin) return ldJoin(ldRow(t.dataset.ldJoin));
  if (t.dataset.ldNewtask) {  // newtask.js: the same form as the log dialog's; the new task is then this row's
    const r = ldRow(t.dataset.ldNewtask);
    return openNewTask({date: LD.date, s: r.s, e: r.s + r.minutes, project: r.project,
                        onCreated: (task, serviceHint) => ldRow(r.id) && ldSetTask(ldRow(r.id), {...task, serviceHint})});
  }
  if (t.dataset.ldAlt != null) { const r = ldRow(LD.open); return ldSetTask(r, r.alts[+t.dataset.ldAlt]); }
  if (t.dataset.ldDel) { const k = ldRow(t.dataset.ldDel)?.key; if (k) LD.deleted.add(k); LD.rows = LD.rows.filter(r => r.id !== t.dataset.ldDel); LD.open = null; renderReview(); render(); return; }
  if (t.dataset.ldNotwork) {  // the row's time was not work: it leaves the proposal, and comes back with Undo
    const r = ldRow(t.dataset.ldNotwork); if (!r) return;
    LD.rows = LD.rows.filter(x => x !== r); LD.open = null; renderReview(); render();
    markDay({add: [r.s, r.e ?? r.s + r.minutes]}, `${hm(r.s)}–${hm(r.e ?? r.s + r.minutes)} marked as not work.`, {label: 'Undo', run: async () => {
      if (await markDay({remove: [r.s, r.e ?? r.s + r.minutes]}) && ldActive() && !LD.rows.includes(r)) { LD.rows.push(r); LD.rows.sort((a, b) => a.s - b.s); renderReview(); render(); }
    }});
    return;
  }
  if (t.id === 'ld-undo') { LD.undoAsk = true; renderReview(); return; }
  if (t.id === 'ld-undo-no') { LD.undoAsk = false; renderReview(); return; }
  if (t.id === 'ld-undo-yes') return undoRun();
  if (t.id === 'ld-retry') return openConfirm();
});

/* Enter on the open row (its line or its tick box): tick it and open the next row still to log; a row with no task
   or service opens its task picker instead. Enter in a field, a menu or another button does what it always does. */
$('#review').addEventListener('keydown', ev => {
  if (ev.key !== 'Enter' || ev.metaKey || ev.ctrlKey || ev.altKey || ev.shiftKey || LD.writing || !LD.open) return;
  const li = ev.target.closest('li[data-row]'), r = ldRow(LD.open);
  if (!r || li?.dataset.row !== r.id || !(ev.target.matches('.eb') || ev.target.matches('[data-ld-tick]'))) return;
  ev.preventDefault();
  if (!(r.taskId && r.serviceId)) { const b = li.querySelector('.taskbtn'); if (b) ldPickTask(r, b); return; }
  r.checked = true; r.edited = true;
  const next = LD.rows.filter(x => ldPending(x) && x.id !== r.id).sort((a, b) => a.s - b.s).find(x => x.s > r.s);
  if (next) ldOpen(next.id, true);
  else { LD.open = null; renderReview(); render(); $('#ld-go')?.focus(); }
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
  // entries under the shortest: none is sent, since the first would stop the run (2026-11-12: a split into 8 + 8 + 7 + 7)
  const short = live.filter(r => r.minutes < minEntry());
  if (short.length) warns.push(`Under ${minEntry()} minutes, so Productive takes none of these: ${short.map(r => `${hm(r.s)} ${r.title} ${dur(r.minutes)}`).join(', ')}. Go back, and join each to a row of the same task or make it ${minEntry()}.`);
  if (ov.size) warns.push(`${[...ov.entries()].filter(([id]) => live.some(r => r.id === id)).map(([id, os]) => `${hm(ldRow(id).s)} ${ldRow(id).title} overlaps ${os.map(o => o.with).join(', ')}`).join('; ')}.`);
  const untimed = LD.plan?.ignoreLogged ? [] : (state.logged?.entries || []).filter(x => x.s == null && live.some(r => r.taskId === String(x.taskId)));
  if (untimed.length) warns.push(`Already logged with no start time, on a task below: ${untimed.map(x => `${x.task || x.project} ${dur(x.minutes)}`).join(', ')}. Check it is not the same work.`);
  if (unopened.length) warns.push(`${unopened.length} ${unopened.length === 1 ? 'entry' : 'entries'} marked “check” ${unopened.length === 1 ? 'was' : 'were'} not opened: ${unopened.map(r => `${hm(r.s)} ${r.title}`).join(', ')}.`);
  const quiet = T.notCovered ? [`${dur(T.notCovered)} not logged.`] : [];
  const claudeNote = r => ['claude', 'session'].includes(r.noteFrom) && !r.noteEdited;
  const lines = live.map(r => `<li class="cl-r"><span class="num">${hm(r.s)}</span><span><b>${esc(r.title)}</b>
      <small>${esc([r.project, r.service, r.budget].filter(Boolean).join(' · '))}${r.billable ? ' <span class="bill">$</span>' : ''}</small>
      ${r.flags.includes('claudefound') ? `<small class="cl-c">${CLAUDE_MARK.split('<span')[0]}Claude found this task by searching Productive; you have not logged to it lately.</small>` : ''}
      ${r.shared && !r.halfOf && r.choice !== 'internal' ? `<small class="cl-s">Shared: ${r.choice === 'half' ? 'half each with your internal work' : 'its agent ran while you did internal work'}.</small>` : ''}
      ${r.note ? `<small class="cl-n">${claudeNote(r) ? `${CLAUDE_MARK.split('<span')[0]}<span class="cl-by">Claude's note:</span> ` : ''}${esc(r.note)}</small>` : ''}
      ${dry?.[r.id] ? `<small class="cl-d">${esc(dry[r.id])}</small>` : ''}</span><span class="num">${dur(r.minutes)}</span></li>`).join('');
  ldDlg.innerHTML = `<div class="dh"><h2 id="ld-title">Log ${live.length} ${live.length === 1 ? 'entry' : 'entries'} in Productive?</h2>
      <button type="button" class="btn icon" data-ld-x aria-label="Close">✕</button></div>
    <div class="scrollarea">
      <p class="cf-sum">${esc(dayTitle(LD.date))} · <b>${dur(T.minutes)}</b> · ${dur(T.billable)} billable</p>
      ${warns.map(w => `<div class="cf-w"><span class="g" aria-hidden="true">!</span><span>${esc(w)}</span></div>`).join('')}
      ${quiet.map(w => `<div class="cf-w quiet"><span class="g" aria-hidden="true">·</span><span>${esc(w)}</span></div>`).join('')}
      ${dry ? `<div class="cf-w quiet"><span class="g" aria-hidden="true">·</span><span>Dry run: each entry below shows what would be sent. Nothing was written.</span></div>` : ''}
      <ul class="cl">${lines}</ul></div>
    <div class="df"><p class="hint">Sent one at a time; stops at the first problem.</p>
      <button type="button" class="btn bordered" data-ld-dry>Dry run</button>
      <button type="button" class="btn bordered" data-ld-x>Back to the review</button>
      <button type="button" class="btn primary" data-ld-write${short.length ? ' disabled' : ' autofocus'}>Log ${live.length} ${live.length === 1 ? 'entry' : 'entries'}</button></div>`;
  if (!ldDlg.open) ldDlg.showModal();
}
ldDlg.addEventListener('click', ev => {
  const t = ev.target.closest('button'); if (!t) return;
  if (t.hasAttribute('data-ld-x')) ldDlg.close();
  if (t.hasAttribute('data-ld-dry')) dryRun(t);
  if (t.hasAttribute('data-ld-write')) { writeRun(); ldDlg.close(); }
});
ldDlg.addEventListener('close', () => { if (LD.claudeLate) ldClaudePlan(...LD.claudeLate); });

const rowBody = r => ({date: LD.date, start: hm24(r.s), minutes: r.minutes, taskId: r.taskId, serviceId: r.serviceId,
                       note: (r.note || '').trim(), run: LD.run, row: r.id, proposed: r.orig || null});
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
  // a journal that could not be read says nothing about the rows: they and Undo stay as they were, and it is said
  LD.journalError = j.error || '';
  if (j.error) return;
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
  const refused = failed.filter(r => r.status === 'refused').length, unknown = failed.length - refused;
  const left = LD.rows.filter(r => r.checked && ldPending(r) && !failed.includes(r)).length;
  const n = LD.undo?.length || 0;
  // "unknown": sent, and no clear answer came: it may be in Productive, so it is never called "not written"
  // what happened, in numbers that add up to the run (2026-11-12: "1 of 3 logged. 1 was refused… 2 not sent yet" made
  // four of three, and the reason was only on the row): the row it stopped at, with Productive's reason, then the rest
  const st = LD.lastRun.stopped && typeof LD.lastRun.stopped === 'object' ? LD.lastRun.stopped : null;
  const rest = Math.max(0, LD.lastRun.total - mine - failed.length);
  const head = `<b>${mine} of ${LD.lastRun.total} written.</b>`
    + (st && st.status === 'refused' ? ` Not written: ${hm(st.s)} ${esc(st.title || 'this row')}. ${esc(st.reason || '')}`
      : refused ? ` ${refused} not written: the reason is on ${refused === 1 ? 'its row' : 'their rows'}.` : '')
    + (unknown ? ` ${unknown} may or may not be in Productive: check the Logged column; Pulsar will not send ${unknown === 1 ? 'it' : 'them'} again.` : '')
    + (rest ? ` A run stops at the first row not written, so ${rest === 1 ? 'the row' : `the ${rest} rows`} after it ${rest === 1 ? 'was' : 'were'} not sent.` : '')
    + (LD.journalError ? ` Pulsar could not read its journal of this run (${LD.journalError}), so these rows and Undo may be out of date: open Log the day again.` : '');
  return `<div class="rv-res${failed.length ? ' warn' : ''}" tabindex="-1" role="status"><p>${head}</p>
    ${LD.undoAsk ? `<p>Delete the ${n} ${n === 1 ? 'entry' : 'entries'} this run logged? ${n === 1 ? 'It goes' : 'They go'} to Productive's Recycle Bin.</p>
      <div class="btnrow"><button type="button" class="btn bordered sm" id="ld-undo-no">Cancel</button><button type="button" class="btn danger sm" id="ld-undo-yes">Delete the ${n}</button></div>`
    : `<div class="btnrow">${refused + left ? `<button type="button" class="btn bordered sm" id="ld-retry">Send the ${refused + left} ticked again</button>` : ''}
      ${n ? `<button type="button" class="btn bordered sm" id="ld-undo">Undo this run (${n})</button>` : ''}</div>`}</div>`;
}
async function undoRun() {
  LD.undoAsk = false; LD.writing = true; LD.undoing = true; LD.writeCount = LD.undo.length; renderReview();
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
  LD.writing = false; LD.undoing = false;
  renderReview(); render();
  refreshLogged().then(() => renderReview());
  toast(left.length ? `Undo deleted ${deleted} and left ${left.length}: ${left.join('; ')}.`
                    : `Undone: the ${deleted} ${deleted === 1 ? 'entry' : 'entries'} this run logged ${deleted === 1 ? 'was' : 'were'} deleted.`, !!left.length);
}
