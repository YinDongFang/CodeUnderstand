param(
    [Parameter(Mandatory = $true)]
    [string] $WorkDir,
    [Parameter(Mandatory = $true)]
    [string] $PromptMdPath,
    [Parameter(Mandatory = $true)]
    [string] $Repo,
    [Parameter(Mandatory = $true)]
    [string] $OutFile
)

if (-not (Test-Path -LiteralPath $PromptMdPath)) {
    [Console]::Error.WriteLine("Prompt file not found: $PromptMdPath")
    exit 1
}

$utf8 = New-Object System.Text.UTF8Encoding $false
$text = [System.IO.File]::ReadAllText($PromptMdPath, $utf8)
$prompt = $text.Replace('{repo}', $Repo)

Push-Location $WorkDir
try {
    # PowerShell 5.1 无 *> 重定向，合并 stderr 后写入文件
    $output = & agent -p --trust $prompt 2>&1
    $code = $LASTEXITCODE
    $output | Set-Content -LiteralPath $OutFile -Encoding UTF8
    exit $code
}
finally {
    Pop-Location
}
