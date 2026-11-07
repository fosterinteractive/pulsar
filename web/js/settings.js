/* Settings: the Productive connection, time zone, daily target, personal windows, the Likely column */
const dlg = $('#settings'), form = $('#settings-form');

function connectionText(p) {
  if (!p.active) return p.problem || 'Productive is not connected.';
  const who = p.personName ? p.personName + (p.orgName ? `, ${p.orgName}` : '') : 'you';
  return `Connected as ${who}, through ${p.active === 'token' ? 'your API token' : "Claude Code's connection"}.`;
}

function fillSettings(c) {
  const f = form.elements, p = c.productive;
  f.productive_source.value = p.source;
  f.token.value = '';
  f.token.placeholder = p.tokenSaved ? 'A token is saved. Paste a new one to replace it.' : 'Paste a token from Productive';
  f.org_id.value = p.orgId || '';
  f.person_id.value = p.personId || '';
  f.timezone.value = c.timezoneSetting || '';
  f.timezone.placeholder = `This computer's: ${c.timezoneSystem || 'its UTC offset'}`;
  f.target_hours.value = c.targetHours ?? '';
  f.target_hours.placeholder = c.personDayHours ? `${c.personDayHours}, from Productive` : '8';
  f.round_to.value = String(c.roundTo || 5);
  f.away_after.value = String(c.awayAfter || 15);
  paintAw(c.activitywatch);
  f.personal_patterns.value = (c.personalPatterns || []).join('\n');
  f.activitywatch_url.value = c.activitywatchUrl || '';
  f.likely.value = c.likely.setting === 'off' ? 'off' : 'auto';
  f.likely.disabled = !c.likely.available;
  $('#likely-hint').textContent = c.likely.available ? '' : 'Shows when Claude Code sessions are on this computer.';
  $('#preview-sec').hidden = !c.preview;
  if (c.preview) {
    f.preview_writes.checked = !!c.preview.writes;
    $('#preview-status').textContent = `Branch ${c.preview.branch} at ${c.preview.commit}${c.preview.changed ? ' (with changes not committed)' : ''}: ${c.preview.subject}`;
  }
  f.ask_claude.checked = !!c.likely.askClaude;
  f.ask_claude.disabled = !c.likely.available || !c.likely.claudeFound;
  paintUses(c.claude?.uses || [], f.ask_claude.disabled);
  f.claude_ask_productive.checked = !!c.likely.askProductive;
  paintAskProductive();
  paintLink(c.likely);
  $('#remove-token').hidden = !p.tokenSaved;
  paintUpdate(c.update);
  paintCalendar(c);  // meetings.js
  const rules = c.rules || [];
  $('#rules-list').innerHTML = rules.length ? rules.map((r, i) => `<div class="rl"><span>“${esc(r.match)}”<small>${esc(r.title)} · ${esc(r.project)}</small></span>
    <button type="button" class="btn sm bordered" data-rule-del="${i}" aria-label="Delete the rule ${esc(r.match)}">Delete</button></div>`).join('')
    : '<p class="hint">No rules yet. Open an activity block in the Day view and press “Rule” next to a window.</p>';
  const st = $('#pd-status');
  st.textContent = connectionText(p);
  st.classList.toggle('warn', !p.active);
}

function paintWarp() {
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  $('#warp-on').checked = !warpOff() && !reduce;
  $('#warp-on').disabled = reduce;
  $('#warp-hint').textContent = reduce ? 'Off while macOS has Reduce motion on (System Settings > Accessibility > Display).'
    : 'Option-click the version at the top to see it.';
}
$('#warp-on').addEventListener('change', ev => {
  try { ev.target.checked ? localStorage.removeItem(WARP_KEY) : localStorage.setItem(WARP_KEY, 'off'); } catch (e) {}
});

/* tabs: Connections, Time and activity, Claude, Rules, Updates. The one last open opens again (a per-browser
   convenience; storage may be off, so every access is guarded). Arrow keys, Home and End move between them. */
