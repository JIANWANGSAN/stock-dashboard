"""Two allowlisted Research sources; no general registry or user configuration."""
import argparse
import contextlib
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import redact_text
from sources._http import capture
from sources.baostock_src import baostock_kdata
from sources.yahoo import yahoo_kline


def fetch(endpoint, symbol, args, out_dir):
    with capture(out_dir, endpoint, endpoint) as cap, contextlib.redirect_stdout(sys.stderr):
        if endpoint == 'bs_kline_qfq':
            rows = baostock_kdata(symbol, **args, adjustflag='2')
        elif endpoint == 'yahoo_kline':
            symbol = symbol.upper()
            market = 'HK' if symbol.endswith('.HK') else 'US'
            rows = yahoo_kline(symbol.removesuffix('.HK'), market=market, **args)
        else:
            raise ValueError('Unsupported backtest endpoint')
    if not rows or not cap.raws:
        raise ValueError('数据源没有返回可用于回测的日线')
    return {'status': 'ok', 'extra': {'raw_files': [r['raw_ref'] for r in cap.raws]}}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    for name in ('endpoint', 'symbol', 'args', 'out-dir'): p.add_argument('--'+name, required=True)
    a = p.parse_args()
    try:
        result = fetch(a.endpoint, a.symbol, json.loads(a.args), a.out_dir)
    except Exception as exc:
        print(json.dumps({'status':'failed','errors':[{'error':redact_text(f"{type(exc).__name__}: {str(exc)[:200]}")}]}, ensure_ascii=False))
        sys.exit(3)
    print(json.dumps(result, ensure_ascii=False))
