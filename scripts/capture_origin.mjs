// .build/shots/capture_origin.mjs — re-shoot fig-1-3/1-3b with a question
// that has never been asked (guarantees a true MISS then a HIT).
import puppeteer from 'puppeteer-core';

const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const BASE = process.env.DOCUMIND_UI ||
  'https://rqy6cawtyg.execute-api.ap-southeast-2.amazonaws.com/documind/';
const OUT = '../../docs/figures';
const Q = 'Why is the worker pool scaled by queue backlog rather than CPU utilisation?';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await puppeteer.launch({
  executablePath: EDGE, headless: true,
  args: ['--no-first-run', '--no-default-browser-check', '--disable-gpu'],
});
try {
  const page = await browser.newPage();
  page.setDefaultTimeout(120000);
  await page.setViewport({ width: 1500, height: 1050, deviceScaleFactor: 2 });
  await page.goto(BASE, { waitUntil: 'networkidle2', timeout: 90000 });
  await sleep(3500);
  await page.evaluate(() => {
    const cards = [...document.querySelectorAll('.card')];
    const card = cards.find((c) => c.querySelector('h2')?.textContent.includes('Ask your documents'));
    (card || document.body).scrollIntoView({ block: 'start' });
  });
  await sleep(800);

  const ask = async () => {
    const sel = 'input[placeholder="Ask a question about your uploaded documents"]';
    const before = await page.$$eval('.msg.assistant', (n) => n.length);
    await page.click(sel);
    await page.type(sel, Q, { delay: 6 });
    await page.click('.chat-input button.btn');
    await page.waitForFunction(
      (n) => document.querySelectorAll('.msg.assistant').length > n,
      { timeout: 120000, polling: 400 }, before);
    await sleep(700);
  };

  await ask();
  await page.screenshot({ path: `${OUT}/fig-1-3-ui-chat.png` });
  console.log('shot fig-1-3 (true origin)');
  await ask();
  await page.screenshot({ path: `${OUT}/fig-1-3b-ui-chat-hit.png` });
  console.log('shot fig-1-3b (hit)');
} finally {
  await browser.close();
}
console.log('origin re-shoot complete');
