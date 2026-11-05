param(
    [string]$Sample = '工作台演示',
    [string]$App = (Join-Path $env:LOCALAPPDATA 'Programs\Lazarus Agent\Lazarus-Agent.exe'),
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
Import-Module Microsoft.PowerShell.Utility -ErrorAction Stop
$root = [IO.Path]::GetFullPath($PSScriptRoot)
$samples = Join-Path $root '可分发样本'
$folder = [IO.Path]::GetFullPath((Join-Path $samples $Sample))
if (!$folder.StartsWith($samples + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw '请选择可分发样本目录内的样本'
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
    throw '请先在 Windows桌面版目录下载并安装程序，或用 -App 指定同版程序路径'
}
$manifest = Get-Content -LiteralPath (Join-Path $root 'Windows桌面版/latest.json') -Raw -Encoding utf8 | ConvertFrom-Json
if ((Get-FileHash -LiteralPath $App).Hash.ToLowerInvariant() -ne $manifest.assets.'windows-x64-portable-exe'.sha256) {
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
if ($process.ExitCode -ne 0) { throw "复测未通过（退出码 $($process.ExitCode)），请查看 $log" }
$actual = Get-Content -LiteralPath $result -Raw -Encoding utf8 | ConvertFrom-Json
if ($CheckOnly) {
    if ($actual.status -ne 'inputs-verified') { throw '输入校验没有完成' }
    Write-Output '输入校验通过；尚未运行设备。'
} else {
    if ($actual.status -ne 'behavior-verified') { throw "未完成行为验收：$($actual.status)" }
    Write-Output '本次固定步骤的行为验收通过，适用系统和 ABI 见新报告。'
}
Write-Output $result
