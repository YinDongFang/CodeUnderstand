param(
    [Parameter(Mandatory = $true)]
    [string] $ZipUrl
)

if ($ZipUrl -notmatch 'github\.com/([^/]+)/([^/]+)/archive/refs/heads/([^.]+)\.zip') {
    [Console]::Error.WriteLine('badurl')
    exit 1
}

$user = $Matches[1]
$repo = $Matches[2]
$branch = $Matches[3]
Write-Output ("{0}|{1}|{2}" -f $user, $repo, $branch)
exit 0
