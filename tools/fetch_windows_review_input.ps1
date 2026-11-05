param(
    [Parameter(Mandatory)][ValidatePattern('^\d+\.\d+\.\d+$')][string]$Version,
    [AllowEmptyString()][string]$BuildRun = '',
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{40}$')][string]$Commit
)
$ErrorActionPreference = 'Stop'
$repo = 'RoamerFly/Lazarus-Agent'
if ($BuildRun) {
    if ($BuildRun -notmatch '^[0-9]+$') { throw 'Invalid candidate build run' }
    $metadata = gh run view $BuildRun --repo $repo --json workflowName,conclusion,headSha | ConvertFrom-Json
    if ($LASTEXITCODE -or $metadata.workflowName -ne 'Release Windows' -or
        $metadata.conclusion -ne 'success' -or $metadata.headSha -ne $Commit) {
        throw 'Candidate build workflow, commit or conclusion does not match'
    }
    gh run download $BuildRun --repo $repo --dir inputs --name "Lazarus-Agent-review-inputs-$Version"
} else {
    gh release download "v$Version" --repo $repo --dir inputs --pattern Lazarus-Agent-windows-x64-setup.exe --pattern latest.json --pattern latest.json.sig
}
if ($LASTEXITCODE) { throw 'Installer inputs download failed' }
foreach ($name in 'Lazarus-Agent-windows-x64-setup.exe','latest.json','latest.json.sig') {
    if (!(Test-Path -LiteralPath (Join-Path inputs $name) -PathType Leaf)) { throw "Missing review input: $name" }
}
