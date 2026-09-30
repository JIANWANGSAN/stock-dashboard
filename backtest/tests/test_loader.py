"""取数适配层测试 —— 全部离线。

这一层踩过的坑有个共同形状：**不报错，给一个看着正常的结果**。
所以每条测试针对的都是「静默错」，而不是「会不会抛异常」。
"""
from __future__ import annotations

import os
import sys
import pathlib
import json
from types import SimpleNamespace
from datetime import date, timedelta

import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from backtest.loader import (  # noqa: E402
    LoaderError, VibeLoader, _assert_same_instrument, _frame_from_baostock,
    SymbolProvenance, _frame_from_yahoo, _to_ns_index, _yahoo_range, assert_a_share_stock,
    canonical_code, market_of,
)


def test_failed_fetch_preserves_stdout_error_envelope(tmp_path, monkeypatch):
    import backtest.loader as loader
    envelope = {"status": "failed", "errors": [{"source": "fixture", "endpoint": "test", "error": "HTTP 429 请求过快"}]}
    monkeypatch.setattr(loader.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=3, stdout=json.dumps(envelope), stderr=""))
    with pytest.raises(LoaderError, match="HTTP 429 请求过快"):
        loader._run_fetch("fixture", "AAPL", {}, tmp_path, sys.executable, 3)


@pytest.mark.parametrize("stdout", ["not JSON", "[]", '{"errors":null}', '{"errors":[null,7,{"error":42}]}'])
def test_failed_fetch_malformed_envelope_keeps_stderr_cause(tmp_path, monkeypatch, stdout):
    import backtest.loader as loader
    monkeypatch.setattr(loader.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=3, stdout=stdout, stderr="fixture interpreter failed"))
    with pytest.raises(LoaderError, match="fixture interpreter failed"):
        loader._run_fetch("fixture", "AAPL", {}, tmp_path, sys.executable, 3)


@pytest.mark.parametrize("raw,want", [
    ("600519", "600519.SH"), ("601127", "601127.SH"), ("688981", "688981.SH"),
    ("000001", "000001.SZ"), ("002050", "002050.SZ"), ("300308", "300308.SZ"),
    ("430139", "430139.BJ"), ("870204", "870204.BJ"),
    ("sz.300308", "300308.SZ"), ("sh.600519", "600519.SH"),
    ("aapl", "AAPL"), ("00700.hk", "00700.HK"),
])
def test_canonical_code(raw, want):
    assert canonical_code(raw) == want


def test_bare_code_without_a_known_range_is_refused():
    """认不出就报错，不猜 —— 猜错的后果是整只票用错市场规则，而结果看着正常。"""
    with pytest.raises(LoaderError):
        canonical_code("999999")


@pytest.mark.parametrize("code,mkt", [
    ("600519.SH", "a_share"), ("300308.SZ", "a_share"),
    ("AAPL", "us_equity"), ("NVDA", "us_equity"), ("00700.HK", "hk_equity"),
])
def test_market_of(code, mkt):
    assert market_of(code) == mkt


@pytest.mark.parametrize("bad", [
    "^HSI", "BTC/USDT", "RB2410.SHFE", "RELIANCE.NS", "EURUSD=X",
    # 👇 这四个是测试自己抓出来的：形状守卫原本写得太宽（美股放到 10 位字母），
    #    它们通过了形状检查，然后被上游的兜底判成 a_share 拿去查 baostock，
    #    而闸口**放行**。「我以为堵上了」的地方恰恰没堵。
    "EURUSD", "BTCUSDT", "RELIANCE", "BRK.B",
])
def test_market_of_refuses_instead_of_defaulting_to_a_share(bad):
    """🔴 上游 `_detect_market` 认不出时一律回落 a_share。
    在只支持三个市场的这一版里，那个回落会把「不支持」变成「用错规则跑完」。"""
    with pytest.raises(LoaderError):
        market_of(bad)


@pytest.mark.parametrize("code", ["510300.SH", "159915.SZ", "113050.SH", "128036.SZ"])
def test_non_stock_a_share_codes_refused(code):
    """ETF / 指数 / 可转债：baostock 那个端点对它们返回**空序列而不报错**。"""
    with pytest.raises(LoaderError, match="不是个股"):
        assert_a_share_stock(code)