const TAB_KEY = 'pulsar-settings-tab';
const settingsTabs = [...document.querySelectorAll('#settings [role="tab"]')];
function showTab(id, focus = false) {
  if (!settingsTabs.some(t => t.id === id)) id = 't-conn';
  for (const t of settingsTabs) {
    const on = t.id === id;
    t.setAttribute('aria-selected', String(on)); t.tabIndex = on ? 0 : -1;
    $('#' + t.getAttribute('aria-controls')).hidden = !on;
    if (on && focus) t.focus();
  }
  $('#settings .scrollarea').scrollTop = 0;
  try { localStorage.setItem(TAB_KEY, id); } catch { /* storage off: the first tab next time */ }
}
/* the tab a field is in, so a message about it can show it */
const tabOf = el => 't-' + (el.closest('[role="tabpanel"]')?.id || 'p-conn').slice(2);
for (const t of settingsTabs) t.onclick = () => { clearSearch(); showTab(t.id); };
$('#settings .tabs').addEventListener('keydown', ev => {
  const i = settingsTabs.indexOf(document.activeElement);
  if (i < 0) return;
  const to = {ArrowDown: i + 1, ArrowUp: i - 1, ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: settingsTabs.length - 1}[ev.key];
  if (to == null) return;
  ev.preventDefault();
  showTab(settingsTabs[(to + settingsTabs.length) % settingsTabs.length].id, true);
});

/* search: every section whose words (title, labels, hints, choices, and a few more in data-k) contain what was
   typed, from every category at once; categories with none fade in the sidebar. Escape clears it first. */
const settingsSearch = $('#settings-search');
function applySearch() {
  const q = settingsSearch.value.trim().toLowerCase();
  const panels = [...document.querySelectorAll('#settings [role="tabpanel"]')];
  $('#settings .side').classList.toggle('searching', !!q);  // results from every category: no one category is open
  if (!q) {
    for (const s of document.querySelectorAll('#settings section')) s.hidden = false;
    for (const t of settingsTabs) t.classList.remove('dim');
    $('#settings-nomatch').hidden = true;
    showTab(settingsTabs.find(t => t.getAttribute('aria-selected') === 'true')?.id);
    return;
  }
  let any = false;
  for (const panel of panels) {
    let hits = 0;
    for (const s of panel.querySelectorAll('section')) {
      const words = `${s.textContent} ${s.dataset.k || ''} ${[...s.querySelectorAll('[placeholder]')].map(x => x.placeholder).join(' ')}`;
      s.hidden = !words.toLowerCase().includes(q);
      hits += !s.hidden;
    }
    panel.hidden = !hits;
    settingsTabs.find(t => t.getAttribute('aria-controls') === panel.id)?.classList.toggle('dim', !hits);
    any = any || hits > 0;
  }
  $('#settings-nomatch').hidden = any;
  $('#settings .scrollarea').scrollTop = 0;
}
function clearSearch() { if (settingsSearch.value) { settingsSearch.value = ''; applySearch(); } }
settingsSearch.addEventListener('input', applySearch);
settingsSearch.addEventListener('keydown', ev => {
  if (ev.key === 'Escape' && settingsSearch.value) { ev.preventDefault(); clearSearch(); }
});

/* Unsaved changes: closing Settings (✕, Close, Escape, the error log) with a field changed and not saved asks first:
   Save, Discard or Keep editing. The search box and the warp switch (kept at once) are not changes. */
let settingsDirty = false;
form.addEventListener('input', markDirty); form.addEventListener('change', markDirty);
function markDirty(ev) { if (ev.target.name || ev.target.matches('#claude-uses select')) settingsDirty = true; }
function closeSettings(then) {
  if (!settingsDirty) { dlg.close(); then?.(); return; }
  const msg = $('#settings-msg');
  msg.className = 'msg warn ask-discard';
  msg.innerHTML = '<span>Your changes are not saved.</span> <button type="button" class="btn sm primary" data-sd="save">Save</button> '
    + '<button type="button" class="btn sm bordered" data-sd="discard">Discard</button> <button type="button" class="btn sm bordered" data-sd="keep">Keep editing</button>';
  msg.onclick = ev => {
    const k = ev.target.closest('[data-sd]')?.dataset.sd; if (!k) return;
    msg.onclick = null; msg.className = 'msg'; msg.textContent = '';
    if (k === 'save') saveSettings().then(() => { if (!dlg.open) then?.(); });
    if (k === 'discard') { settingsDirty = false; dlg.close(); then?.(); }
  };
  msg.querySelector('[data-sd="save"]').focus();
}
dlg.addEventListener('cancel', ev => { if (settingsDirty) { ev.preventDefault(); closeSettings(); } });
/* what quitting now would lose, in a sentence, or '' (the app's window asks before ⌘Q quits, native.js) */
window.pulsarUnsaved = () => {
  if (dlg.open && settingsDirty) return 'Your changes in Settings are not saved.';
  if (typeof logDlg !== 'undefined' && logDlg.open && !logEdit && logForm.elements.note.value.trim()) return 'The log dialog is open with a note that is not logged yet.';
  if (typeof LD !== 'undefined' && LD.writing) return 'Log the day is writing entries to Productive.';
  return '';
};

