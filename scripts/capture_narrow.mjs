// scripts/capture_narrow.mjs — report screenshots at a ~590 CSS-px viewport.
//
// Why 590px: the report spec requires screenshot text to appear at ~12pt on
// the page. Printed height of a glyph = fontPx / imageWidthPx * pageWidth, so
// the CSS viewport width must be ≤ ~630px for the app's 14-16px text.
//
// Robustness: puppeteer's own Edge launcher is flaky on this machine, but the
// Edge CLI launches reliably — so we launch Edge ourselves with a debugging
// port and connect puppeteer to it.
import { spawn, execSync } from 'child_process';
import puppeteer from 'puppeteer-core';
import os from 'os';
import path from 'path';
import fs from 'fs';

const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const BASE = process.env.DOCUMIND_UI ||
  'https://rqy6cawtyg.execute-api.ap-southeast-2.amazonaws.com/documind/';
const OUT = '../../docs/figures';
const PORT = 9300 + (Date.now() % 400);
const DEMO = [
  '../../.build/shots/lecture-retrieval.pdf',
  '../../.build/shots/sprint-notes.pdf',
  '../../.build/shots/study-guide.pdf',
];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const udd = fs.mkdtempSync(path.join(os.tmpdir(), 'edge-dbg-'));

console.log('launching Edge with remote debugging...');
const edge = spawn(EDGE, [
  '--headless', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
  '--hide-scrollbars', `--user-data-dir=${udd}`, `--remote-debugging-port=${PORT}`,
  'about:blank',
], { stdio: 'ignore' });

let connected = false;
for (let i = 0; i < 30; i++) {
  try {
    const r = await fetch(`http://127.0.0.1:${PORT}/json/version`);
    if (r.ok) { connected = true; break; }
  } catch (_) { /* not up yet */ }
  await sleep(1000);
}
if (!connected) { console.error('debug port never came up'); process.exit(2); }

const browser = await puppeteer.connect({ browserURL: `http://127.0.0.1:${PORT}` });
const page = await browser.newPage();
page.setDefaultTimeout(120000);
await page.setViewport({ width: 590, height: 880, deviceScaleFactor: 3 });

const scrollToCard = async (heading) => {
  await page.evaluate((h) => {
    const cards = [...document.querySelectorAll('.card')];
    const c = cards.find((x) => x.querySelector('h2')?.textContent.includes(h));
    (c || document.body).scrollIntoView({ block: 'start' });
    window.scrollBy(0, -10);
  }, heading);
  await sleep(700);
};

const ask = async (q) => {
  const sel = 'input[placeholder="Ask a question about your uploaded documents"]';
  const before = await page.$$eval('.msg.assistant', (n) => n.length);
  await page.click(sel);
  await page.type(sel, q, { delay: 6 });
  await page.click('.chat-input button.btn');
  await page.waitForFunction(
    (n) => document.querySelectorAll('.msg.assistant').length > n,
    { timeout: 120000, polling: 400 }, before);
  await sleep(600);
};

try {
  await page.goto(BASE, { waitUntil: 'networkidle2', timeout: 90000 });
  await sleep(3500);

  // 1 — top of page (header + upload zone + start of pipeline)
  await page.screenshot({ path: `${OUT}/fig-1-1-ui-upload.png` });
  console.log('shot fig-1-1');

  // 2 — kick off a fresh batch; capture live pipeline states
  const input = await page.$('input[type=file]');
  await input.uploadFile(...DEMO);
  await sleep(2500);
  await scrollToCard('Live pipeline');
  await page.screenshot({ path: `${OUT}/fig-1-2-ui-pipeline.png` });
  console.log('shot fig-1-2');

  // 3 — chat: true origin (new question never asked before), then cached
  const q = 'How does the ingestion queue handle messages that repeatedly fail?';
  await scrollToCard('Ask your documents');
  await ask(q);
  await page.screenshot({ path: `${OUT}/fig-1-3-ui-chat.png` });
  console.log('shot fig-1-3 (origin)');
  await ask(q);
  await page.screenshot({ path: `${OUT}/fig-1-3b-ui-chat-hit.png` });
  console.log('shot fig-1-3b (hit)');

  // 4 — cache statistics card
  await scrollToCard('Cache statistics');
  await page.screenshot({ path: `${OUT}/fig-1-4-ui-stats.png` });
  console.log('shot fig-1-4');
} catch (err) {
  console.error('capture failed:', err);
  process.exitCode = 1;
} finally {
  try { await browser.disconnect(); } catch (_) {}
  try { execSync(`taskkill /F /T /PID ${edge.pid}`, { stdio: 'ignore' }); } catch (_) { edge.kill(); }
}
console.log('narrow capture complete');
