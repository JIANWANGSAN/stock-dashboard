// 连板图横线专项核查：只应有 4..max 的横线（0 轴除外）
const path = require('path');
const { chromium } = require('playwright-core');

(async () => {
  const root = __dirname.split(path.sep).join('/');
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1400 } });
  await page.goto('file:///' + root + '/index.html');
  await page.waitForTimeout(2500);
  await page.click('.menu-item[data-panel="ladder"]');
  await page.waitForTimeout(1200);

  const r = await page.evaluate(() => {
    const el = document.getElementById('echart-ladder');
    const inst = window.echarts ? window.echarts.getInstanceByDom(el) : null;
    if (!inst) return { err: 'no instance' };
    const hys = [];
    const walk = (e) => {
      if (!e) return;
      if (Array.isArray(e)) { e.forEach(walk); return; }
      const sh = e.shape || {};
      if (e.type === 'line' && sh.y1 != null && sh.y1 === sh.y2
          && sh.x1 != null && Math.abs(sh.x2 - sh.x1) > 200) hys.push(Math.round(sh.y1));
      (e.children || []).forEach(walk);
    };
    inst.getZr().storage.getDisplayList().forEach(walk);
    const opt = inst.getOption();
    const ya = opt.yAxis[0];
    const px = v => inst.convertToPixel({ yAxisIndex: 0 }, v);
    const near = v => hys.filter(hy => Math.abs(hy - Math.round(px(v))) <= 3);
    const out = { max: ya.max, hys: [...new Set(hys)].sort((a, b) => a - b) };
    for (const v of [1, 2, 3, 4, 5, 6, 7, 8]) out['v' + v] = near(v);
    return out;
  });
  console.log(JSON.stringify(r, null, 1));
  await browser.close();
})();
