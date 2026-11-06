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
  // asked again when the last answer had no assigned tasks because Productive failed (the server tries again after a minute)
  if (!suggestCache || suggestCache.date !== date || suggestCache.data.assignedError) suggestCache = {date, data: await getJSON(`/api/tasks/suggest?date=${date}`)};
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
function makePicker(listEl, searchEl, onPick) {
  let selected = null, groups = [], base = [], timer, loading = false, searched = '';
  function draw() {
    const empty = loading ? 'Loading your tasks…' : searched ? `No task matches “${searched}”.` : 'No tasks to offer. Search for one above.';
    listEl.innerHTML = groups.filter(g => g.tasks.length || g.note).map(g => `<div class="pk-h">${esc(g.label)}</div>`
      + (g.note ? `<div class="note">${esc(g.note)}</div>` : '')
      + g.tasks.map(t => `<button type="button" class="pk${selected?.taskId === t.taskId ? ' on' : ''}" data-id="${esc(t.taskId)}"
          role="option" aria-selected="${selected?.taskId === t.taskId}"><span class="pk-t">${esc(t.title)}</span>
          <span class="pk-p">${esc(t.project)}${g.showMin && t.minutes ? ' · ' + dur(t.minutes) + ' here' : ''}${t.why ? ' · ' + (t.claude ? CLAUDE_MARK : '') + esc(t.why) : ''}${taskNote(t)}</span></button>`).join('')).join('')
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
    redraw(); if (selected) onPick(selected);
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
      const r = await getJSON(`/api/tasks/search?q=${encodeURIComponent(q)}`);
      listEl.classList.remove('busy');
      if (searchEl.value.trim() !== q) return;
      searched = r.error ? '' : q;
      groups = [{label: 'Search results', tasks: r.tasks || [], note: r.error || ''}]; redraw();
    }, 300);
  };
  return {
    /* a fresh start while the suggestions load: no search, no choice */
    loading() { loading = true; base = groups = []; selected = null; searched = ''; searchEl.value = ''; clearTimeout(timer); draw(); },
    /* the suggestions; a search typed meanwhile and a task picked from it stay */
    set(newGroups, pre) {
      loading = false; base = newGroups;
      if (searchEl.value.trim().length < 2) groups = base;
      if (!selected && pre) selected = pre; else pre = null;
      redraw(); if (pre) onPick(pre);
    },
    get selected() { return selected; },
  };
}

/* ---------- the log dialog ---------- */
const logDlg = $('#logdlg'), logForm = $('#log-form');
let logForce = false;
const setLogMsg = (text, warn = false) => { const m = $('#log-msg'); m.textContent = text; m.className = 'msg' + (warn ? ' warn' : ''); };
const logPicker = makePicker($('#log-tasks'), logForm.elements.q, t => { resetForce(); loadServices(t); showDraft(); showTaskLink(t); });
/* a drafted note until the person edits it: Claude's (Ask Claude, then Log on a Likely block) {note, taskId, title},
   or one from their commits {note, from: 'commits'} */
let logDrafted = null;
/* a note Pulsar filled in (a meeting's title, a repeat's usual note): it says so until the person edits it */
let logFilled = false;
let logDrafting = 0;  // the dialog's number while Claude drafts a note for it (draftFromSession), else 0
let logOpened = 0;    // how many times the dialog opened: a note that arrives late is only for the dialog that asked

/* With Claude Code on: Claude drafts the note of a Likely block from its session (what was typed, Claude's replies)
   and commits. It replaces the note only while the person has not typed in it, and only in the dialog that asked. */