@pytest.mark.parametrize("code", ["600519.SH", "300308.SZ", "000001.SZ", "430139.BJ"])
def test_stock_codes_pass(code):
    assert_a_share_stock(code) is None


def test_pre_bse_history_refused_before_fetch(tmp_path, monkeypatch):
    import backtest.loader as loader
    monkeypatch.setattr(loader, "_run_fetch", lambda *a: pytest.fail("unsupported history must not fetch"))
    ld = VibeLoader(out_dir=tmp_path)
    with pytest.raises(LoaderError, match="2021-11-15"):
        ld._fetch_one("430047.BJ", "2020-01-01", "2024-01-01")


# ── 时间分辨率：最重要的一条 ──

@pytest.mark.parametrize("unit", ["s", "us", "ms", "ns"])
def test_index_is_normalised_to_nanoseconds(unit):
    """🔴 pandas 3 保留原始分辨率，而上游 `_align` 把 `asi8` 一律当纳秒解释。
    秒被当纳秒 → 所有 bar 掉到 1970 年 → 与信号完全对不上 → **一笔都不成交**，
    而且**不抛异常**：产出的是一份「总收益 0.00%」的完整报告。"""
    idx = pd.to_datetime(["2024-01-02", "2024-01-03"]).astype(f"datetime64[{unit}]")
    df = pd.DataFrame({"close": [1.0, 2.0]}, index=idx)
    out = _to_ns_index(df)
    assert out.index.dtype == "datetime64[ns]"
    assert out.index[0].year == 2024, "归一后日期必须还是 2024，不能掉到 1970"


def test_to_ns_index_rejects_a_non_time_index():
    with pytest.raises(LoaderError):
        _to_ns_index(pd.DataFrame({"close": [1.0]}, index=[0]))


# ── 拿回来的是不是同一只票 ──

def test_instrument_mismatch_is_caught():
    """实测过：要 000001.SH（上证综指）会拿到 sz.000001（平安银行），全程不报错。"""
    with pytest.raises(LoaderError, match="不是同一只票"):
        _assert_same_instrument("000001.SH", {"query": {"code": "sz.000001"}})


def test_instrument_match_passes():
    assert _assert_same_instrument("600519.SH", {"query": {"code": "sh.600519"}}) is None


def test_missing_query_is_an_error_not_a_pass():
    """🔴 核对不了 ≠ 核对通过。

    第一版这里写的是「没记下查询串就放行」。Codex 指出：这道校验防的正是
    「拿到另一只票、回测照常跑完」，一旦上游不再落 query.code，它就悄悄变成空操作，
    而**失效本身没人看得出来**。⇒ 跑不起来的校验必须报错。
    """
    with pytest.raises(LoaderError, match="无法核对"):
        _assert_same_instrument("600519.SH", {"rows": []})


# ── 解析 ──

def test_baostock_parser_drops_halted_bars_and_says_how_many():
    payload = {"rows": [
        {"date": "2024-01-02", "open": 10, "high": 11, "low": 9, "close": 10.5,
         "preclose": 10, "volume": 1, "turn": 1, "tradestatus": "1"},
        {"date": "2024-01-03", "open": 0, "high": 0, "low": 0, "close": 0,
         "preclose": 10.5, "volume": 0, "turn": 0, "tradestatus": "0"},   # 停牌
    ]}
    df, halted = _frame_from_baostock(payload)
    assert len(df) == 1 and halted == 1
    assert "pre_close" in df.columns, "引擎找的是 pre_close，名字对不上会静默回退到上一根收盘"
    assert "tradestatus" not in df.columns


def test_yahoo_parser_drops_rows_without_a_close():
    payload = {"chart": {"result": [{
        "timestamp": [1704153600, 1704240000, 1704326400],
        "indicators": {"quote": [{"open": [1, 2, 3], "high": [1, 2, 3], "low": [1, 2, 3],
                                  "close": [1.0, None, 3.0], "volume": [10, 20, 30]}]},
    }]}}
    df, dropped = _frame_from_yahoo(payload)
    assert len(df) == 2 and dropped == 1


