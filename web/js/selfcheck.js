/* Settings > Self-check > How well Pulsar picks for you (pulsar/selfcheck.py). For each past day the server
   works out, in the background, the plan Log the day would make without that day's entries, and compares it with what
   was logged. This shows the totals (GET /api/selfcheck), copies them as text and saves the file (GET
   /api/selfcheck/download): numbers only, never a task, client, project, note or window. */

let scLast = null;
const scPct = (a, b) => (b ? `${Math.round(a / b * 100)}%` : 'n/a');
/* the measures, as rows: label, what it means, its value from a sum of days (selfcheck.totals), a share or minutes */
const SC_ROWS = [
  {label: 'Right task', what: 'Of the minutes you logged, the plan had the same task', key: 'rightTask', kind: 'pct'},
  {label: 'Right client', what: 'The same project, or both internal', key: 'rightClient', kind: 'pct'},
  {label: 'No task', what: 'Minutes you logged where the plan had no task', key: 'noTask', kind: 'pct'},
  {label: 'Billed to another client', key: 'wrongClient', kind: 'min',
   what: 'Minutes the plan billed to a client while you logged them to another client or to internal work'},
  {label: 'Billed over', what: 'Billable time proposed for a client beyond what you logged to it that day', key: 'over', kind: 'min'},
  {label: 'Billed under', what: 'Billable time you logged to a client that the plan did not propose', key: 'under', kind: 'min'},
];
/** @param {{key: string, kind: string}} row @param {any} t */
const scVal = (row, t) => (row.kind === 'pct' ? scPct(t[row.key], t.logged) : dur(t[row.key]));
const SC_RULES = [['activity', 'Productive activity'], ['clients', 'Client tasks by window']];

async function scLoad() {
  const r = await getJSON('/api/selfcheck');
  scPaint(r);
}

/* a rule's value beside the plan's own on the same days: "55% (+1 pt)", "3 h 5 min (+5 min)" */
function scVersus(sh, row) {
  const b = sh.base;
  if (!sh.days) return 'n/a';
  let diff;
  if (row.kind === 'pct') {
    const d = b.logged ? Math.round(sh[row.key] / b.logged * 100) - Math.round(b[row.key] / b.logged * 100) : 0;
    diff = d ? `${d > 0 ? '+' : '−'}${Math.abs(d)} pt` : 'same';
  } else {
    const d = sh[row.key] - b[row.key];
    diff = d ? `${d > 0 ? '+' : '−'}${dur(Math.abs(d))}` : 'same';
  }
  return `${esc(scVal(row, sh))} <small>${esc(diff)}</small>`;
}

function scLogLine(l) {
  if (!l?.entries) return '';
  const known = l.viaLogTheDay - l.unknown;
  return `Of your ${l.entries} entries, ${l.viaLogTheDay} came through Log the day`
    + (known ? `: ${scPct(l.asProposed, known)} as proposed, ${scPct(l.taskChanged, known)} with another task, `
      + `${scPct(l.timeChanged, known)} another time, ${scPct(l.serviceChanged, known)} another service` : '')
    + `. ${l.byHand} added another way (the log dialog or Productive).`
    + (l.unknown ? ` ${l.unknown} say nothing of what was proposed: logged before Pulsar kept it, or from Claude Code.` : '');
}

