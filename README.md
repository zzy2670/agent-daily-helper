# 日常助手

一款按计划领取某 AI Agent 日常积分的 Windows 小工具。当前适配“WB”国内版，已在 5.9.3 版本验证后台启动、状态识别和退出。

## 主要功能

- Windows 用户登录后检查一次；电脑持续开机时，在本地时间每天 05:00、13:00 再检查。
- 当天确认领取成功后自动跳过，手动领取过也能识别；本次确认后退出客户端。
- 使用应用内虚拟鼠标。客户端没有运行时，在独立桌面中启动并检查，不切换系统鼠标或前台窗口。
- 识别常见推广弹窗、保留运行记录，失败后按计划任务设置有限重试。
- 桌面助手可以查看状态、手动检查、暂停和恢复自动领取。

## 交给 Codex 部署

将下面这句话发给已连接 Windows 电脑的 Codex：

> 请阅读并检查 https://github.com/zzy2670/agent-daily-helper ，将项目克隆到我的文档目录中的稳定位置，按照仓库的 AGENTS.md 和 README.md 部署、验证计划任务及客户端登录状态；需要我登录时请提示我。

Codex 会检查 Python、客户端和计划任务，按需从官方渠道准备缺少的程序，安装依赖并创建桌面快捷方式。客户端登录状态需要在部署后实际核实；若需要登录，请在客户端完成。

## 手动安装

需要 Windows 10/11、64 位 Python 3.10–3.13（包含 tkinter 和 pip）、Git，以及已安装并登录的国内版客户端。安装依赖需要联网。

在将长期保留项目文件的目录中执行：

```powershell
git clone https://github.com/zzy2670/agent-daily-helper.git
Set-Location agent-daily-helper
powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy.ps1
```

脚本创建项目内 `.venv`，安装固定版本依赖，并注册当前 Windows 用户的计划任务。必要时 Windows 会显示管理员授权窗口。安装后，桌面会出现“AgentDaily积分助手”和“AgentDaily-打开客户端”。

客户端安装在非默认位置时，可指定可执行文件：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy.ps1 -AppPath 'D:\应用目录\WorkBuddy.exe'
```

`-CheckOnly` 只检查前提和冲突，不安装：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy.ps1 -CheckOnly
```

安装器发现旧版同类任务时会停止，以免两套自动化同时运行，已禁用的旧任务也会被识别。请先在任务计划程序中检查；确认弃用旧版后，删除其计划任务，再安装本项目。项目路径被移动或删除后，需从新位置重新部署。

## 使用说明

使用桌面的“AgentDaily-打开客户端”启动并登录；登录后既可以手动正常使用，也可以关闭客户端，让计划任务在后台启动它。脚本只处理它能够确认身份的客户端窗口；若通过其他入口打开的现有进程没有后台控制通道，请先正常退出客户端，再通过该快捷方式打开。

登录后 1 分钟、每天 05:00 和 13:00 的触发时间使用电脑的本地时区；每日是否已经领取按 UTC+8 的日历日判断。关机和睡眠时不会主动唤醒电脑，恢复后允许补跑。一次检查失败后，计划任务按 15 分钟间隔最多重试 3 次。

遇到无法识别的遮挡、登录失效或活动入口变更时，本次检查会停止并记录原因。如果点击领取后结果无法确认，当天后续运行只核对状态，不会再次点击。

运行状态和日志保存在当前用户的 `%LOCALAPPDATA%\AgentDailyHelper`。桌面助手可以查看日志；不要将该目录提交或分享。

## 本机控制与隐私

程序使用客户端已有的登录状态，不要求输入或保存账号密码。运行时会为该客户端启用随机本机回环调试端口，通过页面控件完成操作。通过项目快捷方式打开客户端时，端口会在该客户端运行期间保持开启，退出后关闭。运行期间，同一台电脑上的其他本地进程可能借此读取客户端可访问的信息并操控其已登录会话，因此请在可信设备和可信本地账户环境中使用。

## 卸载

在项目目录执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\uninstall.ps1
```

卸载器只移除属于当前项目的计划任务和快捷方式，保留项目目录、虚拟环境和本机状态记录。需要彻底移除时，可在确认无需保留后自行删除这两处目录。

## 开发与测试

```powershell
.\.venv\Scripts\python.exe test_worker.py
.\.venv\Scripts\python.exe test_close.py
.\.venv\Scripts\python.exe test_config.py
```

测试不会提交真实领取请求。安装 Chrome 时，部分测试会在隔离桌面运行本地合成页面；没有 Chrome 时这些测试会跳过。

MIT License
