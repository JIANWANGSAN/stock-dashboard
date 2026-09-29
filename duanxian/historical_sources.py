"""公开历史补源：每行核对资料日，不把当前资金流或行业标签冒充历史题材。"""
from __future__ import annotations

import datetime as dt
import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout
from zoneinfo import ZoneInfo

from . import fetchers as dr

TZ = ZoneInfo('Asia/Shanghai')
_HISTORY_BUDGET = 45
THS = 'https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool'


def public_limit_reasons(date: str):
    day = dt.datetime.strptime(date, '%Y%m%d').date()
    if day.strftime('%Y%m%d') != date:
        raise ValueError('日期须为YYYYMMDD')
    reasons, seen = {}, set()
    total = None
    for page in range(1, 21):
        response = dr._direct_get(THS, params={'page': page, 'limit': 200,
            'field': '199112,10,9001,330323,330324,330325,9002,330329,133971,133970,1968584,3475914,9003,9004',
            'filter': 'HS,GEM2STAR', 'order_field': '330324', 'order_type': '0', 'date': date},
            headers={'User-Agent': 'Mozilla/5.0'}, timeout=12)
        response.raise_for_status()
        env = response.json()
        if env.get('status_code') != 0:
            raise ValueError('源站未成功返回涨停揭秘')
        data = env['data']; rows = data['info']; count = int(data['page']['total'])
        if total is not None and total != count:
            raise ValueError('翻页期间名单变化，请重试')
        total = count
        if not rows and len(seen) < total:
            raise ValueError('分页缺失')
        for row in rows:
            code = str(row.get('code', ''))
            if not re.fullmatch(r'[0-9]{6}', code) or code in seen:
                raise ValueError('重复或无效代码，分页不完整')
            seen.add(code)
            # 请求参数不是日期证据；逐行封板时间戳必须落在目标日。
            try:
                stamp = float(row['first_limit_up_time'])
                if not math.isfinite(stamp): raise ValueError()
            except (KeyError, TypeError, ValueError):
                raise ValueError(f'{code}缺少有效封板时间，无法核对资料日') from None
            if dt.datetime.fromtimestamp(stamp, TZ).date() != day:
                raise ValueError('源站返回其他交易日，未使用')
            reason = row.get('reason_type')
            if isinstance(reason, str) and reason.strip() and reason not in {'-', '暂无'}:
                reasons[code] = dr._clean_reason(reason)
        if len(seen) >= total:
            if len(seen) != total:
                raise ValueError('分页数量不一致')
            break
        time.sleep(.2)
    else:
        raise ValueError('超过分页上限，未返回不完整名单')
    if not seen:
        raise ValueError(f"{date} 未返回涨停记录，无法核对资料日")
    return reasons, (f'来源：同花顺涨停揭秘；{day}；原因覆盖{len(reasons)}/{len(seen)}条；'
                     '封板时间逐行核对；沪深主板/创业板/科创板，源站编辑归因非公司确认，不含北交所。')


def history(code: str, date: str) -> list[dict]:
    """同花顺板块日线。按源站收盘价计算涨跌；跨年补取上一年，保留逐日来源。"""
    if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', date):
        raise ValueError('历史板块日期须为YYYY-MM-DD')
    day = dt.date.fromisoformat(date)
    if not re.fullmatch(r'(?:88|30)[0-9]{4}', code):
        raise ValueError('无效板块标识')
    begin = day - dt.timedelta(days=30)
    rows = []
    for year in range(begin.year, day.year + 1):
        url = f'https://d.10jqka.com.cn/v4/line/bk_{code}/01/{year}.js'
        response = dr._direct_get(url, headers={'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://q.10jqka.com.cn/'}, timeout=8)
        response.raise_for_status()
        # 不执行远端 JavaScript；只接受带本次代码与年份的固定 JSONP 信封。
        match = re.fullmatch(r'\s*quotebridge_v4_line_bk_' + code + '_01_' + str(year)
                             + r'\((\{.*\})\);?\s*', response.text, re.S)
        if not match:
            raise ValueError('历史行情身份或格式不一致')
        raw = json.loads(match.group(1))['data']
        for line in raw.split(';'):
            if not line:
                continue
            parts = line.split(',')
            current = dt.datetime.strptime(parts[0], '%Y%m%d').date()
            if current > day or current < begin:
                continue
            close, amount = float(parts[4]), float(parts[6])
            if not all(math.isfinite(x) for x in (close, amount)) or close <= 0 or amount < 0:
                raise ValueError('历史行情含非法数值')
            rows.append({'date': current.isoformat(), 'close': close, 'amount': amount, 'source': url})
    rows.sort(key=lambda r: r['date'])
    if len(rows) < 2 or rows[-1]['date'] != date or len({r['date'] for r in rows}) != len(rows):
        raise ValueError(f'未覆盖目标日{date}及前一资料日')
    for i in range(1, len(rows)):
        rows[i]['pct'] = (rows[i]['close'] / rows[i-1]['close'] - 1) * 100
    return rows


