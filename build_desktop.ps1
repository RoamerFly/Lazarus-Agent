param()
$ErrorActionPreference="Stop"
Set-Location -LiteralPath $PSScriptRoot
$venv=Join-Path $env:LOCALAPPDATA 'LegacyApkReviver\web-venv'
if (!(Test-Path "$venv\Scripts\python.exe")){python -m venv $venv; if($LASTEXITCODE){exit $LASTEXITCODE}}
$python="$venv\Scripts\python.exe"
& $python -m pip install -r requirements.txt pyinstaller
if ($LASTEXITCODE){exit $LASTEXITCODE}
& $python -m PyInstaller --noconfirm Lazarus-Agent.spec
exit $LASTEXITCODE
