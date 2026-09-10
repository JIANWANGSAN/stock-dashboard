# A股短线复盘仪表盘

单页 HTML 仪表盘 + Python 数据管线。四个模块：每日必看 / 梯队复盘 / 重点板块 / 均线共振池。

- **看板入口（永久链接·推荐）**：https://JIANWANGSAN.github.io/stock-dashboard/ （GitHub Pages 托管，无沙盒休眠，每天 15:35 自动更新）
- **看板入口（备用）**：双击 `index.html`，或 WorkBuddy 沙盒链接（由 15:05 任务发布）
- **数据刷新**：双击 `refresh.bat`

---

## 一、换一台电脑，怎么跑起来（3 步）

> 前提：装了 Python 3.10+（[python.org](https://www.python.org) 下载，安装时**务必勾选 Add Python to PATH**）

1. 把整个文件夹拷到新电脑（或 `git clone` 本仓库）
2. **双击 `setup.bat`** —— 自动建虚拟环境并安装 `pypinyin`（只做一次）
3. **双击 `refresh.bat`** —— 抓数据，完成后打开 `index.html` 即可

数据抓取只依赖 Python 标准库 + `pypinyin`，无其他第三方依赖。

---

## 二、手机上看（推荐「添加到主屏幕」）

已支持 PWA，用 **Chrome** 打开在线链接 → 右上角 `⋮` → **添加到主屏幕**：

- 桌面出现独立图标，点开**全屏无地址栏**，跟原生 App 一样
- 每次打开自动拉最新数据
- 断网时也能看上次缓存的内容

---

## 三、文件说明

| 文件 | 作用 |
|---|---|
| `index.html` | 看板页面（含全部样式与渲染逻辑） |
| `data.js` / `data.json` | 看板数据（由脚本生成） |
| `manifest.json` / `sw.js` | PWA 配置与离线缓存 |
| `fetch_data.py` | 主采集：指数、新闻、板块榜、连板梯队、三类节点、连板候选 |
| `enrich_tags.py` | 补齐个股标签（地域/概念/拼音），重算推荐卡 |
| `module4.py` | 模块4 均线共振动态池 |
| `collect_review.py` / `build_review.py` / `save_review.py` | 盘后复盘文本生成与归档 |
| `setup.bat` | 新电脑首次初始化 |
| `refresh.bat` | 刷新数据 |

---

## 四、核心口径（改代码前必读）

- **股票池过滤**：一律剔除 北交所（4/8/92 开头）、科创板（688/689）、ST / 退市
- **节点票池**：只保留总市值 ≤ 200 亿；只保留最近 10 个交易日（约两周）的节点
- **失效节点清理**：节点内已无任何连板票（连板中 / 断板反包）时自动删除；当日新节点豁免
- **断板判定**：一路未断板 → `连板中`(≥2板) / `首板`；断板后 ≤3 个交易日重新涨停 → `断板反包`；今天不在涨停池 → `已断板`（UI 折叠）
- **概念抓取**：全量东财板块 → 剔除噪声 / 地域 / 行业大类 → 别名归一（如 光通信模块 → CPO）→ 按板块当日涨幅排序取「当下最热概念」；再叠加新闻事件（重组 / 中标 / 业绩预增…）
- **上板分时量**：一字板取集合竞价首分钟累计额；自然板取首次封板时刻累计额（炸板回封不计）

---

## 五、常见问题

**Q：打开页面提示「未找到 data.js」**
数据还没生成。双击 `refresh.bat` 跑一次即可。

**Q：`refresh.bat` 说 Python not found**
先双击 `setup.bat`；若仍失败，确认 Python 已加入 PATH（命令行输入 `python --version` 验证）。

**Q：拼音缩写是空的**
`pypinyin` 没装上。双击 `setup.bat` 重装一次。

**Q：概念抓得不准**
在 `fetch_data.py` 的 `CONCEPT_FIX` 里按代码锁定，例如：
```python
CONCEPT_FIX = {
    '002980': ['CPO', 'AI眼镜', '物联网'],
}
```

---

## 六、换电脑后让 AI 自动跑起来（必做，否则只能手动看）

光有代码 + `refresh.bat` 只能**手动**刷新。要做到「每天盘前盘后自动更新 + 手机链接永久在线」，需让接手 AI 在新电脑用 `automation_update` **重建 2 个自动化任务**（这些任务存在 WorkBuddy 自动化系统里，不随文件夹走，换电脑会丢；手机链接的永久在线由 GitHub Pages 承担，无休眠、无需保活任务）。把下面两块直接交给新 AI 即可。

### 自动化 1 · 盘后复盘 + 仪表盘刷新
- **名称**：`A股盘后复盘 + 仪表盘刷新`
- **频率**：每周一至周五 `15:05`
- **执行内容**（交给 AI 跑）：
  1. `python fetch_data.py`（指数/新闻/板块榜/连板梯队/三类节点/候选）
  2. `python enrich_tags.py`（补标签 + 重算推荐卡）
  3. `python module4.py`（模块4 均线共振池）
  4. 重新发布线上链接：`workbuddy_sites_deploy`（`action=deploy` / `language=static` / `appName="A股复盘仪表盘"` / `userAskedToPublish=true`）
  5. 盘后复盘文本归档：`collect_review.py` → `build_review.py` → `save_review.py`（输出 `A股复盘记录.md`）

### 自动化 2 · 盘前新闻刷新
- **名称**：`A股仪表盘盘前新闻刷新`
- **频率**：每周一至周五 `09:10`
- **执行内容**：运行新闻/事件缓存刷新（如 `refresh_news.py` 或 `fetch_data.py` 的新闻模块），让开盘前看板已有最新资讯与涨停原因。

> 部署说明：重复发布同一目录会**复用同一 sandbox**，链接不变。手机端用 Chrome「添加到主屏幕」即 PWA 类原生 App，断网也能看缓存。

---

## 七、给接手 AI 的交接话术（直接复制给新 AI）

> 这是我做的 **A股短线复盘仪表盘** 项目，全部代码在 `stock_dashboard` 文件夹。
> 请先读 `README.md`：按「一、换电脑三步」初始化环境，按「六」用 `automation_update` 重建 2 个自动任务（盘后刷新 + 盘前新闻）。手机链接用 GitHub Pages 永久在线，无需保活任务。
> **核心业务口径（第四节）不要改动**，除非我明确要求：剔除北交/科创/ST、节点票 ≤200 亿且只留近 10 个交易日、节点内无连板即删、断板反包判定、概念按当下热点+事件驱动抓取、一字板取集合竞价首分钟额。

---

## 八、GitHub 备份（跨电脑灾备，已自动同步）

项目已推送到 GitHub 公开仓库：**https://github.com/JIANWANGSAN/stock-dashboard**（为启用 GitHub Pages 已转公开，仅含公开 A股数据、无隐私信息）。

- **每日自动同步**：自动化「A股仪表盘 · GitHub 每日备份同步」（交易日 15:35）在盘后主任务完成后执行 `git add -A && git commit && git push`，GitHub 始终保留最新全量项目。**因已启用 GitHub Pages，每次 push 会自动重建站点，故永久链接 https://JIANWANGSAN.github.io/stock-dashboard/ 也每天同步更新。**
- **换电脑恢复（推荐 clone，比拷文件夹更稳）**：
  1. 新电脑装 **WorkBuddy**（它自带 Python 与 Git，你不用单独装这两个——和你现在一样）
  2. 把「七」的话术丢给新 AI，并附上 GitHub 公开仓库地址（https://github.com/JIANWANGSAN/stock-dashboard）与该 token：让 AI 执行 `git clone` + 双击 `setup.bat`/`refresh.bat` + 按「六」重建 2 个自动任务（GitHub Pages 已永久在线，无需重新发布链接）
  - 全程由 AI 在 WorkBuddy 内完成，**你无需敲任何命令、无需懂 Git/Python**
- **注意**：GitHub 只存代码与数据快照，不含自动化任务（在 WorkBuddy 自动化系统里，需新 AI 按「六」重建）；运行缓存在 `.gitignore` 已排除。本地 remote 的访问令牌写在 `.git/config`（不进库），换电脑后由新 AI 重新配置 remote（需你再给一次 token）才能继续自动 push。