function scPaint(r) {
  scLast = r;
  const st = $('#sc-status'), body = $('#sc-body');
  st.classList.remove('warn');
  $('#sc-msg').textContent = '';
  const some = !r.error && r.connected && r.days > 0;
  $('#sc-copy').hidden = $('#sc-save').hidden = $('#sc-save-hint').hidden = !some;
  if (r.error) { st.textContent = `The self-check could not be read: ${r.error}`; st.classList.add('warn'); body.innerHTML = ''; return; }
  if (!r.connected) { st.textContent = 'Connect Productive first: the self-check compares the plan with what you logged.'; body.innerHTML = ''; return; }
  const left = r.todo ? ` ${r.todo} more ${r.todo === 1 ? 'day' : 'days'} to work out, in the background.` : '';
  st.textContent = r.collecting
    ? `Still collecting: ${r.days} of the ${r.minDays} days it needs so far.${left || ' Days with entries are worked out in the background.'}`
    : `From ${r.days} days with entries in the last 30.${left}`
      + (r.older ? ` ${r.older} of them ${r.older === 1 ? 'was' : 'were'} worked out by an earlier version and ${r.older === 1 ? 'is' : 'are'} being worked out again.` : '');
  const m = r.month, w = r.week;
  const table = r.collecting ? '' : `<table class="sc-t" aria-label="How well Pulsar picks, by measure">
    <thead><tr><th scope="col"><span class="vh">Measure</span></th><th scope="col">Last 30 days</th>${w ? '<th scope="col">Last 7 days</th>' : ''}</tr></thead>
    <tbody>${SC_ROWS.map(row => `<tr><th scope="row">${esc(row.label)}<small>${esc(row.what)}</small></th>
      <td>${esc(scVal(row, m))}</td>${w ? `<td>${esc(scVal(row, w))}</td>` : ''}</tr>`).join('')}</tbody></table>
    <p class="hint">${esc(`${dur(m.logged)} logged with a start time, ${dur(m.billableLogged)} of it billable.`
      + (m.untimed ? ` ${dur(m.untimed)} logged without a start is left out of the minute by minute measures.` : '')
      + (r.noActivity ? ` ${r.noActivity} ${r.noActivity === 1 ? 'day' : 'days'} with no activity recorded left out.` : ''))}</p>`;
  const rules = r.collecting ? '' : `<details class="more" id="sc-rules"><summary>Two rules being tried, never used in your plans</summary>
    <p class="hint">Productive activity: an unsure pick gives way to an alternative you commented on or changed in Productive
      within 4 hours of it. Client tasks by window: work outside Claude Code may also take a client's task you logged
      before while the same window was in front, not only an internal one. Under each figure, the difference from the plan as it is, on the same days.</p>
    <table class="sc-t" aria-label="The rules being tried, beside the plan as it is">
      <thead><tr><th scope="col"><span class="vh">Measure</span></th><th scope="col">Now</th>${SC_RULES.map(([, name]) => `<th scope="col">${esc(name)}</th>`).join('')}</tr></thead>
      <tbody>${SC_ROWS.map(row => `<tr><th scope="row">${esc(row.label)}</th><td>${esc(scVal(row, m))}</td>
        ${SC_RULES.map(([k]) => `<td>${scVersus(m.shadow[k], row)}</td>`).join('')}</tr>`).join('')}</tbody></table></details>`;
  body.innerHTML = table + (scLogLine(r.log) ? `<p class="hint">${esc(scLogLine(r.log))}</p>` : '') + rules;
}

/* the totals as plain text, for the person to paste where they choose: no task, client or project names */
function scText(r) {
  const line = t => SC_ROWS.map(row => `${row.label} ${scVal(row, t)}`).join(', ');
  const out = [`Pulsar ${r.version}, how well it picks, ${r.from} to ${r.to} (computed on my Mac).`,
    `Last 30 days (${r.month.days} days): ${line(r.month)}. ${dur(r.month.billableLogged)} billable logged.`];
  if (r.week) out.push(`Last 7 days (${r.week.days} days): ${line(r.week)}.`);
  if (scLogLine(r.log)) out.push(scLogLine(r.log));
  for (const [k, name] of SC_RULES) {
    const sh = r.month.shadow[k];
    if (sh.days) out.push(`Rule being tried, ${name} (${sh.days} days): ${line(sh)}; now ${line(sh.base)}.`);
  }
  return out.join('\n');
}

$('#sc-copy').addEventListener('click', async () => {
  if (!scLast || scLast.error) return;
  const text = scText(scLast), msg = $('#sc-msg');
  try {
    await navigator.clipboard.writeText(text);
  } catch {  // no clipboard access: the old way, from a selected field
    const ta = document.createElement('textarea');
    ta.value = text; ta.setAttribute('readonly', ''); ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.append(ta); ta.select();
    try { document.execCommand('copy'); } catch { msg.textContent = 'Could not copy. Save the self-check instead.'; ta.remove(); return; }
    ta.remove();
  }
  msg.textContent = 'Copied: the totals only.';
});
$('#sc-save').addEventListener('click', () => downloadFromAPI('/api/selfcheck/download', 'pulsar-selfcheck.json'));
