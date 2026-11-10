/* Settings > Self-check (pulsar/selfcheck.py). Check (POST /api/selfcheck/check) works out the last 30 days on the
   server, in the background; this polls GET /api/selfcheck for its progress, then shows a few percents. Copy copies
   the server's line of percents (share): never a minute, a task, a client or a name. */

let scLast = null, scPoll = 0;
const scPct = v => (v == null ? 'n/a' : `${v}%`);
const SC_LOGGED = [['rightTask', 'Right task'], ['rightClient', 'Right client'], ['noTask', 'No task']];
const SC_BILLED = [['otherClient', 'Billable to another client'], ['over', 'Over'], ['under', 'Under']];

async function scLoad() {
  scPaint(await getJSON('/api/selfcheck'));
}

function scProgress(r) {
  if (r.step === 'learning') return 'Checking: waiting for Pulsar to finish learning from your past days…';
  if (r.step === 'windows') return `Checking: reading earlier days, ${r.done} of ${r.of}…`;
  return r.of ? `Checking: ${r.done} of ${r.of} days…` : 'Checking…';
}

function scPaint(r) {
  scLast = r;
  const st = $('#sc-status'), body = $('#sc-body'), btn = $('#sc-check');
  st.classList.remove('warn');
  btn.disabled = !!r.running;  // one Check at a time
  $('#sc-copy').hidden = !r.percents;
  if (r.error) { st.textContent = `The self-check could not be read: ${r.error}`; st.classList.add('warn'); body.innerHTML = ''; return; }
  if (!r.connected) { st.textContent = 'Connect Productive first: the self-check compares Pulsar\'s plan with what you logged.'; body.innerHTML = ''; return; }
  const note = r.last?.note || '';
  st.classList.toggle('warn', !r.running && !!r.last && !r.last.ok);
  st.textContent = r.running ? scProgress(r)
    : r.percents ? `From ${r.days} ${r.days === 1 ? 'day' : 'days'} with entries in the last 30.`
      + (r.days < r.minDays ? ' A few days only: the numbers settle with more.' : '') + (note ? ` ${note}` : '')
    : note || 'Check works out your last 30 days. The first time takes a few minutes; then only new or changed days.';
  const p = r.percents;
  const rows = list => list.map(([k, label]) => `<div class="kv"><span>${esc(label)}</span><span class="num">${esc(scPct(p[k]))}</span></div>`).join('');
  const lt = r.logTheDay;
  body.innerHTML = !p ? '' : `<h4 class="sc-h">Of the minutes you logged</h4>${rows(SC_LOGGED)}
    <h4 class="sc-h">Of the billable minutes you logged</h4>${rows(SC_BILLED)}
    ${lt ? `<p class="hint">${esc(`Log the day rows logged as proposed: ${lt.asProposed}%, another task ${lt.taskChanged}%, another time ${lt.timeChanged}%.`)}</p>` : ''}
    ${(r.rules || []).map(x => `<p class="hint">${esc(`${x.name} (a rule being tried, not used yet): right task ${scPct(x.withRule.rightTask)} `
      + `(now ${scPct(x.now.rightTask)}), right client ${scPct(x.withRule.rightClient)} (now ${scPct(x.now.rightClient)}).`)}</p>`).join('')}`;
  clearTimeout(scPoll);
  if (r.running && $('#settings').open) scPoll = setTimeout(scLoad, 1000);
}

$('#sc-check').addEventListener('click', async () => {
  if (scLast?.running) return;  // one Check at a time: a second click does nothing
  $('#sc-msg').textContent = '';
  scPaint(await postJSON('/api/selfcheck/check', {}));
});

$('#sc-copy').addEventListener('click', async () => {
  if (!scLast?.share) return;
  const text = scLast.share, msg = $('#sc-msg');
  try {
    await navigator.clipboard.writeText(text);
  } catch {  // no clipboard access: the old way, from a selected field
    const ta = document.createElement('textarea');
    ta.value = text; ta.setAttribute('readonly', ''); ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.append(ta); ta.select();
    try { document.execCommand('copy'); } catch { msg.textContent = 'Could not copy.'; ta.remove(); return; }
    ta.remove();
  }
  msg.textContent = 'Copied: the percents only.';
});
