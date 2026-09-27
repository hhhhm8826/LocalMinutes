param(
    [string]$Distro = 'Ubuntu',
    [Parameter(Mandatory = $true)][string]$Repo,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Command
)
$ErrorActionPreference = 'Stop'
if (-not $Repo.StartsWith('/') -or $Repo.StartsWith('/mnt/')) {
    throw 'Linux 파일시스템의 절대 저장소 경로가 필요합니다.'
}
& wsl.exe --distribution $Distro --exec test -d $Repo
if ($LASTEXITCODE -ne 0) { throw "WSL 저장소가 없습니다: $Repo" }
if (-not $Command) { $Command = @('bash', 'scripts/run.sh') }
& wsl.exe --distribution $Distro --cd $Repo --exec @Command
exit $LASTEXITCODE
