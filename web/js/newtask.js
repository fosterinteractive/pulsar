/* "Create a task" in the log dialog, for work no task fits (pulsar/newtask.py). A small form: the project, where in it
   the task goes (under the period's contingency task, in a task list, or directly in the project), a title and
   description drafted from the work done in the dialog's time, and the status, estimate and service Pulsar proposes
   for that place (/api/task/fields). Nothing reaches Productive without a click on "Create in Productive"; the new task
   is then picked in the log dialog, with that service, so the time can be logged to it. The service is not sent with
   the task: a PM sets a task's service. */
const taskDlg = $('#taskdlg'), taskForm = $('#task-form');
const ESTIMATE_MAX = 500 * 60;       // minutes (productive.ESTIMATE_MAX)
let taskOpened = 0;                  // which opening an answer belongs to: a late one never fills a newer form
const TYPED = {title: false, description: false, status: false, estimate: false, service: false};
let taskTyped = {...TYPED};          // typed in or picked by the person: a draft or a proposal never replaces it
let taskPlaces = null;               // the picked project's places (/api/task/places)
let taskFields = null;               // the proposals for the picked place (/api/task/fields)
let taskDrafting = false;            // Claude is drafting

const setTaskMsg = (text, warn = false) => { const m = $('#task-msg'); m.textContent = text; m.className = 'msg' + (warn ? ' warn' : ''); };
const DRAFT_FROM = {
  commits: 'From your commits in this time: check it before creating.',
  meeting: "The meeting's title: check it before creating.",
  none: 'Nothing in this time says what the work was: write the title.',
};

/* the log dialog's time, in the page's minutes */
let taskFor = null;  // opened from a row of Log the day: {date, s, e, project, onCreated}; null from the log dialog
const taskDate = () => taskFor?.date || logForm.elements.date.value;
function taskRange() {
  if (taskFor) return {date: taskFor.date, s: taskFor.s, e: taskFor.e};
  const f = logForm.elements, st = startHHMM(f.start.value), s = st ? +st.slice(0, 2) * 60 + +st.slice(3) : null;
  return {date: f.date.value, s, e: s == null ? null : Math.min(1440, s + Math.max(1, +f.minutes.value || 0))};
}

function fillDraft(d, hint) {
  const f = taskForm.elements;
  if (d.title && !taskTyped.title) f.title.value = d.title;
  if ((d.title || d.description) && !taskTyped.description) f.description.value = d.description || '';
  $('#task-draft-hint').innerHTML = hint;
  showWhere();
}

/* an estimate as typed: "90" (minutes, as the log dialog's Minutes), "45m", "1h 30m", "1h30", "1.5 h"; null when
   empty, NaN when it is not a duration */
function parseEstimate(v) {
  const t = String(v ?? '').trim().toLowerCase();
  if (!t) return null;
  const m = t.match(/^(?:(\d+(?:[.,]\d+)?)\s*h(?:ours?|rs?)?)?\s*(?:(\d+)\s*(?:m|mins?|minutes?)?)?$/);
  if (!m || (m[1] == null && m[2] == null)) return NaN;
  return Math.round(parseFloat((m[1] || '0').replace(',', '.')) * 60 + +(m[2] || 0));
}

const parentTitle = place => taskPlaces?.parents.find(p => 'parent:' + p.taskId === place)?.title || '';

/* the confirmation line: everything that will be set, where, and the service this time then goes to */
function showWhere() {
  const f = taskForm.elements, project = f.project.selectedOptions[0], place = f.place.value, title = f.title.value.trim();
  const where = !place ? '' : place === 'none' ? 'directly in the project, in no task list'
    : place.startsWith('parent:') ? `as a subtask of “${parentTitle(place)}”`
    : `in the task list “${taskPlaces?.lists.find(x => 'list:' + x.id === place)?.name || ''}”`;
  if (!(project?.value && title && where)) { $('#task-where').textContent = ''; return; }
  const status = f.status.value ? `in “${f.status.selectedOptions[0]?.textContent || ''}”` : 'in its workflow’s first status';
  const est = parseEstimate(f.estimate.value), carried = taskFields?.estimate?.parent;
  const estimate = Number.isNaN(est) ? `with the estimate “${f.estimate.value.trim()}”, which is not a duration`
    : est != null ? `with an estimate of ${dur(est)}`
    : carried && place.startsWith('parent:') ? `with no estimate of its own (its parent carries ${dur(carried)})` : 'with no estimate';
  const service = f.service.value ? `this time then goes to “${f.service.selectedOptions[0]?.textContent || ''}”`
    : 'pick this time’s service when logging';
  $('#task-where').textContent = `Creates “${title}” in ${project.dataset.name}, ${where}, assigned to you, ${status}, `
    + `${estimate}. Its service is left to a PM; ${service}.`;
}

