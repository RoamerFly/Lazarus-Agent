param(
    [Parameter(Mandatory)][string]$App,
    [ValidateSet('CheckOnly', 'Prepare', 'Device')][string]$Mode = 'CheckOnly'
)

$ErrorActionPreference = 'Stop'
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

if ($Mode -eq 'Prepare') {
    Write-Output '请在工作台的运行环境中准备 Android 16。准备完成后关闭窗口。'
    $desktop = [Diagnostics.Process]::Start($start)
    $desktop.WaitForExit()
    if ($desktop.ExitCode -ne 0) { throw "工作台退出码 $($desktop.ExitCode)" }
    return
}

$tag = [Guid]::NewGuid().ToString('N').Substring(0, 10)
$output = Join-Path $root ("复测结果/" + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + $tag)
New-Item -ItemType Directory -Path $output | Out-Null
$result = Join-Path $output 'replay.json'
$log = Join-Path $output 'replay.log'
$start.Arguments = '--replay-acceptance "{0}" "{1}" "{2}" --output "{3}" --log "{4}"' -f $planPath, $base, $candidate, $result, $log
if ($Mode -eq 'CheckOnly') { $start.Arguments += ' --check-only' }
$process = [Diagnostics.Process]::Start($start)
Write-Output "复测目录 $output"
$process.WaitForExit()
if ($process.ExitCode -ne 0) { throw "复测退出码 $($process.ExitCode)，请查看 $log" }
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
