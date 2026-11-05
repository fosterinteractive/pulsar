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
  f.personal_patterns.value = (c.personalPatterns || []).join('\n');
  f.activitywatch_url.value = c.activitywatchUrl || '';
  f.likely.value = c.likely.setting === 'off' ? 'off' : 'auto';
  f.likely.disabled = !c.likely.available;
  $('#likely-hint').textContent = c.likely.available ? '' : 'Shows when Claude Code sessions are on this computer.';
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
  toast(ev.target.checked ? 'The warp is on.' : 'The warp is off.');
});

function openSettings() {
  if (!CONFIG) return;
  fillSettings(CONFIG);
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
  });
  const msg = $('#settings-msg'); msg.textContent = ''; msg.className = 'msg';
  const zones = $('#zones');
  if (!zones.childElementCount && Intl.supportedValuesOf) {
    for (const z of Intl.supportedValuesOf('timeZone')) { const o = document.createElement('option'); o.value = z; zones.append(o); }
  }
  dlg.showModal();
}

async function saveSettings(extra = {}) {
  const f = form.elements, msg = $('#settings-msg'), btn = $('#settings-save');
  // a number field holding "1e" reads as "": saving it would quietly drop the target back to the default
  const th = f.target_hours, v = th.value.trim();
  if (th.validity.badInput || (v !== '' && !(+v >= 0.5 && +v <= 24))) {
    msg.className = 'msg warn'; msg.textContent = 'The daily target is a number of hours from 0.5 to 24, or empty for the default. Nothing was saved.';
    th.focus(); return;
  }
  const body = {
    productive_source: f.productive_source.value, org_id: f.org_id.value.trim(), person_id: f.person_id.value.trim(),
    timezone: f.timezone.value.trim(), target_hours: f.target_hours.value.trim(), personal_patterns: f.personal_patterns.value,
    activitywatch_url: f.activitywatch_url.value.trim(), likely: f.likely.value, ...extra,
  };
  if (f.token.value.trim()) body.token = f.token.value.trim();
  btn.disabled = true; msg.className = 'msg'; msg.textContent = 'Saving and checking Productive…';
  const r = await postJSON('/api/settings', body);
  btn.disabled = false;
  if (r.error) { msg.className = 'msg warn'; msg.textContent = r.error; return; }
  applyConfig(r.config);
  fillSettings(r.config);
  msg.className = 'msg' + (r.test.ok ? '' : ' warn');
  msg.textContent = (r.test.ok ? 'Saved. ' : 'Saved, but: ') + r.test.message;
  reloadView();
}

form.addEventListener('submit', ev => { ev.preventDefault(); saveSettings(); });
$('#remove-token').onclick = () => saveSettings({remove_token: true});
$('#settings-cancel').onclick = $('#settings-x').onclick = () => dlg.close();
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
  st.classList.toggle('warn', u.status === 'failed');
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
        try { sessionStorage.setItem('pulsar-updated', v); } catch (e) {}
        dlg.close(); await warpIn(); location.reload(); return;  // the dialog closes first: it would not warp with the page
      }
    } catch (e) { /* still restarting */ }
    if (Date.now() - t0 > 60 * 1000) {
      clearInterval(waitingForVersion); warpRelease();
      toast('Pulsar did not come back. Close it and open it again.', true);
      const st = $('#update-status'); st.textContent = 'Pulsar did not come back. Close and open it again.'; st.classList.add('warn');
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
  $('#updprompt-text').textContent = `This copy is ${u.current}, ${u.behind} releases behind. Updating takes a few seconds: `
    + 'Pulsar downloads the new version, checks it and restarts into it.';
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
  openSettings(); $('#updates').scrollIntoView({block: 'start'});
};
$('#rule-add').onclick = () => openRule('');
$('#rules-list').addEventListener('click', async ev => {
  const b = ev.target.closest('[data-rule-del]'); if (!b) return;
  const rules = (CONFIG.rules || []).filter((_, i) => i !== +b.dataset.ruleDel);
  if (await saveRules(rules)) fillSettings(CONFIG);
});
document.addEventListener('click', ev => { if (ev.target.closest('[data-open-settings]')) openSettings(); });

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
