param(
    [switch]$BuildOfflineKit,
    [string]$Sdk,
    [string]$ImageSdk,
    [string]$JavaHome,
    [string]$WebViewRuntime
)
$ErrorActionPreference="Stop"
Set-Location -LiteralPath $PSScriptRoot
$venv=Join-Path $PSScriptRoot 'build\build-venv'
function Resolve-BasePython {
    $candidates = @()
    if ($env:REVIVER_PYTHON) { $candidates += $env:REVIVER_PYTHON }
    $command = Get-Command python -ErrorAction SilentlyContinue
    if ($command) { $candidates += $command.Source }
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) { $candidates += $launcher.Source }
    foreach ($candidate in $candidates | Select-Object -Unique) {
        try {
            & $candidate -c "import sys; print(sys.executable)" *> $null
            if ($LASTEXITCODE -eq 0) { return $candidate }
        } catch { }
    }
    throw "找不到可用的 Python。请设置 REVIVER_PYTHON，或安装 Python 3.10/3.11。"
}
$basePython=Resolve-BasePython
$python=Join-Path $venv 'Scripts\python.exe'
if (Test-Path $python) {
    try { & $python -c "import sys; print(sys.executable)" *> $null } catch { $LASTEXITCODE=1 }
    if ($LASTEXITCODE -ne 0) { Remove-Item -LiteralPath $venv -Recurse -Force }
}
if (!(Test-Path $python)){& $basePython -m venv $venv; if($LASTEXITCODE){exit $LASTEXITCODE}}
& $python -m pip install -r requirements.txt pyinstaller
if ($LASTEXITCODE){exit $LASTEXITCODE}
& $python -m PyInstaller --noconfirm Lazarus-Agent.spec
if ($LASTEXITCODE){exit $LASTEXITCODE}
$envArguments=@('tools/build_portable_env.py')
if($Sdk){$envArguments += @('--sdk',$Sdk)}
if($JavaHome){$envArguments += @('--java',$JavaHome)}
if($WebViewRuntime){$envArguments += @('--webview',$WebViewRuntime)}
& $python @envArguments
if ($LASTEXITCODE){exit $LASTEXITCODE}
if ($BuildOfflineKit){
    $arguments=@('offline_kit.py','build','--out',(Join-Path $PSScriptRoot 'dist\Lazarus-Agent-Offline-Lab-Windows-x64'))
    if($Sdk){$arguments += @('--sdk',$Sdk)}
    if($ImageSdk){$arguments += @('--image-sdk',$ImageSdk)}
    if($JavaHome){$arguments += @('--java-home',$JavaHome)}
    & $python @arguments
    if($LASTEXITCODE){exit $LASTEXITCODE}
}
exit 0
