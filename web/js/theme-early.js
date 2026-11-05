/* the chosen theme before the first paint, so the page never flashes the other one */
try { const t = localStorage.getItem('pulsar-theme'); if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t; } catch (e) {}
/* just restarted into a new version: start drawn in, so the warp out of it (core.js) is the first thing seen; let go
   after three seconds whatever happens */
try {
  if (sessionStorage.getItem('pulsar-updated') && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
    document.documentElement.classList.add('warp-held');
    setTimeout(() => document.documentElement.classList.remove('warp-held'), 3000);
  }
} catch (e) {}