/* This Mac: the window's own choices (Pulsar.app 1.6 and later, native.js). Read from window.PULSAR_APP.native, then
   asked again (pulsarNative.nativePrefs); each change goes to the window at once (setNativePrefs), and the window
   answers with a pulsar:prefs event. Hidden in a browser and in an older window. */
const appNative = () => (window.PULSAR_APP && typeof window.PULSAR_APP === 'object' ? window.PULSAR_APP.native : null);
function paintApp(n = appNative()) {
  const sec = $('#app-sec'), can = !!(n && window.pulsarNative?.setNativePrefs);
  sec.hidden = !can;
  if (!can) return;
  $('#app-menubar-row').hidden = n.menubar === undefined;  // a window older than 1.6.3 has no menu bar item
  $('#app-menubar').checked = n.menubar !== false;
  $('#app-badge').checked = !!n.badge;
  $('#app-reminder').checked = !!n.reminder;
  $('#app-reminder-time').value = /^\d\d:\d\d$/.test(n.reminderTime || '') ? n.reminderTime : '17:30';
  $('#app-reminder-time').disabled = !n.reminder;
  const login = n.login, box = $('#app-login'), hint = $('#app-login-hint');
  box.checked = login === 'on' || login === 'approve';
  box.disabled = login === 'unsupported';
  hint.hidden = !['approve', 'unsupported'].includes(login);
  hint.innerHTML = login === 'approve'
    ? 'macOS has to allow it first. <button type="button" class="linkbtn" id="app-login-open">Open Login Items</button> and switch Pulsar on.'
    : login === 'unsupported' ? 'On this macOS, add it by hand: System Settings, General, Login Items, then + under Open at Login, and choose Pulsar.' : '';
}
async function refreshApp() {
  if (!appNative() || !window.pulsarNative?.nativePrefs) return paintApp();
  paintApp();
  const got = await window.pulsarNative.nativePrefs();
  if (got?.native) paintApp(got.native);
}
function setApp(change) { $('#app-msg').textContent = ''; window.pulsarNative?.setNativePrefs(change); }
$('#app-menubar').onchange = ev => setApp({menubar: ev.target.checked});
$('#app-badge').onchange = ev => setApp({badge: ev.target.checked});
$('#app-reminder').onchange = ev => { $('#app-reminder-time').disabled = !ev.target.checked; setApp({reminder: ev.target.checked}); };
$('#app-reminder-time').onchange = ev => { if (/^\d\d:\d\d$/.test(ev.target.value)) setApp({reminderTime: ev.target.value}); };
$('#app-login').onchange = ev => setApp({login: ev.target.checked});
$('#app-login-hint').addEventListener('click', ev => { if (ev.target.id === 'app-login-open') window.pulsarNative?.openLoginItems(); });
addEventListener('pulsar:prefs', ev => { if (ev.detail?.native) paintApp(ev.detail.native); });