/* the proposals for the place picked (newtask.propose): a status, an estimate and the service for this time, each
   with why. What the person picked or typed stays; a status or service picked by hand stays while it is offered. */
function fillFields(r) {
  const f = taskForm.elements;
  taskFields = r && !r.error ? r : null;
  const statuses = taskFields?.statuses || [], services = taskFields?.services || [];
  const kept = (name, list) => taskTyped[name] && list.some(x => x.id === f[name].value) ? f[name].value : '';
  const st = kept('status', statuses) || taskFields?.status?.id || '';
  f.status.innerHTML = (st ? '' : `<option value="">${r ? 'Its workflow’s first' : 'Pick where it goes first'}</option>`)
    + statuses.map(x => `<option value="${esc(x.id)}"${x.id === st ? ' selected' : ''}>${esc(x.name)}</option>`).join('');
  if (!taskTyped.estimate) f.estimate.value = taskFields?.estimate?.minutes ? dur(taskFields.estimate.minutes) : '';
  const sv = kept('service', services) || taskFields?.service?.id || '';
  f.service.innerHTML = `<option value="">${services.length ? 'None: pick it when logging' : r ? 'None takes time on this date' : 'Pick where it goes first'}</option>`
    + services.map(x => `<option value="${esc(x.id)}"${x.id === sv ? ' selected' : ''}>${esc(x.name.trim())}${x.budget ? ' · ' + esc(x.budget) : ''}</option>`).join('');
  $('#task-fields-hint').textContent = !r ? '' : r.error ? `Nothing proposed: ${r.error}`
    : `Status: ${taskFields.status.why} Estimate: ${taskFields.estimate.why}`;
  $('#task-service-hint').textContent = taskFields ? `${taskFields.service.why} Not set on the task (a PM sets that): used only for this time.` : '';
  showWhere();
}

async function loadFields() {
  const f = taskForm.elements, project = f.project.value, place = f.place.value, mine = taskOpened, date = taskDate();
  if (!project || !place) return fillFields(null);
  $('#task-fields-hint').textContent = 'Finding the status, estimate and service…';
  const r = await getJSON(`/api/task/fields?project=${project}&date=${date}&place=${encodeURIComponent(place)}`
    + `&title=${encodeURIComponent(parentTitle(place))}`);
  if (mine !== taskOpened || f.project.value !== project || f.place.value !== place) return;  // a newer choice is loading
  fillFields(r);
}

