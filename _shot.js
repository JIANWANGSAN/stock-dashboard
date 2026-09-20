/* _shot.js —— 前端视觉验收（项目约定.md §9.3 第 ③ 步）
 *
 * 用**系统 Edge**（channel:'msedge'，不下载 Chromium）逐面板截图，并断言结构改动已生效。
 *
 * 用法：
 *   NODE_PATH="C:/Users/Administrator/.workbuddy/binaries/node/workspace/node_modules" \
 *   "C:/Users/Administrator/.workbuddy/binaries/node/versions/22.12.0/node.exe" _shot.js
 *
 * 输出：./.shots/ 下的 PNG（该目录已 .gitignore）+ 控制台断言汇总。
 */
const path = require('path');
const fs = require('fs');
const { chromium } = require('playwright-core');

const HERE = __dirname;
const URL = 'file:///' + path.join(HERE, 'index.html').replace(/\\/g, '/');
const OUT = path.join(HERE, '.shots');

// 面板 → 左侧菜单 data-panel
const PANELS = [
  ['daily', '必看'],
  ['ladder', '梯队'],
  ['boards', '板块'],
  ['m4', '均线'],
];

const sleep = ms => new Promise(r => setTimeout(r, ms));

async function shoot(page, tag, panel, label, w, h) {
  await page.click(`.menu-item[data-panel="${panel}"]`);
  await sleep(700);                       // 等 ECharts / 内联 SVG 画完
  const file = path.join(OUT, `${tag}_${panel}.png`);
  await page.screenshot({ path: file, fullPage: true });
  const size = fs.statSync(file).size;
  console.log(`  ${label.padEnd(4)} ${String(panel).padEnd(7)} ${w}x${h}  → ${path.basename(file)}  ${(size / 1024).toFixed(1)} KB`);
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  let failed = 0;

  for (const [w, h, tag] of [[1440, 900, 'desktop'], [411, 915, 'mobile']]) {
    console.log(`\n=== ${tag} ${w}x${h} ===`);
    const ctx = await browser.newContext({
      viewport: { width: w, height: h },
      hasTouch: w < 500, isMobile: w < 500,
      deviceScaleFactor: 2,
    });
    const page = await ctx.newPage();
    const errs = [];
    page.on('pageerror', e => errs.push(String(e)));
    page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });

    await page.goto(URL, { waitUntil: 'load' });
    await sleep(1200);

    for (const [panel, label] of PANELS) await shoot(page, tag, panel, label, w, h);

    // ---- 结构断言 ----
    // ⚠️ 顶层 `const D` **不会挂到 window 上**（项目约定.md §5），所以用裸名 `D` 取，不要写 window.D。
    const chk = await page.evaluate(() => {
      const m = (typeof D !== 'undefined' && D && D.macro) || null;
      const panels = ['panel-ladder', 'panel-boards', 'panel-m4'];
      return {
        panelHeadCount: document.querySelectorAll('.panel-head').length,
        // 三个面板的**直接子级** `.sub`（= 被删的导语条）应为 0；卡片内部的 .sub 不受影响
        panelTopSubCount: panels.reduce(
          (n, id) => n + document.querySelectorAll('#' + id + ' > .sub').length, 0),
        // 只认带箭头的原句，避免误伤合法文案（如卡片标题「我的想法 · 次日连板推荐」）
        staleFlowText: /连板候选池（含竞价硬线与合格判定）\s*→/.test(document.body.innerText),
        // 厄尔尼诺整块已删（2026-09-20）——下面两条是代码侧事实，与 data.js 版本无关
        elninoCard: !!document.getElementById('elnino-card'),
        elninoFn: typeof renderElNino,
        // data.js 里的 elnino 键要等下一次 15:05 刷新才消失，故仅作参考不判失败
        elninoKeyInData: (typeof D !== 'undefined' && D) ? ('elnino' in D) : null,
        macroIndexCount: m && m.indices ? m.indices.length : -1,
        macroIndexNames: m && m.indices ? m.indices.map(i => i.name) : [],
        hasStyleKey: !!(m && m.style),
        notes: m && m.notes ? m.notes.length : -1,
        pageVer: (document.getElementById('foot-updated') || {}).textContent || '',
      };
    });
    const ok = (name, cond) => { if (!cond) failed++; console.log(`  ${cond ? '✅' : '❌'} ${name}`); };
    ok('`.panel-head` 数量 = 0（三条导语条已删除）', chk.panelHeadCount === 0);
    ok('三个面板顶部无 `.sub` 残留', chk.panelTopSubCount === 0);
    ok('正文无原流程导语', !chk.staleFlowText);
    ok('厄尔尼诺卡片 #elnino-card 已不存在', !chk.elninoCard);
    ok('renderElNino 函数已删除', chk.elninoFn === 'undefined');
    if (chk.elninoKeyInData) console.log("  · 提示：data.js 里仍有 'elnino' 键（旧版数据，下次 15:05 刷新后消失）");
    ok('macro.indices 只有 4 条', chk.macroIndexCount === 4);
    ok('indices = 上证指数/深证成指/创业板指/沪深300',
       JSON.stringify(chk.macroIndexNames) === JSON.stringify(['上证指数', '深证成指', '创业板指', '沪深300']));
    ok("macro 已无 'style' 键", !chk.hasStyleKey);
    if (chk.notes !== 3) { failed++; console.log(`  ❌ 研判应为 3 条，实为 ${chk.notes}`); } else console.log('  ✅ 研判 3 条');
    console.log(`  · 页脚：${chk.pageVer.trim()}`);
    if (errs.length) { failed++; console.log('  ❌ 页面报错：\n     ' + errs.slice(0, 5).join('\n     ')); }
    else console.log('  ✅ 无 JS 报错');
    await ctx.close();
  }

  await browser.close();
  console.log(`\n${failed === 0 ? '✅ 全部通过' : '❌ 有 ' + failed + ' 项未通过'}；截图见 ${OUT}`);
  process.exit(failed === 0 ? 0 : 1);
})();