function openSettings(tab) {
  if (!CONFIG) return;
  clearSearch();
  refreshApp();
  let last = null;
  try { last = localStorage.getItem(TAB_KEY); } catch { /* storage off */ }
  showTab(typeof tab === 'string' ? tab : last || 't-conn');
  fillSettings(CONFIG);
  settingsDirty = false; $('#settings-msg').onclick = null;
  paintWarp();
  /* the connection may have changed since the page loaded (a token renewed or expired): ask again, and refresh only
     what the person does not type into */
  getJSON('/api/config').then(c => {
    if (c.error || !dlg.open) return;
    applyConfig(c);
    const st = $('#pd-status');
    st.textContent = connectionText(c.productive); st.classList.toggle('warn', !c.productive.active);
    paintUpdate(c.update);
    paintCalendar(c);
    paintSpent(c.claude?.uses || []);
    paintAw(c.activitywatch);  // its file may have changed since the page loaded
  });
  const msg = $('#settings-msg'); msg.textContent = ''; msg.className = 'msg';
  const zones = $('#zones');
  if (!zones.childElementCount && Intl.supportedValuesOf) {
    for (const z of Intl.supportedValuesOf('timeZone')) { const o = document.createElement('option'); o.value = z; zones.append(o); }
  }
  dlg.showModal();
}

/* Away after, and ActivityWatch's own AFK timeout and Login Items (awsetup.py). ActivityWatch writes a pause only
   after its timeout, so one longer than Away after hides the shorter pauses: Fix sets it to Away after (and saves
   Away after) and restarts ActivityWatch. Login Items are asked through System Events, only on a click: the first
   time macOS asks whether Pulsar may. Shown when ActivityWatch is on this Mac. */
let awState = null;
const awMins = s => { const m = s / 60; return `${Number.isInteger(m) ? m : m.toFixed(1)} minute${m === 1 ? '' : 's'}`; };
function paintAw(a = awState) {
  awState = a;
  const box = $('#aw-setup');
  box.hidden = !a?.installed;
  if (!a?.installed) return;
  const m = +form.elements.away_after.value || 15, st = $('#aw-timeout'), long = a.seconds != null && a.seconds > m * 60;
  st.classList.toggle('warn', !!(a.problem || long));
  st.textContent = a.problem ? `Pulsar could not read ActivityWatch's AFK timeout in ${a.file}: ${a.problem}. It changes nothing there.`
    : long ? `ActivityWatch counts you as away only after ${awMins(a.seconds)}, so pauses from ${m} minutes to ${awMins(a.seconds)} are not in its data.`
    : a.seconds === m * 60 ? `ActivityWatch counts you as away after ${awMins(a.seconds)} too.`
    : `ActivityWatch counts you as away after ${awMins(a.seconds)}${a.fromFile ? '' : ' (its default)'}: a pause shorter than ${m} minutes between two stretches of work counts as work.`;
  $('#aw-fix-row').hidden = $('#aw-fix-hint').hidden = !long;
  $('#aw-fix').textContent = `Fix: set ActivityWatch to ${m} minutes`;
  const on = a.login, login = $('#aw-login');
  login.classList.toggle('warn', on === false);
  login.textContent = on === true ? 'ActivityWatch opens when you log in.'
    : on === false ? 'ActivityWatch is not in your Login Items: after a restart of the Mac it records nothing until you open it.'
    : 'Is ActivityWatch in your Login Items? Without it, it records nothing after a restart of the Mac until you open it.';
  $('#aw-login-check').hidden = on === true;
  $('#aw-login-add').hidden = on !== false;
  $('#aw-login-row').hidden = on === true;
  $('#aw-login-hint').hidden = on === true;
}
form.elements.away_after.addEventListener('change', () => paintAw());
async function awPost(path, body, busy) {
  const msg = $('#aw-msg'), btns = [...document.querySelectorAll('#aw-setup button')];
  for (const b of btns) b.disabled = true;
  msg.classList.remove('warn'); msg.textContent = busy;
  const r = await postJSON(path, body);
  for (const b of btns) b.disabled = false;
  if (r.error) { msg.classList.add('warn'); msg.textContent = r.error; return null; }
  if (CONFIG) { CONFIG.activitywatch = r.activitywatch; CONFIG.awayAfter = r.awayAfter; }
  paintAw(r.activitywatch);
  msg.classList.toggle('warn', !r.result.ok);
  // a check that worked is said by the Login Items line itself
  msg.textContent = r.result.ok && path.endsWith('/login') && !body.add ? '' : r.result.message;
  return r;
}
$('#aw-fix').onclick = async () => {
  const r = await awPost('/api/activitywatch/fix', {minutes: +form.elements.away_after.value},
    'Changing ActivityWatch’s timeout and restarting it. This can take up to a minute.');
  if (r && (r.result.changed || r.result.ok)) reloadView();  // Away after is saved with it
};
$('#aw-login-check').onclick = () => awPost('/api/activitywatch/login', {add: false}, 'Asking macOS. Answer its question if it asks one.');
$('#aw-login-add').onclick = () => awPost('/api/activitywatch/login', {add: true}, 'Adding ActivityWatch to your Login Items.');

