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
        // ★ = 产业板块「当日 TOP10」∩「3日 TOP10」（两条榜都是 SECTOR_WATCH 同一份清单算的）。
        // ⚠️ 不要用 /★/ 扫全页正文 —— 两榜恰好不重合时页面合法地显示「暂无」，会假失败。
        //    这里改成直接按数据算期望值（data.js 换成任意一天都成立）。
        hasStar: (function () {
          if (typeof D === 'undefined' || !D) return null;
          const n1 = new Set((D.sector_daily || []).slice(0, 10).map(b => b.name));
          const n3 = new Set((D.sector_3d || []).slice(0, 10).map(b => b.name));
          return [...n1].filter(x => n3.has(x)).length > 0;
        })(),
        // 高标跟踪（近半月 ≥4 连板）—— 2026-09-21 新增，板块面板置顶
        bbCard: !!document.getElementById('bigboards-card'),
        bbGroups: document.querySelectorAll('#bigboards-box .bb-group').length,
        bbRows: document.querySelectorAll('#bigboards-box .bb-row').length,
        // ⛔ 删除按钮已删（2026-09-21 用户要求）—— 下面两条断言必须恒为 0
        bbDel: document.querySelectorAll('#bigboards-box [data-bb-del]').length,
        bbCur: document.querySelectorAll('#bigboards-box .bb-cur').length,
        bbNote: (document.getElementById('bb-note') || {}).textContent || '',
        bbSum: (document.getElementById('bb-sum') || {}).textContent || '',
        bbHasData: (typeof D !== 'undefined' && D && D.bigboards) ? (D.bigboards.groups || []).length : -1,
        // ⛔ 已删功能的自检（都应为「不存在」）
        bbDelKeyFn: [typeof bbDeleted, typeof bbAddDeleted].join(','),
        kpiHasAmt: /两市成交额|成交额/.test((document.getElementById('mkt-kpi') || {}).textContent || ''),
        mktTitle: (document.querySelector('#mkt-card .card-title') || {}).textContent || '',
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
    // ⚠️ `document` 只存在于页面上下文 —— 下面的「页面侧事实」必须用 page.evaluate 取回。
    // ⚠️ 也别用 `document.body.innerText`：它只返回**可见**文本，板块面板此时是 display:none → 永远拿不到 ★。
    //    改用**总是存在于 DOM** 的 `#board-3d-box` / `#board-summary` 的 textContent。
    const pg = await page.evaluate(() => ({
      hasStarMark: /★/.test((document.getElementById('board-3d-box') || {}).textContent || ''),
      boardSummaryTxt: (document.getElementById('board-summary') || {}).textContent || '',
    }));
    ok('★ 重合机制（当日∩3日）按数据算出的期望值与页面一致',
       chk.hasStar === null || (chk.hasStar
          ? pg.hasStarMark
          : /暂无/.test(pg.boardSummaryTxt)));
    ok('macro.indices 只有 4 条', chk.macroIndexCount === 4);
    ok('indices = 上证指数/深证成指/创业板指/沪深300',
       JSON.stringify(chk.macroIndexNames) === JSON.stringify(['上证指数', '深证成指', '创业板指', '沪深300']));
    ok("macro 已无 'style' 键", !chk.hasStyleKey);
    if (chk.notes !== 3) { failed++; console.log(`  ❌ 研判应为 3 条，实为 ${chk.notes}`); } else console.log('  ✅ 研判 3 条');

    // ---- 仓位闸门：真值 + 边界 + 「与/或」语义（改 mood 后 renderMacro 重绘，结束时还原）----
    // ⛔ KPI 行数 = 7：「两市成交额」卡已删（2026-09-21 用户要求，连数据一起不展示）。**不得加回。**
    ok('KPI 行已无「两市成交额」卡', !chk.kpiHasAmt);
    ok('面板标题已简化为「概览」（不再含「资金主线」）',
       /概览/.test(chk.mktTitle) && !/资金主线/.test(chk.mktTitle));
    ok('KPI 行末新增「仓位建议」卡', chk.kpiCount === 7 && /仓位建议/.test(chk.judgeTxt));
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

    // ---- 高标跟踪（近半月 ≥4 连板）—— 2026-09-21 已改为**纯展示** ----
    // ⛔ 删除按钮 / localStorage 黑名单 / 汇总条本地过滤 —— 全部已删。下面这批断言**必须恒真**，
    //    它们是「减法没被revert」的守卫：一旦有人把删除机制加回来，这里会立刻红。
    //    口径：高标跟踪里「不再跟踪」的票 = 跌停后不再出现在卡片里，**别处统计一律照旧使用**。
    await page.click('.menu-item[data-panel="boards"]');
    await sleep(500);
    ok('板块面板置顶新增「高标跟踪」卡片', chk.bbCard);
    ok(`高标跟踪【无删除按钮】（实测 ${chk.bbDel} 个 [data-bb-del]）`, chk.bbDel === 0);
    ok(`高标跟踪【无「当前N板」标记】（实测 ${chk.bbCur} 个 .bb-cur）`, chk.bbCur === 0);
    ok('前端删除函数已彻底删除（bbDeleted / bbAddDeleted 均 undefined）',
       chk.bbDelKeyFn === 'undefined,undefined');
    if (chk.bbHasData > 0) {
      ok(`高标跟踪已渲染（${chk.bbGroups} 个题材组 / ${chk.bbRows} 只）`,
         chk.bbGroups > 0 && chk.bbRows > 0);
      const st = await page.evaluate(() => ({
        sum: (document.getElementById('bb-sum') || {}).textContent || '',
        rows: document.querySelectorAll('#bigboards-box .bb-row').length,
        ld: document.querySelectorAll('#bigboards-box .bb-row.ld').length,
        st: (D.bigboards && D.bigboards.stats) || null,
      }));
      const e = st.st || {};
      // ⛔ 2026-09-21 晚：跌停票（「不再跟踪」）**不再显示** —— 卡片里只留跟踪中的票。
      ok('高标跟踪【不再显示跌停票】（' + st.ld + ' 行 .bb-row.ld）', st.ld === 0);
      // 实际渲染行数 == stats.tracking（不再等于 total）
      ok(`卡片实际显示行数 == stats.tracking（${st.rows} vs ${e.tracking}）`, st.rows === e.tracking);
      ok(`汇总条报「跟踪中 N 只」且与 stats.tracking 一致（${e.tracking}）`,
         st.sum.indexOf('跟踪中 ' + e.tracking + ' 只') >= 0
         && st.sum.indexOf('跟踪中 ' + e.tracking) >= 0);
      ok(`汇总条附注跌停只数（stats.limit_down = ${e.limit_down}）`,
         e.limit_down === 0
           ? !/已跌停/.test(st.sum)
           : st.sum.indexOf('另有 ' + e.limit_down + ' 只已跌停') >= 0);
      ok('汇总条不再报「共 N 只」（改为只报跟踪中只数）', !/共 \d+ 只/.test(st.sum));
      // localStorage 里不应再有任何高标跟踪相关的键
      const lsKeys = await page.evaluate(() => Object.keys(localStorage).filter(k => /bb_deleted|bigboard/i.test(k)));
      ok(`localStorage 已无高标跟踪黑名单键（实测 ${JSON.stringify(lsKeys)}）`, lsKeys.length === 0);
      // ⚠️ 数据层必须仍是全量（含跌停票）—— 「只不显示」绝不能退化成「从数据里删」
      const dataIntact = await page.evaluate(() => {
        const gs = (D.bigboards && D.bigboards.groups) || [];
        let total = 0, ld = 0;
        gs.forEach(g => (g.stocks || []).forEach(s => { total++; if (s.status === 'limit_down') ld++; }));
        return { total, ld, statsTotal: (D.bigboards.stats || {}).total };
      });
      ok(`数据层仍为全量（含跌停票）：groups 内 ${dataIntact.total} 只 / 其中跌停 ${dataIntact.ld}`
         + ` == stats.total ${dataIntact.statsTotal}`,
         dataIntact.total === dataIntact.statsTotal
         && dataIntact.ld === e.limit_down
         && (e.limit_down === 0 || dataIntact.ld > 0));
    } else {
      console.log('  · 提示：data.js 暂无 bigboards 数据（旧数据），跳过渲染断言');
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

    // ── 日K线「涨停」标黄（约定 §3.9b）──────────────────────────────
    // ① 判据纯函数：用**真实行情样本**（含腾讯前复权 3 位小数、一字板、差一分没封、跌停）逐条断言
    const lim = await page.evaluate(() => {
      const B = (o, c, h, l) => ({ o, c, h, l });
      return {
        // 远望谷 2026-09-11 一字首板：前收 7.31 → 涨停价 8.04（真实样本）
        yizi:      isLimitUpBar(B(8.04, 8.04, 8.04, 8.04), 7.31, '002161', '远望谷'),
        // 远望谷 2026-06-17（前复权 3 位小数）：7.31→8.04 同一天在复权空间的表现 5.677→6.247
        qfq3:      isLimitUpBar(B(5.597, 6.247, 6.247, 5.447), 5.677, '002161', '远望谷'),
        // 闽东电力 2026-09-18：+9.07% 未封（真实样本，必须判否）
        notSealed: isLimitUpBar(B(19.05, 19.60, 19.75, 18.90), 17.97, '000993', '闽东电力'),
        // 「差一分没封」且收盘=最高（前收 10 → 涨停价 11.00，收 10.99）必须判否
        oneCent:   isLimitUpBar(B(11.00, 10.99, 10.99, 10.80), 10.00, '600000', '浦发银行'),
        // 一字跌停（全部相等但下跌）必须判否
        yiziDt:    isLimitUpBar(B(9.00, 9.00, 9.00, 9.00), 10.00, '600000', '浦发银行'),
        // 首根K线（无前收）必须判否
        firstBar:  isLimitUpBar(B(8.00, 8.40, 8.40, 7.90), null, '002161', '远望谷'),
        // 创业板 20%（前收 10 → 涨停价 12.00）
        cyb20:     isLimitUpBar(B(10.00, 12.00, 12.00, 9.95), 10.00, '300001', '特锐德'),
        cyb10:     isLimitUpBar(B(10.00, 11.00, 11.00, 9.95), 10.00, '300001', '特锐德'),
        // 限幅识别
        rMain: limitRatioOf('600000', '浦发银行'), rCyb: limitRatioOf('300001', '特锐德'),
        rKcb:  limitRatioOf('688001', '华兴源创'), rBjs: limitRatioOf('830001', '某北交所'),
        rSt:   limitRatioOf('600000', '*ST 某'),
      };
    });
    ok('涨停判据：一字板（前收7.31→8.04）→ 命中', lim.yizi === true);
    ok('涨停判据：前复权 3 位小数（5.677→6.247）→ 命中', lim.qfq3 === true);
    ok('涨停判据：+9.07% 未封（闽东电力 09-18）→ 不命中', lim.notSealed === false);
    ok('涨停判据：「差一分没封」但收=最高（10.99 vs 11.00）→ 不命中', lim.oneCent === false);
    ok('涨停判据：一字跌停 → 不命中', lim.yiziDt === false);
    ok('涨停判据：首根K线无前收 → 不命中', lim.firstBar === false);
    ok('涨停判据：创业板 20% 涨停 → 命中', lim.cyb20 === true);
    ok('涨停判据：创业板仅 +10% → 不命中', lim.cyb10 === false);
    ok(`限幅识别：主板 ${lim.rMain} / 创业板 ${lim.rCyb} / 科创板 ${lim.rKcb} / 北交所 ${lim.rBjs} / ST ${lim.rSt}`,
       lim.rMain === 0.10 && lim.rCyb === 0.20 && lim.rKcb === 0.20 && lim.rBjs === 0.30 && lim.rSt === 0.05);

    // ② 集成：真的走 renderKline 画一遍，从 ECharts 实例里读回「哪几根被标黄」
    const kline = await page.evaluate(() => {
      const el = document.createElement('div');
      el.style.cssText = 'width:600px;height:320px;position:absolute;left:-9999px;';
      document.body.appendChild(el);
      const bars = [
        { d: '2026-09-10', o: 6.91, c: 7.31, h: 7.40, l: 6.89, v: 399633 },  // 普通阳线
        { d: '2026-09-11', o: 8.04, c: 8.04, h: 8.04, l: 8.04, v: 187876 },  // 一字涨停
        { d: '2026-09-14', o: 8.47, c: 7.24, h: 8.47, l: 7.24, v: 1050332 }, // 天地板（非涨停）
      ];
      renderKline(el, { kind: 'k', bars }, '002161', '远望谷');
      const opt = echarts.getInstanceByDom(el).getOption();
      const data = opt.series[0].data;
      const out = {
        n: data.length,
        isPlain0: Array.isArray(data[0]),
        isPlain2: Array.isArray(data[2]),
        marked1: !Array.isArray(data[1]) && !!(data[1] && data[1].itemStyle),
        color1: data[1] && data[1].itemStyle ? data[1].itemStyle.color : null,
        val1: data[1] && data[1].itemStyle ? JSON.stringify(data[1].value) : null,
      };
      skChart = null;          // 复位全局，避免 resize 时指向已 dispose 的实例
      echarts.dispose(el); el.remove();
      return out;
    });
    ok(`K线涨停标黄：仅第 2 根（一字涨停）带 itemStyle（共 ${kline.n} 根）`,
       kline.n === 3 && kline.isPlain0 && kline.isPlain2 && kline.marked1);
    ok(`K线涨停标黄：颜色为黄色 ${kline.color1}`, kline.color1 === '#facc15');
    ok(`K线涨停标黄：K线数值未被破坏 ${kline.val1}`, kline.val1 === '[8.04,8.04,8.04,8.04]');

    console.log(`  · 页脚：${chk.pageVer.trim()}`);
    if (errs.length) { failed++; console.log('  ❌ 页面报错：\n     ' + errs.slice(0, 5).join('\n     ')); }
    else console.log('  ✅ 无 JS 报错');
    await ctx.close();
  }

  await browser.close();
  console.log(`\n${failed === 0 ? '✅ 全部通过' : '❌ 有 ' + failed + ' 项未通过'}；截图见 ${OUT}`);
  process.exit(failed === 0 ? 0 : 1);
})();
