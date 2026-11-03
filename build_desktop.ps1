param(
    [switch]$BuildOfflineKit,
    [string]$Sdk,
    [string]$ImageSdk,
    [string]$JavaHome
)
$ErrorActionPreference="Stop"
Set-Location -LiteralPath $PSScriptRoot
$venv=Join-Path $PSScriptRoot 'build\build-venv'
if (!(Test-Path "$venv\Scripts\python.exe")){python -m venv $venv; if($LASTEXITCODE){exit $LASTEXITCODE}}
$python="$venv\Scripts\python.exe"
& $python -m pip install -r requirements.txt pyinstaller
if ($LASTEXITCODE){exit $LASTEXITCODE}
& $python -m PyInstaller --noconfirm Lazarus-Agent.spec
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