/* from: {date, s, e, project, onCreated(task, serviceHint)} for a row of Log the day; nothing from the log dialog */
async function openNewTask(from = null) {
  taskFor = from && from.onCreated ? from : null;
  const mine = ++taskOpened, f = taskForm.elements, {date, s, e} = taskRange();
  f.title.value = ''; f.description.value = ''; f.estimate.value = ''; taskTyped = {...TYPED}; taskPlaces = null;
  fillFields(null);
  f.project.innerHTML = '<option value="">Loading your projects…</option>'; f.project.disabled = true;
  f.place.innerHTML = '<option value="">Pick a project first</option>'; f.place.disabled = true;
  $('#task-draft-hint').textContent = 'Drafting from the work in this time…'; $('#task-where').textContent = '';
  // read-only or Preview: said at once (the server refuses it as well); "nothing was written" is for after a try
  const off = CONFIG?.writesBlocked ? CONFIG.writesBlocked.replace(/[:,] (?:so )?nothing was written\./, '.') : '';
  setTaskMsg(off ? `Creating a task is off here. ${off}` : '', !!off);
  $('#task-save').disabled = false;
  taskDlg.showModal();
  // a meeting's title only when the time was a meeting and no work was seen in it (day.js)
  const sw = date === state.date && s != null ? stretchWork(s, e) : {work: null, meet: null};
  const meeting = !sw.work && sw.meet ? sw.meet.title || '' : '';
  const draft = s == null ? {from: 'none'} : await postJSON('/api/task/draft', {date, s, e, meeting});
  if (mine !== taskOpened || !taskDlg.open) return;
  fillDraft(draft, esc(draft.error ? `No draft: ${draft.error}` : DRAFT_FROM[draft.from] || ''));
  // the project of the task picked in the log dialog (the work's own task, a rule's or yours), else the repository's
  const hint = (taskFor ? taskFor.project : logPicker.selected?.project) || '';
  const r = await getJSON(`/api/task/projects?date=${date}&hint=${encodeURIComponent(hint)}&repo=${encodeURIComponent(draft.repo || '')}`);
  if (mine !== taskOpened || !taskDlg.open) return;
  fillProjects(r);
  if (f.project.value) loadPlaces();
  if (!CONFIG?.likely?.askClaude || !draft.repo || draft.from === 'meeting' || s == null) return;
  // with the Claude Code switch on: Claude (Haiku unless chosen) drafts from the session (typed, Claude's replies), commits.
  // Create waits for it: a draft that came in while the button could be clicked would put text nobody read into
  // Productive with that click. After a change, the button stays off a second more.
  const save = $('#task-save');
  taskDrafting = true; save.disabled = true; $('#task-draft-hint').classList.add('drafting');
  $('#task-draft-hint').innerHTML = esc((DRAFT_FROM[draft.from] || '') + ' Claude is drafting a better one; Create waits for it…');
  const c = await postJSON('/api/task/draft', {date, s, e, claude: true, projectName: f.project.selectedOptions[0]?.dataset.name || ''});
  taskDrafting = false; $('#task-draft-hint').classList.remove('drafting');
  if (mine !== taskOpened || !taskDlg.open) return;
  const changed = (c.title || c.description) && !(taskTyped.title && taskTyped.description);
  if (changed) fillDraft(c, CLAUDE_MARK.split('<span')[0] + esc('Drafted by Claude from this session and your commits: check it. A client may see tasks.'));
  else fillDraft({}, esc(c.error ? `No draft from Claude: ${c.error}` : (DRAFT_FROM[draft.from] || '') + (c.why ? ` (Claude's draft: ${c.why}.)` : '')));
  setTimeout(() => { if (mine === taskOpened) save.disabled = false; }, changed ? 1000 : 0);
}

function fillProjects(r) {
  const f = taskForm.elements, list = r.projects || [];
  const opt = p => `<option value="${esc(p.id)}" data-name="${esc(p.name)}"${p.id === r.pick ? ' selected' : ''}>${esc(p.name)}${p.lately ? ` · ${dur(p.lately)} lately` : ''}</option>`;
  const lately = list.filter(p => p.lately), rest = list.filter(p => !p.lately);
  f.project.innerHTML = list.length ? (r.pick ? '' : '<option value="">Pick a project</option>')
    + (lately.length ? `<optgroup label="You logged to these lately">${lately.map(opt).join('')}</optgroup>` : '')
    + (rest.length ? `<optgroup label="${lately.length ? 'Other active projects' : 'Active projects'}">${rest.map(opt).join('')}</optgroup>` : '')
    : '<option value="">No projects</option>';
  f.project.disabled = !list.length;
  if (r.error) setTaskMsg(r.error, true);
}