async function draftFromSession({date, s, e, project}) {
  const mine = logOpened, f = logForm.elements, was = f.note.value;
  logDrafting = mine; showDraft();
  const r = await postJSON('/api/likely/note', {date, s, e, project}).catch(() => ({}));
  if (logDrafting === mine) logDrafting = 0;
  if (mine !== logOpened || !logDlg.open) return;
  if (r.note && f.note.value === was) {
    f.note.value = r.note;
    logDrafted = {note: r.note, from: 'session'}; logFilled = false;
  }
  showDraft();
}
function showDraft() {
  const d = logDrafted, sel = logPicker.selected;
  $('#log-note-hint').innerHTML = !d ? (logFilled ? esc('Filled in for you: clients may see notes on invoices; check it.')
                                        : logDrafting ? esc('Claude is drafting a note from this session…') : '')
    : d.from === 'commits' ? esc('From your commits' + (logDrafting ? '; Claude is drafting one from the session…' : ': check it before logging; clients may see notes.'))
    : d.from === 'session' ? CLAUDE_MARK.split('<span')[0] + esc('Drafted by Claude from this session and your commits: check it before logging; clients may see notes.')
    : CLAUDE_MARK.split('<span')[0] + esc(
    !sel || sel.taskId === d.taskId ? 'Drafted by Claude: check it before logging; clients may see notes.'
      : `Drafted by Claude for “${d.title}”: check it before logging; clients may see notes.`);
  $('#log-note-hint').classList.toggle('drafting', !!logDrafting);  // ask.css: Claude's mark twinkles while it drafts
}
logForm.elements.note.addEventListener('input', () => { if (logDrafted || logFilled || logDrafting) { logDrafted = null; logFilled = false; logDrafting = 0; showDraft(); } });
/* after "Log anyway", any change to what would be logged means a new check for overlaps */
function resetForce() {
  if (!logForce) return;
  logForce = false; $('#log-save').textContent = logEdit ? 'Save changes' : 'Log in Productive'; setLogMsg('');
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
let logEdit = null;  // {id, serviceId, title} while the dialog edits an entry that is already logged
async function openLog({date = state.date, start = 9 * 60, minutes = 30, hint = [], note = '', lead = null, edit = null, drafted = null} = {}) {
  logEdit = edit; logOpened++; logDrafting = 0;
  $('#log-title').textContent = edit ? 'Change a logged entry' : 'Log time in Productive';
  logForm.elements.date.disabled = !!edit;  // an entry changes within its own day
  if (!CONFIG?.productive.active) { toast('Connect Productive in Settings first.', true); openSettings('t-conn'); return; }
  const f = logForm.elements;
  start = Math.max(0, Math.min(1435, Math.round(start)));  // inside the day: hm() would wrap 24:00 to 00:00
  minutes = Math.max(1, Math.min(1440 - start, Math.round(minutes)));
  f.date.value = date; f.start.value = hm(start); f.minutes.value = minutes; f.note.value = note || '';
  logDrafted = drafted?.note && drafted.note === note ? drafted : null;
  logFilled = !edit && !logDrafted && !!f.note.value.trim();
  f.service.innerHTML = '<option value="">Pick a task first</option>'; f.service.disabled = true;
  logForce = false; setLogMsg(''); $('#log-save').textContent = edit ? 'Save changes' : 'Log in Productive';
  noStartHint(date);
  prepareNext(date, edit); showTaskLink(null);  // flow.js
  logDlg.showModal();
  logPicker.loading();
  showDraft();
  fillSuggestions(date, hint, lead);
}

/* entries of that day with no start time: the overlap check cannot see them, so say they are there (it is how a
   time already logged in Productive by duration only gets logged twice) */
function noStartHint(date) {
  const el = $('#log-nostart');
  const rows = date === state.date ? (state.logged?.entries || []).filter(x => x.s == null) : [];
  el.hidden = !rows.length;
  if (rows.length) el.textContent = `This day also has ${rows.length} ${rows.length === 1 ? 'entry' : 'entries'} with no start time `
    + `(${dur(rows.reduce((s, x) => s + x.minutes, 0))}: ${[...new Set(rows.map(x => x.project))].join(', ')}). `
    + 'They are not on the timeline, so check this time is not among them.';
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
  logPicker.set(dedupe([
    {label: lead?.label || '', tasks: led},
    {label: 'From your rules, in this time', tasks: ruled, showMin: true},
    {label: 'You logged to these lately', tasks: (s.recent || []).slice(0, 10)},
    {label: 'Assigned to you', tasks: (s.assigned || []).slice(0, 10), note: s.assignedError || ''},
  ]), led[0] || ruled[0] || null);
}

async function loadServices(task) {
  const f = logForm.elements, date = f.date.value;
  f.service.disabled = true; f.service.innerHTML = '<option value="">Loading services…</option>';
  const r = await getJSON(`/api/services?task=${task.taskId}&date=${date}`);
  if (logPicker.selected?.taskId !== task.taskId || f.date.value !== date) return;  // a newer choice is loading
  if (r.error) { f.service.innerHTML = ''; setLogMsg(r.error, true); return; }
  const list = r.services || [];
  if (!list.length) { f.service.innerHTML = ''; setLogMsg("No service of this task's project takes time on this date. Ask the project's PM.", true); return; }
  // the service you used last on this task; else one of the same name in a newer budget; else the task's own
  const recent = (await suggestions(date)).recent?.find(t => t.taskId === task.taskId);
  if (logPicker.selected?.taskId !== task.taskId || f.date.value !== date) return;
  const pick = (logEdit && task.taskId === logEdit.taskId ? list.find(x => x.id === logEdit.serviceId) : null)
    || list.find(x => x.id === recent?.serviceId)
    || list.find(x => recent?.service && x.name.trim() === recent.service.trim())
    || list.find(x => x.id === r.task?.serviceId) || (list.length === 1 ? list[0] : null);
  f.service.innerHTML = (pick ? '' : '<option value="">Pick a service</option>') + list.map(x =>
    `<option value="${esc(x.id)}"${pick?.id === x.id ? ' selected' : ''}>${esc(x.name.trim())}${x.budget ? ' · ' + esc(x.budget) : ''}${
      x.billable ? ' · billable' : x.billable === false ? ' · not billable' : ''}</option>`).join('');
  f.service.disabled = false;
  // a blocked task can still take time (the blocker may be what you worked on), but say so before anything is written
  setLogMsg(task.blocked ? `This task is ${(task.status || 'blocked').toLowerCase()} in Productive. You can still log to it.` : '',
            !!task.blocked);
}

logForm.addEventListener('submit', async ev => {
  ev.preventDefault();
  const f = logForm.elements, task = logPicker.selected, andNext = ev.submitter?.id === 'log-next';
  if (!task) return setLogMsg('Pick a task.', true);
  if (!f.service.value) return setLogMsg('Pick a service.', true);
  if (!startHHMM(f.start.value)) { f.start.setAttribute('aria-invalid', 'true'); f.start.focus(); return setLogMsg('Write the start as HH:MM, 24-hour: 09:30 or 14:05.', true); }
  f.start.value = startHHMM(f.start.value);
  const btn = $('#log-save'); btn.disabled = $('#log-next').disabled = true; setLogMsg(logEdit ? 'Checking the day, then saving…' : 'Checking the day, then logging…');
  const r = logEdit
    ? await postJSON('/api/entry/update', {id: logEdit.id, date: f.date.value, start: f.start.value, minutes: +f.minutes.value,
                                           taskId: task.taskId, serviceId: f.service.value, note: f.note.value.trim(), force: logForce})
    : await postJSON('/api/log', {date: f.date.value, start: f.start.value, minutes: +f.minutes.value, taskId: task.taskId,
                                  serviceId: f.service.value, note: f.note.value.trim(), force: logForce});
  btn.disabled = $('#log-next').disabled = false;
  if (r.error) {  // the next try checks overlaps again: the failed write may have landed
    logForce = false; btn.textContent = logEdit ? 'Save changes' : 'Log in Productive';
    if (f.date.value === state.date) refreshLogged();
    return setLogMsg(r.error, true);
  }
  if (r.overlap) {
    logForce = true; btn.textContent = logEdit ? 'Save anyway' : 'Log anyway';
    return setLogMsg('This overlaps ' + r.overlap.map(o => `${o.start} ${dur(o.minutes)} ${o.project}`).join('; ') + '.', true);
  }
  logDlg.close(); suggestCache = null;
  const said = logEdit ? `Changed: ${f.start.value}, ${dur(+f.minutes.value)}, ${task.title}.` : `Logged ${dur(+f.minutes.value)} to ${task.title}.`;
  if (f.date.value === state.date) refreshLogged();
  if (!logEdit && afterLog(f.date.value, f.start.value, +f.minutes.value, said, andNext)) return;  // flow.js: Log and next
  toast(said);
});
logDlg.querySelectorAll('[data-close]').forEach(b => b.onclick = () => logDlg.close());
logDlg.addEventListener('keydown', ev => {  // Cmd+Enter (Ctrl+Enter elsewhere) logs from anywhere in the dialog
  if (ev.key === 'Enter' && (ev.metaKey || ev.ctrlKey)) {  // with Shift: Log and next
    ev.preventDefault(); logForm.requestSubmit(ev.shiftKey && !$('#log-next').hidden ? $('#log-next') : null);
  }
});
$('#log-new').onclick = () => {
  const gap = document.querySelector('#lane-logged .gaprail');  // the first active stretch with no entry
  if (gap) { const a = +gap.dataset.a, b = +gap.dataset.b; openLog({start: a, minutes: b - a, hint: rulesInRange(a, b), lead: leadFor(a, b)}); return; }
  const last = (state.logged?.entries || []).filter(x => x.e != null && x.e < 1440).reduce((m, x) => Math.max(m, x.e), 0);
  const start = last || (state.date === todayLocal() ? Math.max(0, nowMin() - 30) : 9 * 60);
  openLog({start, minutes: 30, hint: rulesInRange(start, start + 30)});
};

/* drag in the Logged column to log that stretch */
(function dragToLog() {
  const lane = $('#lane-logged');
  let start = null, sel = null;
  const DAY_END = 1440;  // in clock minutes; an entry cannot run past midnight
  const at = ev => Math.max(0, Math.min(DAY_END, Math.round((ev.clientY - lane.getBoundingClientRect().top) / ppmOf(state.zoom) / 5) * 5));
  lane.addEventListener('pointerdown', ev => {
    if (ev.button !== 0 || ev.target !== lane) return;
    start = at(ev);
    sel = document.createElement('div'); sel.className = 'sel'; lane.append(sel);
    lane.setPointerCapture(ev.pointerId); move(ev);
  });
  function move(ev) {
    if (start == null) return;
    const m = at(ev), ppm = ppmOf(state.zoom);
    let a = Math.min(start, m), b = Math.max(start, m);
    if (b > a && b - a < 5) { b = Math.min(DAY_END, a + 5); a = b - 5; }
    Object.assign(sel.style, {top: a * ppm + 'px', height: Math.max(2, (b - a) * ppm) + 'px'});
    sel.dataset.a = a; sel.dataset.b = b;
    sel.textContent = b > a ? `${hm(a)}–${hm(b)} · ${dur(b - a)}` : '';
  }
  lane.addEventListener('pointermove', move);
  lane.addEventListener('pointerup', () => {
    if (start == null) return;
    const a = +sel.dataset.a, b = +sel.dataset.b;
    sel.remove(); start = null;
    if (b - a >= 5 && a >= 0 && b <= DAY_END) openLog({start: a, minutes: b - a, hint: rulesInRange(a, b), lead: leadFor(a, b)});
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
