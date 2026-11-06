param(
    [Parameter(Mandatory)][string]$App,
    [Parameter(Mandatory)][string]$BuildReceipt,
    [Parameter(Mandatory)][string]$MakeNsis,
    [Parameter(Mandatory)][string]$EvidenceDirectory
)

$ErrorActionPreference = 'Stop'

function Get-Sha256([string]$File) {
    (Get-FileHash -LiteralPath $File -Algorithm SHA256).Hash.ToLowerInvariant()
}

$appPath = (Resolve-Path -LiteralPath $App).ProviderPath
$compiler = (Resolve-Path -LiteralPath $MakeNsis).ProviderPath
$build = Get-Content -LiteralPath $BuildReceipt -Raw -Encoding UTF8 | ConvertFrom-Json
$appHash = Get-Sha256 $appPath
if ($build.version -notmatch '^\d+\.\d+\.\d+$' -or
    $build.source_commit -notmatch '^[0-9a-f]{40}$' -or
    $build.self_test_exit -ne 0 -or $build.exe_sha256 -cne $appHash) {
    throw 'The executable does not match the local build receipt'
}

$root = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($EvidenceDirectory)
if (Test-Path -LiteralPath $root) { throw 'Choose a new evidence directory' }
New-Item -ItemType Directory -Path $root | Out-Null
$installDir = Join-Path $root '安装 目录'
$dataDir = Join-Path $root '应用 数据'
$tempDir = Join-Path $root '临时 目录'
$outputDir = Join-Path $dataDir 'output'
New-Item -ItemType Directory -Path $outputDir, $tempDir | Out-Null
$sentinel = Join-Path $outputDir 'keep.txt'
Set-Content -LiteralPath $sentinel -Value 'preserve this exported output' -Encoding Ascii
$sentinelHash = Get-Sha256 $sentinel

$id = [Guid]::NewGuid().ToString('N')
$appKey = "Software\Lazarus Agent F01 Review $id"
$uninstallKey = "Software\Microsoft\Windows\CurrentVersion\Uninstall\Lazarus Agent F01 Review $id"
$appRegistry = "HKCU:\$appKey"
$uninstallRegistry = "HKCU:\$uninstallKey"
if ((Test-Path -LiteralPath $appRegistry) -or (Test-Path -LiteralPath $uninstallRegistry)) { throw 'The isolated registration already exists' }
$installerSource = Join-Path $PSScriptRoot '../installer/Lazarus-Agent.nsi'
$installer = Join-Path $root 'Lazarus-Agent-local-isolated-setup.exe'
$compilerVersion = (& $compiler /VERSION | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Could not identify the NSIS compiler' }
& $compiler "/DVERSION=$($build.version)" "/DSOURCE_EXE=$appPath" "/DOUTPUT=$installer" `
    "/DAPP_REG_KEY=$appKey" "/DUNINSTALL_REG_KEY=$uninstallKey" "/DDATA_ROOT=$dataDir" '/DNO_SHORTCUTS' `
    $installerSource *> (Join-Path $root 'compile.log')
if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed' }
$installerHash = Get-Sha256 $installer
$uninstaller = Join-Path $installDir 'Uninstall.exe'

try {
    $install = Start-Process -FilePath $installer -ArgumentList "/S /D=$installDir" -WindowStyle Hidden -Wait -PassThru
    if ($install.ExitCode -ne 0) { throw "Installer exited $($install.ExitCode)" }
    $installed = Join-Path $installDir 'Lazarus-Agent.exe'
    $installedHash = Get-Sha256 $installed
    $marker = Get-Content -LiteralPath (Join-Path $installDir '.lazarus-install-complete') -Raw
    $registered = Get-ItemProperty -LiteralPath $uninstallRegistry
    if ($installedHash -cne $appHash -or $marker -cne $build.version -or
        (Get-ItemProperty -LiteralPath $appRegistry).InstallDir -ine $installDir -or
        $registered.DisplayVersion -cne $build.version) {
        throw 'Installed executable, marker or registration differs from the candidate'
    }

    $start = New-Object Diagnostics.ProcessStartInfo
    $start.FileName = $installed
    $start.Arguments = '--self-test'
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    foreach ($name in @($start.EnvironmentVariables.Keys)) {
        if ($name -match '^(REVIVER_|LAZARUS_|JAVA_|ANDROID_)') { $start.EnvironmentVariables.Remove($name) }
    }
    $start.EnvironmentVariables['PATH'] = "$env:SystemRoot\System32;$env:SystemRoot"
    $start.EnvironmentVariables['TEMP'] = $tempDir
    $start.EnvironmentVariables['TMP'] = $tempDir
    $start.EnvironmentVariables['LAZARUS_STORAGE_ROOT'] = $dataDir
    $start.EnvironmentVariables['LAZARUS_MIGRATE_LEGACY'] = '0'
    $process = [Diagnostics.Process]::Start($start)
    $stdout = $process.StandardOutput.ReadToEndAsync()
    $stderr = $process.StandardError.ReadToEndAsync()
    if (!$process.WaitForExit(120000)) {
        $process.Kill()
        throw 'Installed self-test timed out'
    }
    $stdout.Result | Set-Content -LiteralPath (Join-Path $root 'installed-self-test.json') -Encoding UTF8
    $stderr.Result | Set-Content -LiteralPath (Join-Path $root 'installed-self-test.err') -Encoding UTF8
    if ($process.ExitCode -ne 0 -or ($stdout.Result | ConvertFrom-Json).version -cne $build.version) {
        throw 'The installed executable failed its versioned self-test'
    }
} finally {
    if (Test-Path -LiteralPath $uninstaller -PathType Leaf) {
        $uninstall = Start-Process -FilePath $uninstaller -ArgumentList '/S' -WindowStyle Hidden -Wait -PassThru
    }
}

if (!$uninstall -or $uninstall.ExitCode -ne 0 -or (Test-Path -LiteralPath $installDir) -or
    (Test-Path -LiteralPath $appRegistry) -or (Test-Path -LiteralPath $uninstallRegistry) -or
    (Get-Sha256 $sentinel) -cne $sentinelHash) {
    throw 'Uninstall did not remove the isolated installation and preserve exported output'
}

$receipt = [ordered]@{
    scope = 'isolated install on existing Windows; no Android device'
    version = $build.version
    source_commit = $build.source_commit
    installer_script_sha256 = (Get-Sha256 $installerSource)
    nsis_version = $compilerVersion
    installer_sha256 = $installerHash
    exe_sha256 = $appHash
    installed_exe_sha256 = $installedHash
    install_exit = $install.ExitCode
    installed_self_test_exit = $process.ExitCode
    uninstall_exit = $uninstall.ExitCode
    system_only_path_for_self_test = $true
    isolated_registration_removed = $true
    exported_output_preserved = $true
}
$receipt | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $root 'receipt.json') -Encoding UTF8
[pscustomobject]$receipt