async function loadPlaces() {
  const f = taskForm.elements, project = f.project.value, mine = taskOpened, date = taskDate();
  taskPlaces = null; f.place.disabled = true; f.place.innerHTML = '<option value="">Loading…</option>'; fillFields(null);
  if (!project) { f.place.innerHTML = '<option value="">Pick a project first</option>'; return; }
  const r = await getJSON(`/api/task/places?project=${project}&date=${date}`);
  if (mine !== taskOpened || f.project.value !== project) return;  // a newer choice is loading
  taskPlaces = {parents: r.parents || [], lists: r.lists || []};
  f.place.innerHTML = (r.pick ? '' : '<option value="">Pick where it goes</option>')
    + taskPlaces.parents.map(p => `<option value="parent:${esc(p.taskId)}"${r.pick === 'parent:' + p.taskId ? ' selected' : ''}>Under “${esc(p.title)}” (a subtask)</option>`).join('')
    + taskPlaces.lists.map(x => `<option value="list:${esc(x.id)}"${r.pick === 'list:' + x.id ? ' selected' : ''}>In the task list “${esc(x.name)}”</option>`).join('')
    + `<option value="none"${r.pick === 'none' ? ' selected' : ''}>Directly in the project, no task list</option>`;
  f.place.disabled = false;
  if (r.error) setTaskMsg(r.error, true);
  showWhere();
  loadFields();
}

taskForm.elements.project.addEventListener('change', loadPlaces);
taskForm.elements.place.addEventListener('change', () => { showWhere(); loadFields(); });
for (const name of ['title', 'description', 'estimate'])
  taskForm.elements[name].addEventListener('input', () => { taskTyped[name] = true; if (name !== 'description') showWhere(); });
for (const name of ['status', 'service'])
  taskForm.elements[name].addEventListener('change', () => { taskTyped[name] = true; showWhere(); });

taskForm.addEventListener('submit', async ev => {
  ev.preventDefault();
  const f = taskForm.elements, title = f.title.value.replace(/\s+/g, ' ').trim(), place = f.place.value;
  if (!f.project.value) return setTaskMsg('Pick a project.', true);
  if (!place) return setTaskMsg('Pick where in the project the task goes.', true);
  if (!title) { f.title.focus(); return setTaskMsg('Write the task’s title.', true); }
  if (title.length > 140) { f.title.focus(); return setTaskMsg(`Productive takes titles of up to 140 characters; this one has ${title.length}.`, true); }
  const estimate = parseEstimate(f.estimate.value);
  if (Number.isNaN(estimate) || (estimate != null && !(estimate >= 1 && estimate <= ESTIMATE_MAX))) {
    f.estimate.focus();
    return setTaskMsg(`Write the estimate as minutes or hours, e.g. 90 or 1h 30m (up to ${ESTIMATE_MAX / 60} h), or leave it empty.`, true);
  }
  const btn = $('#task-save'), [kind, id] = place.split(':'), serviceHint = f.service.value;
  btn.disabled = true; setTaskMsg('Creating the task in Productive…');
  const r = await postJSON('/api/task/create', {projectId: f.project.value, parentId: kind === 'parent' ? id : '',
                                                taskListId: kind === 'list' ? id : '', title, description: f.description.value.trim(),
                                                statusId: f.status.value, estimate});
  // no clear answer (a timeout, a server error, or Pulsar itself did not answer): the task may exist, so the button
  // stays off until the dialog opens again (Pulsar refuses the same title for ten minutes too); search for it first
  if (r.error && (r.unclear || /^Server not responding/.test(r.error)))
    return setTaskMsg(/search for it/i.test(r.error) ? r.error
      : `${r.error} The task may exist: search for it in the task list before creating it again.`, true);
  btn.disabled = false;
  if (r.error) return setTaskMsg(r.error, true);
  taskDlg.close();
  suggestCache = null;  // log.js: the lists read Productive again
  const name = `“${r.task.title}”${r.task.project ? ` in ${r.task.project}` : ''}`;
  if (taskFor) {  // Log the day: the row takes the new task, and the service proposed for this time when it has it
    taskFor.onCreated({...r.task, why: 'just created'}, serviceHint);
    return toast(`Created ${name}. It is set on that entry: check the service before you log.`);
  }
  logPicker.pick({...r.task, why: 'just created', serviceHint});  // log.js: shown first and picked, with that service
  toast(`Created ${name}. It is picked: check the service, then log.`);
});
taskDlg.querySelectorAll('[data-close]').forEach(b => b.onclick = () => taskDlg.close());
$('#log-newtask').onclick = () => openNewTask();