def test_yahoo_parser_on_empty_result():
    df, dropped = _frame_from_yahoo({"chart": {"result": [{}]}})
    assert df.empty and dropped == 0


# ── Yahoo 窗口关键字 ──

def test_yahoo_range_covers_the_requested_start():
    today = date.today()
    for days, expect_at_least in [(20, "1mo"), (200, "1y"), (900, "5y")]:
        start = (today - timedelta(days=days)).isoformat()
        got = _yahoo_range(start, today.isoformat())
        assert got, f"{days} 天应当有对应窗口"
    # 覆盖不住就该往大了取，不能给一个短窗口然后静默少几年数据
    old = (today - timedelta(days=3000)).isoformat()
    assert _yahoo_range(old, today.isoformat()) in ("10y", "max")


def test_yahoo_range_rejects_reversed_window():
    with pytest.raises(LoaderError):
        _yahoo_range("2025-01-01", "2024-01-01")


# ── 粒度 ──

@pytest.mark.parametrize("interval", ["1m", "5m", "15m", "1H", "4H"])
def test_non_daily_interval_refused(interval, tmp_path):
    """拿日线顶替分钟线，回测结果看着完全正常，但它测的不是日内。"""
    with pytest.raises(LoaderError, match="日线"):
        VibeLoader(out_dir=tmp_path).fetch(["600519.SH"], "2024-01-01", "2024-02-01", interval=interval)


def test_gate_refuses_everything_market_of_refuses():
    """闸口与取数层必须口径一致 —— 取数层拒绝的，闸口不能放行（反过来也一样）。
    这两处分开写就会漂移，而漂移的表现是「闸口说能跑，跑到一半取数说不认识」。"""
    from backtest.gate import Refusal, plan_backtest
    for code in ["EURUSD", "BTCUSDT", "BRK.B", "^HSI", "RELIANCE", "999999"]:
        with pytest.raises(LoaderError):
            market_of(code)
        got = plan_backtest(codes=[code], start="2021-01-01", end="2025-12-31", style="long")
        assert isinstance(got, Refusal), f"{code} 取数层拒绝了，闸口却放行"
    # 👇 非个股这条第一版漏了覆盖：闸口放行、取数层拒绝，
    #    结果是跑到一半以「一只标的的数据都没取到」中止，说的不是真正的原因。
    for code in ["510300.SH", "159915.SZ", "113050.SH", "128036.SZ"]:
        with pytest.raises(LoaderError):
            assert_a_share_stock(code)
        got = plan_backtest(codes=[code], start="2021-01-01", end="2025-12-31", style="long")
        assert isinstance(got, Refusal), f"{code} 取数层拒绝了，闸口却放行"
        assert "不是个股" in got.reason, f"{code} 的拒绝理由要说到点子上"


def test_ambiguous_ticker_shapes_are_refused():
    """`BRK.B` 这类带类别后缀的写法：本层不认，直接拒 —— 不猜成美股也不兜底成 A股。"""
    for code in ["BRK.B", "BF.A", "RDS.A"]:
        with pytest.raises(LoaderError):
            market_of(code)


def test_cross_check_branch_rejects_a_disagreement(monkeypatch):
    """交叉核对分支本身的测试。

    ⚠️ **诚实地说清楚**：在当前的形状（美股 1-5 位纯字母）下，这个分支
    **不可达** —— 上游对 1-5 位纯字母同样判 us_equity，两边永远一致。
    它是第二层：只有将来形状被放宽（比如放到 10 位、把 EURUSD 收进来）时才承重。
    实测过：把形状放宽后，正是这一层把 EURUSD / BTCUSDT 拦下来的。
    ⇒ 所以这里直接把上游判定打桩成不一致，验分支本身是通的。
    """
    import backtest.loader as L
    monkeypatch.setattr(L, "_detect_market", lambda c: "kr_equity")
    with pytest.raises(LoaderError, match="歧义"):
        L.market_of("AAPL")


