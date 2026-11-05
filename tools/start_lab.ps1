param(
    [Parameter(Mandatory)][ValidateSet('30','34','35','36')]$Api,
    [Parameter(Mandatory)][ValidatePattern('^Reviver[A-Za-z0-9]+$')]$Name,
    [Parameter(Mandatory)][ValidateRange(5554,5680)][int]$Port,
    [string]$Sdk = 'E:\Software\Android-Tool\Sdk',
    [string]$Lab = 'E:\Agent\android-reviver-lab',
    [switch]$Window,
    [switch]$ResetBench,
    [switch]$DryRun
)
$ErrorActionPreference='Stop'
function Use-AsciiPath([string]$OriginalPath, [switch]$CreateTarget) {
    # emulator.exe still rejects SDK, AVD and image paths containing non-ASCII
    # characters on some Windows builds. A stable local junction keeps a
    # portable resource pack usable when it was extracted under a Chinese path.
    $OriginalPath=[IO.Path]::GetFullPath($OriginalPath)
    if ($CreateTarget) { New-Item -ItemType Directory -Force -Path $OriginalPath | Out-Null }
    if ($OriginalPath -cmatch '^[\x00-\x7F]+$') { return $OriginalPath }
    $temp=[IO.Path]::GetTempPath()
    if ($temp -cnotmatch '^[\x00-\x7F]+$') {
        $temp=$null
        foreach ($candidate in @($env:REVIVER_TOOL_TEMP,'C:\Temp')) {
            if (-not $candidate -or $candidate -cnotmatch '^[\x00-\x7F]+$') { continue }
            try {
                New-Item -ItemType Directory -Force -Path $candidate -ErrorAction Stop | Out-Null
                $temp=[IO.Path]::GetFullPath($candidate)
                break
            } catch {}
        }
        if (-not $temp) { throw 'Choose a writable ASCII directory in REVIVER_TOOL_TEMP before starting verification.' }
        # Only this helper and its emulator child use the tool-safe directory.
        $env:TEMP=$temp
        $env:TMP=$temp
    }
    $bytes=[Text.Encoding]::UTF8.GetBytes($OriginalPath)
    $hash=([Security.Cryptography.SHA256]::Create().ComputeHash($bytes) | ForEach-Object { $_.ToString('x2') }) -join ''
    $bridgeRoot=Join-Path $temp 'Lazarus-Agent-sdk-links'
    $bridge=Join-Path $bridgeRoot $hash.Substring(0,16)
    if (-not (Test-Path -LiteralPath $bridge)) {
        New-Item -ItemType Directory -Force -Path $bridgeRoot | Out-Null
        New-Item -ItemType Junction -Path $bridge -Target $OriginalPath | Out-Null
    }
    return $bridge
}
if ($Port % 2) { throw 'Use an even emulator console port' }
$Sdk=Use-AsciiPath $Sdk
$Lab=Use-AsciiPath $Lab -CreateTarget
$emulator=Join-Path $Sdk 'emulator\emulator.exe'
$adb=Join-Path $Sdk 'platform-tools\adb.exe'
if ((& $adb devices) -match "^emulator-$Port\s") { throw "Port $Port already has an emulator" }
$avd=Join-Path $Lab 'avd'
$profile=Join-Path $avd "$Name.avd"
if ($ResetBench -and $Name -notin @("ReviverBench$Api")) { throw 'Only workbench-owned devices may be reset' }
if ((Test-Path -LiteralPath $profile) -and -not $ResetBench) { throw 'Choose a fresh profile name to avoid reusing old data' }
$tag='google_apis_playstore'
$abi='x86_64'
$image=Join-Path $Sdk "system-images\android-$Api\$tag\x86_64"
foreach ($installedTag in @('google_apis','default')) {
    if (Test-Path -LiteralPath (Join-Path $image 'system.img')) { break }
    $tag=$installedTag
    $image=Join-Path $Sdk "system-images\android-$Api\$tag\x86_64"
}
if ($Api -eq '30' -and -not (Test-Path (Join-Path $image 'system.img'))) {
    $oldSdk=if($env:REVIVER_OLD_SDK){$env:REVIVER_OLD_SDK}else{'D:\android\AndroidSDK'}
    $tag='google_apis_playstore'
    $abi='x86'
    $image=Join-Path $oldSdk "system-images\android-30\$tag\x86"
}
if (-not (Test-Path -LiteralPath (Join-Path $image 'system.img'))) {
    $tag='default'
    $abi='x86_64'
    $cache=Join-Path $env:LOCALAPPDATA "LegacyApkReviver\lab-sdk\system-images\android-$Api\default\x86_64"
    if (-not (Test-Path -LiteralPath (Join-Path $cache 'system.img'))) { throw "Run prepare_lab.py $Api" }
    $image=Join-Path $Lab "image-$Api"
    if (-not (Test-Path -LiteralPath $image)) {
        New-Item -ItemType Directory -Force -Path $Lab | Out-Null
        New-Item -ItemType Junction -Path $image -Target $cache | Out-Null
    }
}
New-Item -ItemType Directory -Path $profile -Force | Out-Null
@('avd.ini.encoding=UTF-8',"path=$profile","target=android-$Api") | Set-Content "$avd\$Name.ini" -Encoding ascii
@("AvdId=$Name",'avd.ini.encoding=UTF-8',"abi.type=$abi","hw.cpu.arch=$abi",
  'hw.cpu.ncore=2','hw.ramSize=2048','hw.lcd.width=720','hw.lcd.height=1280','hw.lcd.density=240',
  'hw.keyboard=yes','hw.gpu.enabled=yes','disk.dataPartition.size=2G',"image.sysdir.1=$image\",
  "tag.id=$tag","target=android-$Api") | Set-Content "$profile\config.ini" -Encoding ascii
$env:ANDROID_AVD_HOME=$avd
$env:ANDROID_SDK_ROOT=$Sdk
$env:ANDROID_HOME=$Sdk
$env:Path="$(Join-Path $Sdk 'platform-tools');$env:Path"
$arguments=@('-avd',$Name,'-port',$Port,'-no-boot-anim','-no-snapshot','-gpu','software')
if ($ResetBench) { $arguments+='-wipe-data' }
if ($DryRun) {
    [Console]::Out.WriteLine(([PSCustomObject]@{Sdk=$Sdk;Lab=$Lab;AvdHome=$avd;Profile=$profile;Image=$image;Api=$Api;Name=$Name;Port=$Port;Architecture=$abi;Tag=$tag} | ConvertTo-Json -Compress))
    exit 0
}
$style='Normal'
if (-not $Window) { $arguments+=@('-no-window','-no-audio'); $style='Hidden' }
$process=Start-Process $emulator -ArgumentList $arguments -WindowStyle $style -PassThru -RedirectStandardOutput "$Lab\$Name.stdout" -RedirectStandardError "$Lab\$Name.stderr"
"$Name emulator-$Port PID $($process.Id); logs: $Lab"
