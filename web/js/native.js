/* Pulsar.app's window and this page (macos/PulsarWindow.swift). The window's menus, Cmd+W, Cmd+Q, right-click menus,
   Dock badge and end-of-day reminder act through pulsarNative here; the page tells the window what it shows (view,
   day, theme, open dialogs, unsaved work) so the menus, the title and the title bar can follow.

   - In a browser there is no window: nothing here runs.
   - A window of 1.5 or older (no window.PULSAR_APP) has none of these menus, so it only gets an automatic new sign-in
     and the offer to reinstall Pulsar (its app is older than any release that describes one).
   - The window of 1.6.0 on injects window.PULSAR_APP at the start of every load: {version, path, pid, prefs: the Mac's
     own choices (hour12, firstWeekday 1 = Sunday, locale, languages, accent), native: the app's own (badge, reminder,
     reminderTime, login)}. Events for the page: `pulsar:wake` after a sleep or at midnight (cancelable: a listener
     that takes it calls preventDefault, else this file moves on to today itself), and `pulsar:prefs` when those
     choices change. --mac-accent on <html> is the Mac's accent colour. */
(() => {
  const H = window.webkit?.messageHandlers?.pulsar;
  if (!H) return;
  const SHELL = window.PULSAR_APP && typeof window.PULSAR_APP === 'object' ? window.PULSAR_APP : null;
  const send = m => { try { H.postMessage(m); } catch (e) { /* the window is going */ } };
  const q = s => document.querySelector(s);
  const click = s => { const el = q(s); if (el && !el.disabled) { el.click(); return true; } return false; };
  const fn = f => typeof f === 'function';
  const vt = v => String(v || '0').split('.').map(n => parseInt(n, 10) || 0);
  const older = (a, b) => { const x = vt(a), y = vt(b); for (let i = 0; i < 3; i++) if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) < (y[i] || 0); return false; };

  /* ---------- a new sign-in, by itself: the launcher checks the server before it signs the page in again ---------- */
  new MutationObserver(() => {
    if (!q('#signin')?.open) return;
    let last = 0;
    try { last = +sessionStorage.getItem('pulsar-auto-signin') || 0; } catch (e) {}
    if (Date.now() - last < 60 * 1000) return;  // once a minute at most: the notice stays if it does not work
    try { sessionStorage.setItem('pulsar-auto-signin', String(Date.now())); } catch (e) {}
    send({action: 'signin'});
  }).observe(q('#signin'), {attributes: true, attributeFilter: ['open']});

  /* ---------- a newer app than this window (pulsar/appupdate.py), through Settings' own Update button ----------
     The button says when Pulsar restarts: "Update to X and restart" when the release also brings a new window (the
     code first, then the app by itself once the new code has loaded: sessionStorage pulsar-update-app, set by
     settings.js), "Update and restart" when only the app is behind, "Restart Pulsar" once the new app waits in
     Applications. A window of 1.5 or older cannot quit by itself: there the person quits it (Cmd+Q). */
  function appOffer() {
    const u = typeof CONFIG !== 'undefined' && CONFIG ? CONFIG.update : null, a = u?.app;
    if (!u || !u.installed || !a || CONFIG.preview) return null;
    if (a.pending) return {pending: true, version: a.pendingVersion};
    if (!a.min || !a.version || a.failed === a.version || !older(SHELL?.version || '0', a.min)) return null;
    return {version: a.version, mine: SHELL?.version || ''};
  }
  let appBusy = false;
  function appHint(text) {
    let h = q('#app-hint');
    if (!text) { h?.remove(); return; }
    if (!h) { h = document.createElement('p'); h.className = 'hint'; h.id = 'app-hint'; q('#update-status')?.after(h); }
    h.textContent = text;
  }
  function paintApp() {
    const now = q('#update-now'), st = q('#update-status'), u = CONFIG?.update, offer = appOffer();
    if (!now || !st || !u || appBusy) return;
    delete now.dataset.app;
    if (!offer) { appHint(''); return; }
    q('#open-settings')?.classList.add('has-update');
    if (offer.pending) {
      st.textContent = `Pulsar ${offer.version} is in Applications.` + (SHELL ? '' : ' Quit Pulsar (Cmd+Q) to finish: it opens again by itself.');
      appHint('');
      if (SHELL) { now.hidden = false; now.textContent = 'Restart Pulsar'; now.dataset.app = 'restart'; }
      return;
    }
    const after = SHELL ? 'Pulsar restarts once; settings and data stay.'
      : 'Then quit Pulsar (Cmd+Q): it opens again by itself, as the new app. Settings and data stay.';
    if (!now.hidden) {  // a newer release too (settings.js): the code first, then the app by itself
      if (u.status !== 'ready' && SHELL) now.textContent = `Update to ${u.latest} and restart`;
      appHint(`${u.latest} also brings a new window and menus. ${after}`);
      return;
    }
    now.hidden = false; now.dataset.app = 'reinstall';
    now.textContent = SHELL ? 'Update and restart' : 'Update the app';
    if (!st.classList.contains('warn')) st.textContent = `A new Pulsar app is ready: ${offer.version}${offer.mine ? ` (this one is ${offer.mine})` : ''}.`;
    appHint(`The window and menus are new. ${after}`);
  }
  async function reinstall(auto) {
    const now = q('#update-now'), st = q('#update-status');
    if (appBusy || !now || !st) return;
    appBusy = true; now.disabled = true; q('#app-line')?.remove();
    st.classList.remove('warn'); st.textContent = 'Downloading and checking the new Pulsar app…';
    if (auto) toast('Updating the Pulsar app: Pulsar restarts in a moment.');
    const r = await postJSON('/api/update/app', {});
    appBusy = false; now.disabled = false;
    if (r.error) {
      st.textContent = r.error; st.classList.add('warn');
      if (auto) toast(`The Pulsar app was not updated: ${r.error}`, true);
      if (r.installLine) {
        st.insertAdjacentHTML('afterend', `<div id="app-line"><p class="hint">In Terminal, this line downloads Pulsar, checks its signature and puts it in Applications:</p>
          <textarea readonly rows="4" style="width:100%;font:11px/1.4 ui-monospace,Menlo,monospace">${esc(r.installLine)}</textarea>
          <div class="btnrow"><button type="button" class="btn bordered" data-app-copy>Copy the line</button></div></div>`);
      }
      return;
    }
    now.hidden = true; appHint('');
    if (SHELL) { st.textContent = 'Pulsar quits and opens again as the new app…'; setTimeout(() => send({action: 'quit'}), 800); }
    else st.textContent = 'The new Pulsar is in Applications. Quit Pulsar (Cmd+Q) to finish: it opens again by itself.';
  }
  // before settings.js's own click on Update (which installs the code): the app's two cases are this file's
  document.addEventListener('click', ev => {
    const b = ev.target.closest('#update-now');
    if (!b?.dataset.app) return;
    ev.stopPropagation(); ev.preventDefault();
    if (b.dataset.app === 'restart') send({action: 'quit'}); else reinstall(false);
  }, true);
  document.addEventListener('click', ev => {
    if (!ev.target.closest('[data-app-copy]')) return;
    const t = q('#app-line textarea'); t.select();
    try { document.execCommand('copy'); toast('Copied. Paste it in Terminal and press Return.'); } catch (e) {}
  });
  if (fn(window.paintUpdate)) {
    const paint = window.paintUpdate;
    window.paintUpdate = function (u) {
      const out = paint.apply(this, arguments);
      paintApp();
      const offer = appOffer(), go = q('#updprompt-go');
      if (go) go.textContent = offer && !offer.pending && SHELL ? 'Update and restart' : 'Update';
      let asked = '';
      try { asked = sessionStorage.getItem('pulsar-update-app') || ''; } catch (e) {}
      if (asked) {  // the person clicked Update: the app follows by itself, one restart, no second click
        try { sessionStorage.removeItem('pulsar-update-app'); } catch (e) {}
        if (offer && !offer.pending && SHELL) { setTimeout(() => reinstall(true), 1500); return out; }
      }
      if (offer && !offer.pending) {  // once a day, a word outside Settings too
        let said = '';
        try { said = localStorage.getItem('pulsar-app-offer') || ''; } catch (e) {}
        const day = new Date().toISOString().slice(0, 10);
        if (said !== day) {
          try { localStorage.setItem('pulsar-app-offer', day); } catch (e) {}
          setTimeout(() => toast(`A new Pulsar app is ready: Settings, Updates, ${SHELL ? 'Update and restart' : 'Update the app'}.`), 2500);
        }
      }
      return out;
    };
  }

  if (!SHELL) return;  // a window of 1.5 or older: nothing below applies to it

  /* ---------- the new app says it started (pulsar/appupdate.py ends a reinstall) ---------- */
  setTimeout(() => postJSON('/api/app/started', {version: SHELL.version}), 1500);

  /* ---------- the Mac's own choices ---------- */
  function setPrefs(prefs, native) {
    if (prefs) SHELL.prefs = prefs;
    if (native) SHELL.native = native;
    if (SHELL.prefs?.accent) document.documentElement.style.setProperty('--mac-accent', SHELL.prefs.accent);
    if (typeof paintAccent === 'function') paintAccent();  // core.js: the accent's text and fill, from the Mac's accent
    window.dispatchEvent(new CustomEvent('pulsar:prefs', {detail: {prefs: SHELL.prefs, native: SHELL.native}}));
  }
  setPrefs();

  /* ---------- what the page shows, for the window's menus, title and title bar ---------- */
  const opened = [];  // the page's dialogs in the order they opened: Cmd+W closes the last
  new MutationObserver(list => {
    for (const m of list) {
      const d = m.target;
      if (d.tagName !== 'DIALOG') continue;
      const i = opened.indexOf(d);
      if (i >= 0) opened.splice(i, 1);
      if (d.open) opened.push(d);
    }
    report();
  }).observe(document.body, {attributes: true, subtree: true, attributeFilter: ['open', 'class', 'hidden']});
  new MutationObserver(report).observe(document.documentElement, {attributes: true, attributeFilter: ['data-theme']});
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', report);
  document.addEventListener('input', ev => { if (ev.target.closest?.('dialog')) report(); }, true);
  addEventListener('pulsar:pause', report);  // pause.js: Pause or Resume in the Dock icon's menu, ⏸ in the menu bar
  if (fn(window.syncURL)) {  // every change of view, day, rows or range goes through it
    const sync = window.syncURL;
    window.syncURL = function () { const out = sync.apply(this, arguments); report(); return out; };
  }
  setInterval(report, 30 * 1000);

  function ldOn() { try { return typeof LD === 'object' && !!LD.on; } catch (e) { return false; } }
  function title() {
    try {
      if (state.view === 'report') return `Report, ${q('#rlabel')?.textContent || ''}`.replace(/, $/, '');
      return new Intl.DateTimeFormat(SHELL.prefs?.locale || undefined, {weekday: 'long', day: 'numeric', month: 'long',
        year: 'numeric', timeZone: 'UTC'}).format(new Date(state.date + 'T12:00:00Z'));
    } catch (e) { return 'Pulsar'; }
  }
  /* What quitting now would lose, in a sentence, or ''. The page may say more with window.pulsarUnsaved(). */
  function unsaved() {
    try { if (fn(window.pulsarUnsaved)) { const u = window.pulsarUnsaved(); if (u) return String(u); } } catch (e) {}
    try { if (ldOn() && LD.writing) return 'Log the day is writing to Productive.'; } catch (e) {}
    if (q('#logdlg')?.open) return 'The time in the log dialog is not logged yet.';
    if (q('#taskdlg')?.open && q('#task-form')?.elements.title?.value.trim()) return 'The new task is not created yet.';
    if (q('#ruledlg')?.open && q('#rule-form')?.elements.match?.value.trim()) return 'The new rule is not saved yet.';
    try { if (ldOn() && LD.rows.some(r => r.edited)) return 'Your changes in Log the day are not logged yet.'; } catch (e) {}
    return '';
  }
  let shownToday = '', timer = 0, sent = '';  // shownToday: the day that was today when the page last showed it
  function snapshot() {
    const dialogs = [...document.querySelectorAll('dialog[open]')].map(d => d.id).filter(id => id !== 'signin');
    let reportReady = false, signed = true;
    try { reportReady = !!(R.data && !R.data.error); } catch (e) {}
    try { signed = !signedOut; } catch (e) {}
    return {action: 'state', ready: true, view: state.view, date: state.date, zoom: state.zoom, today: todayLocal(),
            theme: document.documentElement.dataset.theme || 'system', bg: getComputedStyle(document.body).backgroundColor,
            title: title(), modal: dialogs.length > 0, dialogs, pop: !!q('#pop')?.classList.contains('on'),
            logDay: ldOn(), unsaved: unsaved(), signedIn: signed, report: reportReady,
            paused: typeof PAUSE === 'object' && !!PAUSE.paused, pausedSince: typeof PAUSE === 'object' ? PAUSE.sinceText || '' : ''};
  }
  function report() {
    clearTimeout(timer);
    timer = setTimeout(() => {
      if (typeof state !== 'object') return;
      if (state.date === todayLocal()) shownToday = state.date;
      const s = snapshot(), j = JSON.stringify(s);
      if (j !== sent) { sent = j; send(s); }
    }, 60);
  }

  /* ---------- Cmd+W: the page's top dialog or popover first ---------- */
  function closeTop() {
    const dp = q('.dp:not([hidden])');
    if (dp) { dp.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true})); return true; }
    const pk = q('#ld-pk');
    if (pk && !pk.hidden) { pk.hidden = true; return true; }
    const open = [...document.querySelectorAll('dialog[open]')].filter(d => d.id !== 'signin');
    if (open.length) {
      const d = opened.filter(x => x.open && x.id !== 'signin').pop() || open[open.length - 1];
      const ev = new Event('cancel', {cancelable: true});
      d.dispatchEvent(ev);
      if (!ev.defaultPrevented && d.open) d.close();
      return true;
    }
    if (q('#pop')?.classList.contains('on')) { fn(window.closePop) ? closePop(true) : q('#pop').classList.remove('on'); return true; }
    const dl = q('#report-dl[open]');
    if (dl) { dl.open = false; return true; }
    if (ldOn() && fn(window.closeLogDay)) { closeLogDay(); return true; }
    return false;
  }

  /* ---------- the window's menus ---------- */
  const ZOOMS = [1, 5, 15, 30, 60];
  const toDay = () => { if (state.view !== 'day') click('#view button[data-v="day"]'); };
  const COMMANDS = {
    newEntry: () => { toDay(); click('#log-new'); },
    logDay: () => { toDay(); click('#log-day'); },
    logDayToday: () => { toDay(); if (state.date !== todayLocal() && fn(window.go)) go(todayLocal()); click('#log-day'); },
    day: () => click('#view button[data-v="day"]'),
    report: () => click('#view button[data-v="report"]'),
    today: () => click('#today'),
    prev: () => click('#prev'),
    next: () => click('#next'),
    refresh: () => click('#refresh'),
    rows: n => click(`#zoom button[data-v="${Number(n)}"]`),
    rowsStep: step => {
      if (state.view !== 'day') return;
      const i = ZOOMS.indexOf(state.zoom), j = Math.max(0, Math.min(ZOOMS.length - 1, (i < 0 ? 3 : i) + Number(step)));
      if (ZOOMS[j] !== state.zoom) COMMANDS.rows(ZOOMS[j]);
    },
    print: () => { if (state.view === 'report') click('#report-dl [data-dl="print"]'); },
    exportDays: () => { if (state.view === 'report') click('#report-dl [data-dl="days"]'); },
    exportEntries: () => { if (state.view === 'report') click('#report-dl [data-dl="entries"]'); },
    theme: t => { if (fn(window.applyTheme)) applyTheme(t); report(); },
    settings: () => { if (fn(window.openSettings)) openSettings(); },
    pause: () => { if (typeof PAUSE === 'object' && !PAUSE.paused) startPause(); },   // the Dock icon's menu, the menu bar
    resume: () => { if (typeof PAUSE === 'object' && PAUSE.paused) resumePause(); },
    checkUpdates: () => { if (fn(window.openSettings)) openSettings('t-upd'); click('#update-check'); },
    logs: () => click('#open-logs'),
  };
  function run(name, arg) {
    const f = COMMANDS[name];
    if (!f || (q('dialog[open]:not(#signin)') && name !== 'theme')) return false;
    f(arg);
    return true;
  }

  /* ---------- right-click on the day: the entry's or the stretch's own commands, as a menu of the window ---------- */
  let picks = {};
  function hiddenClick(data) {  // the page's own handler for "Log this time" (day.js), as from its popover
    const b = document.createElement('button');
    b.hidden = true;
    Object.assign(b.dataset, data);
    document.body.append(b); b.click(); b.remove();
  }
  function menuFor(ev) {
    const t = ev.target;
    if (!(t instanceof Element) || t.closest('input, textarea, select, dialog, .pop, .pkp')) return null;
    if (state.view !== 'day') return null;
    const Z = state.zoom, rail = t.closest('#dayview .gaprail'), away = t.closest('#dayview .awayband');
    if (rail) return [{title: 'Log This Time…', run: () => rail.click()}];
    if (away && away.dataset.kind !== 'off') return [{title: 'Log This Time…', run: () => logAway(+away.dataset.a, +away.dataset.b)}];  // day.js
    if (away) return null;  // time off: nothing to log
    const card = t.closest('#dayview .card[data-k]');
    if (!card) return null;
    const i = +card.dataset.i, k = card.dataset.k;
    try {
      if (k === 'logged') {
        const x = loggedCache[i];
        if (!x) return null;
        const url = fn(window.productiveTaskURL) ? productiveTaskURL(x.taskId) : '';
        return [{title: 'Edit Entry…', run: () => editEntry(x)},
                {title: 'Delete Entry…', run: () => askDelete(x, {clientX: ev.clientX, clientY: ev.clientY})},
                ...(url ? ['-', {title: 'Open in Productive', url}] : [])];
      }
      if (k === 'act') {
        const b = blocksCache[i];
        if (!b) return null;
        const top = [...b.items].sort((p, r) => r.min - p.min)[0];
        const rule = top && fn(window.ruleSuggestion) && typeof topWindow === 'function' ? ruleSuggestion(topWindow(top)) : '';
        return [{title: 'Log This Time…', run: () => hiddenClick({logA: b.start * Z, logB: b.end * Z, act: i})},
                {title: 'Add a Rule for This Window…', run: () => openRule(rule)}];
      }
      if (k === 'likely') {
        const b = likelyCache[i];
        if (!b) return null;
        return [{title: 'Log This Time…', run: () => hiddenClick({logA: b.start * Z, logB: b.end * Z, likely: i})}];
      }
      if (k === 'repeat') return [{title: 'Log This Time…', run: () => card.click()}];
    } catch (e) { return null; }
    return null;
  }
  document.addEventListener('contextmenu', ev => {
    /** @type {any[]} */
    const items = menuFor(ev);
    if (!items) return;  // anywhere else: the window's own menu (Copy, Look Up)
    ev.preventDefault();
    picks = {};
    let n = 0;
    send({action: 'menu', x: ev.clientX, y: ev.clientY, items: items.map(it => {
      if (it === '-') return {separator: true};
      const id = 'm' + (++n);
      picks[id] = it.run || null;
      return {id, title: it.title, enabled: it.enabled !== false, url: it.url || ''};
    })});
  }, true);
  function menuPick(id) { const f = picks[id]; picks = {}; if (fn(f)) f(); }

  /* ---------- today's time not logged: the Dock badge and the reminder ---------- */
  let lastGap = null;  // {minutes, at}: from the day view, when it shows today
  let todayAt = 0;      // when the day view last gave the menu bar today's numbers
  if (fn(window.renderAside)) {
    const aside = window.renderAside;
    window.renderAside = function (gapMin) {
      const out = aside.apply(this, arguments);
      try {
        if (state.date === todayLocal() && state.logged && !state.logged.error && state.act && !state.act.error) {
          lastGap = {minutes: Math.round(gapMin), at: Date.now()};
          send({action: 'badge', minutes: lastGap.minutes, date: state.date});
          // the menu bar line: what the day view shows, each time it draws today (no read of its own)
          const work = state.act.items.filter(x => x.cat !== 'personal').reduce((s, x) => s + x.e - x.s, 0);
          const sum = state.logged.entries.reduce((s, x) => s + x.minutes, 0);
          todayAt = Date.now();
          send({action: 'today', date: state.date, active: Math.round(work), logged: Math.round(sum), notLogged: lastGap.minutes});
        }
      } catch (e) {}
      return out;
    };
  }
  async function todayUnlogged() {
    // the window's end-of-day reminder asks this: none on a day that is not a working day (Settings > Time)
    if (typeof workingDay === 'function' && !workingDay(todayLocal())) return null;
    if (lastGap && Date.now() - lastGap.at < 10 * 60 * 1000 && state.date === todayLocal()) return lastGap.minutes;
    const d = todayLocal(), r = await getJSON(`/api/report?from=${d}&to=${d}`);
    const day = r && !r.error && r.days ? r.days[0] : null;
    return day ? Math.round(day.notLogged || 0) : null;
  }
  async function badge() {
    if (state.date === todayLocal() && state.view === 'day' && lastGap && Date.now() - lastGap.at < 30 * 60 * 1000) return;
    const m = await todayUnlogged();
    if (m != null) send({action: 'badge', minutes: m, date: todayLocal()});
  }
  setTimeout(badge, 20 * 1000);
  setInterval(badge, 30 * 60 * 1000);

  /* ---------- today in the menu bar: active work and logged time (the window's status item) ---------- */
  let todaySoon = 0;
  async function todayLine() {
    if (Date.now() - todayAt < 10 * 60 * 1000) return;  // the day view gave them lately (above)
    const d = todayLocal(), r = await getJSON(`/api/report?from=${d}&to=${d}`);
    const day = r && !r.error && r.days ? r.days[0] : null;
    if (day) send({action: 'today', date: d, active: Math.round(day.active || 0), logged: Math.round(day.logged || 0),
                   notLogged: Math.round(day.notLogged || 0)});
  }
  const todayLater = (ms = 3000) => { clearTimeout(todaySoon); todaySoon = setTimeout(todayLine, ms); };
  todayLater(8 * 1000);
  // while the day view shows another day (or Report): a read of its own every 10 minutes (each also reads Productive,
  // and MemTime's database when it is there)
  setInterval(todayLine, 10 * 60 * 1000);

  /* ---------- after a sleep, or at midnight: the day it is now ---------- */
  function wake() {
    const ev = new CustomEvent('pulsar:wake', {cancelable: true});
    if (window.dispatchEvent(ev) && typeof state === 'object' && state.view === 'day') {  // no listener took it
      if (shownToday && state.date === shownToday && todayLocal() !== shownToday && fn(window.go)) go(todayLocal());
      else if (state.date === todayLocal() && fn(window.load)) load({keepScroll: true});
    }
    report();
    setTimeout(badge, 5000);
    setTimeout(todayLine, 5000);
    // a new release while the Mac slept: looked for at once (the server's hourly look counts awake time only)
    setTimeout(async () => {
      const r = await postJSON('/api/update/check', {});
      if (r && !r.error && r.update && typeof CONFIG === 'object' && CONFIG && fn(window.paintUpdate)) {
        CONFIG.update = r.update; window.paintUpdate(r.update);
      }
    }, 10 * 1000);
  }

  /* ---------- the bridge: what the window calls (PulsarWindow.swift, callPage) ---------- */
  const asks = new Map();
  let askN = 0;
  function ask(what) {
    return new Promise(done => {
      const id = ++askN;
      asks.set(id, done);
      send({action: 'ask', id, what});
      setTimeout(() => { if (asks.has(id)) { asks.delete(id); done(null); } }, 4000);
    });
  }
  window.pulsarNative = {
    app: SHELL, run, closeTop, unsaved, wake, menuPick, todayUnlogged,
    prefs: (prefs, native, problem) => { setPrefs(prefs, native); if (problem) toast(problem, true); },
    answer: (id, value) => { const f = asks.get(id); if (f) { asks.delete(id); f(value); } },
    /* for Settings (the page's own controls): {system, native} now, and changes to the app's own choices */
    nativePrefs: () => ask('prefs'),
    setNativePrefs: set => send({action: 'prefs', set}),  // {badge, reminder, reminderTime: "17:30", login}
    openLoginItems: () => send({action: 'openLoginItems'}),
  };
  report();
})();
