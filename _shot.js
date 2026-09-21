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
        // 「产业板块 · 当日 TOP10」整表已删（2026-09-20）；3日表与小结必须还在
        boardDailyBox: !!document.getElementById('board-daily-box'),
        dUptime: !!document.getElementById('d-uptime'),
        board3dBox: !!document.getElementById('board-3d-box'),
        boardSummary: !!document.getElementById('board-summary'),
        // ⚠️ 这类 DOM 读取必须写在 evaluate 内，不能拿到 Node 作用域用（会 ReferenceError: document is not defined）
        hasStar: /★/.test(document.body.innerText),
        // 高标跟踪（近半月 ≥4 连板）—— 2026-09-21 新增，板块面板置顶
        bbCard: !!document.getElementById('bigboards-card'),
        bbGroups: document.querySelectorAll('#bigboards-box .bb-group').length,
        bbRows: document.querySelectorAll('#bigboards-box .bb-row').length,
        bbDel: document.querySelectorAll('#bigboards-box [data-bb-del]').length,
        bbNote: (document.getElementById('bb-note') || {}).textContent || '',
        bbHasData: (typeof D !== 'undefined' && D && D.bigboards) ? (D.bigboards.groups || []).length : -1,
        macroIndexCount: m && m.indices ? m.indices.length : -1,
        macroIndexNames: m && m.indices ? m.indices.map(i => i.name) : [],
        hasStyleKey: !!(m && m.style),
        notes: m && m.notes ? m.notes.length : -1,
        pageVer: (document.getElementById('foot-updated') || {}).textContent || '',
        // 仓位闸门（2026-09-21 新增）：KPI 行最后一个卡片
        kpiCount: document.querySelectorAll('#mkt-kpi .kpi').length,
        judgeTxt: (function () {
          const cs = document.querySelectorAll('#mkt-kpi .kpi');
          return cs.length ? (cs[cs.length - 1].textContent || '').trim() : '';
        })(),
        moodZtRate: (m && m.mood) ? m.mood.zt_rate : null,
        moodDt: (m && m.mood) ? m.mood.dt : null,
      };
    });
    const ok = (name, cond) => { if (!cond) failed++; console.log(`  ${cond ? '✅' : '❌'} ${name}`); };
    ok('`.panel-head` 数量 = 0（三条导语条已删除）', chk.panelHeadCount === 0);
    ok('三个面板顶部无 `.sub` 残留', chk.panelTopSubCount === 0);
    ok('正文无原流程导语', !chk.staleFlowText);
    ok('厄尔尼诺卡片 #elnino-card 已不存在', !chk.elninoCard);
    ok('renderElNino 函数已删除', chk.elninoFn === 'undefined');
    if (chk.elninoKeyInData) console.log("  · 提示：data.js 里仍有 'elnino' 键（旧版数据，下次 15:05 刷新后消失）");
    ok('「产业板块·当日 TOP10」整表已不存在（#board-daily-box / #d-uptime）',
       !chk.boardDailyBox && !chk.dUptime);
    ok('「产业板块·3日 TOP10」表仍在', chk.board3dBox);
    ok('「小结·主线 vs 脉冲」仍在', chk.boardSummary);
    ok('★ 重合机制仍生效（3日表里应有 ★）', chk.hasStar);
    ok('macro.indices 只有 4 条', chk.macroIndexCount === 4);
    ok('indices = 上证指数/深证成指/创业板指/沪深300',
       JSON.stringify(chk.macroIndexNames) === JSON.stringify(['上证指数', '深证成指', '创业板指', '沪深300']));
    ok("macro 已无 'style' 键", !chk.hasStyleKey);
    if (chk.notes !== 3) { failed++; console.log(`  ❌ 研判应为 3 条，实为 ${chk.notes}`); } else console.log('  ✅ 研判 3 条');

    // ---- 仓位闸门：真值 + 边界 + 「与/或」语义（改 mood 后 renderMacro 重绘，结束时还原）----
    ok('KPI 行末新增「仓位建议」卡', chk.kpiCount === 8 && /仓位建议/.test(chk.judgeTxt));
    // 期望值按当前数据动态算（data.js 换成任意一天的数据都成立），不写死
    const expEmpty = (typeof chk.moodZtRate === 'number' && typeof chk.moodDt === 'number')
      && chk.moodZtRate < 0.75 && chk.moodDt > 10;
    ok(`真实数据（封板率 ${chk.moodZtRate != null ? (chk.moodZtRate * 100).toFixed(2) + '%' : '—'}`
       + ` / 跌停 ${chk.moodDt != null ? chk.moodDt : '—'}）→ 判定「${expEmpty ? '空仓' : '可参与'}」`,
       /空仓/.test(chk.judgeTxt) === expEmpty);
    const gate = await page.evaluate(() => {
      const read = () => {
        const cs = document.querySelectorAll('#mkt-kpi .kpi');
        const last = cs[cs.length - 1];
        return { txt: (last.textContent || '').trim(), empty: last.classList.contains('judge-empty') };
      };
      const b4 = { r: D.macro.mood.zt_rate, d: D.macro.mood.dt };
      const t = (r, d) => { D.macro.mood.zt_rate = r; D.macro.mood.dt = d; renderMacro(); return read(); };
      const cur = read();
      const hit = t(0.70, 15);        // 两条都满足 → 应「空仓」
      const boundR = t(0.75, 15);     // 封板率恰为 75% → 严格小于，不触发
      const boundD = t(0.70, 10);     // 跌停恰为 10 → 严格大于，不触发
      const onlyR = t(0.70, 5);       // 只满足封板率 → 「且」语义，不触发
      const onlyD = t(0.95, 30);      // 只满足跌停 → 不触发
      const miss = (function () {     // 数据缺失 → 给「—」，不默认「可参与」
        D.macro.mood.zt_rate = null; renderMacro(); const r = read();
        D.macro.mood.zt_rate = b4.r; return r;
      })();
      const back = t(b4.r, b4.d);     // 还原
      return { cur, hit, boundR, boundD, onlyR, onlyD, miss, back };
    });
    ok('触发态（封板率70% / 跌停15）→ 显示「空仓」且带警示样式',
       /空仓/.test(gate.hit.txt) && gate.hit.empty);
    ok('边界：封板率 = 75% 整 → 不触发（严格小于）', !/空仓/.test(gate.boundR.txt));
    ok('边界：跌停 = 10 整 → 不触发（严格大于）', !/空仓/.test(gate.boundD.txt));
    ok('只满足「封板率<75%」→ 不触发（确认是「且」不是「或」）', !/空仓/.test(gate.onlyR.txt));
    ok('只满足「跌停>10」→ 不触发', !/空仓/.test(gate.onlyD.txt));
    ok('封板率数据缺失 → 显示「—」（不误报为可参与）', /—/.test(gate.miss.txt));
    ok('模拟后已还原为真实数据', gate.back.txt === gate.cur.txt);

    // ---- 高标跟踪（近半月 ≥4 连板）----
    ok('板块面板置顶新增「高标跟踪」卡片', chk.bbCard);
    if (chk.bbHasData > 0) {
      ok(`高标跟踪已渲染（${chk.bbGroups} 个题材组 / ${chk.bbRows} 只 / ${chk.bbDel} 个删除按钮）`,
         chk.bbGroups > 0 && chk.bbRows > 0 && chk.bbDel === chk.bbRows);
      page.on('dialog', d => d.accept());          // 删除按钮有 confirm()，这里自动确认
      const code = await page.evaluate(() => {
        const b = document.querySelector('#bigboards-box [data-bb-del]');
        return b ? b.getAttribute('data-bb-del') : '';
      });
      const b4 = chk.bbRows;
      if (code) { await page.click('#bigboards-box [data-bb-del]'); await sleep(400); }
      const del = await page.evaluate(c => ({
        rows: document.querySelectorAll('#bigboards-box .bb-row').length,
        gone: !Array.from(document.querySelectorAll('#bigboards-box [data-bb-del]'))
                   .some(b => b.getAttribute('data-bb-del') === c),
        ls: localStorage.getItem('bb_deleted_v1') || '',
      }), code);
      ok('删除按钮：点击后该票立即从列表消失', !!code && del.rows === b4 - 1 && del.gone);
      ok('删除记录已写入本机黑名单(localStorage)', del.ls.indexOf(code) >= 0);
      await page.evaluate(() => { localStorage.removeItem('bb_deleted_v1'); renderBigboards(); });
      const back = await page.evaluate(() => document.querySelectorAll('#bigboards-box .bb-row').length);
      ok('清掉本机黑名单后即恢复（证明是本地过滤，未动数据）', back === b4);
    } else {
      console.log('  · 提示：data.js 暂无 bigboards 数据（旧数据），跳过渲染/删除断言');
    }

    // 触发态**特写**截图（供确认「空仓」警示样式；拍完立即还原，不影响其它截图与线上数据）
    // ⚠️ 前面的 shoot() 会把页面停在「均线」面板，`#mkt-kpi` 在「必看」面板里 → 必须先切回去，
    //    否则 element.screenshot 会因 element is not visible 超时。
    await page.click('.menu-item[data-panel="daily"]');
    await sleep(500);
    const saved = await page.evaluate(() => ({ r: D.macro.mood.zt_rate, d: D.macro.mood.dt }));
    await page.evaluate(() => { D.macro.mood.zt_rate = 0.70; D.macro.mood.dt = 15; renderMacro(); });
    await sleep(300);
    const kpiEl = await page.$('#mkt-kpi');
    if (kpiEl) {
      const f = path.join(OUT, `${tag}_kpi_empty.png`);
      await kpiEl.screenshot({ path: f });
      console.log(`  · 触发态特写 → ${path.basename(f)}  ${(fs.statSync(f).size / 1024).toFixed(1)} KB`);
    }
    await page.evaluate(s => { D.macro.mood.zt_rate = s.r; D.macro.mood.dt = s.d; renderMacro(); }, saved);

    console.log(`  · 页脚：${chk.pageVer.trim()}`);
    if (errs.length) { failed++; console.log('  ❌ 页面报错：\n     ' + errs.slice(0, 5).join('\n     ')); }
    else console.log('  ✅ 无 JS 报错');
    await ctx.close();
  }

  await browser.close();
  console.log(`\n${failed === 0 ? '✅ 全部通过' : '❌ 有 ' + failed + ' 项未通过'}；截图见 ${OUT}`);
  process.exit(failed === 0 ? 0 : 1);
})();