def test_both_layers_independently_refuse_a_forex_pair(monkeypatch):
    """两层各自都拦得住 EURUSD —— 冗余是真的，不是一层掩着另一层。"""
    import backtest.loader as L
    # 只留形状这一层（把交叉核对变成永远一致）
    monkeypatch.setattr(L, "_detect_market", lambda c: "us_equity")
    with pytest.raises(LoaderError, match="认不出"):
        L.market_of("EURUSD")


# ── 以下针对 Codex 审计指出的问题（每条都做过变异验证）──

def test_yahoo_parser_requires_all_four_prices():
    """只 dropna(close) 会留下 open=NaN 的行 —— 撮合价取的就是 open，
    那一根不会报错，表现是「少成交了一笔」而不是「数据有问题」。"""
    payload = {"chart": {"result": [{
        "timestamp": [1704153600, 1704240000, 1704326400, 1704412800],
        "indicators": {"quote": [{
            "open":  [1.0, None, 3.0, 4.0],      # 第 2 行缺开盘
            "high":  [1.0, 2.0, 3.0, 4.0],
            "low":   [1.0, 2.0, 3.0, 0.0],       # 第 4 行 low=0，非正价格
            "close": [1.0, 2.0, 3.0, 4.0],
            "volume": [10, 20, 30, 40]}]},
    }]}}
    df, dropped = _frame_from_yahoo(payload)
    assert len(df) == 2 and dropped == 2
    assert df[["open", "high", "low", "close"]].notna().all().all()


def test_ns_guard_survives_python_dash_O():
    """分辨率守卫不能用 assert —— `python -O` 会把 assert 整条删掉，
    而这正是最不能被关掉的一道（它防的是那个不抛异常的静默错）。"""
    src = (pathlib.Path(__file__).resolve().parents[1] / "loader.py").read_text()
    body = src[src.index("def _fetch_one"):]
    assert "assert df.index.dtype" not in body, "关键守卫不能写成 assert"
    assert "datetime64[ns]" in body and "raise LoaderError" in body


def test_success_clears_a_previous_failure_for_the_same_code(tmp_path, monkeypatch):
    """同一只票不能既在 failures 又在 provenance —— 报告层会同时看到「成功」和「失败」。"""
    ld = VibeLoader(out_dir=tmp_path)
    ld.failures["600519.SH"] = "上一次超时"
    df = pd.DataFrame({"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1.0]},
                      index=pd.to_datetime(["2024-01-02"]).astype("datetime64[ns]"))
    prov = SymbolProvenance(code="600519.SH", market="a_share", endpoint="x", rows=1)
    monkeypatch.setattr(ld, "_fetch_one", lambda c, s, e: (df, prov))
    ld.fetch(["600519.SH"], "2024-01-01", "2024-02-01")
    assert "600519.SH" not in ld.failures
    assert "600519.SH" in ld.provenance


def test_failure_clears_a_previous_success_for_the_same_code(tmp_path, monkeypatch):
    ld = VibeLoader(out_dir=tmp_path)
    ld.provenance["600519.SH"] = SymbolProvenance(code="600519.SH", market="a_share", endpoint="x", rows=9)
    def boom(c, s, e): raise LoaderError("这次挂了")
    monkeypatch.setattr(ld, "_fetch_one", boom)
    ld.fetch(["600519.SH"], "2024-01-01", "2024-02-01")
    assert "600519.SH" in ld.failures and "600519.SH" not in ld.provenance


def test_unexpected_parse_error_lands_in_failures(tmp_path, monkeypatch):
    """解析层的意外不该炸穿整次回测 —— 那样看不出是哪只票、哪个端点出的问题。"""
    ld = VibeLoader(out_dir=tmp_path)
    def boom(c, s, e): raise ValueError("数组长度不齐")
    monkeypatch.setattr(ld, "_fetch_one", boom)
    out = ld.fetch(["600519.SH", "AAPL"], "2024-01-01", "2024-02-01")
    assert out == {} and set(ld.failures) == {"600519.SH", "AAPL"}
    assert "数组长度不齐" in ld.failures["600519.SH"]
