# Full workflow (UTF-8 source OK; non-ASCII paths built via UTF-8 bytes for cmd compatibility).
param(
    [Parameter(Mandatory = $true)]
    [string] $ZipUrl
)

$ErrorActionPreference = 'Stop'
$ScriptRoot = $PSScriptRoot
if (-not $ScriptRoot) {
    $ScriptRoot = Split-Path -Parent -LiteralPath $MyInvocation.MyCommand.Path
}

function Utf8BytesToString([byte[]]$Bytes) {
    return [System.Text.Encoding]::UTF8.GetString($Bytes)
}

# UTF-8 for: 提示词.md
$promptFileName = Utf8BytesToString([byte[]](0xE6, 0x8F, 0x90, 0xE7, 0xA4, 0xBA, 0xE8, 0xAF, 0x8D, 0x2E, 0x6D, 0x64))
$PromptMd = Join-Path $ScriptRoot $promptFileName

if ($ZipUrl -notmatch 'github\.com/([^/]+)/([^/]+)/archive/refs/heads/([^.]+)\.zip') {
    [Console]::Error.WriteLine('Invalid GitHub ZIP URL. Expected: https://github.com/<user>/<repo>/archive/refs/heads/<branch>.zip')
    exit 1
}

$ghUser = $Matches[1]
$repo = $Matches[2]
$branch = $Matches[3]

if (-not (Test-Path -LiteralPath $PromptMd)) {
    [Console]::Error.WriteLine("Prompt file not found next to scripts: $PromptMd")
    exit 1
}

$RootCd = (Get-Location).Path
$ProjectsDir = Join-Path $RootCd 'projects'
$QuestionsDir = Join-Path $RootCd 'questions'
New-Item -ItemType Directory -Force -Path $ProjectsDir | Out-Null
New-Item -ItemType Directory -Force -Path $QuestionsDir | Out-Null

$zipFile = Join-Path $ProjectsDir "$repo.zip"
$extracted = Join-Path $ProjectsDir "$repo-$branch"
$target = Join-Path $ProjectsDir $repo

Write-Host "[download] $ghUser/$repo @ $branch"

if (Test-Path -LiteralPath $target) {
    Write-Host "[download] removing existing: $target"
    Remove-Item -LiteralPath $target -Recurse -Force
}

$proxyUrl = "https://gh-proxy.org/$ZipUrl"
& curl.exe -fSL --connect-timeout 60 --max-time 0 -o $zipFile $proxyUrl
if ($LASTEXITCODE -ne 0) {
    [Console]::Error.WriteLine('curl download failed')
    exit $LASTEXITCODE
}

Expand-Archive -LiteralPath $zipFile -DestinationPath $ProjectsDir -Force
if (-not (Test-Path -LiteralPath $extracted)) {
    [Console]::Error.WriteLine("Extracted folder not found: $extracted")
    exit 1
}
Move-Item -LiteralPath $extracted -Destination $target -Force

$outFile = Join-Path $QuestionsDir "$repo.txt"
$outRel = Join-Path 'questions' "$repo.txt"

Write-Host "[download] agent -> $outFile"
$agentScript = Join-Path $ScriptRoot 'download_cmd_agent.ps1'
& $agentScript -WorkDir $target -PromptMdPath $PromptMd -Repo $repo -OutFile $outFile
$agentRc = $LASTEXITCODE
if ($agentRc -ne 0) {
    Write-Warning "agent exit code $agentRc; still committing questions file if present."
}

Set-Location -LiteralPath $RootCd
git add -- $outRel

# UTF-8 for: 自动创建题目 (trailing space)
$commitPrefix = Utf8BytesToString([byte[]](
        0xE8, 0x87, 0xAA, 0xE5, 0x8A, 0xA8, 0xE5, 0x88, 0x9B, 0xE5, 0xBB, 0xBA, 0xE9, 0xA2, 0x98, 0xE7, 0x9B, 0xAE, 0x20
    ))
$commitMsg = "$commitPrefix$repo"
git commit -m $commitMsg
if ($LASTEXITCODE -ne 0) {
    [Console]::Error.WriteLine('git commit failed (nothing to commit? user.name/email?)')
    exit 1
}

git push
if ($LASTEXITCODE -ne 0) {
    [Console]::Error.WriteLine('git push failed')
    exit 1
}

Write-Host '[download] done'
exit 0
