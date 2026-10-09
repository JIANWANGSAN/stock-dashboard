# 桌面快捷方式说明

## 日常怎么用

**双击桌面上的「Vibe AStock 复盘」图标** —— 就这一件事。

图标会自动完成三步，你不需要记端口、不需要手动开服务、不需要手动开浏览器：

1. 看看本地服务（8910）是否已经在跑
2. 没跑的话，自动用项目自带的 Python 环境把它拉起来，等就绪
3. 用 Chrome 独立窗口（无地址栏）打开复盘页面

已经在跑时不会重复启动，直接秒开。

## 三个脚本各管什么

日常你只会用到桌面图标，下面三个是维护用的：

| 文件 | 作用 | 什么时候用 |
|---|---|---|
| `launcher.py` | 启动器本体（被桌面图标调用） | 不用手动碰 |
| `make_icon.py` | 生成 `vibe-astock.ico` | 想改图标样式时 |
| `make_shortcut.py` | 创建 / 列出 / 移除桌面快捷方式 | 换机器、重装、删图标 |

## 重建与移除

```bat
:: 重新生成桌面图标（改了样式或换了机器）
C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe assets\make_icon.py
C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe assets\make_shortcut.py

:: 查看当前状态（桌面路径、文件是否存在、字段是否写对）
C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe assets\make_shortcut.py --list

:: 删除桌面快捷方式
C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe assets\make_shortcut.py --remove
```

直接删桌面上的 `.lnk` 文件也可以，无需跑脚本。

## 图标设计

深蓝夜色底（盘后复盘的屏幕感）+ 四根红色空心K线逐级升高 + 金色上箭头。

**红色代表上涨** —— 这是 A 股「红涨绿跌」的惯例，与欧美市场相反。改配色时别搞反。

想换样式改 `make_icon.py` 里的颜色常量，重跑即可。脚本会用像素采样自检，
确认箭头和K线真的画进了画布（防止两套坐标系混用导致元素画到画外）。

## 常见问题

**双击没反应 / 一闪而过**
多半是启动器报错了但窗口立刻关了。用这个看真实报错：

```
cd /d D:\软件\WorkBuddytwo\神秘力量\vibe-astock-main
C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe assets\launcher.py
```

**提示「端口 8910 被其他程序占用」**
有别的程序占了 8910。关掉它，或改 `launcher.py` 顶部的 `PORT` 常量（同时改
`server.py` 的启动命令才一致）。

**提示「等待 90s 仍未就绪」**
首次启动要装依赖、读缓存，机器慢的话可能不够。改 `launcher.py` 的 `WAIT_MAX`。

**页面打开了但数据是空的**
服务活着但取数失败，多半是网络问题（本项目取数依赖同花顺/腾讯公开接口）。
直接访问 `http://127.0.0.1:8910/` 手动确认。

**服务想彻底停掉**

```
:: 找出并结束占用 8910 的进程
netstat -ano | findstr :8910
taskkill /PID <上面查到的PID> /T /F
```

## 换机器怎么办

1. 把整个项目目录拷过去（注意 `vibe-astock-venv-parked` 也要一起，它是独立目录）
2. 确认 `assets/vibe-astock.ico` 和 `assets/launcher.py` 都在
3. 跑上面「重建与移除」里的两条命令重新生成快捷方式

快捷方式里存的是**绝对路径**，换机器或挪目录后必须重建，否则指向旧位置。

## 已知限制

- 启动器用 `DETACHED_PROCESS` 让服务脱离自身进程树。已实测服务能在启动器
  退出后持续运行（同一会话内 15 秒连续存活确认）。但**自动化测试环境会在命令
  结束时清理进程组**，所以在 agent 沙盒里跑完立刻失联属于环境特性，不是缺陷 ——
  你双击时由 explorer 启动，没有这层清理。
- 快捷方式指向的是系统托管 Python（`C:\Users\...\workbuddy\binaries\python\...`），
  它只用来跑 `launcher.py` 这个壳；真正起服务的解释器由 `launcher.py` 自己挑，
  与这个选择无关。
