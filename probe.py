# -*- coding: utf-8 -*-
"""第五轮：板块备用源 / 涨停池历史深度"""
import sys, json, time
sys.stdout.reconfigure(encoding='utf-8')
import urllib.request

UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36'}

def get(url, timeout=12):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('utf-8', errors='ignore')

print('--- A. 涨停池历史深度 ---')
for d in ['20260904', '20260820', '20260810', '20260801', '20260720', '20260703']:
    try:
        r = get('https://push2ex.eastmoney.com/getTopicZTPool?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&Pageindex=0&pagesize=5&sort=fbt%3Aasc&date=' + d)
        j = json.loads(r)
        pool = (j.get('data') or {}).get('pool') or []
        names = [p['n'] for p in pool[:3]]
        print(f'  {d}: {len(pool) if pool else 0} 条 (tc={ (j.get("data") or {}).get("tc") }) {names}')
    except Exception as e:
        print(f'  {d}: 失败 {type(e).__name__}')
    time.sleep(0.5)

print()
print('--- B. 板块备用源 ---')
cands = [
    ('东财push2主域', 'https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=10&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m:90+t:3+f:!50&fields=f3,f12,f14,f104,f105'),
    ('东财82节点', 'https://82.push2.eastmoney.com/api/qt/clist/get?pn=1&pz=10&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m:90+t:3+f:!50&fields=f3,f12,f14,f104,f105'),
    ('东财48节点', 'https://48.push2.eastmoney.com/api/qt/clist/get?pn=1&pz=10&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m:90+t:3+f:!50&fields=f3,f12,f14,f104,f105'),
    ('腾讯行业板块', 'https://proxy.finance.qq.com/cgi/cgi-bin/rank/pt/getRank?board_type=hy2&sort_type=price&direct=down&offset=0&count=10'),
    ('腾讯概念板块', 'https://proxy.finance.qq.com/cgi/cgi-bin/rank/pt/getRank?board_type=gn&sort_type=price&direct=down&offset=0&count=10'),
    ('新浪板块', 'https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeStockCount?node=hangye_ZL01'),
]
for name, url in cands:
    try:
        r = get(url)
        print(f'  [{name}] OK -> {r[:220]}')
    except Exception as e:
        print(f'  [{name}] 失败 {type(e).__name__}')
    time.sleep(0.4)

print()
print('--- C. 个股标签(slist) 是否仍可用 ---')
try:
    r = get('https://push2.eastmoney.com/api/qt/slist/get?spt=3&fltt=2&invt=2&secid=1.605577&fields=f12,f13,f14,f3&pn=1&np=1&pz=30')
    j = json.loads(r)
    print('  龙版传媒所属板块:', [it['f14'] for it in j['data']['diff'][:14]])
except Exception as e:
    print('  失败', type(e).__name__)
