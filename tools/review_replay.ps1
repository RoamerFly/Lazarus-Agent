param(
    [string]$Sample = '工作台演示',
    [string]$App = (Join-Path $env:LOCALAPPDATA 'Programs\Lazarus Agent\Lazarus-Agent.exe'),
    [switch]$CheckOnly,
    [switch]$Counterexample,
    [switch]$LocalBuild
)
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Utility/Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop
$root = [IO.Path]::GetFullPath($PSScriptRoot)
if ($Counterexample -and !$PSBoundParameters.ContainsKey('Sample')) { $Sample = 'N03-agent-1' }
if ($LocalBuild -and !$Counterexample -and !$PSBoundParameters.ContainsKey('Sample')) { $Sample = 'P01-agent-1' }
if ($LocalBuild -and !$PSBoundParameters.ContainsKey('App')) { throw '请用 -App 指定本地候选 EXE' }
$category = if ($Counterexample) { '反例样本' } else { '可分发样本' }
$samples = Join-Path $root $category
$folder = [IO.Path]::GetFullPath((Join-Path $samples $Sample))
if (!$folder.StartsWith($samples + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw '请选择样本目录内的样本'
}
$report = Join-Path $folder 'agent-acceptance.json'
$base = Join-Path $folder 'base.apk'
$candidate = Join-Path $folder 'candidate.apk'
$proof = Get-Content -LiteralPath $report -Raw -Encoding utf8 | ConvertFrom-Json
if ((Get-FileHash -LiteralPath $base).Hash.ToLowerInvariant() -ne $proof.input_sha256 -or
    (Get-FileHash -LiteralPath $candidate).Hash.ToLowerInvariant() -ne $proof.candidate_sha256) {
    throw '样本与验收报告的 APK 哈希不符'
}
if (!(Test-Path -LiteralPath $App -PathType Leaf)) {
    throw '请先按复核说明校验并安装程序，或用 -App 指定同版程序路径'
}
if ($LocalBuild) {
    $build = Get-Content -LiteralPath (Join-Path $root '本地构建回执.json') -Raw -Encoding utf8 | ConvertFrom-Json
    if ($build.self_test_exit -ne 0 -or $build.exe_sha256 -cnotmatch '^[a-f0-9]{64}$') {
        throw '本地候选构建回执无效'
    }
    $expectedExe = $build.exe_sha256
} else {
    $manifest = Get-Content -LiteralPath (Join-Path $root 'Windows桌面版/latest.json') -Raw -Encoding utf8 | ConvertFrom-Json
    $expectedExe = $manifest.assets.'windows-x64-portable-exe'.sha256
}
if ((Get-FileHash -LiteralPath $App).Hash.ToLowerInvariant() -cne $expectedExe) {
    throw '程序与包内版本不符，请使用本包固定版本的安装器或便携版'
}
$output = Join-Path $root ('复测结果/' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $output | Out-Null
$result = Join-Path $output 'replay.json'
$log = Join-Path $output 'replay.log'
$arguments = '--replay-acceptance "{0}" "{1}" "{2}" --output "{3}" --log "{4}"' -f $report, $base, $candidate, $result, $log
if ($CheckOnly) { $arguments += ' --check-only' }
Write-Output "样本：$Sample；报告目录：$output"
Write-Output '实际设备复测需要先在工作台准备目标 Android 环境，运行中请勿同时操作专用模拟器。'
$process = Start-Process -FilePath $App -ArgumentList $arguments -WorkingDirectory $root -WindowStyle Hidden -PassThru
$last = ''
while (!$process.HasExited) {
    if (Test-Path -LiteralPath $log) {
        $progress = Get-Content -LiteralPath $log -Tail 1 -Encoding utf8
        if ($progress -ne $last) { Write-Output $progress; $last = $progress }
    }
    Start-Sleep -Seconds 2
    $process.Refresh()
}
$expectedExit = if ($Counterexample -and !$CheckOnly) { 1 } else { 0 }
if ($process.ExitCode -ne $expectedExit) { throw "复测退出码 $($process.ExitCode) 与预期 $expectedExit 不符，请查看 $log" }
$actual = Get-Content -LiteralPath $result -Raw -Encoding utf8 | ConvertFrom-Json
if ($CheckOnly) {
    if ($actual.status -ne 'inputs-verified') { throw '输入校验没有完成' }
    Write-Output '输入校验通过；尚未运行设备。'
} elseif ($Counterexample) {
    $expected = Get-Content -LiteralPath (Join-Path $folder 'expected-outcome.json') -Raw -Encoding utf8 | ConvertFrom-Json
    if ($expected.exit_code -ne 1 -or $actual.status -ne 'incomplete' -or
        $actual.candidate_sha256 -ne $proof.candidate_sha256 -or
        (@($actual.targets) -join ',') -ne (@($proof.targets) -join ',') -or
        @($actual.rows).Count -ne @($proof.targets).Count) { throw '反例报告的身份、范围或状态不符合预期' }
    for ($targetIndex = 0; $targetIndex -lt @($actual.targets).Count; $targetIndex++) {
        $row = $actual.rows[$targetIndex]
        $candidateResult = $row.candidate
        $api = $actual.targets[$targetIndex]
        if ($row.api -ne $api -or $candidateResult.device.api -ne $api) { throw '反例设备版本与复测目标不一致' }
        if ($row.status -ne 'failed' -or $candidateResult.install -ne 'passed' -or
            $candidateResult.launch -ne 'passed' -or $candidateResult.function -ne 'failed' -or
            $candidateResult.device.installed_sha256 -ne $proof.candidate_sha256 -or
            @($candidateResult.steps).Count -ne @($expected.steps).Count) { throw '反例没有在指定的行为检查处失败' }
        for ($index = 0; $index -lt @($expected.steps).Count; $index++) {
            $observed = $candidateResult.steps[$index]
            $wanted = $expected.steps[$index]
            foreach ($field in @('action','value','status','match_count')) {
                if ($observed.$field -ne $wanted.$field) { throw "反例步骤 $index 的 $field 与预期不符" }
            }
        }
    }
    Write-Output '反例复核符合预期：APK 已安装并启动，矛盾行为断言失败，候选未获功能验收。'
} else {
    if ($actual.status -ne 'behavior-verified') { throw "未完成行为验收：$($actual.status)" }
    Write-Output '本次固定步骤的行为验收通过，适用系统和 ABI 见新报告。'
}
Write-Output $result
