/* the chosen theme before the first paint, so the page never flashes the other one */
try { const t = localStorage.getItem('pulsar-theme'); if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t; } catch (e) {}
