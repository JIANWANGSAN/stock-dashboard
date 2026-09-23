// 节点票池：09-18 节点单图截图（确认新华文轩已回池）
const path = require('path');
const { chromium } = require('playwright-core');

(async () => {
  const root = __dirname.split(path.sep).join('/');
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1600 }, deviceScaleFactor: 2 });
  await page.goto('file:///' + root + '/index.html');
  await page.waitForTimeout(2800);
  await page.click('.menu-item[data-panel="ladder"]');
  await page.waitForTimeout(1500);

  const info = await page.evaluate(() => {
    const boxes = [...document.querySelectorAll('[id^="node"], .node-card, .node-box')];
    const target = boxes.find(b => (b.textContent || '').includes('2026-09-18') || (b.textContent || '').includes('澳弘电子'));
    if (!target) return { found: false, n: boxes.length, sample: boxes.slice(0, 3).map(b => (b.textContent || '').slice(0, 60)) };
    return { found: true, hasXHWX: (target.textContent || '').includes('新华文轩'), text: (target.textContent || '').slice(0, 260) };
  });
  console.log(JSON.stringify(info, null, 1));

  const out = path.join(root, '.shots', 'node_0918.png');
  if (info.found) {
    const handle = await page.evaluateHandle(() => {
      const boxes = [...document.querySelectorAll('[id^="node"], .node-card, .node-box')];
      return boxes.find(b => (b.textContent || '').includes('2026-09-18') || (b.textContent || '').includes('澳弘电子')) || null;
    });
    const el = handle.asElement();
    if (el) {
      const box = await el.boundingBox();
      if (box) {
        await page.screenshot({ path: out, clip: { x: Math.max(0, box.x - 8), y: Math.max(0, box.y - 8), width: Math.min(1400, box.width + 16), height: Math.min(box.height + 16, 1400) } });
      }
    }
  }
  console.log('saved:', out);
  await browser.close();
})();
