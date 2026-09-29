# Research 数据源迁移说明

来源：Simon Lin 的 Vibe-Research，仓库 https://github.com/simonlin1212/Vibe-Research 。
2026-09-08 从本机 `vibe-research-agent-Codex` 迁入指定公开源码：
`common.py`、`sources/_http.py`、`sources/probability.py`、`sources/baostock_src.py`、`sources/yahoo.py`。
继承 Vibe-Research 的 MIT 许可（本目录 LICENSE）。没有复制配置、凭据、缓存或用户记录。

`fetch_backtest.py` 为 AStock 适配入口，仅开放 baostock 前复权日线和 Yahoo 日线两个端点。
其余取数逻辑保持来源版本；进程独立运行，不向 AStock API 注入 Research 的全局模块路径。
概率分类和请求预算测试随迁移保留；未迁移仅服务 Research 信封映射器的测试。

回测引擎及 HKUDS/Vibe-Trading 归属另见 ../backtest/NOTICE.md 与 LICENSE.upstream。