def historical_activity(date: str, groups: dict | None = None) -> str:
    """历史成交与价格资料；资金流不可用时不把成交额叫净流入。"""
    from bs4 import BeautifulSoup
    kind = 'gn' if groups else 'thshy'
    response = dr._direct_get(f'https://q.10jqka.com.cn/{kind}/',
        headers={'User-Agent': 'Mozilla/5.0'}, timeout=8)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, 'html.parser')
    catalog = {}
    for link in soup.select('a[href*="/detail/code/"]'):
        match = re.search(r'/' + kind + r'/detail/code/((?:88|30)[0-9]{4})/', link.get('href', ''))
        if match:
            catalog[match.group(1)] = link.get_text(strip=True)
    members = [{'code': code, 'name': name} for code, name in catalog.items()
               if not groups or any(k.lower() in name.lower() for keys in groups.values() for k in keys)]
    if not members:
        raise ValueError('当前板块目录未返回匹配项')
    def one(r):
        try:
            code = r['code']
            if groups:
                detail = dr._direct_get(f'https://q.10jqka.com.cn/gn/detail/code/{code}/',
                    headers={'User-Agent': 'Mozilla/5.0'}, timeout=8)
                detail.raise_for_status()
                identity = BeautifulSoup(detail.content, 'html.parser').select_one('input#clid')
                if identity is None:
                    raise ValueError('概念指数代码未返回')
                code = str(identity.get('value', ''))
            rows=history(code,date)
            prior=rows[-6] if len(rows)>=6 else None
            return {'name':r['name'],**rows[-1], 'five': ((rows[-1]['close']/prior['close']-1)*100 if prior else None),
                    'start':prior['date'] if prior else None}, None
        except Exception as exc:
            return None, f"{r.get('name')}：{str(exc)[:80] if isinstance(exc, ValueError) else type(exc).__name__}"
        finally:
            time.sleep(.2)
    pool = ThreadPoolExecutor(max_workers=3)
    futures = {pool.submit(one, member): member for member in members}
    results = []
    completed = set()
    try:
        for future in as_completed(futures, timeout=_HISTORY_BUDGET):
            completed.add(future)
            results.append(future.result())
    except FuturesTimeout:
        for future, member in futures.items():
            if future in completed:
                continue
            if future.done() and not future.cancelled():
                results.append(future.result())
            elif future.cancel():
                results.append((None, f"{member['name']}：超出总预算，尚未执行"))
            else:
                results.append((None, f"{member['name']}：本次取数超时"))
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    valid=[v for v,e in results if v]; missing=[e for v,e in results if e]
    if not valid:
        raise ValueError('所有板块均缺目标日历史行情')
    lines=[f'{date} 历史板块成交与涨跌（同花顺板块日线，已核对资料日；不是主力净流入）。',
        f'当前板块目录回看历史，覆盖{len(valid)}/{len(members)}项；不代表当时完整板块集合，不用于历史成分回测。',
        '板块存在成分重叠，成交额不可相加；近5交易日不等同本周。历史主力净流入与个股成交额全市场排名本源不提供。']
    if missing:
        lines.insert(0, '数据降级：以下仅为成功取回的样本，非完整板块榜单，不可据此判断全市场最活跃板块。')
    chosen=sorted(valid,key=lambda x:x['amount'],reverse=True)[:12] if not groups else valid
    for v in chosen:
        five=f"，近5交易日({v['start']}至{date}){v['five']:+.2f}%" if v['five'] is not None else '，近5日不足'
        lines.append(f"  {v['name']}：当日{v['pct']:+.2f}%，成交{v['amount']/1e8:.2f}亿元{five}")
    if missing:
        lines.append('未覆盖：'+'；'.join(missing))
    return '\n'.join(lines)
