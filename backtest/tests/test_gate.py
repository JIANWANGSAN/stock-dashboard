"""闸口测试 —— 重点在**拒绝**分支：能算出数字不等于这个数字有意义。"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from backtest.gate import MARKETS, Plan, Refusal, plan_backtest  # noqa: E402

LONG = dict(start="2021-01-01", end="2025-12-31", style="long")


def test_a_share_long_passes_with_full_limits():
    p = plan_backtest(codes=["600519.SH"], **LONG)
    assert isinstance(p, Plan) and p
    assert p.market.key == "a_share" and p.market.engine == "ChinaAEngine"
    joined = " ".join(p.limits)
    # 限制必须**逐条到位**：少一条，读结果的人就少知道一个约束
    assert "T+1" in joined and "不允许" in joined and "100 股整手" in joined
    assert "印花税" in joined and "CNY" in joined and "±10%" in joined


@pytest.mark.parametrize("code,market", [
    ("600519.SH", "a_share"), ("300308.SZ", "a_share"),
    ("AAPL", "us_equity"), ("00700.HK", "hk_equity"),
])
def test_market_routing(code, market):
    p = plan_backtest(codes=[code], **LONG)
    assert isinstance(p, Plan) and p.market.key == market


def test_bare_six_digits_get_an_exchange():
    """裸六位必须被补成带后缀的写法 —— 否则市场归属是兜底兜出来的，不是判出来的。"""
    p = plan_backtest(codes=["600519"], **LONG)
    assert isinstance(p, Plan) and p.codes == ["600519.SH"]


# ── 以下每一条都是"能算但没意义"，闸口必须拦住 ──

def test_mixed_markets_refused():
    r = plan_backtest(codes=["600519.SH", "AAPL"], **LONG)
    assert isinstance(r, Refusal) and not r
    assert "一个市场" in r.reason and r.remedy


def test_a_share_intraday_refused_because_t_plus_1():
    r = plan_backtest(codes=["600519.SH"], start="2024-01-01", end="2025-12-31", style="intraday")
    assert isinstance(r, Refusal)
    assert "T+1" in r.reason          # 理由要说到点子上，不是泛泛的"不支持"


def test_us_intraday_refused_because_no_minute_bars():
    """美股能 T+0，所以拒绝理由必须是**数据粒度**，不能复用 T+1 那套说辞。"""
    r = plan_backtest(codes=["AAPL"], start="2024-01-01", end="2025-12-31", style="intraday")
    assert isinstance(r, Refusal)
    assert "分钟" in r.reason and "T+1" not in r.reason


def test_too_short_window_refused_for_long_style():
    r = plan_backtest(codes=["600519.SH"], start="2025-06-01", end="2025-12-31", style="long")
    assert isinstance(r, Refusal) and "480" in r.reason


def test_same_window_passes_for_swing():
    """同一个区间：长线不够、短线够 —— 门槛必须跟口径走，不是一刀切。"""
    kw = dict(codes=["600519.SH"], start="2025-01-01", end="2025-12-31")  # 约 260 根：短线够、长线不够
    assert isinstance(plan_backtest(style="long", **kw), Refusal)
    assert isinstance(plan_backtest(style="swing", **kw), Plan)


def test_short_selling_refused_on_a_share_but_allowed_elsewhere():
    assert isinstance(plan_backtest(codes=["600519.SH"], allow_short=True, **LONG), Refusal)
    assert isinstance(plan_backtest(codes=["AAPL"], allow_short=True, **LONG), Plan)


@pytest.mark.parametrize("codes", [[], ["999999"], ["^HSI"], ["BTC/USDT"]])
def test_unusable_codes_refused(codes):
    assert isinstance(plan_backtest(codes=codes, **LONG), Refusal)


def test_reversed_window_refused():
    r = plan_backtest(codes=["600519.SH"], start="2025-12-31", end="2021-01-01", style="long")
    assert isinstance(r, Refusal) and "start" in r.reason


def test_unknown_style_refused():
    r = plan_backtest(codes=["600519.SH"], start="2021-01-01", end="2025-12-31", style="逢低吸纳")
    assert isinstance(r, Refusal) and "口径" in r.reason


def test_refusal_is_falsy_and_plan_is_truthy():
    """`if not plan:` 是调用方唯一要写的判断 —— 这个约定不能坏。"""
    assert not plan_backtest(codes=["600519.SH", "AAPL"], **LONG)
    assert plan_backtest(codes=["600519.SH"], **LONG)


def test_every_market_declares_every_rule():
    """新增市场时忘填一项 = 结果少一条限制，而没人会发现。"""
    for key, m in MARKETS.items():
        assert m.engine and m.lot and m.fees and m.currency, key
        assert isinstance(m.can_short, bool) and isinstance(m.same_day_roundtrip, bool)


def test_config_carries_style_position_adjustment():
    long_p = plan_backtest(codes=["600519.SH"], **LONG)
    swing_p = plan_backtest(codes=["600519.SH"], start="2024-01-01", end="2025-12-31", style="swing")
    assert long_p.to_config()["position_adjustment"] == "hold"
    assert swing_p.to_config()["position_adjustment"] == "rebalance"


@pytest.mark.parametrize("cash", [0, -1, float("nan"), float("inf"), "一百万", None, 5000])
def test_bad_initial_cash_refused(cash):
    """一手都买不起时引擎照样输出「总收益 0.00%」的完整报告 —— 看不出是资金不够。"""
    r = plan_backtest(codes=["600519.SH"], start="2021-01-01", end="2025-12-31",
                      style="long", initial_cash=cash)
    assert isinstance(r, Refusal), f"{cash!r} 不该放行"


def test_plan_records_allow_short():
    """运行期要拿它与策略真正发出的信号对一次 —— 只靠调用方声明拦不住。"""
    p = plan_backtest(codes=["AAPL"], start="2021-01-01", end="2025-12-31",
                      style="long", allow_short=True)
    assert p.allow_short is True
    assert plan_backtest(codes=["AAPL"], start="2021-01-01", end="2025-12-31", style="long").allow_short is False
