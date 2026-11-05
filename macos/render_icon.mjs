// Renders macos/AppIcon.svg into a macOS .iconset folder (transparent PNGs, every size iconutil wants).
//
//   node macos/render_icon.mjs <AppIcon.svg> <out.iconset>
//
// Needs Node 18+, playwright-core and Google Chrome. playwright-core is found the normal way
// (npm install playwright-core next to this file or globally), or from PLAYWRIGHT_CORE=/path/to/playwright-core.
// Chrome is /Applications/Google Chrome.app unless CHROME=/path/to/chrome says otherwise.
// Each size is rendered from the vector, not scaled down from 1024, so small sizes stay sharp.

import { createRequire } from 'node:module';
import { readFileSync, mkdirSync } from 'node:fs';
import { join, resolve } from 'node:path';

const [svgPath, outDir] = process.argv.slice(2);
if (!svgPath || !outDir) {
  console.error('usage: node render_icon.mjs <AppIcon.svg> <out.iconset>');
  process.exit(2);
}

const require = createRequire(import.meta.url);
let chromium;
try {
  ({ chromium } = require(process.env.PLAYWRIGHT_CORE || 'playwright-core'));
} catch (err) {
  console.error('playwright-core not found. Install it (npm install playwright-core) or set PLAYWRIGHT_CORE=/path/to/playwright-core.');
  process.exit(1);
}
const chrome = process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';

// name -> pixel size, as iconutil expects them
const SIZES = {
  'icon_16x16.png': 16, 'icon_16x16@2x.png': 32,
  'icon_32x32.png': 32, 'icon_32x32@2x.png': 64,
  'icon_128x128.png': 128, 'icon_128x128@2x.png': 256,
  'icon_256x256.png': 256, 'icon_256x256@2x.png': 512,
  'icon_512x512.png': 512, 'icon_512x512@2x.png': 1024,
};

const svg = readFileSync(resolve(svgPath));
const src = 'data:image/svg+xml;base64,' + svg.toString('base64');
mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch({ executablePath: chrome, headless: true });
try {
  const page = await browser.newPage({ deviceScaleFactor: 1 });
  for (const [name, size] of Object.entries(SIZES)) {
    await page.setViewportSize({ width: size, height: size });
    await page.setContent(
      `<html><body style="margin:0;background:transparent"><img src="${src}" width="${size}" height="${size}" style="display:block"></body></html>`);
    await page.waitForFunction(() => document.images[0].complete && document.images[0].naturalWidth > 0);
    await page.screenshot({ path: join(outDir, name), omitBackground: true, clip: { x: 0, y: 0, width: size, height: size } });
    console.log(`${name} ${size}px`);
  }
} finally {
  await browser.close();
}
