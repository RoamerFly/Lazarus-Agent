param(
    [Parameter(Mandatory)][string]$Archive,
    [Parameter(Mandatory)][string]$Receipt,
    [Parameter(Mandatory)][string]$App,
    [Parameter(Mandatory)][string]$EvidenceDirectory,
    [ValidateSet('Inputs', 'Prepare', 'Device')][string]$Mode = 'Inputs'
)

$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Utility/Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop
Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Archive/Microsoft.PowerShell.Archive.psd1') -ErrorAction Stop
Add-Type -AssemblyName System.IO.Compression.FileSystem

function Read-Json([string]$Path) {
    Get-Content -LiteralPath $Path -Encoding UTF8 -Raw | ConvertFrom-Json
}

function Get-Sha256([string]$Path) {
    (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Confirm-ExtractedEntry([string]$Zip, [string]$Directory, [string[]]$Names) {
    $opened = [IO.Compression.ZipFile]::OpenRead($Zip)
    try {
        foreach ($name in $Names) {
            $entry = $opened.GetEntry($name)
            if (!$entry) { throw "归档中缺少 $name" }
            $stream = $entry.Open()
            try { $expected = (Get-FileHash -InputStream $stream -Algorithm SHA256).Hash.ToLowerInvariant() }
            finally { $stream.Dispose() }
            if ((Get-Sha256 (Join-Path $Directory $name)) -cne $expected) {
                throw "解压文件与固定归档不一致 $name"
            }
        }
    } finally { $opened.Dispose() }
}

$archivePath = (Resolve-Path -LiteralPath $Archive).ProviderPath
$appPath = (Resolve-Path -LiteralPath $App).ProviderPath
$packageReceipt = Read-Json $Receipt
$archiveHash = Get-Sha256 $archivePath
$exeHash = Get-Sha256 $appPath
if ($archiveHash -cne $packageReceipt.archive_sha256 -or $exeHash -cne $packageReceipt.exe_sha256 -or
    $packageReceipt.local_review.scope -cne 'local-engineering-review' -or
    $packageReceipt.local_review.version -cnotmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
    throw '评审包或便携 EXE 与固定回执不符'
}
$version = $packageReceipt.local_review.version
$commit = $packageReceipt.local_review.source_commit
$root = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($EvidenceDirectory)
$materials = Join-Path $root '评审材料'
$runtime = Join-Path $root 'Android 准备入口'
$storage = Join-Path $runtime '复核运行数据'
$inputsPath = Join-Path $root 'inputs.json'
$preparePath = Join-Path $root 'prepare-stage.json'
$shell = Join-Path $env:SystemRoot 'System32/WindowsPowerShell/v1.0/powershell.exe'

if ($Mode -eq 'Inputs') {
    if (Test-Path -LiteralPath $root) { throw '请为 Inputs 指定新的结果目录' }
    Expand-Archive -LiteralPath $archivePath -DestinationPath $materials
} else {
    $prior = Read-Json $inputsPath
    if ($prior.status -cne 'inputs-verified' -or $prior.archive_sha256 -cne $archiveHash -or
        $prior.exe_sha256 -cne $exeHash) { throw '请先用相同 ZIP 和 EXE 执行 Inputs' }
}
Confirm-ExtractedEntry $archivePath $materials @('SHA256SUMS.txt', '校验文件.ps1')
& $shell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $materials '校验文件.ps1')
if ($LASTEXITCODE -ne 0) { throw '评审包解压内容校验失败' }
$build = Read-Json (Join-Path $materials '本地构建回执.json')
if ($build.version -cne $version -or $build.source_commit -cne $commit -or $build.exe_sha256 -cne $exeHash) {
    throw '解压材料的构建身份不符'
}

foreach ($name in @(Get-ChildItem Env: | Where-Object Name -match '^(LAZARUS_|REVIVER_|ANDROID_|JAVA_)' |
                   Select-Object -ExpandProperty Name)) {
    [Environment]::SetEnvironmentVariable($name, $null, 'Process')
}
$env:LAZARUS_STORAGE_ROOT = $storage
$env:LAZARUS_MIGRATE_LEGACY = '0'
$env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"

if ($Mode -eq 'Prepare') {
    $nested = Join-Path $materials "v$version-归档阅读器专项复测输入-r3.zip"
    if (!(Test-Path -LiteralPath (Join-Path $runtime 'SHA256SUMS.txt'))) {
        Expand-Archive -LiteralPath $nested -DestinationPath $runtime
    }
    Confirm-ExtractedEntry $nested $runtime @('SHA256SUMS.txt', '复测归档阅读器.ps1')
    $reports = Join-Path $runtime '复测结果'
    $priorReports = @(Get-ChildItem -LiteralPath $reports -Recurse -Filter prepare.json -ErrorAction SilentlyContinue |
                      Select-Object -ExpandProperty FullName)
    & $shell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $runtime '复测归档阅读器.ps1') -App $appPath -Mode Prepare
    $exitCode = $LASTEXITCODE
    $newReports = @(Get-ChildItem -LiteralPath $reports -Recurse -Filter prepare.json -ErrorAction SilentlyContinue |
                    Where-Object FullName -NotIn $priorReports)
    if ($newReports.Count -ne 1) { throw '本次运行未留下唯一的环境回执' }
    $raw = $newReports[0].FullName
    $prepare = Read-Json $raw
    if ($prepare.source_commit -cne $commit -or $prepare.exe_sha256 -cne $exeHash -or $prepare.target_api -ne 36) {
        throw '环境回执与评审构建不一致'
    }
    [ordered]@{archive_sha256=$archiveHash; exe_sha256=$exeHash; status=$prepare.status;
              exit_code=$exitCode; raw_report=$raw; raw_report_sha256=(Get-Sha256 $raw)} |
        ConvertTo-Json | Set-Content -LiteralPath $preparePath -Encoding UTF8
    exit $exitCode
}

