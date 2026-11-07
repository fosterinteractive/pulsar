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

/* Drawing. The columns come back one by one, each answer used to redraw the whole day: now the answers of one frame
   are drawn once (drawSoon), nothing is drawn while the warp pulls in (holdDrawing), and a part whose HTML has not
   changed is left alone (setHTML), so its cards, icons and layout stay as they are. */
function setHTML(el, html) { if (el && el._html !== html) { el.innerHTML = html; el._html = html; } }
function setText(el, text) { if (el && el.textContent !== text) el.textContent = text; }
function forgetHTML(el) { if (el) el._html = undefined; }  // something else changed it (a drag): draw it again
let drawHeld = false, drawWanted = false, drawFrame = 0;
function drawSoon() {
  drawWanted = true;
  if (drawHeld || drawFrame) return;
  drawFrame = requestAnimationFrame(() => { drawFrame = 0; if (drawWanted && !drawHeld) { drawWanted = false; render(); } });
}
function holdDrawing(on) {
  drawHeld = on;
  if (!on && drawWanted) { cancelAnimationFrame(drawFrame); drawFrame = 0; drawWanted = false; render(); }
}

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
  act: null, likely: null, logged: null, repeats: null, scrolledFor: null,
};

/* today as YYYY-MM-DD in the chosen zone, built from its parts: what a locale's date looks like changes between macOS
   versions (en-CA gives 2026-11-05 on some and 11/5/2026 on others), the parts do not */