/* "Ask before any Claude Code session changes Productive" adds its rules only with the switch on (claudelink.py):
   off, the box cannot be ticked, and one ticked earlier says it waits for the switch */
function paintAskProductive() {
  const f = form.elements, off = f.ask_claude.disabled || !f.ask_claude.checked;
  f.claude_ask_productive.disabled = off;
  $('#claude-ask-off').hidden = !(off && f.claude_ask_productive.checked);
}
form.elements.ask_claude.addEventListener('change', paintAskProductive);

/* the one Claude Code switch: where Pulsar stands in Claude Code (claudelink.py) */
function paintLink(l) {
  const k = l.link || {}, el = $('#claude-link'), again = $('#claude-relink');
  const on = !!l.askClaude, m = k.managed || {};
  el.classList.remove('warn');
  if (!l.claudeFound) el.textContent = 'Claude Code was not found on this Mac.';
  else if (!on) el.textContent = 'Turns on Claude’s task picks, notes and task drafts, and logging from a Claude Code session. Switching it off takes it all back.';
  else if (k.registered && k.current) {
    el.textContent = k.rules ? 'On. New Claude Code sessions can read your day and log through Pulsar, asking you first.'
      : 'On, but some of Pulsar’s rules are missing in Claude Code. Add them again below.';
    if (!k.rules) el.classList.add('warn');
  }
  else { el.textContent = k.registered ? 'On, but Claude Code starts an older copy of Pulsar.' : 'On, but Pulsar is not in Claude Code.';
         el.classList.add('warn'); }
  // what keeps Pulsar's rules or server from applying: an organization's managed settings, Claude Code's own folder
  const notes = [];
  if (on && m.permissionRulesOnly) notes.push('Your organization’s managed settings let only their own permission rules apply, so Claude Code ignores Pulsar’s ask and deny rules.');
  if (on && m.mcpServersOnly) notes.push('Your organization’s managed settings start only their own MCP servers, so Claude Code does not start Pulsar.');
  if (on && k.configDir) notes.push(`Claude Code’s folder is ${k.configDir} (CLAUDE_CONFIG_DIR): Pulsar uses it. A Claude Code started without that variable does not see Pulsar or its rules.`);
  if (notes.length) { el.textContent += ' ' + notes.join(' '); el.classList.add('warn'); }
  paintAccount(k, on);
  again.hidden = !(on && l.claudeFound && !(k.registered && k.current && k.rules));
}

/* Everything Pulsar asks Claude (claudeuses.py), one row each: what it does and sends, and a model to choose where
   Pulsar runs Claude itself. The limit of one run follows the model picked; the runs and cost since Pulsar started
   are what Claude Code said each run cost. */
