/* The error log: a dialog to read it by period, a download to send to whoever maintains Pulsar, and the page's
   own errors sent to the same log. */
const logsDlg = $('#logsdlg');
let logsDays = 1;

async function openLogs(days = logsDays) {
  logsDays = days;
  document.querySelectorAll('#logs-range button').forEach(b => b.classList.toggle('on', +b.dataset.v === days));
  $('#logs-download').dataset.days = days;
  $('#logs-list').innerHTML = '<p class="hint">Loading…</p>';
  if (!logsDlg.open) logsDlg.showModal();
  const r = await getJSON(`/api/logs?days=${days}`);
  if (days !== logsDays) return;
  const list = r.entries || [];
  const when = t => new Intl.DateTimeFormat('en-GB', {day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: TZ}).format(new Date(t));
  $('#logs-list').innerHTML = r.error ? `<p class="status warn">${esc(r.error)}</p>`
    : !list.length ? `<p class="hint">No errors ${days === 1 ? 'in the last 24 hours' : `in the last ${days} days`}.</p>`
    : list.map(e => `<div class="lg"><div class="lg-h"><span class="lg-k ${e.kind === 'error' ? 'err' : ''}">${e.kind === 'error' ? 'Error' : 'Warning'}</span>
        <span class="num">${esc(when(e.time))}</span><span class="lg-w">${esc(e.where)}</span><span class="lg-v">v${esc(e.version || '?')}</span></div>
        <div class="lg-m">${esc(e.message)}</div>
        ${e.detail ? `<details><summary>Details</summary><pre>${esc(e.detail)}</pre></details>` : ''}</div>`).join('');
}

$('#open-logs').onclick = () => openLogs();
$('#settings-logs').onclick = () => { $('#settings').close(); openLogs(); };
document.querySelectorAll('#logs-range button').forEach(b => b.onclick = () => openLogs(+b.dataset.v));
logsDlg.querySelectorAll('[data-close]').forEach(b => b.onclick = () => logsDlg.close());

/* the page's own errors: at most 20 per page load, so a loop cannot flood the log */
let pageErrors = 0;
function reportPageError(message, source, line, stack) {
  if (++pageErrors > 20) return;
  postJSON('/api/client-error', {message: String(message || '').slice(0, 500), source, line, stack: String(stack || '').slice(0, 3000)});
}
addEventListener('error', ev => {  // script errors only: images that fail are handled in core.js
  if (ev instanceof ErrorEvent) reportPageError(ev.message, ev.filename, ev.lineno, ev.error?.stack);
});
addEventListener('unhandledrejection', ev => reportPageError(ev.reason?.message || ev.reason, '', 0, ev.reason?.stack));

$('#logs-download').addEventListener('click', ev => {
  ev.preventDefault();
  downloadFromAPI(`/api/logs/download?days=${ev.currentTarget.dataset.days || logsDays}`, 'pulsar-log.txt');
});
