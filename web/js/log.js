/* Logging time to Productive from the page, and rules (words in a window -> a task).
   Nothing reaches Productive without a click on "Log in Productive". */

/* ---------- rules ---------- */
const itemText = it => `${it.app} ${it.title} ${it.workspace || ''} ${it.host} ${it.url || ''}`.toLowerCase();

function ruleOf(it) {
  const rules = CONFIG?.rules || [];
  if (!rules.length) return null;
  const text = itemText(it);
  return rules.find(r => text.includes(r.match.toLowerCase())) || null;
}

/* the rules' tasks inside [a, b) of the day's activity, most minutes first */
function rulesInRange(a, b) {
  const out = new Map();
  for (const it of state.act?.items || []) {
    const s = Math.max(a, it.s), e = Math.min(b, it.e);
    if (e <= s || it.cat === 'personal') continue;
    const r = ruleOf(it); if (!r) continue;
    const o = out.get(r.taskId) || {taskId: r.taskId, title: r.title, project: r.project, minutes: 0};
    o.minutes += e - s; out.set(r.taskId, o);
  }
  return [...out.values()].sort((x, y) => y.minutes - x.minutes);
}

/* what a new rule should look for in this window: a page's path, else the site, else the title */
function ruleSuggestion(it) {
  if (it.url) return it.url.replace(/^https?:\/\//, '').split('/').slice(0, 3).join('/');
  return it.host || it.title || it.app;
}

async function saveRules(rules) {
  const r = await postJSON('/api/settings', {rules});
  if (r.error) { toast(r.error, true); return false; }
  applyConfig(r.config); render(); return true;
}

/* ---------- task picker, shared by the log and rule dialogs ---------- */
let suggestCache = null;
async function suggestions(date) {
  // asked again when the last answer lacked your assigned tasks or your last 30 days because Productive failed (the
  // server tries again after a minute)
  if (!suggestCache || suggestCache.date !== date || suggestCache.data.assignedError || suggestCache.data.historyError) suggestCache = {date, data: await getJSON(`/api/tasks/suggest?date=${date}`)};
  return suggestCache.data;
}
function dedupe(groups) {
  const seen = new Set();
  return groups.map(g => ({...g, tasks: g.tasks.filter(t => !seen.has(t.taskId) && seen.add(t.taskId))}));
}
/* what Productive says of a task, in words (never colour alone): overdue or due when, blocked or in progress */
function taskNote(t) {
  const today = todayLocal(), bits = [];
  const when = d => dayLabel(d, {day: 'numeric', month: 'short'});
  if (t.due) bits.push(t.due < today ? `<span class="pk-warn">overdue, due ${esc(when(t.due))}</span>`
    : t.due === today ? '<span class="pk-warn">due today</span>' : `due ${esc(when(t.due))}`);
  if (t.blocked) bits.push(`<span class="pk-warn">${esc((t.status || 'blocked').toLowerCase())}</span>`);
  else if (/progress/i.test(t.status || '')) bits.push('in progress');
  return bits.length ? ' · ' + bits.join(' · ') : '';
}
/* the day a search ranks "yours" against: the log dialog's date, Log the day's, else the day in view */
function searchDate() {
  if (logDlg.open && logForm.elements.date.value) return logForm.elements.date.value;
  return typeof LD !== 'undefined' && LD.on ? LD.date : state.date;
}
function makePicker(listEl, searchEl, onPick) {
  let selected = null, groups = [], base = [], timer, loading = false, searched = '', byHand = false;
  function draw() {
    const empty = loading ? 'Loading your tasks…' : searched ? `No task matches “${searched}”.` : 'No tasks to offer. Search for one above.';
    listEl.innerHTML = groups.filter(g => g.tasks.length || g.note).map(g => `<div class="pk-h">${esc(g.label)}</div>`
      + (g.note ? `<div class="note">${esc(g.note)}</div>` : '')
      + g.tasks.map(t => `<button type="button" class="pk${selected?.taskId === t.taskId ? ' on' : ''}" data-id="${esc(t.taskId)}"
          role="option" aria-selected="${selected?.taskId === t.taskId}"><span class="pk-t">${esc(t.title)}</span>
          <span class="pk-p">${t.yours ? `<span class="pk-mine">${t.yours === 'logged' ? 'yours, logged lately' : 'assigned to you'}</span> · ` : ''}${esc(t.project)}${g.showMin && t.minutes ? ' · ' + dur(t.minutes) + ' here' : ''}${t.why ? ' · ' + (t.claude ? CLAUDE_MARK : '') + esc(t.why) : ''}${taskNote(t)}</span></button>`).join('')).join('')
      || `<div class="note">${esc(empty)}</div>`;
  }
  /* a redraw replaces the buttons: the one that had the focus gets it back */
  function redraw() {
    const id = listEl.contains(document.activeElement) ? document.activeElement.dataset.id : null;
    draw();
    if (id != null) [...listEl.querySelectorAll('.pk')].find(b => b.dataset.id === id)?.focus();
  }
  listEl.onclick = ev => {
    const b = ev.target.closest('.pk'); if (!b) return;
    selected = groups.flatMap(g => g.tasks).find(t => t.taskId === b.dataset.id) || null;
    byHand = !!selected; redraw(); if (selected) onPick(selected);
  };
  /* Enter in the search picks the first task shown (it never submits the form with a task no longer on screen);
     the arrow keys move between the search and the tasks */
  searchEl.addEventListener('keydown', ev => {
    const first = listEl.querySelector('.pk');
    if (ev.key === 'Enter') { ev.preventDefault(); first?.click(); listEl.querySelector('.pk.on')?.focus(); }
    if (ev.key === 'ArrowDown' && first) { ev.preventDefault(); first.focus(); }
  });
  listEl.addEventListener('keydown', ev => {
    const all = [...listEl.querySelectorAll('.pk')], i = all.indexOf(document.activeElement);
    if (i < 0 || !['ArrowDown', 'ArrowUp'].includes(ev.key)) return;
    ev.preventDefault();
    if (ev.key === 'ArrowUp' && i === 0) searchEl.focus(); else all[Math.max(0, Math.min(all.length - 1, i + (ev.key === 'ArrowDown' ? 1 : -1)))].focus();
  });
  searchEl.oninput = () => {
    clearTimeout(timer);
    const q = searchEl.value.trim();
    timer = setTimeout(async () => {
      if (q.length < 2) { searched = ''; groups = base; redraw(); return; }
      listEl.classList.add('busy');
      const r = await getJSON(`/api/tasks/search?q=${encodeURIComponent(q)}&date=${encodeURIComponent(searchDate())}`);
      listEl.classList.remove('busy');
      if (searchEl.value.trim() !== q) return;
      searched = r.error ? '' : q;
      groups = [{label: 'Search results', tasks: r.tasks || [], note: r.error || ''}]; redraw();
    }, 300);
  };
  return {
    /* a fresh start while the suggestions load: no search, no choice */
    loading() { loading = true; base = groups = []; selected = null; byHand = false; searched = ''; searchEl.value = ''; clearTimeout(timer); draw(); },
    /* the suggestions; a search typed meanwhile and a task picked from it stay */
    set(newGroups, pre) {
      loading = false; base = newGroups;
      if (searchEl.value.trim().length < 2) groups = base;
      if (!selected && pre) selected = pre; else pre = null;
      redraw(); if (pre) onPick(pre);
    },
    /* a group put first once the suggestions are there (range.js), its first task picked unless one was picked by hand */
    lead(g) {
      if (loading || !g?.tasks.length) return;
      base = dedupe([g, ...base.filter(x => x.label !== g.label)]);
      if (searchEl.value.trim().length < 2) groups = base;
      const pre = byHand || selected?.taskId === g.tasks[0].taskId ? null : g.tasks[0];
      if (pre) selected = pre;
      redraw(); if (pre) onPick(pre);
    },
    /* a task just created (newtask.js): first, in a group of its own, and picked as if by hand, so a late answer
       (the stretch's lead, range.js) never replaces it */
    pick(t) {
      loading = false; searched = ''; searchEl.value = ''; clearTimeout(timer);
      base = [{label: 'Just created', tasks: [t]}, ...base.filter(g => g.label !== 'Just created')
        .map(g => ({...g, tasks: g.tasks.filter(x => x.taskId !== t.taskId)}))];
      groups = base; selected = t; byHand = true; redraw(); onPick(t);
    },
    get selected() { return selected; },
  };
}

/* ---------- the log dialog ---------- */
const logDlg = $('#logdlg'), logForm = $('#log-form');
let logForce = false;
const setLogMsg = (text, warn = false) => { const m = $('#log-msg'); m.textContent = text; m.className = 'msg' + (warn ? ' warn' : ''); };
const logPicker = makePicker($('#log-tasks'), logForm.elements.q, t => { resetForce(); showFlags(null); loadServices(t); showDraft(); showTaskLink(t); });
/* a drafted note until the person edits it: Claude's (Ask Claude, then Log on a Likely block) {note, taskId, title},
   or one from their commits {note, from: 'commits'} */
let logDrafted = null;
/* a note Pulsar filled in (a meeting's title, a repeat's usual note): it says so until the person edits it */
let logFilled = false;
let logDrafting = 0;  // the dialog's number while Claude drafts a note for it (draftFromSession), else 0
let logOpened = 0;    // how many times the dialog opened: a note that arrives late is only for the dialog that asked

let logDraftFrom = '';  // whose session Claude drafts from, in words, when it is not "this session" (a stretch)

/* With Claude Code on: Claude drafts the note of a Likely block, or of a stretch (from, day.js logStretch), from its
   session (what was typed, Claude's replies) and commits. It replaces the note only while the person has not typed in
   it, and only in the dialog that asked. */
async function draftFromSession({date, s, e, project, from = ''}) {
  const mine = logOpened, f = logForm.elements, was = f.note.value;
  logDrafting = mine; logDraftFrom = from; showDraft();
  const r = await postJSON('/api/likely/note', {date, s, e, project}).catch(() => ({}));
  if (logDrafting === mine) logDrafting = 0;
  if (mine !== logOpened || !logDlg.open) return;
  if (r.note && f.note.value === was) {
    f.note.value = r.note;
    logDrafted = {note: r.note, from: 'session', source: from}; logFilled = false;
  }
  showDraft();
  if (!r.note && r.error && !logDrafted) $('#log-note-hint').textContent = `No note from Claude: ${r.error}`;
}
function showDraft() {
  const d = logDrafted, sel = logPicker.selected;
  if (logEdit) {  // nothing is drafted for an entry already logged
    $('#log-note-hint').textContent = logEdit.noteRich ? 'This note has formatting in Productive. Editing it here saves it as plain text.' : '';
    return;
  }
  $('#log-note-hint').innerHTML = !d ? (logFilled ? esc('Filled in for you. Clients may see notes.')
                                        : logDrafting ? esc(`Claude is drafting a note from ${logDraftFrom || 'this session'}…`) : '')
    : d.from === 'commits' ? esc('From your commits' + (logDrafting ? `; Claude is drafting one from ${logDraftFrom || 'the session'}…` : '. Clients may see notes.'))
    : d.from === 'session' ? CLAUDE_MARK.split('<span')[0] + esc(`Drafted by Claude from ${d.source || 'this session'}. Clients may see notes.`)
    : CLAUDE_MARK.split('<span')[0] + esc(
    !sel || sel.taskId === d.taskId ? 'Drafted by Claude. Clients may see notes.'
      : `Drafted by Claude for “${d.title}”. Clients may see notes.`);
  $('#log-note-hint').classList.toggle('drafting', !!logDrafting);  // ask.css: Claude's mark twinkles while it drafts
}
logForm.elements.note.addEventListener('input', () => { if (logDrafted || logFilled || logDrafting) { logDrafted = null; logFilled = false; logDrafting = 0; showDraft(); } });
/* after an overlap ("Log anyway", or the pieces around it), any change to what would be logged means a new check */
function resetForce() {
  if (!logForce && !logSplit) return;
  logForce = false; logSplit = null; $('#log-anyway').hidden = true;
  $('#log-save').textContent = logEdit ? 'Save changes' : 'Log in Productive'; setLogMsg('');
}
/* the time asked for, less what is logged in it (server.pieces_around): the main button logs those pieces, one entry
   each with the same task, service and note; Log anyway stays, second */
let logSplit = null;  // {pieces: [{start, minutes}], tooShort}
const pieceText = p => `${hmText(p.start)}–${hm(+p.start.slice(0, 2) * 60 + +p.start.slice(3) + p.minutes)} (${dur(p.minutes)})`;
const andList = xs => xs.length < 2 ? xs.join('') : `${xs.slice(0, -1).join(', ')} and ${xs[xs.length - 1]}`;
/* the entries in the way: each one, or from three on their span and projects */
function overlapText(os) {
  if (os.length < 3) return os.map(o => `${o.start} ${dur(o.minutes)} ${o.project}`).join('; ');
  const a = Math.min(...os.map(o => +o.start.slice(0, 2) * 60 + +o.start.slice(3)));
  const b = Math.max(...os.map(o => +o.start.slice(0, 2) * 60 + +o.start.slice(3) + o.minutes));
  return `${os.length} entries from ${hm(a)} to ${hm(b)} (${dur(os.reduce((t, o) => t + o.minutes, 0))}): ${[...new Set(os.map(o => o.project))].join(', ')}`;
}
function showOverlap(r) {
  const them = r.overlap.length === 1 ? 'it' : 'them', pieces = !logEdit && r.pieces?.length ? r.pieces : null;
  logForce = !pieces; logSplit = pieces ? {pieces, tooShort: r.tooShort || 0} : null;
  $('#log-anyway').hidden = !pieces;
  $('#log-save').textContent = logEdit ? 'Save anyway' : !pieces ? 'Log anyway'
    : pieces.length === 1 ? `Log ${dur(pieces[0].minutes)} around ${them}` : `Log ${pieces.length} entries around ${them}`;
  setLogMsg((r.changed ? 'The day changed since you looked. ' : '')
    + `This overlaps ${overlapText(r.overlap)}.`
    + (pieces ? ` Around ${them}: ${andList(pieces.map(pieceText))}` + (r.tooShort ? `; ${dur(r.tooShort)} more is too short to log.` : '.')
       : r.tooShort ? ` The ${dur(r.tooShort)} around ${them} is too short to log on its own.` : ''), true);
}
for (const name of ['date', 'start', 'minutes', 'service'])
  for (const type of ['input', 'change']) logForm.elements[name].addEventListener(type, resetForce);
/* the services a task takes depend on the date (a budget has a period), and so do the suggestions:
   a new date reloads both. The rule hints belong to the time first chosen, so they go. */
logForm.elements.date.addEventListener('change', () => {
  const date = logForm.elements.date.value;
  if (!date) return;
  noStartHint(date);
  if (logPicker.selected) loadServices(logPicker.selected);
  fillSuggestions(date, []);
});

/* lead: a group offered first and picked: {label, tasks}, {label, ids} (the page knows only the tasks' ids), or
   {label, url} (the server works them out, each with why). drafted: the drafted note (logDrafted), when `note` is it */
/* while the dialog edits an entry that is already logged: {id, taskId, serviceId, service, budget, title, noteRich,
   seen (the entry as the page showed it), orig (the fields as the dialog opened)} */
let logEdit = null;
async function openLog({date = state.date, start = 9 * 60, minutes = 30, hint = [], note = '', lead = null, edit = null, drafted = null, range = false, exact = false, stretch = null} = {}) {
  logEdit = edit; logOpened++; logDrafting = 0;
  logStretchNow = stretch && !edit && date === state.date ? stretch : null;  // flow.js: Skip goes on from it
  $('#log-title').textContent = edit ? 'Edit entry' : 'Log time in Productive';
  logForm.elements.date.disabled = !!edit;  // an entry changes within its own day
  if (!CONFIG?.productive.active) { toast('Connect Productive in Settings first.', true); openSettings('t-conn'); return; }
  const f = logForm.elements;
  // inside the day: hm() would wrap 24:00 to 00:00. An entry shows the minute it starts in, as Productive does, and
  // its own length: the dialog never moves it by itself
  start = edit ? Math.max(0, Math.min(1439, Math.floor(start))) : Math.max(0, Math.min(1435, Math.round(start)));
  minutes = Math.max(1, Math.min(realMinutes(date, start, 1440), Math.round(minutes)));
  if (!edit && !exact) {  // a drag to the minute stays as it was picked
    const free = freeStart(date, start);  // a block that starts inside a logged entry starts where that entry ends
    if (free > start && minutes - (free - start) >= 1) { minutes -= free - start; start = free; }
    minutes = roundMinutes(date, start, minutes);
  }
  f.date.value = date; f.start.value = hm24(start); f.minutes.value = minutes; f.note.value = note || '';
  if (edit) edit.orig = {start: f.start.value, minutes, note: f.note.value};
  logDrafted = drafted?.note && drafted.note === note ? drafted : null;
  logFilled = !edit && !logDrafted && !!f.note.value.trim();
  f.service.innerHTML = '<option value="">Pick a task first</option>'; f.service.disabled = true;
  logForce = false; logSplit = null; $('#log-anyway').hidden = true;
  setLogMsg(''); $('#log-save').textContent = edit ? 'Save changes' : 'Log in Productive';
  noStartHint(date); showFlags(null);
  prepareNext(date, edit); showTaskLink(null);  // flow.js
  $('#log-newtask').hidden = !!edit;  // newtask.js: a new task is for new time, not for an entry being changed
  logDlg.showModal();
  logPicker.loading();
  showDraft();
  fillSuggestions(date, hint, lead);
  rangeOpen(range && !edit ? {date, start, minutes} : null);  // range.js: what a picked stretch holds
}

/* where a block's time is free: a start inside an entry already logged that day moves to that entry's end (and on
   past the next one, if they touch), so "Log this time" never opens on time that is logged */
function freeStart(date, start) {
  if (date !== state.date) return start;
  let s = start;
  for (let moved = true; moved;) {
    moved = false;
    for (const x of state.logged?.entries || []) if (x.s != null && x.e != null && x.s <= s && s < x.e) { s = Math.ceil(x.e); moved = true; }
  }
  return Math.min(s, 1435);
}
/* a block's minutes on the step of Settings' "Round entries to" (5, 10 or 15): the nearest step, at least one. Next
   to an entry already logged that day it runs up to that entry instead, whenever rounding would run into it or leave
   less than a step before it: those minutes are taken in, never left as a crumb "not logged". Minutes typed in the
   dialog are never rounded. */
function roundMinutes(date, start, minutes) {
  const step = CONFIG?.roundTo || 5;
  let m = Math.max(step, Math.round(minutes / step) * step);
  const next = date === state.date ? Math.min(1440, ...(state.logged?.entries || []).filter(x => x.s != null && x.s > start).map(x => x.s)) : 1440;
  const room = Math.floor(next - start);
  if (next < 1440 && room >= 1 && (m > room || room - m < step)) m = room;
  return Math.max(1, Math.min(m, 1440 - start));
}

/* the picked task's ticket flags (pulsar/flags.py): closed, no budget on the date, no estimate where the project's
   tasks have one; and over its estimate with the length in the form, worked out here so it follows that length
   (the same words as flags.over_estimate). Read only: one quiet line, and a link to fix it in Productive. */
let flagsShown = {task: null, flags: [], nums: null};
function showFlags(task, flags, nums) {
  flagsShown = {task, flags: flags || [], nums: nums || null};
  const el = $('#log-flags'), url = productiveTaskURL(task?.taskId);
  const texts = flagsShown.flags.map(f => f.text), over = overEstimate();
  if (over) texts.push(over);
  el.hidden = !texts.length;
  el.innerHTML = texts.length ? '! ' + texts.map(esc).join(' · ')
    + (url ? ` · <a href="${esc(url)}" target="_blank" rel="noopener noreferrer">Open in Productive</a>` : '') : '';
  burnLine();
}
/* how much of the task's estimate and of the service's time budget is used with this entry, always while they are
   known, so it shows before going over (Nick, 2026-11-10, as Timely does): "8 h 30 min of 10 h with this entry, 1 h 30
   min left", amber from flags.NEAR (80%). Over the estimate is the flag line's (overEstimate); over the budget is said
   here, as nothing else says it. The budget only where Productive lets this person read it (productive.budget_time). */
const BURN_NEAR = 0.8;
let logServices = [];
function burnLine() {
  const el = $('#log-burn'), n = flagsShown.nums, t = flagsShown.task, f = logForm.elements, mins = +f.minutes.value || 0;
  const mine = (id, field) => logEdit && id && String(logEdit[field]) === String(id) ? logEdit.minutes || 0 : 0;  // an edit's own minutes are counted already
  const parts = [];
  if (t && n?.estimate && n.worked != null) {
    const total = n.worked + mins - mine(t.taskId, 'taskId');
    if (total <= n.estimate)
      parts.push({near: total >= BURN_NEAR * n.estimate,
                  text: `Estimate: ${dur(total)} of ${dur(n.estimate)} with this entry, ${dur(n.estimate - total)} left`});
  }
  const svc = t ? logServices.find(x => x.id === f.service.value) : null, b = svc?.budgetTime;
  if (b?.budgeted && b.worked != null) {
    const total = b.worked + mins - mine(svc.id, 'serviceId');
    parts.push(total > b.budgeted
      ? {over: true, text: `Budget: +${dur(total - b.budgeted)} over the service's ${dur(b.budgeted)}`}
      : {near: total >= BURN_NEAR * b.budgeted, text: `Budget: ${dur(total)} of ${dur(b.budgeted)} with this entry, ${dur(b.budgeted - total)} left`});
  }
  el.hidden = !parts.length;
  el.innerHTML = parts.map(p => `<span class="${p.over ? 'over' : p.near ? 'near' : ''}">${esc(p.text)}</span>`).join('<br>');
}
logForm.elements.service.addEventListener('change', burnLine);

function overEstimate() {
  const n = flagsShown.nums, t = flagsShown.task, mins = +logForm.elements.minutes.value || 0;
  if (!n?.estimate || n.worked == null || !mins) return '';
  // an entry changed on its own task is in the time worked already
  const total = n.worked + mins - (logEdit && t && String(logEdit.taskId) === String(t.taskId) ? logEdit.minutes || 0 : 0);
  return total > n.estimate ? `+${dur(total - n.estimate)} over the estimate (${dur(total)} of ${dur(n.estimate)})` : '';
}
logForm.elements.minutes.addEventListener('input', () => { if (flagsShown.task) showFlags(flagsShown.task, flagsShown.flags, flagsShown.nums); });

/* entries of that day with no start time: the overlap check cannot see them, so say they are there (it is how a
   time already logged in Productive by duration only gets logged twice) */
function noStartHint(date) {
  const el = $('#log-nostart');
  const rows = date === state.date ? (state.logged?.entries || []).filter(x => x.s == null) : [];
  el.hidden = !rows.length;
  if (rows.length) el.textContent = `Also logged with no start time: ${dur(rows.reduce((s, x) => s + x.minutes, 0))} `
    + `(${[...new Set(rows.map(x => x.project))].join(', ')}). Check this time is not in it.`;
}

/* each request has a number: an answer that arrives after a newer request (the dialog reopened, the date changed)
   is dropped, so it can never pick its own task over the one on screen */
let suggestSeq = 0;
async function fillSuggestions(date, hint, lead = null) {
  const seq = ++suggestSeq;
  const [s, named, found] = await Promise.all([suggestions(date),
    lead?.ids?.length ? getJSON(`/api/tasks/estimates?ids=${lead.ids.join(',')}`) : null,
    lead?.url ? getJSON(lead.url) : null]);
  if (seq !== suggestSeq || !logDlg.open || logForm.elements.date.value !== date) return;
  const known = t => ({...((s.recent || []).find(x => x.taskId === t.taskId) || {}), ...t});
  const ruled = hint.map(h => ({...known(h), minutes: Math.round(h.minutes)}));
  const led = [...(lead?.ids || []).map(id => named?.tasks?.[id]).filter(t => t?.title).map(t => ({...t, why: t.why || lead.idsWhy || ''})),
               ...(lead?.tasks || []), ...(found?.tasks || [])].map(known);
  const ranged = rangeLead(date);  // range.js: Log the day's task for the work that holds most of the stretch
  logPicker.set(dedupe([
    ...(ranged ? [{...ranged, tasks: ranged.tasks.map(known)}] : []),
    {label: lead?.label || '', tasks: led},
    {label: 'Your rules', tasks: ruled, showMin: true},
    {label: 'You logged to these lately', tasks: (s.recent || []).slice(0, 10), note: s.historyError || ''},
    {label: 'Assigned to you', tasks: (s.assigned || []).slice(0, 10), note: s.assignedError || ''},
  ]), (ranged && known(ranged.tasks[0])) || led[0] || ruled[0] || null);
}

async function loadServices(task) {
  const f = logForm.elements, date = f.date.value;
  f.service.disabled = true; f.service.innerHTML = '<option value="">Loading services…</option>';
  const r = await getJSON(`/api/services?task=${task.taskId}&date=${date}&flags=1`);
  if (logPicker.selected?.taskId !== task.taskId || f.date.value !== date) return;  // a newer choice is loading
  showFlags(task, r.flags, r.task && {estimate: r.task.estimate, worked: r.task.worked});
  // an entry edited on its own task keeps its service, even one that takes no new time on this date: never another
  // picked for it
  const own = logEdit && task.taskId === logEdit.taskId && logEdit.serviceId
    ? {id: logEdit.serviceId, name: logEdit.service || 'Its service', budget: logEdit.budget || ''} : null;
  if (r.error && !own) { f.service.innerHTML = ''; setLogMsg(r.error, true); return; }
  let list = r.services || [];
  logServices = list;
  const ownGone = own && !list.some(x => x.id === own.id);
  if (ownGone) list = [{...own, gone: true}, ...list];
  if (!list.length) {  // the flag line says so (no budget on this date), with the link to Productive
    f.service.innerHTML = '<option value="">None takes time on this date</option>';
    if (!r.flags?.some(x => x.kind === 'nobudget')) setLogMsg("No service of this task's project takes time on this date. Ask the project's PM.", true);
    return;
  }
  // the service you used last on this task; else one of the same name in a newer budget; else, for a task just
  // created (newtask.js), the one its form picked; else the task's own
  const sug = await suggestions(date), recent = sug.recent?.find(t => t.taskId === task.taskId);
  // a task you never logged to: the service you used last in its project (the list is newest first)
  const near = recent ? null : sug.recent?.find(t => t.project && t.project === task.project);
  if (logPicker.selected?.taskId !== task.taskId || f.date.value !== date) return;
  const pick = (own ? list.find(x => x.id === own.id) : null)
    || list.find(x => x.id === recent?.serviceId)
    || list.find(x => recent?.service && x.name.trim() === recent.service.trim())
    || list.find(x => task.serviceHint && x.id === task.serviceHint)
    || list.find(x => x.id === r.task?.serviceId)
    || list.find(x => x.id === near?.serviceId) || list.find(x => near?.service && x.name.trim() === near.service.trim())
    || (list.length === 1 ? list[0] : null);
  f.service.innerHTML = (pick ? '' : '<option value="">Pick a service</option>') + list.map(x =>
    `<option value="${esc(x.id)}"${pick?.id === x.id ? ' selected' : ''}>${esc(x.name.trim())}${x.budget ? ' · ' + esc(x.budget) : ''}${
      x.billable ? ' · billable' : x.billable === false ? ' · not billable' : ''}${x.gone ? ' · takes no new time on this date' : ''}</option>`).join('');
  f.service.disabled = false;
  burnLine();
  // a blocked task can still take time (the blocker may be what you worked on), but say so before anything is written
  const said = [task.blocked ? `This task is ${(task.status || 'blocked').toLowerCase()} in Productive. You can still log to it.` : '',
                ownGone ? (r.error ? r.error + ' ' : '') + "Its service takes no new time on this date. It stays unless you pick "
                  + 'another; Productive may refuse a change.' : ''].filter(Boolean);
  setLogMsg(said.join(' '), said.length > 0);
}

logForm.addEventListener('submit', async ev => {
  ev.preventDefault();
  const f = logForm.elements, task = logPicker.selected, andNext = ev.submitter?.id === 'log-next';
  const anyway = ev.submitter?.id === 'log-anyway', pieces = !logEdit && !anyway ? logSplit?.pieces : null;
  if (!task) return setLogMsg('Pick a task.', true);
  if (!f.service.value) return setLogMsg('Pick a service.', true);
  const times = timesProblem(); if (times) return setLogMsg(times, true);  // flow.js: End, and past midnight
  const mins = Number(f.minutes.value);
  if (!(Number.isInteger(mins) && mins >= 1 && mins <= 1440)) {
    f.minutes.setAttribute('aria-invalid', 'true'); f.minutes.focus();
    return setLogMsg('Minutes are a whole number from 1 to 1440.', true);
  }
  f.minutes.removeAttribute('aria-invalid');
  if (!startHHMM(f.start.value)) { f.start.setAttribute('aria-invalid', 'true'); f.start.focus(); return setLogMsg('Write the start as a time: 09:30, 14:05 or 2:05 pm.', true); }
  f.start.value = startHHMM(f.start.value);
  const btn = $('#log-save'), btns = [btn, $('#log-next'), $('#log-anyway')], had = document.activeElement;
  btns.forEach(b => { b.disabled = true; }); setLogMsg(logEdit ? 'Checking the day, then saving…' : 'Checking the day, then logging…');
  const r = logEdit
    ? await postJSON('/api/entry/update', {id: logEdit.id, date: f.date.value, seen: logEdit.seen, force: logForce, ...editChanges(task)})
    : await postJSON('/api/log', {date: f.date.value, start: f.start.value, minutes: +f.minutes.value, taskId: task.taskId,
                                  serviceId: f.service.value, note: f.note.value.trim(), force: logForce || anyway,
                                  ...(pieces ? {pieces} : {})});
  btns.forEach(b => { b.disabled = false; });
  if (btns.includes(had) && !logDlg.contains(document.activeElement)) had.focus();  // a disabled button lost it
  if (r.error) {  // the next try checks overlaps again: the failed write may have landed
    resetForce();
    if (f.date.value === state.date) refreshLogged();
    return setLogMsg(r.error, true);
  }
  if (r.overlap) return showOverlap(r);
  if (r.notLogged) {  // some pieces were logged, then one was not: the next try offers only what is still free
    resetForce();
    r.created.forEach((_, i) => afterLog(f.date.value, r.pieces[i].start, r.pieces[i].minutes, '', false));
    if (f.date.value === state.date) refreshLogged();
    return setLogMsg(`Logged ${r.created.length} of ${r.pieces.length} entries: ${andList(r.pieces.slice(0, r.created.length).map(pieceText))}. `
      + (r.unclear ? 'No clear answer for the rest: it may be in Productive. Check the Logged column before logging again.'
         : `The rest was not logged: ${r.why} Log again to see what is still free.`), true);
  }
  logDlg.close(); suggestCache = null;
  if (r.unchanged) return toast('Nothing to change: the entry is already like this.');
  const done = Array.isArray(r.created) ? r.pieces : [{start: f.start.value, minutes: +f.minutes.value}];
  const total = done.reduce((t, p) => t + p.minutes, 0);
  const said = logEdit ? `Changed: ${f.start.value}, ${dur(+f.minutes.value)}, ${task.title}.`
    : pieces?.length === 1 ? `Logged ${dur(total)} to ${task.title}, ${pieceText(done[0]).split(' (')[0]}, around the time already logged.`
    : pieces ? `Logged ${dur(total)} to ${task.title} as ${done.length} entries around the time already logged: ${andList(done.map(pieceText))}.`
    : `Logged ${dur(total)} to ${task.title}.`;
  if (f.date.value === state.date) refreshLogged();
  // flow.js: Log and next goes on after the last piece; each one is left out of the next stretches
  done.slice(0, -1).forEach(p => afterLog(f.date.value, p.start, p.minutes, said, false));
  const last = done[done.length - 1];
  if (!logEdit && afterLog(f.date.value, last.start, last.minutes, said, andNext)) return;
  toast(said);
});
/* Edit sends only what was changed in the dialog, so an untouched start, note or service stays exactly as Productive
   has it (a note with formatting, a service that takes no new time, a start with seconds) */
function editChanges(task) {
  const f = logForm.elements, o = logEdit.orig, out = {};
  if (f.start.value !== o.start) out.start = f.start.value;
  if (+f.minutes.value !== o.minutes) out.minutes = +f.minutes.value;
  if (f.note.value.trim() !== o.note.trim()) out.note = f.note.value.trim();
  if (task.taskId !== logEdit.taskId) out.taskId = task.taskId;
  if (out.taskId || f.service.value !== logEdit.serviceId) out.serviceId = f.service.value;
  return out;
}
logDlg.querySelectorAll('[data-close]').forEach(b => b.onclick = () => logDlg.close());
logDlg.addEventListener('keydown', ev => {  // Cmd+Enter (Ctrl+Enter elsewhere) logs from anywhere in the dialog
  if (ev.key === 'Enter' && (ev.metaKey || ev.ctrlKey)) {  // with Shift: Log and next
    ev.preventDefault(); logForm.requestSubmit(ev.shiftKey && !$('#log-next').hidden ? $('#log-next') : null);
  }
});
$('#log-new').onclick = () => {
  const gap = document.querySelector('#lane-logged .gaprail');  // the first active stretch with no entry
  if (gap) { logStretch(+gap.dataset.a, +gap.dataset.b); return; }  // day.js
  const last = (state.logged?.entries || []).filter(x => x.e != null && x.e < 1440).reduce((m, x) => Math.max(m, x.e), 0);
  const start = last || (state.date === todayLocal() ? Math.max(0, nowMin() - 30) : 9 * 60);
  openLog({start, minutes: 30, hint: rulesInRange(start, start + 30), range: true});
};

/* drag in the Logged column to log that stretch */
(function dragToLog() {
  const lane = $('#lane-logged');
  let start = null, sel = null, y0 = 0;
  const DAY_END = 1440;  // in clock minutes; an entry cannot run past midnight
  const MOVED = 4;        // pixels: less is a click that slipped, not a drag
  const at = ev => Math.max(0, Math.min(DAY_END, Math.round((ev.clientY - lane.getBoundingClientRect().top) / ppmOf(state.zoom))));
  lane.addEventListener('pointerdown', ev => {
    if (ev.button !== 0 || ev.target !== lane) return;
    start = at(ev); y0 = ev.clientY;
    sel = document.createElement('div'); sel.className = 'sel'; lane.append(sel);
    lane.setPointerCapture(ev.pointerId); move(ev);
  });
  function move(ev) {
    if (start == null) return;
    const m = Math.abs(ev.clientY - y0) < MOVED ? start : at(ev), ppm = ppmOf(state.zoom);
    const a = Math.min(start, m), b = Math.max(start, m);
    Object.assign(sel.style, {top: a * ppm + 'px', height: Math.max(2, (b - a) * ppm) + 'px'});
    sel.dataset.a = a; sel.dataset.b = b;
    sel.textContent = b > a ? `${hm(a)}–${hm(b)} · ${dur(realMinutes(state.date, a, b))}` : '';
  }
  lane.addEventListener('pointermove', move);
  lane.addEventListener('pointerup', () => {
    if (start == null) return;
    const a = +sel.dataset.a, b = +sel.dataset.b;
    sel.remove(); start = null;
    if (b - a >= 1 && a >= 0 && b <= DAY_END) logStretch(a, b, true);  // day.js: the minutes picked, never rounded
  });
  lane.addEventListener('pointercancel', () => { if (start == null) return; sel.remove(); start = null; });
})();

/* ---------- the rule dialog ---------- */
const ruleDlg = $('#ruledlg'), ruleForm = $('#rule-form');
const rulePicker = makePicker($('#rule-tasks'), ruleForm.elements.q, () => {});
function previewRule() {
  const m = ruleForm.elements.match.value.trim().toLowerCase();
  const mins = m ? (state.act?.items || []).filter(it => itemText(it).includes(m)).reduce((s, it) => s + it.e - it.s, 0) : 0;
  $('#rule-preview').textContent = m ? `Matches ${dur(mins)} of ${dayLabel(state.date)}'s activity.` : '';
}
async function openRule(match = '') {
  ruleForm.elements.match.value = match; previewRule();
  $('#rule-msg').textContent = '';
  ruleDlg.showModal();
  rulePicker.loading();
  const s = await suggestions(state.date);
  rulePicker.set(dedupe([{label: 'You logged to these lately', tasks: (s.recent || []).slice(0, 12)},
                         {label: 'Assigned to you', tasks: (s.assigned || []).slice(0, 12)}]));
}
ruleForm.elements.match.addEventListener('input', previewRule);
ruleForm.addEventListener('submit', async ev => {
  ev.preventDefault();
  const match = ruleForm.elements.match.value.trim(), t = rulePicker.selected, msg = $('#rule-msg');
  if (!match || !t) { msg.textContent = 'Write the words to look for and pick a task.'; msg.className = 'msg warn'; return; }
  const rules = [...(CONFIG.rules || []).filter(r => r.match.toLowerCase() !== match.toLowerCase()),
                 {match, taskId: t.taskId, title: t.title, project: t.project}];
  if (await saveRules(rules)) {
    ruleDlg.close();
    toast(`Rule saved: “${match}” goes to ${t.title}.`);
    if (dlg.open) fillSettings(CONFIG);
  }
});
ruleDlg.querySelectorAll('[data-close]').forEach(b => b.onclick = () => ruleDlg.close());