function todayLocal() {
  const p = Object.fromEntries(new Intl.DateTimeFormat('en-US', {timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit'})
    .formatToParts(new Date()).map(x => [x.type, x.value]));
  return `${p.year}-${p.month}-${p.day}`;
}
function nowMin() {
  const p = Object.fromEntries(new Intl.DateTimeFormat('en-GB', {timeZone: TZ, hour: '2-digit', minute: '2-digit', hourCycle: 'h23'})
    .formatToParts(new Date()).map(x => [x.type, x.value]));
  return +p.hour * 60 + +p.minute;
}
/* The two nights a year the clocks change, the page's minutes are the clock's, while an entry's minutes are real ones
   (pulsar/server.py clock, page_span): 01:30 to 03:30 is 60 minutes the night the clocks go forward, 180 from 00:30
   to 02:30 the night they go back. Every other day these are the clock's own numbers. */
function zoneOffset(ms) {  // Pulsar's zone's UTC offset at a moment, in minutes
  const p = Object.fromEntries(new Intl.DateTimeFormat('en-US', {timeZone: TZ, hourCycle: 'h23', year: 'numeric',
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit'}).formatToParts(new Date(ms))
    .map(x => [x.type, x.value]));
  return Math.round((Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour % 24, +p.minute, +p.second) - Math.floor(ms / 1000) * 1000) / 60000);
}
/* the moment the clock shows minute m of `date`: the first one when it shows it twice, and a minute the clocks skip
   as if they had not changed yet (as the server reads a start) */
function clockMoment(date, m) {
  const naive = Date.parse(date + 'T00:00:00Z') + m * 60000, offs = [zoneOffset(naive - 64800000), zoneOffset(naive + 64800000)];
  const fit = offs.map(o => naive - o * 60000).filter((t, i) => zoneOffset(t) === offs[i]);
  return fit.length ? Math.min(...fit) : naive - offs[0] * 60000;
}
/* the real minutes from clock minute a to clock minute b of `date` */
function realMinutes(date, a, b) {
  return validDate(date) ? Math.round((clockMoment(date, b) - clockMoment(date, a)) / 60000) : Math.round(b - a);
}
/* the clock minute of `date` that comes `minutes` real minutes after clock minute a (over 1440: the next day). The
   night the clocks go back, an end inside the hour shown twice can read before its start: then the clock's sum. */
function clockAfter(date, a, minutes) {
  if (!validDate(date)) return a + minutes;
  const t = clockMoment(date, a) + minutes * 60000, end = Math.round((t + zoneOffset(t) * 60000 - Date.parse(date + 'T00:00:00Z')) / 60000);
  return end > a ? end : a + minutes;
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
/* Warp: an update looks like a jump past a black hole. The page is drawn in towards a point, a core opens there with
   a thin ring of light around it (as light bent around a black hole makes an Einstein ring), and then everything lets
   go with a small flash. Only transform and opacity change, which the graphics card does for free: an SVG
   displacement filter bent the page for real but recomputed every pixel on every frame (20-35 fps). Under a second;
   nothing at all with reduced motion or with the switch in Settings off. The core and the flash sit outside <body>,
   so the pull does not shrink them. */
/* off with macOS's Reduce motion, or with the Warp animation box in Settings (kept on this computer, like the theme) */
const WARP_KEY = 'pulsar-warp';
function warpOff() { try { return localStorage.getItem(WARP_KEY) === 'off'; } catch (e) { return false; } }
const calmMotion = () => matchMedia('(prefers-reduced-motion: reduce)').matches || warpOff();
/* Everything moves through the Web Animations API on transform and opacity only, so the browser hands the motion
   to the graphics card's compositor: it stays smooth while the page is busy (a refresh redrawing the day) and on
   modest computers. JavaScript only starts and stops animations; it never draws a frame. The core and the flash
   are squares around the centre, not full-screen layers. */
const W8 = {core: null, anims: [], from: 0, to: 0, t0: 0, dur: 1, afterUpdate: false, native: false};
/* In the Pulsar window the window itself draws the warp (PulsarWindow.swift: a picture of the page bent by Core Image
   on the graphics card, which holds through a reload); the page only says how far in to go. */
const NATIVE_WARP = !!(window.PULSAR_NATIVE_WARP && window.webkit?.messageHandlers?.pulsar);
const warpDark = () => getComputedStyle(document.documentElement).getPropertyValue('--warp-core').trim().startsWith('0');
/* The window photographs the page before it bends it (50-70 ms in the Pulsar window): a pull from nothing starts that
   much later than asked, so it is waited for too. Without it the letting go came while the pull was only about 60%
   of the way in. */
const SNAPSHOT_MS = 100;
function nativeWarp(level, instant = false) {
  const from = instant ? level : warpLevel(), dur = instant ? 0 : Math.max(1, 520 * Math.abs(level - from));
  const shot = !W8.native && level > 0 && !instant ? SNAPSHOT_MS : 0;
  Object.assign(W8, {from, to: level, t0: performance.now() + shot, dur: Math.max(1, dur), native: level > 0});
  document.documentElement.classList.remove('warp-held');  // the window's picture covers the page from here
  window.webkit.messageHandlers.pulsar.postMessage({action: 'warp', to: level, instant, dark: warpDark()});
  return new Promise(done => setTimeout(done, level > 0 ? dur + shot : 600));
}
const WARP_IN_EASE = 'cubic-bezier(.55, 0, .9, .45)';  // slow at first, as gravity pulls
const pageScale = level => `scale(${1 - .06 * level})`;
const corePose = level => ({opacity: Math.min(1, 1.4 * level), transform: `scale(${.5 + .5 * Math.min(1, level)})`});

function warpParts() {
  if (W8.core) return W8;
  const W = innerWidth, H = innerHeight, cx = W / 2, cy = H * .45, R = Math.min(W, H) * .15, S = Math.round(R * 2.6);
  const core = document.createElement('div');
  core.className = 'warp-core';
  Object.assign(core.style, {left: `${cx - S / 2}px`, top: `${cy - S / 2}px`, width: `${S}px`, height: `${S}px`, opacity: '0'});
  // the core takes the theme's colour (a dark hole on the dark theme, a bright one on the light theme), then the ring
  core.style.background = `radial-gradient(circle closest-side, rgba(var(--warp-core), .94) 0, rgba(var(--warp-core), .94) 32%, `
    + `rgba(var(--warp-core), .5) 48%, rgba(var(--warp-core), 0) 62%, rgba(190, 170, 255, 0) 69%, rgba(190, 170, 255, .38) 77%, `
    + `rgba(120, 220, 255, .16) 83%, rgba(120, 220, 255, 0) 96%)`;
  document.documentElement.append(core);
  Object.assign(document.body.style, {transformOrigin: `50% ${cy}px`, willChange: 'transform'});
  W8.core = core; W8.cy = cy; W8.box = Math.round(Math.min(W, H) * .9);
  return W8;
}
/* how far in the warp is right now (the ramp's ease-in, worked out from the clock: nothing is read from the page) */
function warpLevel() {
  const t = Math.min(1, Math.max(0, (performance.now() - W8.t0) / W8.dur));
  return W8.from + (W8.to - W8.from) * t * t * t;
}
function warpStop() { W8.anims.forEach(a => a.cancel()); W8.anims = []; }
function lensEnd() {
  warpStop();
  Object.assign(document.body.style, {transform: '', willChange: '', transformOrigin: ''});
  W8.core?.remove(); W8.core = null; W8.afterUpdate = false;
  document.documentElement.classList.remove('warp-held');
}
/* pull in to `level` (0..1) and hold it there, the core breathing a little, until warpRelease() */
function warpTo(level, instant = false) {
  if (calmMotion()) return Promise.resolve();
  if (NATIVE_WARP) return nativeWarp(level, instant);
  const w = warpParts(), from = instant ? level : warpLevel(), dur = instant ? 0 : Math.max(1, 520 * Math.abs(level - from));
  warpStop();
  Object.assign(W8, {from, to: level, t0: performance.now(), dur: Math.max(1, dur)});
  const opts = {duration: dur, easing: WARP_IN_EASE, fill: 'forwards'};
  const page = document.body.animate([{transform: pageScale(from)}, {transform: pageScale(level)}], opts);
  const core = w.core.animate([corePose(from), corePose(level)], opts);
  W8.anims = [page, core];
  document.documentElement.classList.remove('warp-held');  // the animation holds the page from here
  return page.finished.then(() => {
    if (W8.anims[0] !== page || level <= 0) return;  // something newer took over
    W8.anims.push(w.core.animate([{transform: corePose(level).transform}, {transform: `scale(${(.5 + .5 * level) * 1.05})`}],
      {duration: 480, direction: 'alternate', iterations: Infinity, easing: 'ease-in-out'}));
  }).catch(() => {});  // cancelled by a newer warp: settled all the same, so no caller waits for ever
}
function warpIn() { return warpTo(1); }  // before a reload into a new version: in, and held until the page goes
function warpRelease() {
  if (NATIVE_WARP) return W8.native ? nativeWarp(0) : Promise.resolve();
  if (!W8.core) return Promise.resolve();
  return warpOut(warpLevel());
}
/* let go: the page springs just past its size and back, the core goes, a flash in the logo's colours */
function warpOut(from = 1) {
  if (calmMotion()) { lensEnd(); return Promise.resolve(); }
  const w = warpParts();
  warpStop();
  const flash = document.createElement('div');
  flash.className = 'warp-flash';
  Object.assign(flash.style, {left: `${innerWidth / 2 - w.box / 2}px`, top: `${w.cy - w.box / 2}px`, width: `${w.box}px`, height: `${w.box}px`});
  document.documentElement.append(flash);
  flash.animate([{opacity: 0, transform: 'scale(.25)'}, {opacity: 1, offset: .18}, {opacity: 0, transform: 'scale(2.6)'}],
    {duration: 700, easing: 'cubic-bezier(.15, .7, .3, 1)'}).finished.catch(() => {}).then(() => flash.remove());
  const opts = {duration: 520, easing: 'cubic-bezier(.2, .9, .3, 1)', fill: 'forwards'};
  const page = document.body.animate([{transform: pageScale(from)}, {transform: 'scale(1.012)', offset: .45}, {transform: 'none'}], opts);
  const core = w.core.animate([corePose(from), {...corePose(0), offset: .55}, corePose(0)], opts);
  W8.anims = [page, core];
  Object.assign(W8, {from: 0, to: 0, dur: 1});
  return page.finished.then(() => { if (W8.anims[0] === page) lensEnd(); }).catch(() => {});
}
/* the warp for as long as `work` takes (at most maxMs): in, held, out when it is done. What the data brings while it
   pulls in is drawn once, when it is all the way in, so the page it lets go of is the new one and nothing redraws
   while it moves. */
let warping = null;
function warpUntil(work, maxMs = 6000) {
  if (calmMotion() || warping) return work;
  const done = Promise.race([Promise.resolve(work).catch(() => {}), new Promise(r => setTimeout(r, maxMs))]);
  holdDrawing(true);
  warping = Promise.all([warpTo(1), done]).then(() => holdDrawing(false)).then(warpRelease)
    .finally(() => { warping = null; holdDrawing(false); });
  return work;
}
function warp() { return warpUntil(null, 0); }  // the whole jump in place (an Option-click on the version)
/* a page that has just restarted into a new version opens inside the lens; main.js lets go once its data is drawn.
   Whatever happens, it lets go after eight seconds. */
try {
  if (sessionStorage.getItem('pulsar-updated') && !calmMotion()) {
    warpTo(1, true); W8.afterUpdate = true;
    setTimeout(() => { if ((W8.core || W8.native) && W8.afterUpdate) warpRelease(); }, 8000);
  }
} catch (e) {}

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
/* Claude's mark, next to what Claude suggested (Ask Claude): a small eight-ray spark in Claude's orange. The words for
   screen readers come with it, so the icon is never the only sign. */
const CLAUDE_MARK = '<svg class="cl-mark" viewBox="0 0 16 16" aria-hidden="true"><g stroke="currentColor" stroke-width="2" stroke-linecap="round">'
  + '<path d="M8 2v4M8 10v4M2 8h4M10 8h4M3.8 3.8l2.6 2.6M9.6 9.6l2.6 2.6M12.2 3.8 9.6 6.4M6.4 9.6 3.8 12.2"/></g></svg>'
  + '<span class="vh">Claude suggests: </span>';
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

/* Tab reaches every button and link. WebKit (the app's window) skips them unless macOS's keyboard navigation is on,
   so each one with no tabindex of its own gets 0, now and whenever the page draws new ones; a tabindex already set
   (a tab list's -1, a card's 0) stays as it is. */
const TABBABLE = 'button:not([tabindex]), a[href]:not([tabindex]), summary:not([tabindex])';
function makeTabbable(root) {
  if (root.matches?.(TABBABLE)) root.tabIndex = 0;
  root.querySelectorAll?.(TABBABLE).forEach(el => { el.tabIndex = 0; });
}
makeTabbable(document);
new MutationObserver(list => { for (const m of list) for (const n of m.addedNodes) if (n.nodeType === 1) makeTabbable(n); })
  .observe(document.body, {childList: true, subtree: true});