const MODEL_NAMES = {haiku: 'Haiku', sonnet: 'Sonnet', opus: 'Opus'};
const money = x => x >= 0.995 ? `$${x.toFixed(2).replace(/\.00$/, '')}` : `$${x < 0.01 ? x.toFixed(3) : x.toFixed(2)}`;
let claudeUses = [];
function paintUses(uses, off) {
  claudeUses = uses;
  $('#claude-uses').innerHTML = uses.map(u => `<div class="cu" role="listitem" data-use="${esc(u.id)}">
    <div class="cu-t"><b>${esc(u.name)}</b> ${esc(u.does)}<details class="cu-s"><summary>What it sends</summary>${esc(u.sends)}</details></div>
    <div class="cu-m">${u.model ? `<select data-use="${esc(u.id)}" aria-label="Model for ${esc(u.name)}"${off ? ' disabled' : ''}>
      ${Object.keys(u.caps).map(m => `<option value="${esc(m)}"${m === u.model ? ' selected' : ''}>${esc(MODEL_NAMES[m] || m)}${m === u.default ? ' (default)' : ''}</option>`).join('')}
      </select><small class="cu-cap"></small><small class="cu-spent"></small>` : '<small>Your session’s own model</small>'}</div></div>`).join('');
  for (const sel of document.querySelectorAll('#claude-uses select')) { sel.onchange = () => paintCap(sel); paintCap(sel); }
  paintSpent(uses);
}
function paintCap(sel) {
  const cap = claudeUses.find(x => x.id === sel.dataset.use)?.caps?.[sel.value];
  sel.parentElement.querySelector('.cu-cap').textContent = cap ? `At most ${money(cap)} a run` : '';
}
function paintSpent(uses) {
  for (const u of uses) {
    const el = document.querySelector(`#claude-uses [data-use="${CSS.escape(u.id)}"] .cu-spent`);
    if (el) el.textContent = u.runs ? `${u.runs} ${u.runs === 1 ? 'run' : 'runs'}, ${money(u.spent)}, since Pulsar started` : '';
  }
}

/* which Claude account Pulsar's runs of Claude use (Claude Code's own sign-in, read from its .claude.json) */
function paintAccount(k, on) {
  const el = $('#claude-account'), a = k.account;
  el.classList.remove('warn'); el.replaceChildren();
  if (!on || !a) { el.hidden = true; return; }
  el.hidden = false;
  el.append(`Claude Code is signed in as ${a.email ? a.email + ', ' : ''}${a.plan}${a.consumer ? '' : '.'}`);
  if (a.consumer) {
    el.classList.add('warn');
    el.append(', a consumer plan: Anthropic may train on these chats if that is on for your account. ');
    const link = document.createElement('a');
    link.href = k.dataUsage; link.target = '_blank'; link.rel = 'noopener noreferrer'; link.textContent = 'How Claude Code uses data';
    el.append(link, '.');
  }
}

async function saveSettings(extra = {}) {
  const f = form.elements, msg = $('#settings-msg'), btn = $('#settings-save');
  // a number field holding "1e" reads as "": saving it would quietly drop the target back to the default
  const th = f.target_hours, v = th.value.trim();
  if (th.validity.badInput || (v !== '' && !(+v >= 0.5 && +v <= 24))) {
    msg.className = 'msg warn'; msg.textContent = 'The daily target is a number of hours from 0.5 to 24, or empty for the default. Nothing was saved.';
    showTab(tabOf(th)); th.focus(); return;
  }
  const body = {
    productive_source: f.productive_source.value, org_id: f.org_id.value.trim(), person_id: f.person_id.value.trim(),
    timezone: f.timezone.value.trim(), target_hours: f.target_hours.value.trim(), round_to: +f.round_to.value,
    away_after: +f.away_after.value,
    personal_patterns: f.personal_patterns.value,
    activitywatch_url: f.activitywatch_url.value.trim(), likely: f.likely.value, ask_claude: f.ask_claude.checked,
    claude_models: Object.fromEntries([...document.querySelectorAll('#claude-uses select')].map(x => [x.dataset.use, x.value])),
    claude_ask_productive: f.claude_ask_productive.checked,
    ...(CONFIG?.preview ? {preview_writes: f.preview_writes.checked} : {}), ...extra,
  };
  if (f.token.value.trim()) body.token = f.token.value.trim();
  btn.disabled = true; msg.className = 'msg'; msg.textContent = 'Saving…';
  const r = await postJSON('/api/settings', body);
  btn.disabled = false;
  if (r.error) { msg.className = 'msg warn'; msg.textContent = r.error; return; }
  applyConfig(r.config);
  fillSettings(r.config);
  settingsDirty = false;
  // Productive was asked again only if the connection changed. Saved and fine: the dialog closes, as Save does
  // everywhere; a connection that now fails keeps it open, with the reason, so it can be fixed at once.
  reloadView();
  if (r.test && !r.test.ok) {
    msg.className = 'msg warn'; msg.textContent = `Saved, but Productive: ${r.test.message}`;
    const st = $('#pd-status'); st.textContent = r.test.message; st.classList.add('warn');
    showTab('t-conn');
    return;
  }
  if (r.link && !r.link.ok) {  // the switch is saved; adding Pulsar to Claude Code (or taking it out) did not work
    msg.className = 'msg warn'; msg.textContent = `Saved, but ${r.link.message}`;
    showTab('t-claude');
    return;
  }
  dlg.close();
  toast(r.link ? `Settings saved. ${r.link.message}` : r.test ? `Settings saved. ${r.test.message}` : 'Settings saved.');
}

