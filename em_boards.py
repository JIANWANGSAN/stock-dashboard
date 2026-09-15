# -*- coding: utf-8 -*-
"""东财板块映射（共享模块）：板块名 → 东财板块代码（BKxxxx）。

用途：
  1) fetch_data.py —— 给板块涨幅榜附加 `em_code`，前端点击板块即可看它的日K/周K（东财 secid=90.BKxxxx）
  2) module4.py    —— 找「两榜重合板块」的成分股

本模块自带 http_get，**不导入 fetch_data**，避免循环导入。
"""
import json, ssl, time, re, urllib.request
from concurrent.futures import ThreadPoolExecutor

EM_HOSTS = ['push2.eastmoney.com', 'push2delay.eastmoney.com', '82.push2.eastmoney.com']
_UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

# 腾讯板块名 → 东财实际存在的板块名（东财查不到原名时的兜底别名）
EM_ALIAS = {
    '棉花': '棉纺', '棉花概念': '棉纺', '棉纺': '棉纺',
    '玉米': '粮食种植', '玉米概念': '粮食种植',
    '光芯片': 'CPO', '博通概念': 'CPO', '谷歌概念': 'CPO', '光通信': '光通信模块',
    '覆铜板': 'PCB', 'PCB概念': 'PCB',
    '猪肉': '猪肉概念',
}


def em_http(url, timeout=8, retry=2):
    """裸 HTTP（关闭证书校验，兼容企业代理/MITM）。"""
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    except Exception:
        ctx = None
    for i in range(retry):
        try:
            req = urllib.request.Request(url, headers=_UA)
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                return r.read().decode('utf-8', errors='ignore')
        except Exception:
            if i == retry - 1:
                return None
            time.sleep(0.6)
    return None


def em_get(path, timeout=10):
    """东财接口请求，带主机冗余。path 形如 /api/qt/clist/get?..."""
    for host in EM_HOSTS:
        t = em_http('https://%s%s' % (host, path), timeout=timeout)
        if t:
            return t
    return None


def norm_board(name):
    if not name:
        return ''
    return re.sub(r'(概念|板块|行业|产业|指数|股)$', '', name.strip())


def build_em_board_map(max_pages=13):
    """东方财富 概念(t:3) + 行业(t:2) 板块列表 → {原名: code, 归一名: code}（分页并发）。"""
    jobs = [(fs, pn) for fs in ('m:90+t:3+f:!50', 'm:90+t:2+f:!50')
            for pn in range(1, max_pages)]

    def _page(job):
        fs, pn = job
        path = ('/api/qt/clist/get?pn=%d&pz=100&po=1&np=1'
                '&fltt=2&invt=2&fid=f3&fs=%s&fields=f12,f14' % (pn, fs))
        t = em_get(path)
        if not t:
            return []
        try:
            diff = (json.loads(t).get('data') or {}).get('diff') or []
        except Exception:
            return []
        return [(it.get('f12'), it.get('f14')) for it in diff]

    with ThreadPoolExecutor(max_workers=6) as ex:
        pages = list(ex.map(_page, jobs))

    m = {}
    for rows in pages:
        for c, n in rows:
            if c and n:
                m[n] = c
                m[norm_board(n)] = c
    return m


def match_em_code(name, em_map):
    """板块名 → 东财代码：原名 → 归一名 → 别名 → 互相包含（宽松兜底）。"""
    if name in em_map:
        return em_map[name]
    n = norm_board(name)
    if n and n in em_map:
        return em_map[n]
    alias = EM_ALIAS.get(name) or EM_ALIAS.get(n)
    if alias:
        if alias in em_map:
            return em_map[alias]
        na = norm_board(alias)
        if na and na in em_map:
            return em_map[na]
    for k, v in em_map.items():
        if len(k) >= 2 and (k in name or name in k):
            return v
    return None
