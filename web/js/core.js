/* Shared by every view: settings from the server, state, formatting, theme, toast */
/* from /api/config, set by main.js before the first render: the time zone (undefined = this browser's), the daily target */
let TARGET = 480, TZ, CONFIG = null;
/* row height per zoom: 1 and 5 min rows are lower, or a day at 1 min would be 58 000 px tall */
const SLOT_PX = {1: 24, 5: 28, 15: 40, 30: 40, 60: 40};
const LABEL_EVERY = {1: 5, 5: 15, 15: 30, 30: 30, 60: 60};
const ppmOf = Z => (SLOT_PX[Z] || 40) / Z;
/* calm hues, no red: red is kept for the now line */
const HUES = [150, 212, 268, 32, 186, 320, 95, 236, 48, 290, 170, 12];
let projectHue = new Map();
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

/* a real calendar day, YYYY-MM-DD, in a sane range: anything else in the address or a date field is ignored */
function validDate(d) {
  if (typeof d !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(d)) return false;
  const t = new Date(d + 'T12:00:00Z'), y = +d.slice(0, 4);
  return !isNaN(t) && y >= 1900 && y <= 2999 && t.toISOString().slice(0, 10) === d;
}
const daysBetween = (a, b) => Math.round((new Date(b + 'T12:00:00Z') - new Date(a + 'T12:00:00Z')) / 864e5);
const ZOOMS = [1, 5, 15, 30, 60], SOURCES = ['auto', 'aw', 'memtime'];

/* the address is only a suggestion: a value that is not one of ours falls back to the default, silently */
const params = new URLSearchParams(location.search);
const state = {
  date: validDate(params.get('date')) ? params.get('date') : todayLocal(),
  zoom: ZOOMS.includes(+params.get('zoom')) ? +params.get('zoom') : 30,
  source: SOURCES.includes(params.get('source')) ? params.get('source') : 'auto',
  act: null, likely: null, logged: null, scrolledFor: null,
};