form.addEventListener('submit', ev => { ev.preventDefault(); saveSettings(); });
$('#remove-token').onclick = () => saveSettings({remove_token: true});
$('#claude-relink').onclick = () => saveSettings({relink: true});
$('#settings-cancel').onclick = $('#settings-x').onclick = () => closeSettings();
$('#open-settings').onclick = openSettings;

/* version and updates: what runs, since when, what is newer, and whether it installs by itself */
function paintUpdate(u) {
  if (!u) return;
  const offer = u.installed && (u.status === 'available' || u.status === 'ready');
  $('#upd-version').textContent = u.current + (CONFIG?.codename ? ` · ${CONFIG.codename}` : '');  // each minor version is a pulsar
  $('#upd-since').textContent = u.installed ? (u.since || '–') : `running from source (files changed ${u.since || '–'})`;
  $('#upd-checked').textContent = u.checked || 'not yet';
  const st = $('#update-status');
  st.textContent = u.message || (u.installed ? 'Up to date as far as Pulsar knows.' : 'This copy runs from source, so it does not update itself.');
  st.classList.toggle('warn', u.status === 'failed' || u.status === 'expired' || u.status === 'stale');
  const now = $('#update-now');
  now.hidden = !offer;
  now.textContent = u.status === 'ready' ? `Restart into ${u.latest}` : `Update to ${u.latest}`;
  const chip = $('#ver-chip');
  chip.textContent = `v${u.current}` + (offer || (u.status === 'available') ? ` · ${u.latest} available` : '');
  chip.classList.toggle('has-update', u.status === 'available' || u.status === 'ready');
  $('#open-settings').classList.toggle('has-update', u.status === 'available' || u.status === 'ready');
}
/* the server restarts within seconds, into the expected version or a newer one: any version other than the one
   this page started with means it is back. A minute without that, and it is time to say so. */
let waitingForVersion = null;
function waitForVersion() {
  clearInterval(waitingForVersion);
  const from = CONFIG.version, t0 = Date.now();
  waitingForVersion = setInterval(async () => {
    try {
      const r = await fetch('/api/version');
      const v = r.ok ? (await r.json()).version : null;
      if (v && v !== from) {
        clearInterval(waitingForVersion);
        dlg.close(); reopenSignedIn(v); return;  // main.js; the dialog closes first: it would not warp with the page
      }
    } catch (e) { /* still restarting */ }
    if (Date.now() - t0 > 60 * 1000) {
      clearInterval(waitingForVersion); warpRelease();
      toast('Pulsar did not come back. Close it and open it again.', true);
      const st = $('#update-status'); st.textContent = 'Pulsar did not come back. Close it and open it again.'; st.classList.add('warn');
    }
  }, 1500);
}
$('#update-check').onclick = async () => {
  const btn = $('#update-check'); btn.disabled = true; $('#update-status').textContent = 'Checking for a new version…';
  const r = await postJSON('/api/update/check', {});
  btn.disabled = false;
  if (r.error) { $('#update-status').textContent = r.error; return; }
  CONFIG.update = r.update; paintUpdate(r.update);
};
/* The warp follows the update: half way in while the new version downloads and tests itself, all the way in while
   Pulsar restarts, out once the new version has drawn its data (main.js). The dialog closes: it would not bend. */
