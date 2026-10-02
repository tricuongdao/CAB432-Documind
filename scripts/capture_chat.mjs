// .build/shots/capture_chat.mjs — phase 2: capture the chat (origin + cache hit)
// and cache-statistics shots with step logging and a hard error dump.
import puppeteer from 'puppeteer-core';

const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const BASE = process.env.DOCUMIND_UI ||
  'https://rqy6cawtyg.execute-api.ap-southeast-2.amazonaws.com/documind/';
const OUT = '../../docs/figures';
const Q = 'What is the role of the dead letter queue in the pipeline?';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

process.on('uncaughtException', (e) => { console.error('UNCAUGHT:', e); process.exit(9); });
process.on('unhandledRejection', (e) => { console.error('UNHANDLED:', e); process.exit(9); });

const scrollToCard = async (page, heading) => {
  await page.evaluate((h) => {
    const cards = [...document.querySelectorAll('.card')];
    const card = cards.find((c) => c.querySelector('h2')?.textContent.includes(h));
    (card || document.body).scrollIntoView({ block: 'start' });
    window.scrollBy(0, -12);
  }, heading);
  await sleep(800);
};

console.log('launching...');
const browser = await puppeteer.launch({
  executablePath: EDGE,
  headless: true,
  args: ['--no-first-run', '--no-default-browser-check', '--disable-gpu'],
});
browser.on('disconnected', () => console.error('!! browser disconnected'));

try {
  const page = await browser.newPage();
  page.setDefaultTimeout(120000);
  page.on('pageerror', (e) => console.error('pageerror:', String(e).slice(0, 200)));
  await page.setViewport({ width: 1500, height: 1050, deviceScaleFactor: 2 });
  console.log('loading page...');
  await page.goto(BASE, { waitUntil: 'networkidle2', timeout: 90000 });
  await sleep(3500);

  const ask = async (label) => {
    const sel = 'input[placeholder="Ask a question about your uploaded documents"]';
    const before = await page.$$eval('.msg.assistant', (n) => n.length);
    console.log(`ask(${label}): typing...`);
    await page.click(sel);
    await page.type(sel, Q, { delay: 6 });
    await page.click('.chat-input button.btn');
    await page.waitForFunction(
      (n) => document.querySelectorAll('.msg.assistant').length > n,
      { timeout: 120000, polling: 400 },
      before,
    );
    await sleep(700);
    console.log(`ask(${label}): done`);
  };

  await scrollToCard(page, 'Ask your documents');
  await ask('origin');
  await page.screenshot({ path: `${OUT}/fig-1-3-ui-chat.png` });
  console.log('shot fig-1-3 (origin)');
  await ask('cache');
  await page.screenshot({ path: `${OUT}/fig-1-3b-ui-chat-hit.png` });
  console.log('shot fig-1-3b (hit)');

  await scrollToCard(page, 'Cache statistics');
  await page.screenshot({ path: `${OUT}/fig-1-4-ui-stats.png` });
  console.log('shot fig-1-4 (stats)');
} catch (err) {
  console.error('FAILED:', err);
  process.exitCode = 1;
} finally {
  await browser.close();
}
console.log('phase-2 complete');
