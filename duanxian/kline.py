"""个股日K（腾讯 hist 源，与本仓库其他日K同源）。

只用这一条链路的原因：东财 push2his 在部分网络下被封；同花顺日K盘后不含当日；
腾讯 hist 收盘后即含当日，且历史事实不会变 → **落盘永久缓存**，不反复打源站。

对外`daily(code, days)` 返回 [{date, open, high, low, close, volume, amount}]，
按日期升序。取数失败返回 None —— 上层如实显示「无数据」，绝不假装成空K线。
"""
from __future__ import annotations

import json
import os
from typing import Optional

from duanxian.paths import data_path

DEFAULT_DAYS = 60


def _cache_dir() -> str:
    d = data_path("cache/kline")
    os.makedirs(d, exist_ok=True)
    return d


def _sym(code: str) -> str:
    c = str(code).zfill(6)
    return ("sh" if c.startswith(("6", "9")) else "sz") + c


def _cache_file(code: str, days: int) -> str:
    return os.path.join(_cache_dir(), "%s_%d.json" % (str(code).zfill(6), days))


def daily(code: str, days: int = DEFAULT_DAYS) -> Optional[list]:
    """近 N 个交易日日K（升序）。命中缓存直接返回；取不到返回 None。"""
    code = str(code).zfill(6)
    path = _cache_file(code, days)
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                rows = json.load(fh)
            if isinstance(rows, list) and rows:
                return rows
        except (OSError, ValueError):
            pass
    try:
        import akshare as ak

        df = ak.stock_zh_a_hist_tx(symbol=_sym(code), adjust="")
        if df is None or not len(df):
            return None
        rows = []
        for _, r in df.iterrows():
            try:
                rows.append({
                    "date": str(r["date"])[:10],
                    "open": float(r["open"]), "high": float(r["high"]),
                    "low": float(r["low"]), "close": float(r["close"]),
                    "volume": float(r.get("amount", 0) or 0),   # 腾讯源 amount = 成交量(手)
                })
            except (KeyError, TypeError, ValueError):
                continue
        rows.sort(key=lambda x: x["date"])
    except Exception:  # noqa: BLE001
        return None
    if not rows:
        return None
    rows = rows[-days:]
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False)
    except OSError:
        pass
    return rows


if __name__ == "__main__":
    import sys
    _c = sys.argv[1] if len(sys.argv) > 1 else "600977"
    _n = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    print(json.dumps(daily(_c, _n), ensure_ascii=False, indent=2))