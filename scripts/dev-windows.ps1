param(
    [string]$Distro = 'Ubuntu',
    [string]$Repo = '/home/cs2023/src/local-meeting-minutes',
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Command
)
$ErrorActionPreference = 'Stop'
if (-not $Repo.StartsWith('/') -or $Repo.StartsWith('/mnt/')) {
    throw 'Linux 파일시스템의 절대 저장소 경로가 필요합니다.'
}
& wsl.exe --distribution $Distro --exec test -d $Repo
if ($LASTEXITCODE -ne 0) { throw "WSL 저장소가 없습니다: $Repo" }
if (-not $Command) { $Command = @('bash', '-l') }
& wsl.exe --distribution $Distro --cd $Repo --exec @Command
exit $LASTEXITCODE
