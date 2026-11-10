/* Pause (pulsar/pause.py): "I'm stepping away". From the click until Resume nothing counts as work: the Day view hatches
   it in amber and has no rail to log there, Log the day proposes nothing there, and the Report, the reminder and the
   Claude Code tools leave it out. The trackers go on recording. Resume writes the paused time to each day's "not work"
   marks (a click on it counts it as work again). Paused for Settings' "Ask about a long pause" with work seen since,
   Pulsar asks when the window comes to the front whether the pause is still on, and offers to resume from when you came
   back. The button at the top shows which it is; the Dock icon's menu has Pause and Resume too (native.js). */
let PAUSE = {paused: false};
let pauseAskAt = 0;
const pauseDlg = $('#pauseprompt');

/* a moment in Pulsar's time zone: its day and its minute of the day */
function tzMoment(sec) {
  const p = Object.fromEntries(new Intl.DateTimeFormat('en-GB', {timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23'}).formatToParts(new Date(sec * 1000)).map(x => [x.type, x.value]));
  return {date: `${p.year}-${p.month}-${p.day}`, min: +p.hour * 60 + +p.minute};
}

/* the pause still on, on the day shown: [[a, b]] in minutes of the day, up to now on today */
function pausedSpans() {
  if (!PAUSE.paused || !PAUSE.since) return [];
  const from = tzMoment(PAUSE.since), today = todayLocal(), d = state.date;
  if (d < from.date || d > today) return [];
  const a = d === from.date ? from.min : 0, b = d === today ? nowMin() : 1440;
  return b > a ? [[a, b]] : [];
}

function paintPause() {
  const b = $('#pause-btn'), on = !!PAUSE.paused;
  b.classList.toggle('on', on);
  b.setAttribute('aria-pressed', String(on));
  $('#pause-label').textContent = on ? `Paused since ${PAUSE.sinceText}` : 'Pause';
  b.title = on ? `Paused since ${PAUSE.sinceText}: nothing counts as work. Click to resume.`
    : 'Pause: from now Pulsar counts nothing as work, until you resume';
  dispatchEvent(new Event('pulsar:pause'));  // native.js: the Dock icon's menu and the menu bar say it too
}

async function readPause(ask = 0) {
  const r = await getJSON(`/api/pause${ask ? `?ask=${ask}` : ''}`);
  if (r.error) return null;
  const was = PAUSE.paused;
  PAUSE = r;
  paintPause();
  if (was !== r.paused && state.view === 'day') render();
  return r;
}

/* after a change: the day's marks again (Resume wrote some), the day redrawn, the Report read again */
async function afterPause(r) {
  PAUSE = {paused: r.paused, since: r.since, sinceText: r.sinceText, minutes: r.minutes};
  paintPause();
  wroteAt = Date.now();
  const d = state.date;
  if (r.marked && r.marked[d]) {
    const m = await getJSON(`/api/daymarks?date=${d}`);
    if (!m.error && d === state.date) state.marks = {...m, date: d};
  }
  if (state.view === 'day') { render(); if (ldActive()) renderReview(); }
}

async function startPause() {
  const r = await postJSON('/api/pause', {on: true});
  if (r.error) { toast(r.error, true); return; }
  await afterPause(r);
  toast('Paused: from now nothing counts as work. Resume when you are back.');
}

/* resume at `until` (seconds; now when not given) */
async function resumePause(until) {
  const since = PAUSE.sinceText;
  const r = await postJSON('/api/pause', until ? {on: false, until} : {on: false});
  if (r.error) { toast(r.error, true); return; }
  await afterPause(r);
  const end = until ? tzMoment(until) : null;
  toast(`Resumed. ${since}–${end ? hm(end.min) : 'now'} is not work: a click on it counts it again.`);
}

/* the question: paused since, working since, and the choices */
function askPause(r) {
  $('#pauseprompt-text').textContent = `Paused since ${r.sinceText} (${dur(r.minutes)}), and ${r.backText ? `you have been working since ${r.backText}` : `the tracker saw ${dur(r.workMinutes)} of work since`}.`;
  $('#pauseprompt-notes').textContent = r.backText
    ? `Resume from ${r.backText} counts the time after it as work. Resume now counts none of the pause.`
    : 'Resume now counts none of the pause.';
  const back = $('#pauseprompt-back');
  back.hidden = !r.backText;
  back.textContent = r.backText ? `Resume from ${r.backText}` : '';
  back.dataset.until = r.back ? String(r.back) : '';
  pauseDlg.dataset.answered = '';
  pauseDlg.showModal();
  if (back.hidden) $('#pauseprompt-now').focus();
}

/* when the window comes to the front, at most once a minute: ask if the pause is long and you are working */
async function maybeAskPause() {
  if (!CONFIG || signedOut || Date.now() - pauseAskAt < 60 * 1000 || document.visibilityState !== 'visible') return;
  pauseAskAt = Date.now();
  const r = await readPause(1);
  if (r?.ask && !document.querySelector('dialog[open]')) askPause(r);
}

$('#pause-btn').onclick = async () => {
  if (!PAUSE.paused) return startPause();
  const r = await readPause(2);  // working since the pause began: ask from when; otherwise resume now
  if (r?.ask && r.backText) askPause(r);
  else if (r?.paused) resumePause();
};
const pauseAnswer = how => {
  pauseDlg.dataset.answered = how;
  pauseDlg.close();
  if (how === 'now') resumePause();
  else if (how === 'back') resumePause(+$('#pauseprompt-back').dataset.until);
};
$('#pauseprompt-now').onclick = () => pauseAnswer('now');
$('#pauseprompt-back').onclick = () => pauseAnswer('back');
$('#pauseprompt-stay').onclick = $('#pauseprompt-x').onclick = () => pauseDlg.close();
/* closed without Resume (Stay paused, ✕, Escape, a click outside): the pause goes on, asked again after another stretch */
pauseDlg.addEventListener('close', () => {
  if (!pauseDlg.dataset.answered) postJSON('/api/pause', {stay: true});
});

document.addEventListener('visibilitychange', maybeAskPause);
addEventListener('focus', maybeAskPause);
setTimeout(() => readPause().then(() => maybeAskPause()), 2500);  // after the page has settled
setInterval(() => { readPause(); }, 60 * 1000);
setInterval(maybeAskPause, 10 * 60 * 1000);
