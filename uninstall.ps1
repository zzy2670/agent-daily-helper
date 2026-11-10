[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [switch]$ElevatedRetry,
    [string]$ExpectedUserSid
)

$ErrorActionPreference = 'Stop'
$base = [IO.Path]::GetFullPath($PSScriptRoot)
$pythonw = Join-Path $base '.venv\Scripts\pythonw.exe'
$taskName = 'AgentDaily-Helper'
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$desktop = [Environment]::GetFolderPath('Desktop')
$managerLink = Join-Path $desktop 'AgentDaily积分助手.lnk'
$launcherLink = Join-Path $desktop 'AgentDaily-打开客户端.lnk'
$managerArgs = '"' + (Join-Path $base 'manager.pyw') + '"'
$launcherArgs = '"' + (Join-Path $base 'app_launcher.pyw') + '"'
$taskArgs = '"' + (Join-Path $base 'worker.py') + '" --source scheduled'
$mutex = $null
$mutexHeld = $false

function Find-Task {
    return @(Get-ScheduledTask -ErrorAction Stop | Where-Object { $_.TaskPath -eq '\' -and $_.TaskName -eq $taskName })
}

function Assert-OwnTask($task) {
    if (@($task.Actions).Count -ne 1 -or
        -not ([IO.Path]::GetFullPath([string]$task.Actions[0].Execute) -ieq $pythonw) -or
        [string]$task.Actions[0].Arguments -cne $taskArgs) {
        throw '同名计划任务不属于当前项目，未删除。'
    }
    $owner = [string]$task.Principal.UserId
    try {
        if ($owner -match '^S-1-') { $ownerSid = ([Security.Principal.SecurityIdentifier]::new($owner)).Value }
        else { $ownerSid = ([Security.Principal.NTAccount]::new($owner)).Translate([Security.Principal.SecurityIdentifier]).Value }
    } catch { throw '无法确认计划任务所属用户，未删除。' }
    if ($ownerSid -cne $sid) { throw '计划任务属于其他用户，未删除。' }
}

function Owns-Shortcut([string]$path, [string]$expectedArgs) {
    if (!(Test-Path -LiteralPath $path -PathType Leaf)) { return $false }
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($path)
    return ($link.TargetPath -ieq $pythonw -and $link.Arguments -ceq $expectedArgs)
}

try {
    if ($env:OS -ne 'Windows_NT') { throw '此项目仅支持 Windows。' }
    if ($ExpectedUserSid -and $ExpectedUserSid -cne $sid) {
        throw '授权后的管理员账户与当前用户不同，已停止。请使用原 Windows 用户提权后重试。'
    }
    $tasks = @(Find-Task)
    if ($tasks.Count -gt 1) { throw '发现多个同名计划任务，无法安全判断归属。' }
    if ($tasks.Count) { Assert-OwnTask $tasks[0] }
    $ownManager = Owns-Shortcut $managerLink $managerArgs
    $ownLauncher = Owns-Shortcut $launcherLink $launcherArgs
    if ($CheckOnly) {
        [pscustomobject]@{Status='检查通过（未卸载）';TaskFound=[bool]$tasks.Count;ManagerShortcut=$ownManager;ApplicationShortcut=$ownLauncher} | Format-List
        exit 0
    }
    $mutex = [Threading.Mutex]::new($false, 'Local\AgentDailyHelper')
    try { $mutexHeld = $mutex.WaitOne(0) }
    catch [Threading.AbandonedMutexException] { $mutexHeld = $true }
    if (!$mutexHeld) { throw '自动领取或其他部署正在运行，请稍后重试。' }
    if ($tasks.Count) {
        try {
            if ($tasks[0].State -eq 'Running') {
                Stop-ScheduledTask -TaskName $taskName -ErrorAction Stop
                $deadline = [DateTime]::UtcNow.AddSeconds(10)
                do {
                    Start-Sleep -Milliseconds 200
                    $current = @(Find-Task)
                    if (!$current.Count -or $current[0].State -ne 'Running') { break }
                } while ([DateTime]::UtcNow -lt $deadline)
                if ($current.Count -and $current[0].State -eq 'Running') { throw '当前任务仍在运行，未卸载。' }
            }
            $current = @(Find-Task)
            if ($current.Count) { Assert-OwnTask $current[0]; Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction Stop }
        } catch {
            $errorText = $_.Exception.Message
            if (-not $ElevatedRetry -and ($errorText -match '拒绝访问|access is denied|0x80070005|access denied')) {
                Write-Host '删除计划任务需要管理员授权，将打开 Windows 系统授权窗口。'
                $mutex.ReleaseMutex()
                $mutexHeld = $false
                $hostPath = (Get-Process -Id $PID).Path
                $childArgs = @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"' + $PSCommandPath + '"'),'-ElevatedRetry','-ExpectedUserSid',$sid)
                try { $child = Start-Process -FilePath $hostPath -ArgumentList $childArgs -Verb RunAs -WindowStyle Hidden -PassThru -Wait }
                catch { throw '未能完成系统授权。请用当前 Windows 用户的管理员 PowerShell 重新运行 uninstall.ps1。' }
                if ($child.ExitCode -ne 0) { throw '授权后的卸载失败；请确认使用同一 Windows 用户并重试。' }
                if (@(Find-Task).Count) { throw '任务仍然存在，请检查任务计划程序。' }
                Write-Host '已卸载自动领取任务。项目文件、虚拟环境和本机状态记录已保留。'
                exit 0
            }
            throw "计划任务卸载失败：$errorText"
        }
    }
    if ($ownManager) { Remove-Item -LiteralPath $managerLink -ErrorAction Stop }
    if ($ownLauncher) { Remove-Item -LiteralPath $launcherLink -ErrorAction Stop }
    if (@(Find-Task).Count) { throw '卸载后计划任务仍然存在，请检查任务计划程序。' }
    [pscustomobject]@{Status='已卸载';Task=$taskName;Note='项目文件、Python 虚拟环境和本机状态记录已保留'} | Format-List
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
} finally {
    if ($mutexHeld -and $mutex) { $mutex.ReleaseMutex() }
    if ($mutex) { $mutex.Dispose() }
}
