param([int]$Port=8767)
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
$venv=Join-Path $env:LOCALAPPDATA 'LegacyApkReviver\web-venv'
$python=Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)){
    python -m venv $venv
    if($LASTEXITCODE){throw 'Unable to create Python virtual environment'}
}
& $python -c 'import fastapi, uvicorn, cv2' 2>$null
if($LASTEXITCODE){
    & $python -m pip install -r requirements.txt
    if($LASTEXITCODE){throw 'Unable to install workbench dependencies'}
}
if($env:JAVA_HOME){$env:Path="$env:JAVA_HOME\bin;$env:Path"}
$env:REVIVER_PORT=$Port
& $python -m uvicorn workbench:app --host 127.0.0.1 --port $Port --no-access-log
exit $LASTEXITCODE
