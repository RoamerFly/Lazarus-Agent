param(
    [Parameter(Mandatory)][string]$App,
    [Parameter(Mandatory)][string]$NsisZip,
    [Parameter(Mandatory)][string]$EvidenceDirectory
)

$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Utility/Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop
Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Archive/Microsoft.PowerShell.Archive.psd1') -ErrorAction Stop
$root = [IO.Path]::GetFullPath($PSScriptRoot)
$appPath = (Resolve-Path -LiteralPath $App).ProviderPath
$nsisPath = (Resolve-Path -LiteralPath $NsisZip).ProviderPath

function Get-Sha256([string]$File) {
    (Get-FileHash -LiteralPath $File -Algorithm SHA256).Hash.ToLowerInvariant()
}

& (Join-Path $root '校验文件.ps1')
$buildPath = Join-Path $root '本地构建回执.json'
$build = Get-Content -LiteralPath $buildPath -Raw -Encoding UTF8 | ConvertFrom-Json
$appSha = Get-Sha256 $appPath
if ($build.self_test_exit -ne 0 -or $build.exe_sha256 -cne $appSha) {
    throw '程序与本地构建回执的哈希不符'
}
$nsisSha = Get-Sha256 $nsisPath
if ($nsisSha -cne 'c7d27f780ddb6cffb4730138cd1591e841f4b7edb155856901cdf5f214394fa1') {
    throw 'NSIS 3.11 便携 ZIP 的 SHA-256 不符'
}

$evidence = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($EvidenceDirectory)
if (Test-Path -LiteralPath $evidence) { throw '请选择新的复核结果目录' }
New-Item -ItemType Directory -Path $evidence | Out-Null
$compilerRoot = Join-Path $evidence 'compiler'
Expand-Archive -LiteralPath $nsisPath -DestinationPath $compilerRoot
$compiler = Join-Path $compilerRoot 'nsis-3.11/makensis.exe'
if (!(Test-Path -LiteralPath $compiler -PathType Leaf)) { throw '未找到已核验 ZIP 中的 NSIS 编译器' }

$setup = Join-Path $evidence 'setup-check'
& (Join-Path $root 'tools/test_local_windows_install.ps1') -App $appPath -BuildReceipt $buildPath `
    -MakeNsis $compiler -EvidenceDirectory $setup
$raw = Join-Path $setup 'receipt.json'
$result = Get-Content -LiteralPath $raw -Raw -Encoding UTF8 | ConvertFrom-Json
if ($result.version -cne $build.version -or $result.source_commit -cne $build.source_commit -or
    $result.installed_exe_sha256 -cne $appSha -or $result.install_exit -ne 0 -or
    $result.installed_self_test_exit -ne 0 -or $result.uninstall_exit -ne 0 -or
    !$result.isolated_registration_removed -or !$result.exported_output_preserved) {
    throw '安装复核结果与预期不一致'
}

$os = Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
$summary = [ordered]@{
    version = $result.version
    source_commit = $result.source_commit
    exe_sha256 = $appSha
    nsis_zip_sha256 = $nsisSha
    setup_receipt_sha256 = Get-Sha256 $raw
    windows_edition = $os.EditionID
    windows_release = $os.DisplayVersion
    windows_build = "$($os.CurrentBuildNumber).$($os.UBR)"
    status = 'isolated-install-and-uninstall-verified'
    scope = 'Windows installation, executable self-test, uninstall and output retention'
}
$summary | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidence 'receipt.json') -Encoding UTF8
Write-Output '安装、自检、卸载和导出保留通过。Android 设备功能需另行复测。'