if ($Mode -eq 'Device') {
    if (!(Test-Path -LiteralPath $preparePath -PathType Leaf)) { throw '请先完成 Prepare 并保留 Android 16 环境回执' }
    $prior = Read-Json $preparePath
    $prepareRoot = (Join-Path $runtime '复测结果') + [IO.Path]::DirectorySeparatorChar
    $raw = [IO.Path]::GetFullPath($prior.raw_report)
    if ($prior.exit_code -ne 0 -or $prior.status -notin 'runtime-already-ready', 'runtime-became-ready' -or
        $prior.archive_sha256 -cne $archiveHash -or $prior.exe_sha256 -cne $exeHash -or
        !$raw.StartsWith($prepareRoot, [StringComparison]::OrdinalIgnoreCase) -or
        (Get-Sha256 $raw) -cne $prior.raw_report_sha256) {
        throw '请先在相同结果目录完成 Prepare 并取得有效的 Android 16 环境回执'
    }
}

$rows = foreach ($sample in '工作台演示', 'N03-agent-1') {
    $negative = $sample -eq 'N03-agent-1'
    $argsForReplay = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $materials '复测样本.ps1'),
                       '-LocalBuild', '-App', $appPath, '-Sample', $sample)
    if ($negative) { $argsForReplay += '-Counterexample' }
    if ($Mode -eq 'Inputs') { $argsForReplay += '-CheckOnly' }
    $output = @(& $shell @argsForReplay)
    $exitCode = $LASTEXITCODE
    $output | ForEach-Object { Write-Host $_ }
    if ($exitCode -ne 0) { throw "样本复测失败 $sample" }
    $raw = $output[-1]
    $result = Read-Json $raw
    $expected = if ($Mode -eq 'Inputs') {'inputs-verified'} elseif ($negative) {'incomplete'} else {'behavior-verified'}
    if ($result.status -cne $expected) { throw "复测状态与预期不符 $sample" }
    if ($Mode -eq 'Device') {
        $device = $result.rows[0].candidate.device
        if ($device.api -ne 36 -or $device.abis -notcontains 'x86_64' -or
            $device.installed_sha256 -cne $result.candidate_sha256) {
            throw "设备安装回读与复测样本不一致 $sample"
        }
    }
    [ordered]@{sample=$sample; status=$result.status; steps=@($result.steps).Count;
              raw_report=$raw; raw_report_sha256=(Get-Sha256 $raw)}
}
$os = Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
$file = if ($Mode -eq 'Inputs') { $inputsPath } else { Join-Path $root 'device.json' }
$status = if ($Mode -eq 'Inputs') {'inputs-verified'} else {'fixed-replays-observed'}
[ordered]@{version=$version; source_commit=$commit; archive_sha256=$archiveHash; exe_sha256=$exeHash;
          windows_build="$($os.CurrentBuildNumber).$($os.UBR)"; status=$status; rows=$rows} |
    ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $file -Encoding UTF8
Write-Output $file
