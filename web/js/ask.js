/* Ask Claude while it works (15 to 60 seconds): the button's working state, drawn by ask.css. day.js calls askStart()
   before the request and askEnd() after it; this file only sets classes and the status line.
   * The status line, in place of the Likely header's summary, changes with the time only: Pulsar gets no progress
     from Claude, so it never claims a step it cannot see.
   * Screen readers hear "Claude is working" once, then the toast with the answer. The button stays focusable
     (aria-disabled, not disabled), so the focus ring stays where it was.
   * A hidden window stops the status timer and pauses the motion; nothing runs once it has ended. */
/** @type {[number, string][]} */
const ASK_LINES = [[0, 'Sending the day to Claude…'], [3, 'Picking tasks and drafting notes…'],
                   [25, 'Still working: it can take a minute…'], [65, 'Taking longer than usual…']];
const ASK = {btn: $('#ask-claude'), head: $('#ask-claude').closest('.colhead'), status: null, live: null,
             t0: 0, line: -1, timer: 0, endTimer: 0};
{
  const label = ASK.btn.textContent.trim() || 'Ask Claude';
  ASK.btn.innerHTML = '<span class="ask-ring" aria-hidden="true"><span class="ask-beam"></span><span class="ask-flash"></span></span>'
    + '<span class="ask-glow" aria-hidden="true"></span>'
    + `<span class="ask-lab"><span class="ask-idle">${esc(label)}</span>`
    + `<span class="ask-busy"><span aria-hidden="true">${CLAUDE_MARK.split('<span')[0]}Asking…</span><span class="vh">Asking Claude…</span></span></span>`;
  ASK.status = document.createElement('span');
  ASK.status.className = 'ask-status'; ASK.status.setAttribute('aria-hidden', 'true');
  $('#h-likely').after(ASK.status);
  ASK.live = document.createElement('span');
  ASK.live.className = 'vh'; ASK.live.setAttribute('role', 'status');
  document.body.append(ASK.live);
}
const askBusy = () => ASK.btn.getAttribute('aria-busy') === 'true';

/* the line for the time so far, and a timer for the next one only */
function askLine() {
  clearTimeout(ASK.timer); ASK.timer = 0;
  if (!askBusy() || document.hidden) return;
  const s = (performance.now() - ASK.t0) / 1000;
  let i = 0;
  while (i + 1 < ASK_LINES.length && ASK_LINES[i + 1][0] <= s) i++;
  if (i !== ASK.line) {
    ASK.line = i; ASK.status.textContent = ASK_LINES[i][1];
    ASK.status.animate([{opacity: 0}, {opacity: 1}], {duration: 450, easing: 'ease-out'});
  }
  if (i + 1 < ASK_LINES.length) ASK.timer = setTimeout(askLine, (ASK_LINES[i + 1][0] - s) * 1000 + 30);
}
function askPause() {
  ASK.btn.classList.toggle('ask-paused', document.hidden && askBusy());
  if (document.hidden) { clearTimeout(ASK.timer); ASK.timer = 0; } else askLine();
}
document.addEventListener('visibilitychange', askPause);

function askStart() {
  clearTimeout(ASK.endTimer);
  ASK.btn.classList.remove('ask-done', 'ask-stop');
  ASK.btn.classList.add('asking'); ASK.head.classList.add('ask-on');
  ASK.btn.setAttribute('aria-busy', 'true'); ASK.btn.setAttribute('aria-disabled', 'true');
  ASK.t0 = performance.now(); ASK.line = -1;
  ASK.live.textContent = 'Claude is working. It takes up to a minute.';
  askPause();
}
/* outcome 'picked': a bloom, and the blocks that got a task lit once; anything else ('none', 'error', 'stale'): a fade */
function askEnd(outcome) {
  clearTimeout(ASK.timer); ASK.timer = 0;
  ASK.btn.classList.remove('asking', 'ask-paused'); ASK.head.classList.remove('ask-on');
  ASK.btn.removeAttribute('aria-busy'); ASK.btn.removeAttribute('aria-disabled');
  ASK.live.textContent = '';
  ASK.btn.classList.add(outcome === 'picked' ? 'ask-done' : 'ask-stop');
  ASK.endTimer = setTimeout(() => ASK.btn.classList.remove('ask-done', 'ask-stop'), 1000);
  if (outcome === 'picked') askLight();
}
/* after render(): each Likely card with a pick gets one pass of light, in the order of the day */
function askLight() {
  let k = 0;
  likelyCache.forEach((b, i) => {
    const card = claudePick(b) && document.querySelector(`#lane-likely .card.likely[data-i="${i}"]`);
    if (!card) return;
    card.style.setProperty('--ask-i', String(k++));
    card.classList.add('ask-lit');
    // the light's own end (the mark's flash inside ends earlier and bubbles up too)
    card.addEventListener('animationend', function done(ev) {
      if (ev.animationName !== 'ask-sheen' && ev.animationName !== 'ask-wash') return;
      card.classList.remove('ask-lit'); card.removeEventListener('animationend', done);
    });
  });
}