$('#update-now').onclick = () => startUpdate();
async function startUpdate() {
  const btn = $('#update-now'), u = CONFIG.update; btn.disabled = true;
  try { sessionStorage.setItem('pulsar-update-app', '1'); } catch (e) {}  // native.js: the app too, once the code is in
  $('#update-status').textContent = u.status === 'ready' ? 'Restarting…' : `Downloading and checking Pulsar ${u.latest}…`;
  dlg.close();
  warpTo(u.status === 'ready' ? 1 : .45);
  const r = await postJSON(u.status === 'ready' ? '/api/update/apply' : '/api/update/install', {});
  btn.disabled = false;
  if (r.update) { CONFIG.update = r.update; paintUpdate(r.update); }
  if (!r.error && (r.ok || r.update?.status === 'ready')) {
    $('#update-status').textContent = 'Restarting into the new version…'; warpTo(1); waitForVersion(); return;
  }
  warpRelease();
  toast(r.error || r.update?.message || 'Pulsar was not updated.', true);
}

/* The reminder: when this copy is two or more releases behind, ask once every two days, until it is updated or that
   release is skipped (a newer one asks again). Pulsar never updates without a click. */
const REMIND_BEHIND = 2, REMIND_EVERY = 2 * 24 * 3600;
const remindDlg = $('#updprompt');
function maybeRemind(u = CONFIG?.update) {
  if (!u || !u.installed || u.status !== 'available' || (u.behind || 0) < REMIND_BEHIND) return;
  if (u.skip === u.latest || Date.now() / 1000 - (u.asked || 0) < REMIND_EVERY) return;
  if (signedOut || document.querySelector('dialog[open]')) return;
  $('#updprompt-title').textContent = `Pulsar ${u.latest} is out`;
  $('#updprompt-text').textContent = `You have ${u.current}, ${u.behind} releases behind. Updating takes a few seconds.`;
  $('#updprompt-notes').textContent = u.notes || '';
  remindDlg.showModal();
  const now = Math.floor(Date.now() / 1000);
  u.asked = now; postJSON('/api/settings', {update_asked: now});
}
$('#updprompt-go').onclick = () => { remindDlg.close(); startUpdate(); };
$('#updprompt-later').onclick = () => remindDlg.close();
$('#updprompt-skip').onclick = () => {
  const u = CONFIG.update; remindDlg.close(); u.skip = u.latest;
  postJSON('/api/settings', {update_skip: u.latest});
  toast(`Pulsar ${u.latest} skipped. A newer release will ask again.`);
};
/* what the server found since the page opened (it looks every few hours) */
setInterval(async () => {
  const c = await getJSON('/api/config');
  if (c.error || !CONFIG) return;
  CONFIG.update = c.update; paintUpdate(c.update); maybeRemind(c.update);
}, 3 * 3600 * 1000);
$('#ver-chip').onclick = ev => {
  if (ev.altKey) { warp(); return; }  // Option-click: the warp an update plays, to look at
  openSettings('t-upd');
};
$('#rule-add').onclick = () => openRule('');
$('#rules-list').addEventListener('click', async ev => {
  const b = ev.target.closest('[data-rule-del]'); if (!b) return;
  const rules = (CONFIG.rules || []).filter((_, i) => i !== +b.dataset.ruleDel);
  if (await saveRules(rules)) fillSettings(CONFIG);
});
document.addEventListener('click', ev => { if (ev.target.closest('[data-open-settings]')) openSettings('t-conn'); });

/* The changelog: this copy's CHANGELOG.md, read the first time it is opened. Each release folds. */
$('#changelog').addEventListener('toggle', async ev => {
  const box = $('#changelog-list');
  if (!ev.target.open || box.dataset.loaded) return;
  box.textContent = 'Loading…';
  const r = await getJSON('/api/changelog');
  if (r.error) { box.textContent = r.error; return; }  // not kept: opening it again asks again
  box.dataset.loaded = '1';
  const day = d => (validDate(d) ? new Date(d + 'T12:00:00Z').toLocaleDateString('en-GB',
    {day: '2-digit', month: 'short', year: 'numeric', timeZone: 'UTC'}) : '');
  box.innerHTML = (r.releases || []).map(x => `<details class="rel"><summary><b class="num">${esc(x.version)}</b>
    <span>${esc(x.codename)}</span><span class="when">${esc(day(x.date))}</span></summary>
    <ul>${x.lines.map(l => `<li>${esc(l)}</li>`).join('')}</ul></details>`).join('')
    || '<p class="hint">This copy has no changelog.</p>';
});
