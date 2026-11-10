[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [string]$AppPath,
    [switch]$ElevatedRetry,
    [string]$ExpectedUserSid
)

$ErrorActionPreference = 'Stop'
$base = [IO.Path]::GetFullPath($PSScriptRoot)
$venv = Join-Path $base '.venv'
$pythonVenv = Join-Path $venv 'Scripts\python.exe'
$pythonwVenv = Join-Path $venv 'Scripts\pythonw.exe'
$taskName = 'AgentDaily-Helper'
$legacyTaskName = 'WorkBuddy-Daily-Credits'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$sid = $identity.User.Value
$desktop = [Environment]::GetFolderPath('Desktop')
$dataDir = if ($env:AGENT_DAILY_DATA) { [IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($env:AGENT_DAILY_DATA)) } else { Join-Path $env:LOCALAPPDATA 'AgentDailyHelper' }
$actionArgs = '"' + (Join-Path $base 'worker.py') + '" --source scheduled'
$managerArgs = '"' + (Join-Path $base 'manager.pyw') + '"'
$launcherArgs = '"' + (Join-Path $base 'app_launcher.pyw') + '"'
$managerLink = Join-Path $desktop 'AgentDaily积分助手.lnk'
$launcherLink = Join-Path $desktop 'AgentDaily-打开客户端.lnk'
$mutex = $null
$mutexHeld = $false
$initialConsoleEncoding = [Console]::OutputEncoding
try { [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false) } catch { }

function Find-Python {
    # Windows PowerShell 5.1 can turn native stderr into a terminating error.
    # A missing launcher version is expected; continue to the next candidate.
    $ErrorActionPreference = 'Continue'
    $probe = "import sys,struct,tkinter; assert (3,10) <= sys.version_info < (3,14); assert struct.calcsize('P') == 8; print(sys.executable)"
    $launcher = Get-Command 'py.exe' -ErrorAction SilentlyContinue
    if ($launcher) {
        foreach ($version in @('-3.13','-3.12','-3.11','-3.10','-3')) {
            $out = & $launcher.Source $version -X utf8 -c $probe 2>$null
            if ($LASTEXITCODE -eq 0 -and $out) { return [string]@($out)[-1] }
        }
    }
    $python = Get-Command 'python.exe' -ErrorAction SilentlyContinue
    if ($python) {
        $out = & $python.Source -X utf8 -c $probe 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) { return [string]@($out)[-1] }
    }
    throw '未找到带 tkinter 的 64 位 Python 3.10 至 3.13。请先安装 Python，并重试本脚本。'
}

