/* Ask when I come back (the window's own choice, Settings > Away, on unless switched off): after time away from the
   keyboard the window (PulsarWindow.swift, checkIdle) asks awayBack whether to offer logging it, and shows a small panel
   near the menu bar: Break, or Log This Time… (the log dialog on that stretch, as a break's own button: logAway). Not
   offered while paused, for time over midnight or three hours or more (time off), or for time mostly logged already or
   in a meeting. The window asks from Away after, the one away time Pulsar and ActivityWatch share (Save sets
   ActivityWatch to it: server.sync_activitywatch). */
function awayAskAfter() { return CONFIG?.awayAfter || 15; }
let awayAskSent = 0;
function sendAwayAsk() {  // main.js applyConfig: the window's threshold follows the settings
  const m = awayAskAfter();
  if (m !== awayAskSent && window.pulsarNative?.setNativePrefs && appNative()?.askBack !== undefined) {
    awayAskSent = m;
    window.pulsarNative.setNativePrefs({awayAsk: m});
  }
}

/* a task's short name for the panel: the part before " - " (“Pulsar - Time tracking app for Productive” is “Pulsar”);
   the log dialog shows it whole */
function shortTask(title) {
  const t = String(title || '').trim(), head = t.split(/\s[-–—:|]\s/)[0].trim();
  return (head.length >= 3 ? head : t).slice(0, 32);
}

/* {a, b, title, task} in minutes of today, or null: what the window's panel says, if anything */
async function awayBack(fromSec, toSec) {
  if (!CONFIG || signedOut || PAUSE.paused) return null;
  const from = tzMoment(fromSec), to = tzMoment(toSec), today = todayLocal();
  if (from.date !== today || to.date !== today) return null;
  const a = from.min, b = to.min, len = b - a;
  if (len < awayAskAfter() - 1 || len >= 180) return null;
  const mine = state.date === today && state.logged && !state.logged.error;
  const entries = (mine ? state.logged : await getJSON(`/api/entries?date=${today}`))?.entries || [];
  const over = (s, e) => Math.max(0, Math.min(b, e) - Math.max(a, s));
  if (entries.reduce((t, x) => t + (x.s == null ? 0 : over(x.s, entryEnd(x))), 0) >= len / 2) return null;
  const meets = state.date === today ? state.meet?.events || [] : [];
  if (meets.some(m => !m.all_day && !m.declined && over(+m.s, +m.e) >= len / 2)) return null;
  const before = entryBefore(entries, a);
  return {a, b, title: `Away ${hm(a)}–${hm(b)} · ${dur(len)}`, task: before?.task ? `Before you left: ${shortTask(before.task)}` : ''};
}

/* the panel's Log This Time…: today's day, then the log dialog on the stretch */
async function awayLog(arg) {
  const today = todayLocal(), a = +arg?.a, b = +arg?.b;
  if (!(b > a)) return;
  if (state.view !== 'day') setView('day');
  if (state.date !== today) {
    go(today);
    for (let i = 0; i < 50 && !(state.date === today && state.logged); i++) await new Promise(r => setTimeout(r, 100));
  }
  logAway(a, b);
}

$('#app-askback').onchange = ev => setApp({askBack: ev.target.checked});
