// scripts/capture_ui.mjs — drive the deployed DocuMind UI with Edge headless
// and capture the report screenshots (upload, pipeline, chat origin/hit, stats).
// Usage: cd .build/shots && node ../../scripts/capture_ui.mjs
import puppeteer from 'puppeteer-core';

const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const BASE = process.env.DOCUMIND_UI ||
  'https://rqy6cawtyg.execute-api.ap-southeast-2.amazonaws.com/documind/';
const OUT = '../../docs/figures';
const DEMO = [
  '../../.build/shots/lecture-retrieval.pdf',
  '../../.build/shots/sprint-notes.pdf',
  '../../.build/shots/study-guide.pdf',
];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const scrollToCard = async (page, heading) => {
  await page.evaluate((h) => {
    const cards = [...document.querySelectorAll('.card')];
    const card = cards.find((c) => c.querySelector('h2')?.textContent.includes(h));
    (card || document.body).scrollIntoView({ block: 'start' });
    window.scrollBy(0, -12);
  }, heading);
  await sleep(700);
};

const ask = async (page, q) => {
  const sel = 'input[placeholder="Ask a question about your uploaded documents"]';
  const before = await page.$$eval('.msg.assistant', (n) => n.length);
  await page.click(sel);
  await page.type(sel, q, { delay: 6 });
  await page.click('.chat-input button.btn');
  await page.waitForFunction(
    (n) => document.querySelectorAll('.msg.assistant').length > n,
    { timeout: 90000, polling: 400 },
    before,
  );
  await sleep(500);
};

const browser = await puppeteer.launch({
  executablePath: EDGE,
  headless: true,
  args: ['--no-first-run', '--no-default-browser-check', '--disable-gpu'],
});
try {
  const page = await browser.newPage();
  page.setDefaultTimeout(90000);
  await page.setViewport({ width: 1500, height: 1050, deviceScaleFactor: 2 });
  await page.goto(BASE, { waitUntil: 'networkidle2', timeout: 90000 });
  await sleep(3500);

  // 1 — upload zone + document list at rest
  await page.screenshot({ path: `${OUT}/fig-1-1-ui-upload.png` });
  console.log('shot fig-1-1 (upload zone)');

  // 2 — kick off a fresh batch so the pipeline shows live states
  const input = await page.$('input[type=file]');
  await input.uploadFile(...DEMO);
  await sleep(2500);
  await scrollToCard(page, 'Live pipeline');
  await page.screenshot({ path: `${OUT}/fig-1-2-ui-pipeline.png` });
  console.log('shot fig-1-2 (pipeline live)');
  await sleep(40000);
  await page.screenshot({ path: `${OUT}/fig-1-2b-ui-pipeline-review.png` });
  console.log('shot fig-1-2b (pipeline later states)');

  // 3 — chat: origin answer, then the same question served from cache
  const q = 'What is the role of the dead letter queue in the pipeline?';
  await scrollToCard(page, 'Ask your documents');
  await ask(page, q);
  await page.screenshot({ path: `${OUT}/fig-1-3-ui-chat.png` });
  console.log('shot fig-1-3 (chat origin, cache miss)');
  await ask(page, q);
  await page.screenshot({ path: `${OUT}/fig-1-3b-ui-chat-hit.png` });
  console.log('shot fig-1-3b (chat cache hit)');

  // 4 — cache statistics card
  await scrollToCard(page, 'Cache statistics');
  await page.screenshot({ path: `${OUT}/fig-1-4-ui-stats.png` });
  console.log('shot fig-1-4 (cache stats)');
} finally {
  await browser.close();
}