function Find-Task([string]$name) {
    return @(Get-ScheduledTask -ErrorAction Stop | Where-Object { $_.TaskPath -eq '\' -and $_.TaskName -eq $name })
}

function Invoke-CheckedPython([string]$program, [string[]]$arguments, [string]$failureMessage) {
    # Native stderr (e.g. a pip warning) is not itself a failed exit code.
    $ErrorActionPreference = 'Continue'
    & $program @arguments 2>&1 | ForEach-Object { Write-Host ([string]$_) }
    if ($LASTEXITCODE -ne 0) { throw $failureMessage }
}

function Assert-OwnTask($task) {
    if ($null -eq $task) { return }
    if (@($task.Actions).Count -ne 1 -or
        -not ([IO.Path]::GetFullPath([string]$task.Actions[0].Execute) -ieq $pythonwVenv) -or
        [string]$task.Actions[0].Arguments -cne $actionArgs) {
        throw "存在同名但不属于当前目录的计划任务 $taskName，未覆盖。请先检查或更名该任务。"
    }
    $owner = [string]$task.Principal.UserId
    try {
        if ($owner -match '^S-1-') { $ownerSid = ([Security.Principal.SecurityIdentifier]::new($owner)).Value }
        else { $ownerSid = ([Security.Principal.NTAccount]::new($owner)).Translate([Security.Principal.SecurityIdentifier]).Value }
    } catch { throw '无法确认同名计划任务所属用户，未覆盖。' }
    if ($ownerSid -cne $sid) { throw '同名计划任务属于其他用户，未覆盖。' }
}

function Assert-OwnShortcut([string]$path, [string]$expectedArgs) {
    if (!(Test-Path -LiteralPath $path)) { return }
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($path)
    if ($link.TargetPath -ine $pythonwVenv -or $link.Arguments -cne $expectedArgs) {
        throw "桌面已有同名快捷方式且不属于当前项目，未覆盖：$path"
    }
}

function Get-AppExecutable([string]$python) {
    $ErrorActionPreference = 'Continue'
    $previous = $env:AGENT_DAILY_APP
    try {
        if ($AppPath) {
            $expanded = [IO.Path]::GetFullPath($AppPath)
            if (!(Test-Path -LiteralPath $expanded -PathType Leaf)) { throw '指定的客户端程序不存在。' }
            if ($previous -and -not ([IO.Path]::GetFullPath($previous) -ieq $expanded)) {
                throw 'AGENT_DAILY_APP 环境变量与 -AppPath 不一致；请先解除冲突。'
            }
            $env:AGENT_DAILY_APP = $expanded
        }
        Push-Location -LiteralPath $base
        try {
            $out = & $python -X utf8 -c 'from runtime_config import find_executable; print(find_executable())' 2>$null
            if ($LASTEXITCODE -ne 0 -or !$out) {
                throw '未发现国内版客户端。请先安装并登录，或通过 -AppPath 指定程序路径。'
            }
            return [string]@($out)[-1]
        } finally { Pop-Location }
    } finally { $env:AGENT_DAILY_APP = $previous }
}

function Set-Shortcut([string]$path, [string]$argsText, [string]$icon, [string]$description) {
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($path)
    $link.TargetPath = $pythonwVenv
    $link.Arguments = $argsText
    $link.WorkingDirectory = $base
    $link.IconLocation = $icon + ',0'
    $link.Description = $description
    $link.Save()
}

try {
    if ($env:OS -ne 'Windows_NT') { throw '此项目仅支持 Windows。' }
    if ($ExpectedUserSid -and $ExpectedUserSid -cne $sid) {
        throw '授权后的管理员账户与当前用户不同，已停止。请使用原 Windows 用户提权后重试。'
    }
    foreach ($name in @('worker.py','manager.pyw','app_launcher.pyw','settings.py',
                         'cdp_client.py','isolated_desktop.py','runtime_config.py',
                         'page_ui.js','requirements.txt')) {
        if (!(Test-Path -LiteralPath (Join-Path $base $name) -PathType Leaf)) { throw "项目文件不完整：$name" }
    }
    $old = @(Find-Task $legacyTaskName)
    if ($old.Count) {
        throw "检测到已有任务 $legacyTaskName。为避免重复领取，未作改动；请先检查并自行移除该旧计划任务后重试。"
    }
    $existing = @(Find-Task $taskName)
    if ($existing.Count -gt 1) { throw '发现多个同名任务，无法安全判断归属。' }
    if ($existing.Count -eq 1) { Assert-OwnTask $existing[0] }
    $managerWasPresent = Test-Path -LiteralPath $managerLink
    $launcherWasPresent = Test-Path -LiteralPath $launcherLink
    Assert-OwnShortcut $managerLink $managerArgs
    Assert-OwnShortcut $launcherLink $launcherArgs
    $python = Find-Python
    $requirements = @(Get-Content -LiteralPath (Join-Path $base 'requirements.txt') |
      ForEach-Object { $_.Trim() } | Where-Object { $_ -and -not $_.StartsWith('#') })
    if (!$requirements.Count -or @($requirements | Where-Object { $_ -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]*==[A-Za-z0-9][A-Za-z0-9.+!-]*$' }).Count) {
        throw 'requirements.txt 必须只包含固定版本依赖。'
    }
    if ($CheckOnly) {
        $app = Get-AppExecutable $python
        [pscustomobject]@{Status='检查通过（未安装）';Python=$python;Application=$app;Task=$taskName;ExistingTask=[bool]$existing.Count} | Format-List
        exit 0
    }
    $mutex = [Threading.Mutex]::new($false, 'Local\AgentDailyHelper')
    try { $mutexHeld = $mutex.WaitOne(0) }
    catch [Threading.AbandonedMutexException] { $mutexHeld = $true }
    if (!$mutexHeld) { throw '自动领取或其他部署正在运行，请稍后重试。' }
    if (!(Test-Path -LiteralPath $pythonVenv -PathType Leaf)) {
        Invoke-CheckedPython $python @('-X','utf8','-m','venv',$venv) '创建 Python 虚拟环境失败。'
    }
    if (!(Test-Path -LiteralPath $pythonwVenv -PathType Leaf)) { throw '虚拟环境缺少 pythonw.exe，请检查 Python 安装。' }
    Invoke-CheckedPython $pythonVenv @('-X','utf8','-m','pip','install','--disable-pip-version-check','--only-binary=:all:','-r',(Join-Path $base 'requirements.txt')) '依赖安装失败，请检查网络与 Python 环境后重试。'
    Invoke-CheckedPython $pythonVenv @('-X','utf8','-c','import tkinter,psutil,win32api,win32event,win32com.client,websocket') '依赖已安装，但导入检查失败。'
    $app = Get-AppExecutable $pythonVenv
    if ($AppPath) {
        New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
        [IO.File]::WriteAllText((Join-Path $dataDir 'app_path.txt'), $app, [Text.UTF8Encoding]::new($false))
    }
    $action = New-ScheduledTaskAction -Execute $pythonwVenv -Argument $actionArgs -WorkingDirectory $base
    $logon = New-ScheduledTaskTrigger -AtLogOn -User $identity.Name
    $logon.Delay = 'PT1M'
    $morning = New-ScheduledTaskTrigger -Daily -At '05:00'
    $afternoon = New-ScheduledTaskTrigger -Daily -At '13:00'
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 4) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 15)
    $settings.WakeToRun = $false
    $principal = New-ScheduledTaskPrincipal -UserId $identity.Name -LogonType Interactive -RunLevel Limited
    $task = New-ScheduledTask -Action $action -Trigger @($logon,$morning,$afternoon) -Settings $settings -Principal $principal -Description 'AI Agent daily reward helper.'
    try {
        if ($existing.Count) { Register-ScheduledTask -TaskName $taskName -InputObject $task -Force -ErrorAction Stop | Out-Null }
        else { Register-ScheduledTask -TaskName $taskName -InputObject $task -ErrorAction Stop | Out-Null }
    } catch {
        $errorText = $_.Exception.Message
        if (-not $ElevatedRetry -and ($errorText -match '拒绝访问|access is denied|0x80070005|access denied')) {
            Write-Host '注册任务需要管理员授权，将打开 Windows 系统授权窗口。'
            $mutex.ReleaseMutex()
            $mutexHeld = $false
            $hostPath = (Get-Process -Id $PID).Path
            $childArgs = @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"' + $PSCommandPath + '"'),'-ElevatedRetry','-ExpectedUserSid',$sid)
            if ($AppPath) { $childArgs += @('-AppPath',('"' + [IO.Path]::GetFullPath($AppPath) + '"')) }
            try { $child = Start-Process -FilePath $hostPath -ArgumentList $childArgs -Verb RunAs -WindowStyle Hidden -PassThru -Wait }
            catch { throw '未能完成系统授权。请用当前 Windows 用户的管理员 PowerShell 重新运行 deploy.ps1。' }
            if ($child.ExitCode -ne 0) {
                $remaining = @(Find-Task $taskName)
                $newLinkRemains = ((-not $managerWasPresent -and (Test-Path -LiteralPath $managerLink)) -or (-not $launcherWasPresent -and (Test-Path -LiteralPath $launcherLink)))
                if ($existing.Count) { throw '授权后的部署失败，原有任务保留但可能已部分更新；请检查桌面快捷方式和任务计划程序。' }
                if ($remaining.Count -or $newLinkRemains) { throw '授权后的部署失败，检测到同名任务或新快捷方式仍存在，可能有未回滚的部分；未继续删除，请检查任务计划程序及桌面。' }
                throw '授权后的部署失败，未发现新任务或快捷方式残留；请确认使用同一 Windows 用户后重试。'
            }
            $done = @(Find-Task $taskName)
            if ($done.Count -ne 1) { throw '系统授权完成，但未能读取计划任务，请检查任务计划程序。' }
            Assert-OwnTask $done[0]
            if (!(Test-Path -LiteralPath $managerLink) -or !(Test-Path -LiteralPath $launcherLink)) {
                throw '计划任务已安装，但桌面快捷方式缺失。请用当前 Windows 用户的管理员 PowerShell 重试。'
            }
            Assert-OwnShortcut $managerLink $managerArgs
            Assert-OwnShortcut $launcherLink $launcherArgs
            Write-Host '计划任务已安装。登录状态及首次实际领取仍需在应用内确认。'
            exit 0
        }
        throw "计划任务注册失败：$errorText。请用当前 Windows 用户的管理员 PowerShell 重试。"
    }
    try {
        Set-Shortcut $managerLink $managerArgs $app '查看自动领取状态'
        Set-Shortcut $launcherLink $launcherArgs $app '打开支持后台控制的客户端'
        $installed = @(Find-Task $taskName)
        if ($installed.Count -ne 1) { throw '注册后未能读回计划任务。' }
        Assert-OwnTask $installed[0]
        if (@($installed[0].Triggers).Count -ne 3 -or !$installed[0].Settings.Enabled -or $installed[0].Settings.WakeToRun) {
            throw '任务触发器或运行设置的读回结果不符合预期。'
        }
        Assert-OwnShortcut $managerLink $managerArgs
        Assert-OwnShortcut $launcherLink $launcherArgs
    } catch {
        $postError = $_.Exception.Message
        if ($existing.Count) {
            throw "部署未完成：$postError。已有任务已保留，但可能已部分更新；请检查任务及桌面快捷方式后重试。"
        }
        $rollbackErrors = @()
        try {
            $created = @(Find-Task $taskName)
            if ($created.Count -gt 1) { throw '同名任务不唯一' }
            if ($created.Count) { Assert-OwnTask $created[0]; Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction Stop }
            if (@(Find-Task $taskName).Count) { throw '任务仍然存在' }
        } catch { $rollbackErrors += '计划任务' }
        if (-not $managerWasPresent -and (Test-Path -LiteralPath $managerLink)) {
            try { Assert-OwnShortcut $managerLink $managerArgs; Remove-Item -LiteralPath $managerLink -ErrorAction Stop }
            catch { $rollbackErrors += '助手快捷方式' }
        }
        if (-not $launcherWasPresent -and (Test-Path -LiteralPath $launcherLink)) {
            try { Assert-OwnShortcut $launcherLink $launcherArgs; Remove-Item -LiteralPath $launcherLink -ErrorAction Stop }
            catch { $rollbackErrors += '客户端快捷方式' }
        }
        if ($rollbackErrors.Count) {
            throw "首次部署未完成：$postError。以下项目未能安全回滚：$($rollbackErrors -join '、')；请检查后重试。"
        }
        throw "首次部署未完成：$postError。新建计划任务及新快捷方式已回滚；请检查桌面权限后重试。"
    }
    [pscustomobject]@{Status='已安装';Task=$taskName;Triggers='登录后 1 分钟、当地时间 05:00 和 13:00';Shortcut=$managerLink;ApplicationShortcut=$launcherLink;Login='尚未验证，请在客户端登录后检查'} | Format-List
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
} finally {
    if ($mutexHeld -and $mutex) { $mutex.ReleaseMutex() }
    if ($mutex) { $mutex.Dispose() }
    try { [Console]::OutputEncoding = $initialConsoleEncoding } catch { }
}
