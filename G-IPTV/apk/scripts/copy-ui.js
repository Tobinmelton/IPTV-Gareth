// Copy the UI (mockups/stage12.html) into the Capacitor web dir as index.html.
// The WebView loads this; it then talks to the G-IPTV server for data/streams.
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..', '..');
const src = path.join(root, 'mockups', 'stage12.html');
const destDir = path.join(__dirname, '..', 'www');
const dest = path.join(destDir, 'index.html');

fs.mkdirSync(destDir, { recursive: true });
fs.copyFileSync(src, dest);
console.log(`[build:ui] ${path.relative(root, src)} -> apk/www/index.html`);
