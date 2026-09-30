// 连板高度梯队单图截图（确认「0 上面直接就是 4」的观感）
const path = require('path');
const { chromium } = require('playwright-core');

(async () => {
  const root = __dirname.split(path.sep).join('/');
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1200 }, deviceScaleFactor: 2 });
  await page.goto('file:///' + root + '/index.html');
  await page.waitForTimeout(2500);
  await page.click('.menu-item[data-panel="ladder"]');
  await page.waitForTimeout(1500);
  const card = await page.$('#echart-ladder');
  const box = await (card || page).boundingBox();
  const clip = box ? { x: Math.max(0, box.x - 12), y: Math.max(0, box.y - 60), width: Math.min(1400, box.width + 24), height: box.height + 80 } : undefined;
  const out = path.join(root, '.shots', 'ladder_final.png');
  await page.screenshot({ path: out, clip });
  console.log('saved:', out);
  await browser.close();
})();
