<#
  豆包(签到模式)启动器 · Windows 11
  ---------------------------------------------------------------------------
  豆包客户端运行期间登录态只在内存:Cookie 库被独占锁定(文件通道读不到),
  且未带 CDP 调试端口启动时,auto-signin-kit 两个读取通道都拿不到会话。
  本启动器让豆包以"签到模式"(带 --remote-debugging-port)常驻,签到即可走
  界面通道(签名由客户端 JS 生成)。

  行为(对齐 macOS 的 scripts/doubao-signin-mode.command):
    - 已带调试端口运行  → 提示并退出,不动它;
    - 普通模式运行      → 先完全退出(优雅关闭,登录态照常落盘)再带端口拉起;
    - 未运行            → 直接带端口拉起。

  端口须与 config/domains/doubao_cn.yaml 的 cdp_port 一致(默认 9223),
  可用环境变量 DOUBAO_CDP_PORT 覆盖。

  用法:
    scripts\doubao-signin-mode.bat                  # 双击/命令行: 启动签到模式
    scripts\doubao-signin-mode.bat -InstallShortcut # 在桌面创建 Doubao-signin.lnk
    powershell -File scripts\doubao-signin-mode.ps1 -Port 9223 -Exe "D:\Doubao\app\Doubao.exe"
#>
[CmdletBinding()]
param(
    [int]$Port = 0,
    [string]$Exe,
    [switch]$InstallShortcut
)

$ErrorActionPreference = 'Stop'

if ($env:OS -ne 'Windows_NT') {
    Write-Error '本启动器仅支持 Windows(11);macOS 请用 scripts/doubao-signin-mode.command。'
    exit 2
}

if (-not $Port) {
    $Port = if ($env:DOUBAO_CDP_PORT) { [int]$env:DOUBAO_CDP_PORT } else { 9223 }
}
if (-not $Exe) { $Exe = $env:DOUBAO_EXE }

function Test-DoubaoCdp {
    param([int]$P)
    # 校验 /json/version 回 Browser,避免别的 Chromium 应用占用端口造成误判。
    try {
        $resp = Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 "http://127.0.0.1:$P/json/version"
        return $resp.Content -match 'Browser'
    } catch {
        return $false
    }
}

function Resolve-DoubaoExe {
    param([string]$Override)
    if ($Override) {
        if (Test-Path -LiteralPath $Override) { return (Resolve-Path -LiteralPath $Override).Path }
        throw "指定的豆包可执行文件不存在: $Override"
    }
    # 1) 运行中的客户端反查(最可靠,路径非标准也能命中)
    $running = Get-Process -Name Doubao -ErrorAction SilentlyContinue |
        Where-Object { $_.Path } | Select-Object -First 1
    if ($running) { return $running.Path }
    # 2) 桌面快捷方式「豆包.lnk」指向的启动器同级 app\Doubao.exe
    $desktopLnk = Join-Path ([Environment]::GetFolderPath('Desktop')) '豆包.lnk'
    if (Test-Path -LiteralPath $desktopLnk) {
        try {
            $target = (New-Object -ComObject WScript.Shell).CreateShortcut($desktopLnk).TargetPath
            $cand = Join-Path (Split-Path -Parent $target) 'app\Doubao.exe'
            if (Test-Path -LiteralPath $cand) { return (Resolve-Path -LiteralPath $cand).Path }
        } catch { }
    }
    # 3) 常见安装目录
    foreach ($cand in @(
        (Join-Path $env:LOCALAPPDATA 'Doubao\app\Doubao.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Doubao\app\Doubao.exe'),
        (Join-Path $env:ProgramFiles 'Doubao\app\Doubao.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Doubao\app\Doubao.exe')
    )) {
        if ($cand -and (Test-Path -LiteralPath $cand)) { return (Resolve-Path -LiteralPath $cand).Path }
    }
    throw '未找到豆包客户端(app\Doubao.exe)。请用 -Exe 或环境变量 DOUBAO_EXE 指定完整路径。'
}

$doubaoExe = Resolve-DoubaoExe -Override $Exe

if ($InstallShortcut) {
    $bat = Join-Path $PSScriptRoot 'doubao-signin-mode.bat'
    $link = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Doubao-signin.lnk'
    $shell = New-Object -ComObject WScript.Shell
    $sc = $shell.CreateShortcut($link)
    $sc.TargetPath = $bat
    $sc.WorkingDirectory = $PSScriptRoot
    $sc.Description = '豆包(签到模式): 带 CDP 调试端口启动/重启豆包'
    if (Test-Path -LiteralPath $doubaoExe) { $sc.IconLocation = "$doubaoExe,0" }
    $sc.Save()
    Write-Host "已创建桌面快捷方式: $link"
    exit 0
}

if (Test-DoubaoCdp -P $Port) {
    Write-Host "豆包已在签到模式运行(CDP 端口 $Port 可达),无需重启。"
    exit 0
}

$running = Get-Process -Name Doubao -ErrorAction SilentlyContinue
if ($running) {
    Write-Host '豆包正在普通模式运行,正在完全退出以便带调试端口重启…'
    $running | ForEach-Object { [void]$_.CloseMainWindow() }
    for ($i = 0; $i -lt 10; $i++) {
        Start-Sleep -Seconds 1
        if (-not (Get-Process -Name Doubao -ErrorAction SilentlyContinue)) { break }
    }
    if (Get-Process -Name Doubao -ErrorAction SilentlyContinue) {
        Write-Host '未在 10 秒内退出,强制结束(登录态在 profile 里,不丢)…'
        taskkill /IM Doubao.exe /T /F | Out-Null
        Start-Sleep -Seconds 2
    }
}

Write-Host "以签到模式启动豆包(exe=$doubaoExe,port=$Port)…"
Start-Process -FilePath $doubaoExe `
    -ArgumentList "--remote-debugging-port=$Port" `
    -WorkingDirectory (Split-Path -Parent $doubaoExe)

for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 800
    if (Test-DoubaoCdp -P $Port) {
        Write-Host "签到模式就绪: CDP 端口 $Port 已监听,现在可以执行签到。"
        exit 0
    }
}
Write-Warning "豆包已启动,但调试端口 $Port 在预期时间内未就绪;若客户端弹出了登录/首页,请稍后再试,或确认端口未被占用。"
exit 1