function todayLocal() { return new Intl.DateTimeFormat('en-CA', {timeZone: TZ}).format(new Date()); }
function nowMin() {
  const p = Object.fromEntries(new Intl.DateTimeFormat('en-GB', {timeZone: TZ, hour: '2-digit', minute: '2-digit', hourCycle: 'h23'})
    .formatToParts(new Date()).map(x => [x.type, x.value]));
  return +p.hour * 60 + +p.minute;
}
function shiftDate(d, n) { const t = new Date(d + 'T12:00:00Z'); t.setUTCDate(t.getUTCDate() + n); return t.toISOString().slice(0, 10); }
function dur(m) {
  m = Math.round(m); if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60), r = m % 60; return r ? `${h} h ${r} min` : `${h} h`;
}
function hm(m) { m = Math.round(m) % 1440; return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`; }
/* each project of the day gets its own hue, in name order, so the same day always looks the same */
function assignHues(names) { projectHue = new Map([...new Set(names)].sort().map((n, i) => [n, HUES[i % HUES.length]])); }
function color(name) { return `hsl(${projectHue.get(name) ?? 220} 45% 50%)`; }
function icon(key, label) {
  const letter = esc((label || '?').trim().charAt(0).toUpperCase());
  return key ? `<img class="ico" data-icon="${esc(key)}" alt="" data-letter="${letter}">`
             : `<span class="ico letter">${letter}</span>`;
}
/* Icons need the session token too, which an <img src> cannot send: fetch each once, keep it as a blob URL, and fill
   in every icon the page draws (a watcher catches the ones added later). */
const iconURLs = new Map();
async function iconURL(key) {
  if (!iconURLs.has(key)) {
    iconURLs.set(key, fetch('/icon/' + encodeURIComponent(key), {headers: withSession()})
      .then(r => (r.ok ? r.blob() : Promise.reject(r.status))).then(b => URL.createObjectURL(b)).catch(() => null));
  }
  return iconURLs.get(key);
}
function fillIcons(root = document) {
  root.querySelectorAll('img.ico[data-icon]:not([src])').forEach(async img => {
    const url = await iconURL(img.dataset.icon);
    if (url) img.src = url; else img.dispatchEvent(new Event('error'));
  });
}
new MutationObserver(() => fillIcons()).observe(document.documentElement, {childList: true, subtree: true});
/* a file from the API (it needs the token, so a plain link cannot fetch it) */
async function downloadFromAPI(path, fallbackName) {
  const r = await fetch(path, {headers: withSession()});
  if (!r.ok) { toast(`Download failed (HTTP ${r.status}).`, true); return; }
  const name = (r.headers.get('Content-Disposition') || '').match(/filename="([^"]+)"/)?.[1] || fallbackName;
  const a = document.createElement('a');
  a.href = URL.createObjectURL(await r.blob()); a.download = name;
  document.body.append(a); a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
}
/* an icon that fails to load becomes its letter (one listener instead of inline handlers, which the CSP forbids) */
document.addEventListener('error', ev => {
  const img = ev.target;
  if (!(img instanceof HTMLImageElement) || !img.classList.contains('ico')) return;
  const span = document.createElement('span');
  span.className = 'ico letter'; span.textContent = img.dataset.letter || '?';
  img.replaceWith(span);
}, true);

/* ---------- data ---------- */
/* Every API call carries this browser's session token (localStorage belongs to Pulsar's own address and port, so no
   other site or local service ever sees it). Without a valid one the API answers 401 {signIn: true}: then one notice
   covers the page and no further call is made, no retry loop, no toast per failed call. */
const SESSION = 'pulsar-session';
function session() { try { return localStorage.getItem(SESSION) || ''; } catch (e) { return ''; } }
function withSession(headers = {}) { const t = session(); return t ? {...headers, 'X-Pulsar-Session': t} : headers; }
let signedOut = false;
const SIGNED_OUT = {error: 'This browser is not signed in to Pulsar.', signIn: true};
function showSignIn() {
  if (!signedOut) {
    signedOut = true;
    $('#toast').classList.remove('on'); $('#pop').classList.remove('on');
  }
  const d = $('#signin');
  if (!d.open) d.showModal();
}
/* the notice stays until the page is reloaded signed in: Escape does not dismiss it */
$('#signin').addEventListener('cancel', ev => ev.preventDefault());
$('#signin').addEventListener('close', () => { if (signedOut) $('#signin').showModal(); });
/* Warp: an update looks like a jump to warp. Before the reload into the new version the page draws in a little, as
   if space bent towards a point (warpIn); the new version snaps out of it, just past its size and back, with a small
   flash in the logo's colours (warpOut). Under a second; nothing at all with reduced motion. */
const WARP_HELD = {transform: 'scale(.95, .92)', filter: 'blur(1.2px) brightness(1.25)'};
const calmMotion = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
function warpIn() {
  if (calmMotion()) return Promise.resolve();
  return document.documentElement.animate([{transform: 'none', filter: 'none'}, WARP_HELD],
    {duration: 420, easing: 'cubic-bezier(.55, 0, .9, .45)', fill: 'forwards'}).finished.catch(() => {});
}
function warpOut() {
  const root = document.documentElement;
  root.getAnimations().forEach(a => a.cancel());  // a warpIn still holding the page (Option-click replays both)
  if (calmMotion()) { root.classList.remove('warp-held'); return; }
  root.animate([WARP_HELD, {transform: 'scale(1.012, 1.018)', filter: 'blur(0) brightness(1.06)', offset: .5},
                {transform: 'none', filter: 'none'}], {duration: 560, easing: 'cubic-bezier(.2, .9, .3, 1)'});
  root.classList.remove('warp-held');  // the animation starts from the same place, so nothing jumps
  const flash = document.createElement('div');
  flash.className = 'warp-flash';
  document.body.append(flash);
  flash.animate([{opacity: 0, transform: 'scale(.25)'}, {opacity: 1, offset: .18}, {opacity: 0, transform: 'scale(2.6)'}],
    {duration: 700, easing: 'cubic-bezier(.15, .7, .3, 1)'}).finished.catch(() => {}).then(() => flash.remove());
}

/* In the Pulsar window (Pulsar.app) the page can ask the app for things a browser does itself: printing, and a new
   sign-in (the app gets a fresh one-time code from its launcher). */
const APP = window.webkit?.messageHandlers?.pulsar || null;
const toApp = (action) => APP?.postMessage({action});
if (APP) {
  $('#signin-text').textContent = 'Pulsar needs to sign in again.';
  $('#signin-reload').textContent = 'Sign in';
}
$('#signin-reload').onclick = () => (APP ? toApp('signin') : location.reload());
async function callJSON(path, opts) {
  if (signedOut) return {...SIGNED_OUT};
  let r;
  try { r = await fetch(path, {...(opts || {}), headers: withSession((opts || {}).headers)}); }
  catch (e) { return {error: 'Server not responding: ' + e.message}; }
  let data = null;
  try { data = await r.json(); } catch (e) { /* not JSON: said below */ }
  if (r.status === 401 && data?.signIn) { showSignIn(); return {...SIGNED_OUT}; }
  if (!data || typeof data !== 'object') return {error: `The server answered ${r.status} without data.`};
  return data;
}
function postJSON(path, body) {
  return callJSON(path, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Pulsar': '1'}, body: JSON.stringify(body)});
}
function getJSON(path) { return callJSON(path); }
/* a short note at the bottom after a refresh: what changed in the logged time */
let toastTimer;
function toast(text, warn = false) {
  if (signedOut) return;  // the sign-in notice says it all
  const t = $('#toast');
  t.innerHTML = `<span class="ic">${warn ? '!' : '✓'}</span><span>${esc(text)}</span>`;
  t.classList.toggle('warn', warn); t.classList.add('on');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove('on'), warn ? 7000 : 3500);
}
function loggedChange(before, after) {
  if (after.error) return {text: `Logs not updated: ${after.error}`, warn: true, changed: false};
  if (after.warning) return {text: after.warning, warn: true, changed: false};
  const n = x => x?.entries?.length || 0, m = x => (x?.entries || []).reduce((s, e) => s + e.minutes, 0);
  const dn = n(after) - n(before), dm = m(after) - m(before);
  const base = `${n(after)} entries, ${dur(m(after))}`;
  if (!before || before.error || (!dn && !dm)) return {text: `${base}, no changes`, warn: false, changed: false};
  const sign = v => (v > 0 ? '+' : '−');
  const diff = [dn ? `${sign(dn)}${Math.abs(dn)} ${Math.abs(dn) === 1 ? 'entry' : 'entries'}` : '', dm ? `${sign(dm)}${dur(Math.abs(dm))}` : '']
    .filter(Boolean).join(', ');
  return {text: `${base} (${diff})`, warn: false, changed: true};
}

/* ---------- controls ---------- */
function syncURL() {
  const p = new URLSearchParams({view: state.view, date: state.date, zoom: state.zoom, source: state.source, range: R.range});
  if (R.range === 'custom') { p.set('from', R.from); p.set('to', R.to); }
  history.replaceState(null, '', '?' + p);
}
/* theme: Auto follows the system; Light and Dark are remembered in this browser */
function applyTheme(t) {
  if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t;
  else { t = 'system'; delete document.documentElement.dataset.theme; }
  const btn = $('#theme-btn'), label = {system: 'Theme: follows the system', light: 'Theme: light', dark: 'Theme: dark'}[t];
  btn.textContent = {system: '◐', light: '☀︎', dark: '☾'}[t];
  btn.title = label + '. Click to switch.'; btn.setAttribute('aria-label', label);
  btn.dataset.v = t;
  try { t === 'system' ? localStorage.removeItem('pulsar-theme') : localStorage.setItem('pulsar-theme', t); } catch (e) {}
}
$('#theme-btn').onclick = () => applyTheme({system: 'light', light: 'dark', dark: 'system'}[$('#theme-btn').dataset.v]);
applyTheme(document.documentElement.dataset.theme);
