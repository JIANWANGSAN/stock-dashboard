# -*- coding: utf-8 -*-
"""重建 `ths_board_map.py`（东财产业板块 → 同花顺板块指数 映射）。

何时需要重跑：`fetch_data.SECTOR_WATCH` 的板块清单有增删，或某板块改名后兜底取不到K线。

原理：同花顺板块指数没有公开的「代码→名称」清单接口，但 K线接口
`https://d.10jqka.com.cn/v6/line/bk_<码>/01/last.js` 的返回里**带 name 字段**。
故直接扫码段建「名称→代码」全表，再按人工确认的别名表（ALIAS）对齐到东财板块。

用法：python build_ths_map.py        # 扫描 + 校验 + 写回 ths_board_map.py
"""
import sys, json, ssl, urllib.request
from concurrent.futures import ThreadPoolExecutor
sys.stdout.reconfigure(encoding='utf-8')
import fetch_data as F

# 同花顺板块指数码段（实测覆盖全部行业与概念）：881xxx 行业 / 885xxx-8863xx 概念
RANGES = [(881100, 881301), (885300, 886000), (886000, 886400)]

# 人工确认的别名表：东财展示名 → 同花顺板块名（None = 同花顺没有对应板块）
ALIAS = {
    'CPO': '共封装光学(CPO)', 'PCB': 'PCB概念', '半导体': '半导体', '存储': '存储芯片',
    '数据中心': '数据中心(AIDC)', '云计算': '云计算', 'AIGC': 'AIGC概念', '商业航天': '商业航天',
    '机器人': '机器人概念', '无人驾驶': '无人驾驶', '电力': '电力', '电网': '电网设备',
    '核聚变': '可控核聚变', '光伏': '光伏概念', '锂电池': '锂电池概念', '军工': '军工',
    '石油': '石油加工贸易', '天然气': '天然气', '小金属': '小金属', '黄金': '黄金概念',
    '银行': '银行', '保险': '保险', '证券': '证券', '创新药': '创新药',
    'CRO': 'CRO概念', '稀土': '稀土永磁', '消费电子': '消费电子',
    '消费': None,          # 东财 BK1652「新消费」，同花顺全码段无对应
}

_ctx = ssl.create_default_context(); _ctx.check_hostname = False; _ctx.verify_mode = ssl.CERT_NONE
UA = {'User-Agent': F.UA['User-Agent'], 'Referer': 'http://q.10jqka.com.cn/'}


def probe(code):
    """返回 (码, 名称, 日K根数)；取不到返回 None"""
    u = 'https://d.10jqka.com.cn/v6/line/bk_%s/01/last.js' % code
    try:
        req = urllib.request.Request(u, headers=UA)
        with urllib.request.urlopen(req, timeout=6, context=_ctx) as r:
            b = r.read().decode('utf-8', 'ignore')
        if '(' not in b:
            return None
        j = json.loads(b[b.index('(') + 1:b.rindex(')')])
        nm = (j.get('name') or '').strip()
        if not nm:
            return None
        return code, nm, len([x for x in (j.get('data') or '').split(';') if x])
    except Exception:
        return None


def scan():
    codes = [str(c) for a, b in RANGES for c in range(a, b)]
    table = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for r in ex.map(probe, codes):
            if r:
                table.setdefault(r[1], r[0])
    return table


def main():
    print('扫描同花顺板块指数码段…')
    table = scan()
    print('命中 %d 个板块指数' % len(table))

    name2code = {}
    for disp, bk in F.SECTOR_WATCH:
        ths_name = ALIAS.get(disp)
        if ths_name is None:
            print('  ⏭  %-6s %-6s → 同花顺无对应（东财接口恢复后再取）' % (disp, bk))
            continue
        code = table.get(ths_name)
        if not code:
            print('  ❌ %-6s %-6s → 别名「%s」在扫描结果里找不到' % (disp, bk, ths_name))
            continue
        name2code[bk] = (code, ths_name)
        print('  ✅ %-6s %-6s → bk_%-6s %s' % (disp, bk, code, ths_name))

    lines = ['# -*- coding: utf-8 -*-',
             '"""东财产业板块 → 同花顺板块指数 映射表。',
             '',
             '用途：东财 K线接口（push2his/api/qt/stock/kline/get）对单一 IP 高频请求会**按路径封禁**',
             '（表现为 RemoteDisconnected / curl exit 56，而同主机的分时接口仍正常），',
             '此时用同花顺板块指数作为 K线兜底数据源。',
             '',
             '同花顺接口：',
             '  日K  https://d.10jqka.com.cn/v6/line/bk_<码>/01/last.js',
             '  周K  https://d.10jqka.com.cn/v6/line/bk_<码>/11/last.js（部分板块只有 12 分片可用）',
             '  返回：quotebridge_v6_line_bk_<码>_<周期>_last({"name":"板块名","data":"日期,开,高,低,收,量,额;..."})',
             '  —— 价格字段顺序是「开,高,低,收」，与东财的「开,收,高,低」不同，写盘前必须换序。',
             '',
             '⚠️ 同花顺板块指数与东财板块指数的**基期不同**，点位不可混用。',
             '   故调用方按「整批单一数据源」策略取数：先探测东财，通则全用东财，不通则全用同花顺。',
             '',
             '本文件由 `python build_ths_map.py` 自动生成（别名表 ALIAS 为人工确认）。',
             '"""',
             '',
             '# 东财板块代码 → (同花顺指数码, 同花顺板块名)',
             'THS_MAP = {']
    for bk, (c, nm) in name2code.items():
        lines.append("    '%s': ('%s', '%s')," % (bk, c, nm))
    if 'BK1652' not in name2code:
        lines.append("    # 'BK1652'（新消费）：同花顺无对应板块（全码段扫描 881100-886400 均无），")
        lines.append("    # 只能等东财 K线接口解封后取数。")
    lines.append('}')
    lines.append('')
    open('ths_board_map.py', 'w', encoding='utf-8').write('\n'.join(lines))
    print('✅ 已写回 ths_board_map.py（%d 个板块）' % len(name2code))


if __name__ == '__main__':
    main()
