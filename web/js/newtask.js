/* "Create a task" in the log dialog, for work no task fits (pulsar/newtask.py). A small form: the project, where in it
   the task goes (under the period's contingency task, in a task list, or directly in the project), and a title and
   description drafted from the work done in the dialog's time. Nothing reaches Productive without a click on "Create
   in Productive"; the new task is then picked in the log dialog, so the time can be logged to it. */
const taskDlg = $('#taskdlg'), taskForm = $('#task-form');
let taskOpened = 0;                  // which opening an answer belongs to: a late one never fills a newer form
let taskTyped = {title: false, description: false};  // typed in by the person: a draft never replaces it
let taskPlaces = null;               // the picked project's places (/api/task/places)
let taskDrafting = false;            // Claude is drafting

const setTaskMsg = (text, warn = false) => { const m = $('#task-msg'); m.textContent = text; m.className = 'msg' + (warn ? ' warn' : ''); };
const DRAFT_FROM = {
  commits: 'From your commits in this time: check it before creating.',
  meeting: "The meeting's title: check it before creating.",
  none: 'Nothing in this time says what the work was: write the title.',
};

/* the log dialog's time, in the page's minutes */
function taskRange() {
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

/* the confirmation line: what will be created, and where */
function showWhere() {
  const f = taskForm.elements, project = f.project.selectedOptions[0], place = f.place.value, title = f.title.value.trim();
  const where = !place ? '' : place === 'none' ? 'directly in the project, in no task list'
    : place.startsWith('parent:') ? `as a subtask of “${taskPlaces?.parents.find(p => 'parent:' + p.taskId === place)?.title || ''}”`
    : `in the task list “${taskPlaces?.lists.find(x => 'list:' + x.id === place)?.name || ''}”`;
  $('#task-where').textContent = project?.value && title && where
    ? `Creates “${title}” in ${project.dataset.name}, ${where}, assigned to you. A PM sets its service and estimate.` : '';
}

async function openNewTask() {
  const mine = ++taskOpened, f = taskForm.elements, {date, s, e} = taskRange();
  f.title.value = ''; f.description.value = ''; taskTyped = {title: false, description: false}; taskPlaces = null;
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
  const hint = logPicker.selected?.project || '';
  const r = await getJSON(`/api/task/projects?date=${date}&hint=${encodeURIComponent(hint)}&repo=${encodeURIComponent(draft.repo || '')}`);
  if (mine !== taskOpened || !taskDlg.open) return;
  fillProjects(r);
  if (f.project.value) loadPlaces();
  if (!CONFIG?.likely?.askClaude || !draft.repo || draft.from === 'meeting' || s == null) return;
  // with the Claude Code switch on: Claude (Haiku unless chosen) drafts from the session (typed, Claude's replies), commits
  taskDrafting = true; $('#task-draft-hint').classList.add('drafting');
  $('#task-draft-hint').innerHTML = esc((DRAFT_FROM[draft.from] || '') + ' Claude is drafting a better one…');
  const c = await postJSON('/api/task/draft', {date, s, e, claude: true, projectName: f.project.selectedOptions[0]?.dataset.name || ''});
  taskDrafting = false; $('#task-draft-hint').classList.remove('drafting');
  if (mine !== taskOpened || !taskDlg.open) return;
  if ((c.title || c.description) && !(taskTyped.title && taskTyped.description)) fillDraft(c, CLAUDE_MARK.split('<span')[0] + esc('Drafted by Claude from this session and your commits: check it. A client may see tasks.'));
  else fillDraft({}, esc(c.error ? `No draft from Claude: ${c.error}` : (DRAFT_FROM[draft.from] || '') + (c.why ? ` (Claude's draft: ${c.why}.)` : '')));
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
  const f = taskForm.elements, project = f.project.value, mine = taskOpened, date = logForm.elements.date.value;
  taskPlaces = null; f.place.disabled = true; f.place.innerHTML = '<option value="">Loading…</option>'; showWhere();
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
}

taskForm.elements.project.addEventListener('change', loadPlaces);
taskForm.elements.place.addEventListener('change', showWhere);
for (const name of ['title', 'description'])
  taskForm.elements[name].addEventListener('input', () => { taskTyped[name] = true; if (name === 'title') showWhere(); });

taskForm.addEventListener('submit', async ev => {
  ev.preventDefault();
  const f = taskForm.elements, title = f.title.value.replace(/\s+/g, ' ').trim(), place = f.place.value;
  if (!f.project.value) return setTaskMsg('Pick a project.', true);
  if (!place) return setTaskMsg('Pick where in the project the task goes.', true);
  if (!title) { f.title.focus(); return setTaskMsg('Write the task’s title.', true); }
  if (title.length > 140) { f.title.focus(); return setTaskMsg(`Productive takes titles of up to 140 characters; this one has ${title.length}.`, true); }
  const btn = $('#task-save'), [kind, id] = place.split(':');
  btn.disabled = true; setTaskMsg('Creating the task in Productive…');
  const r = await postJSON('/api/task/create', {projectId: f.project.value, parentId: kind === 'parent' ? id : '',
                                                taskListId: kind === 'list' ? id : '', title, description: f.description.value.trim()});
  btn.disabled = false;
  if (r.error) return setTaskMsg(r.error, true);
  taskDlg.close();
  suggestCache = null;  // log.js: the lists read Productive again
  logPicker.pick({...r.task, why: 'just created'});  // log.js: shown first and picked, its services load
  toast(`Created “${r.task.title}”${r.task.project ? ` in ${r.task.project}` : ''}. It is picked: check the service, then log.`);
});
taskDlg.querySelectorAll('[data-close]').forEach(b => b.onclick = () => taskDlg.close());
$('#log-newtask').onclick = openNewTask;
