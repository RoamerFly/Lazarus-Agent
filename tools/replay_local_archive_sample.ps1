param(
    [Parameter(Mandatory)][string]$App,
    [ValidateSet('CheckOnly', 'Prepare', 'Device')][string]$Mode = 'CheckOnly'
)

$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Utility/Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop
$root = [IO.Path]::GetFullPath($PSScriptRoot)
$exe = (Resolve-Path -LiteralPath $App).ProviderPath

function Get-Sha256([string]$File) {
    (Get-FileHash -LiteralPath $File -Algorithm SHA256).Hash.ToLowerInvariant()
}

$files = @('base.apk', 'candidate.apk', 'build-receipt.json', 'replay-plan.json',
    'manifest-readback.json', '复测说明.md', $MyInvocation.MyCommand.Name)
$checksums = @{}
foreach ($line in (Get-Content -LiteralPath (Join-Path $root 'SHA256SUMS.txt') -Encoding UTF8)) {
    if ($line -cnotmatch '^([0-9a-f]{64})  (.+)$' -or $matches[2] -cnotin $files -or $checksums.ContainsKey($matches[2])) {
        throw '复核文件清单不完整或包含不匹配的文件名'
    }
    $checksums[$matches[2]] = $matches[1]
}
foreach ($file in $files) {
    if ((Get-Sha256 (Join-Path $root $file)) -cne $checksums[$file]) {
        throw "复核文件哈希不符 $file"
    }
}

