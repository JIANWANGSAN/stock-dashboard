"""把本产品的取数层接到回测引擎上 —— 整个移植里唯一的新代码。

引擎与数据之间只有一条缝：``loader.fetch()`` 返回 ``{代码: OHLCV DataFrame}``。
上游自带 25 个 loader，我们一个都不用 —— 数据一律走本产品的注册表端点，
这样回测用的每一根 bar 都带 ``raw_ref``，与看板上的数字同源、可复算。

支持的市场（第一版）::

    A股    600519.SH / 300308.SZ / 300308   → bs_kline_qfq（baostock 前复权，含 preclose 与停牌标记）
    美股    AAPL / NVDA                      → yahoo_kline
    港股    00700.HK / 0700.HK               → yahoo_kline

🔴 **只做日线**。要 5 分钟线就明说取不到，绝不悄悄拿日线顶替 ——
   那会让一个"日内策略"的回测结果看着完全正常，而它测的根本不是日内。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

from backtest.engines._market_hooks import _detect_market

# 仓库根 = 本文件的上两级（backtest/loader.py → backtest/ → 仓库根）
REPO_ROOT = Path(__file__).resolve().parent.parent
FETCH_SCRIPT = REPO_ROOT / "research_data" / "fetch_backtest.py"

SUPPORTED_MARKETS = ("a_share", "us_equity", "hk_equity")
#: 引擎按 bar 撮合，这一版只喂日线。上游支持的分钟级要等做 T 那一期。
SUPPORTED_INTERVALS = ("1D", "1d", "D", "day", "daily")

_A_FIELDS = "date,open,high,low,close,preclose,volume,turn,tradestatus"
#: Yahoo 只认这些窗口关键字，给不了起止日期 —— 取覆盖得住的最小那个再切。
_YAHOO_RANGES = (("1mo", 31), ("3mo", 92), ("6mo", 184), ("1y", 366),
                 ("2y", 731), ("5y", 1827), ("10y", 3653), ("max", 10**6))


class LoaderError(RuntimeError):
    """取数在**能不能开始回测**这一层就失败了 —— 不该被当成"这只票没数据"往下走。"""


def price_basis(market: str) -> str:
    """Source-specific price/accounting limits, shared by preview and runtime disclosure."""
    source = (
        "A 股取 baostock 前复权日线（adjustflag=2），不是当日未复权成交价；复权影响不等于分红现金到账。"
        if market == "a_share" else
        "港美股取 Yahoo chart 的 indicators.quote OHLC，不使用 adjclose；不可把它当作含分红的总回报序列。"
    )
    return source + "引擎不单独记现金分红、再投资或公司行动现金流。历史序列按本次取数版本重建，可能随公司行动或上游修订重述，并非当时可见快照；不同日期重跑可变，收益偏差方向不作保证。"


@dataclass
class SymbolProvenance:
    """一只票的数据来自哪儿 —— 回测结论要能顺着这条链查回去。"""

    code: str
    market: str
    endpoint: str
    raw_refs: List[str] = field(default_factory=list)
    rows: int = 0
    first_bar: Optional[str] = None
    last_bar: Optional[str] = None
    halted_bars: int = 0
    note: str = ""
    price_basis: str = ""


def canonical_code(code: str) -> str:
    """把代码规整成**市场判得出来**的写法。

    ``_detect_market`` 对认不出的格式一律回落 ``a_share`` —— 也就是说打错的美股代码
    会被静默当成 A 股去跑（T+1、涨跌停、整手全套上身），而结果看着完全正常。
    ⇒ 六位纯数字在这里就补上交易所后缀，让归属是**判出来的**而不是**兜底兜出来的**。
    """
    c = str(code).strip().upper()
    if not c:
        raise LoaderError("代码是空的")
    # baostock 风格 sz.300308 / sh.600519 → 300308.SZ
    m = re.fullmatch(r"(SZ|SH|BJ)\.(\d{6})", c)
    if m:
        return f"{m.group(2)}.{m.group(1)}"
    if re.fullmatch(r"\d{6}", c):
        return f"{c}.{_a_share_exchange(c)}"
    return c


def _a_share_exchange(six: str) -> str:
    """六位代码 → 交易所后缀。

    按号段前缀查表，**认不出就报错**，不猜一个 —— 猜错的后果是整只票用错市场规则
    （涨跌停带、T+1、费率全套），而回测结果看着完全正常。
    """
    return _a_lookup(six)[0]


#: 号段 → (交易所, 是不是个股)。长前缀在前，匹配到第一个即用。
#: 🔴 `is_stock=False` 的（ETF / 指数 / 可转债）在这一版取不到 —— baostock 的
#:    前复权日 K 是**个股**接口，对它们返回 0 行而**不报错**。不在这儿拦住，
#:    就会变成「回测跑完了、结果是空的」，而空的原因没人看得出来。
_A_PREFIX: tuple[tuple[str, str, bool], ...] = (
    # 上交所
    ("688", "SH", True), ("689", "SH", True),          # 科创板
    ("110", "SH", False), ("111", "SH", False), ("113", "SH", False),  # 沪市可转债
    ("6", "SH", True),                                  # 主板股票
    ("50", "SH", False), ("51", "SH", False), ("52", "SH", False),
    ("53", "SH", False), ("56", "SH", False), ("58", "SH", False),     # 沪市基金 / ETF
    # 深交所
    ("300", "SZ", True), ("301", "SZ", True), ("302", "SZ", True),     # 创业板
    ("000", "SZ", True), ("001", "SZ", True),
    ("002", "SZ", True), ("003", "SZ", True),           # 主板
    ("12", "SZ", False),                                # 深市可转债
    ("15", "SZ", False), ("16", "SZ", False), ("18", "SZ", False),     # 深市基金 / ETF
    # 北交所
    ("43", "BJ", True), ("83", "BJ", True), ("87", "BJ", True),
    ("88", "BJ", True), ("92", "BJ", True),
)


def _a_lookup(six: str) -> tuple[str, bool]:
    for prefix, ex, is_stock in _A_PREFIX:
        if six.startswith(prefix):
            return ex, is_stock
    raise LoaderError(f"认不出六位代码 {six} 属于哪个交易所，请写全后缀，如 {six}.SH / {six}.SZ")


def assert_a_share_stock(code: str) -> None:
    """A 股这一版只回测**个股**。ETF / 指数 / 可转债 明确拒掉，不让它静默跑出空结果。"""
    six = bare(code)
    try:
        _, is_stock = _a_lookup(six)
    except LoaderError:
        return          # 认不出的交给取数时报错，别在这儿抢着下结论
    if not is_stock:
        raise LoaderError(
            f"{code} 不是个股（ETF / 指数 / 可转债 号段）—— 这一版的日线取数只覆盖个股，"
            f"对它返回的是空序列而不是报错，所以在这里就拦下来"
        )


def bare(code: str) -> str:
    """去掉交易所后缀，取端点要的裸代码。"""
    return code.split(".")[0]


#: 三种支持的写法 → 市场。**由我们自己判**，不依赖上游的兜底。
_SHAPES: tuple[tuple[Any, str], ...] = (
    (re.compile(r"^\d{6}\.(SZ|SH|BJ)$", re.I), "a_share"),
    (re.compile(r"^\d{3,5}\.HK$", re.I), "hk_equity"),
    # 美股代码 1-5 位字母（NYSE / Nasdaq 的实际上限）。
    # ⚠️ 放宽到 10 位就会把 EURUSD / BTCUSDT 收进来 —— 它们随后会被上游的兜底
    #    判成 a_share 拿去查 baostock。**第一版这里就是写宽了，被测试抓出来的。**
    (re.compile(r"^[A-Z]{1,5}$"), "us_equity"),
)


def market_of(code: str) -> str:
    """判市场。**认不出就报错，不兜底；与上游判定不一致也报错。**

    🔴 上游 `_detect_market` 对认不出的格式一律回落 `a_share`。在只支持三个市场的
       这一版里，那个回落把「不支持」变成了「用错市场规则跑完，结果看着正常」。
    🔴 所以这里**自己判**，再拿上游的结论交叉核对：两边不一致说明这个写法处在
       某种灰色地带（如 BRK.B），宁可拒掉也不选一边 —— 选错那边就是上面那种静默错。
    """
    c = canonical_code(code)
    mine = next((m for pat, m in _SHAPES if pat.fullmatch(c)), None)
    if mine is None:
        raise LoaderError(
            f"认不出 {code!r} 是哪个市场的代码。这一版认这三种写法："
            "A股 600519.SH / 美股 AAPL / 港股 00700.HK"
        )
    theirs = _detect_market(c)
    if theirs != mine:
        raise LoaderError(
            f"{code!r} 的市场归属有歧义（本层判 {mine}，引擎侧判 {theirs}）—— "
            f"已拒绝。归属判错意味着整只票套错市场规则，而回测结果看不出异常"
        )
    return mine


# ── 取数 ──


def _run_fetch(endpoint: str, symbol: str, args: Dict[str, Any], out_dir: Path,
               python: str, timeout: int) -> Dict[str, Any]:
    """跑一次端点，返回信封。

    🔴 stderr **不并进 stdout**：baostock 会往 stderr 打 "login success!"，
       合进去就把 JSON 顶坏了（这个坑我自己先踩了一次）。
    """
    cmd = [python, str(FETCH_SCRIPT), "--endpoint", endpoint, "--symbol", symbol,
           "--args", json.dumps(args, ensure_ascii=False), "--out-dir", str(out_dir)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(REPO_ROOT))
    except subprocess.TimeoutExpired as exc:
        raise LoaderError(f"{endpoint} 取 {symbol} 超时（{timeout}s）") from exc
    if p.returncode != 0:
        # 受控取数脚本的失败原因写在 stdout 信封，stderr 可能完全为空。
        # 只取契约中的错误字符串，不把整份原始输出（可能有 raw/配置）当诊断回传。
        try:
            failed = json.loads(p.stdout)
        except json.JSONDecodeError:
            failed = None
        errors = failed.get("errors") if isinstance(failed, dict) else None
        reasons = [e["error"] for e in errors if isinstance(e, dict) and isinstance(e.get("error"), str) and e["error"].strip()] if isinstance(errors, list) else []
        tail = (p.stderr or "").strip().splitlines()[-3:]
        reason = " / ".join(reasons or tail) or "未返回可读错误原因"
        raise LoaderError(f"{endpoint} 取 {symbol} 退出码 {p.returncode}：{reason}")
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError as exc:
        raise LoaderError(f"{endpoint} 取 {symbol} 的输出不是 JSON（前 200 字：{p.stdout[:200]!r}）") from exc


def _raw_rows(envelope: Dict[str, Any], out_dir: Path) -> tuple[list, list[str]]:
    """从信封指到的 raw 文件里把整段序列读出来，并带回 raw_ref。

    信封里只有摘要证据（最新价、点数），完整序列在 raw —— 这正是我们要的：
    **回测吃的每一根 bar 都能指回落盘的原始响应。**
    """
    refs = list((envelope.get("extra") or {}).get("raw_files") or [])
    if not refs:
        raise LoaderError("信封里没有 raw_files —— 取不到完整序列，只有摘要")
    try:
        payload = json.loads((out_dir / refs[0]).read_text())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        # 包成 LoaderError 才会进 failures 并带上端点 / 代码上下文；
        # 直接炸穿的话，一只票的 raw 损坏会让整次回测崩掉且不说是哪只。
        raise LoaderError(f"raw 文件读不了（{refs[0]}）：{exc}") from exc
    return payload, refs


def _frame_from_baostock(payload: Dict[str, Any]) -> tuple[pd.DataFrame, int]:
    rows = payload.get("rows") or []
    if not rows:
        return pd.DataFrame(), 0
    df = pd.DataFrame(rows)
    # tradestatus: "1" 正常交易，其余为停牌。停牌 bar 的价格不是可成交价，剔除；
    # 剔了多少要**说出来** —— 停牌天数本身就是"这只票能不能这么回测"的判据。
    halted = 0
    if "tradestatus" in df.columns:
        mask = df["tradestatus"].astype(str) == "1"
        halted = int((~mask).sum())
        df = df[mask]
    df = df.drop(columns=[c for c in ("tradestatus",) if c in df.columns])
    # 引擎判涨跌停时找的字段叫 `pre_close`（见 BaseEngine.base_price_fields），
    # baostock 给的是 `preclose`。名字对不上不会报错 —— 它会**静默回退**到用
    # 上一根的收盘价当前收，于是停牌 / 除权那天的涨跌停带算出来是错的。
    if "preclose" in df.columns:
        df = df.rename(columns={"preclose": "pre_close"})
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    return df, halted


def _frame_from_yahoo(payload: Dict[str, Any], start: str | None = None, end: str | None = None) -> tuple[pd.DataFrame, int]:
    res = ((payload.get("chart") or {}).get("result") or [{}])[0]
    ts = res.get("timestamp") or []
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    if not ts:
        return pd.DataFrame(), 0
    data = {"date": pd.to_datetime(ts, unit="s", utc=True).tz_convert(None).normalize()}
    for k in ("open", "high", "low", "close", "volume"):
        col = q.get(k) or [None] * len(ts)
        data[k] = list(col)
    df = pd.DataFrame(data).set_index("date").sort_index()
    # Yahoo 会给出缺值的行（当天没成交 / 数据缺）。
    # 🔴 **四个价格缺一不可**，不能只看 close：撮合价取的是 open、涨跌停带要 high/low。
    #    只 dropna(close) 会留下 open=NaN 的行 —— 那一根不会报错，
    #    它会让当天的成交价变成 NaN，表现为「少成交了一笔」而不是「数据有问题」。
    if start is not None or end is not None:
        df = df.loc[start:end]
    before = len(df)
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df[df[["open", "high", "low", "close"]].map(lambda v: pd.notna(v) and float(v) > 0).all(axis=1)]
    return df, before - len(df)


def _yahoo_range(start: str, end: str) -> str:
    """覆盖得住请求区间的最小窗口关键字。"""
    try:
        days = (datetime.fromisoformat(end).date() - datetime.fromisoformat(start).date()).days
    except ValueError as exc:
        raise LoaderError(f"日期格式要 YYYY-MM-DD：start={start!r} end={end!r}") from exc
    if days < 0:
        raise LoaderError(f"start 晚于 end：{start} > {end}")
    # 与今天的距离才是 Yahoo 真正要覆盖的跨度（它的窗口一律从今天往回数）
    span = (date.today() - datetime.fromisoformat(start).date()).days
    for key, cover in _YAHOO_RANGES:
        if cover >= span:
            return key
    return "max"


def _to_ns_index(df: pd.DataFrame) -> pd.DataFrame:
    """把索引统一成 ``datetime64[ns]``。

    🔴 pandas 3 会**保留**原始时间分辨率：baostock 那条路解析出来是 ``[us]``、
       Yahoo 那条是 ``[s]``；而上游 ``_align`` 里 ``pd.DatetimeIndex(index.asi8)``
       把整数一律当**纳秒**解释（pandas 2 一律压成纳秒，所以上游没这问题）。
       秒被当纳秒 → 所有 bar 掉到 1970 年 → 与信号完全对不上 → **一笔都不成交**。

    ⚠️ 这个 bug **不抛异常**：它产出的是一份"总收益 0.00%、最大回撤 0.00%"的
       完整报告，排版整齐、指标齐全。⇒ 在数据边界钉死分辨率，并在下面断言。
    """
    if not isinstance(df.index, pd.DatetimeIndex):
        raise LoaderError(f"索引不是时间索引：{type(df.index).__name__}")
    if df.index.dtype != "datetime64[ns]":
        df = df.copy()
        df.index = df.index.astype("datetime64[ns]")
    return df


def _assert_same_instrument(code: str, payload: dict) -> None:
    """核对取回来的确实是要的那一只。

    端点会用自己的规则把代码映射成 baostock 写法。我们已经传了带后缀的形式，
    但**映射规则是它的、不是我们的** —— 它哪天变了，表现是「拿到另一只票、
    回测照常跑完、数字看着完全正常」。所以这里按 raw 里落下的实际查询再对一次。
    """
    actual = str(((payload or {}).get("query") or {}).get("code") or "")
    if not actual:
        # 🔴 **核对不了 ≠ 核对通过**。上游哪天不再落 query.code，这道校验就会
        #    悄悄变成空操作，而它防的正是「拿到另一只票、回测照常跑完」。
        #    一道跑不起来的校验必须报错，不能报成功。
        raise LoaderError(
            f"raw 里没有 query.code，无法核对取回来的是不是 {code} —— 已中止"
            "（这道校验防的是「拿错票但结果看着正常」，它自己失效时不能默认通过）"
        )
    six, ex = bare(code), code.split(".")[-1]
    want = f"{ex.lower()}.{six}"
    if actual.lower() != want:
        raise LoaderError(
            f"要的是 {code}，取数层实际查的是 {actual} —— 两者不是同一只票，"
            f"已中止（继续跑下去会在错误的标的上算出一份看着正常的回测）"
        )


class VibeLoader:
    """回测引擎眼里的"数据源"。

    引擎只调 ``fetch()``；``name`` 是 benchmark 那边用 ``getattr`` 读的。
    """

    name = "vibe"

    def __init__(self, out_dir: Path, python: Optional[str] = None, timeout: int = 120) -> None:
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.python = python or sys.executable
        self.timeout = timeout
        #: 每只票的来源，回测报告要用
        self.provenance: Dict[str, SymbolProvenance] = {}
        #: 一根 bar 都没取到的票 —— **不是空着算了**，交给上层决定是拒跑还是缩小范围
        self.failures: Dict[str, str] = {}

    # ── 引擎调的就是这一个 ──
    def fetch(self, codes: Iterable[str], start_date: str, end_date: str,
              fields: Optional[Any] = None, interval: str = "1D") -> Dict[str, pd.DataFrame]:
        if interval not in SUPPORTED_INTERVALS:
            raise LoaderError(
                f"这一版只做日线，取不到 {interval!r} 的数据。"
                "别把它当日线跑 —— 那样出来的结果测的不是你想测的东西。"
            )
        out: Dict[str, pd.DataFrame] = {}
        for raw_code in codes:
            code = canonical_code(raw_code)
            try:
                df, prov = self._fetch_one(code, start_date, end_date)
            except LoaderError as exc:
                self.failures[code] = str(exc)
                self.provenance.pop(code, None)     # 同一只票不能既成功又失败
                continue
            except Exception as exc:                # noqa: BLE001
                # 解析层的意外（数组长度不齐、字段类型变了…）也要落到 failures，
                # 而不是炸穿整次回测 —— 那样看不出是哪只票、哪个端点出的问题。
                self.failures[code] = f"{type(exc).__name__}: {exc}"
                self.provenance.pop(code, None)
                continue
            if df.empty:
                self.failures[code] = "区间内一根 bar 都没有"
                self.provenance.pop(code, None)
                continue
            out[code] = df
            self.provenance[code] = prov
            self.failures.pop(code, None)           # 这次成功了，清掉上一次的失败记录
        return out

    def _fetch_one(self, code: str, start_date: str, end_date: str):
        mkt = market_of(code)
        if mkt not in SUPPORTED_MARKETS:
            raise LoaderError(f"这一版只支持 A股 / 美股 / 港股，{code} 判为 {mkt}")

        if mkt == "a_share":
            assert_a_share_stock(code)
            if code.endswith(".BJ") and date.fromisoformat(start_date) < date(2021, 11, 15):
                raise LoaderError("北交所仅支持 2021-11-15 起的区间；此前精选层/新三板规则未建模")
            endpoint = "bs_kline_qfq"
            args = {"start_date": start_date, "end_date": end_date, "fields": _A_FIELDS}
            # 🔴 传**带后缀**的写法。只传六位的话端点会用自己的规则重判，
            #    而它的规则与我们的不一定一致 —— 实测 000001 被判成平安银行(sz)，
            #    要上证综指(sh)的人会拿到一只银行股，**全程不报错**。
            symbol = code
            parse = _frame_from_baostock
        else:
            endpoint = "yahoo_kline"
            args = {"interval": "1d", "range_": _yahoo_range(start_date, end_date)}
            symbol = code if mkt == "hk_equity" else bare(code)
            parse = _frame_from_yahoo

        env = _run_fetch(endpoint, symbol, args, self.out_dir, self.python, self.timeout)
        if env.get("status") == "failed":
            raise LoaderError(f"{endpoint} 返回 failed：{env.get('note') or '未说明原因'}")
        payload, refs = _raw_rows(env, self.out_dir)
        if mkt == "a_share":
            _assert_same_instrument(code, payload)
        if mkt != "a_share":
            result = ((payload.get("chart") or {}).get("result") or [{}])[0]
            returned = str((result.get("meta") or {}).get("symbol") or "").upper()
            def comparable(value):
                return (value.split(".")[0].lstrip("0") or "0") + ".HK" if value.endswith(".HK") else value.removesuffix(".US").replace(".", "-")
            if not returned or comparable(returned) != comparable(symbol):
                raise LoaderError("Yahoo 返回标的与请求不一致或缺少身份；已停止，未使用其他标的数据")
            df, dropped = parse(payload, start_date, end_date)
        else:
            df, dropped = parse(payload)

        # Yahoo 拿的是整窗口，切到请求区间；baostock 已经按日期取过了，切一次也无害
        if not df.empty:
            df = _to_ns_index(df).loc[str(start_date):str(end_date)]
            # 上面那个函数的存在理由就是这道检查 —— 分辨率一旦漂回去，
            # 表现是「回测跑完但零成交」，不是报错，所以必须在这里挡住。
            # ⚠️ 不用 assert：`python -O` 会把 assert 整条删掉，而这正是最不能被
            #    关掉的一道（它防的是那个不抛异常的静默错）。
            if df.index.dtype != "datetime64[ns]":
                raise LoaderError(f"索引分辨率没钉住：{df.index.dtype}（应为 datetime64[ns]）")

        prov = SymbolProvenance(
            code=code, market=mkt, endpoint=endpoint, raw_refs=refs, rows=len(df),
            first_bar=str(df.index[0].date()) if len(df) else None,
            last_bar=str(df.index[-1].date()) if len(df) else None,
            halted_bars=dropped,
            note=("停牌 / 无成交 %d 根已剔除" % dropped) if dropped else "",
            price_basis=price_basis(mkt),
        )
        return df, prov