$build = Get-Content -LiteralPath (Join-Path $root 'build-receipt.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$planPath = Join-Path $root 'replay-plan.json'
$plan = Get-Content -LiteralPath $planPath -Raw -Encoding UTF8 | ConvertFrom-Json
$base = Join-Path $root 'base.apk'
$candidate = Join-Path $root 'candidate.apk'
if ((Get-Sha256 $exe) -cne $build.exe_sha256 -or
    (Get-Sha256 $base) -cne $plan.input_sha256 -or
    (Get-Sha256 $candidate) -cne $plan.candidate_sha256 -or
    ($plan.targets -join ',') -ne '36' -or @($plan.steps).Count -ne 6) {
    throw '程序、APK 或 Android 16 六步计划与复核材料不匹配'
}

$start = New-Object Diagnostics.ProcessStartInfo
$start.FileName = $exe
$start.WorkingDirectory = $root
$start.UseShellExecute = $false
$start.CreateNoWindow = $true
foreach ($name in @($start.EnvironmentVariables.Keys)) {
    if ($name -match '^(REVIVER_|LAZARUS_|JAVA_|ANDROID_)') { $start.EnvironmentVariables.Remove($name) }
}
$start.EnvironmentVariables['LAZARUS_STORAGE_ROOT'] = Join-Path $root '复核运行数据'
$start.EnvironmentVariables['LAZARUS_MIGRATE_LEGACY'] = '0'
$start.EnvironmentVariables['PATH'] = "$env:SystemRoot\System32;$env:SystemRoot"

$tag = [Guid]::NewGuid().ToString('N').Substring(0, 10)
$output = Join-Path $root ("复测结果/" + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + $tag)
New-Item -ItemType Directory -Path $output | Out-Null
if ($Mode -eq 'Prepare') {
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    $listener.Start()
    try { $port = $listener.LocalEndpoint.Port } finally { $listener.Stop() }
    $start.EnvironmentVariables['REVIVER_PORT'] = [string]$port
    $runtimeUrl = "http://127.0.0.1:$port/api/runtime"
    $statePath = Join-Path $output 'prepare.json'
    $desktop = [Diagnostics.Process]::Start($start)
    $initial = $null
    $last = $null
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $readyAfter = $null
    Write-Output '请在工作台中准备 Android 16。此窗口报告环境就绪后，再关闭工作台。'
    while (!$desktop.HasExited) {
        try {
            $response = Invoke-WebRequest -Uri $runtimeUrl -UseBasicParsing -TimeoutSec 3
            $last = [Text.Encoding]::UTF8.GetString($response.RawContentStream.ToArray()) | ConvertFrom-Json
            if ($null -eq $initial) { $initial = $last.targets.'36' }
            if ($last.targets.'36'.ready -and $null -eq $readyAfter) {
                $readyAfter = [Math]::Round($clock.Elapsed.TotalSeconds, 2)
                Write-Output '已观测到 Android 16 环境就绪，可以关闭工作台。'
            }
        } catch [Net.WebException] {
            # The loopback service can be unavailable during startup and shutdown.
        }
        if ($null -eq $initial -and $clock.Elapsed.TotalSeconds -gt 90) { break }
        Start-Sleep -Seconds 1
    }
    $desktop.Refresh()
    $desktopExit = if ($desktop.HasExited) { $desktop.ExitCode } else { $null }
    $final = $last.targets.'36'
    $status = if ($null -eq $initial) { 'runtime-unobserved' }
              elseif ($desktopExit -ne 0 -or !$final.ready) { 'runtime-not-ready' }
              elseif ($initial.ready) { 'runtime-already-ready' }
              else { 'runtime-became-ready' }
    [ordered]@{
        version = $build.version
        source_commit = $build.source_commit
        exe_sha256 = $build.exe_sha256
        target_api = 36
        status = $status
        desktop_exit = $desktopExit
        ready_observed_seconds_since_launch = $readyAfter
        before = $initial
        after = $final
        preparation = $last.active
        scope = 'Android runtime inventory on this Windows host; no device replay'
    } | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $statePath -Encoding UTF8
    Write-Output "环境回执 $statePath"
    if ($status -notin 'runtime-already-ready', 'runtime-became-ready') {
        [Console]::Error.WriteLine('未确认 Android 16 环境就绪，请查看环境回执。')
        exit 2
    }
    return
}

$result = Join-Path $output 'replay.json'
$log = Join-Path $output 'replay.log'
$start.Arguments = '--replay-acceptance "{0}" "{1}" "{2}" --output "{3}" --log "{4}"' -f $planPath, $base, $candidate, $result, $log
if ($Mode -eq 'CheckOnly') { $start.Arguments += ' --check-only' }
$process = [Diagnostics.Process]::Start($start)
Write-Output "复测目录 $output"
$process.WaitForExit()
if ($process.ExitCode -ne 0) {
    [Console]::Error.WriteLine("复测退出码 $($process.ExitCode)，请查看 $log")
    if (Test-Path -LiteralPath $log -PathType Leaf) {
        [Console]::Error.WriteLine((Get-Content -LiteralPath $log -Tail 1 -Encoding UTF8))
    }
    if (Test-Path -LiteralPath $result -PathType Leaf) {
        try {
            $incomplete = Get-Content -LiteralPath $result -Raw -Encoding UTF8 | ConvertFrom-Json
            foreach ($row in $incomplete.rows) {
                if ($row.candidate.failure) { [Console]::Error.WriteLine($row.candidate.failure) }
            }
        } catch {
            [Console]::Error.WriteLine('复测回执无法读取，请查看日志。')
        }
    }
    exit $process.ExitCode
}
$actual = Get-Content -LiteralPath $result -Raw -Encoding UTF8 | ConvertFrom-Json
if ($actual.input_sha256 -cne $plan.input_sha256 -or $actual.candidate_sha256 -cne $plan.candidate_sha256 -or
    ($actual.targets -join ',') -ne '36' -or @($actual.steps).Count -ne @($plan.steps).Count) {
    throw '复测回执的身份或范围与计划不符'
}
for ($i = 0; $i -lt @($plan.steps).Count; $i++) {
    if ($actual.steps[$i].action -cne $plan.steps[$i].action -or $actual.steps[$i].value -cne $plan.steps[$i].value) {
        throw "第 $($i + 1) 步与复测计划不符"
    }
}
if ($Mode -eq 'CheckOnly') {
    if ($actual.status -cne 'inputs-verified') { throw '未完成输入校验' }
    Write-Output '输入身份校验通过。'
    return
}

$row = $actual.rows[0]
$proof = $row.candidate
if ($actual.status -cne 'behavior-verified' -or @($actual.rows).Count -ne 1 -or
    $row.api -ne 36 -or $row.status -cne 'behavior-verified' -or $proof.device.api -ne 36 -or
    $proof.device.installed_sha256 -cne $plan.candidate_sha256 -or
    $proof.install -cne 'passed' -or $proof.launch -cne 'passed' -or $proof.function -cne 'passed' -or
    @($proof.steps | Where-Object status -eq 'passed').Count -ne 6) {
    throw '候选未完成 Android 16 安装、启动和六步行为验收'
}
Write-Output 'Android 16 安装、启动和六步行为验收通过。'
