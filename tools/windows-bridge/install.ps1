# One-time setup for the Everything Hub project-owned Windows build bridge.
# Requires Windows x64, Git, Python 3.12+, and GitHub CLI (gh).
# No service, firewall, DNS, database, or production settings are changed.
$ErrorActionPreference = "Stop"
$Repo = "rshure001/EverythingHub-Crawler"
$RunnerRoot = Join-Path $env:USERPROFILE "EverythingHub-Windows-Runner"
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { throw "Install GitHub CLI (gh), then rerun." }
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "Git is required." }
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw "Python 3.12+ is required." }
gh auth status
if ($LASTEXITCODE -ne 0) { throw "Run 'gh auth login' with an account that has admin access to $Repo." }
$Release = Invoke-RestMethod -Headers @{ "User-Agent" = "EverythingHub-Windows-Bridge" } -Uri "https://api.github.com/repos/actions/runner/releases/latest"
$Asset = $Release.assets | Where-Object { $_.name -match '^actions-runner-win-x64-.*\.zip$' } | Select-Object -First 1
if (-not $Asset) { throw "Latest Windows x64 runner package was not found." }
New-Item -ItemType Directory -Force -Path $RunnerRoot | Out-Null
$ZipPath = Join-Path $RunnerRoot $Asset.name
Invoke-WebRequest -Uri $Asset.browser_download_url -OutFile $ZipPath
Expand-Archive -Path $ZipPath -DestinationPath $RunnerRoot -Force
Remove-Item $ZipPath -Force
$Token = gh api --method POST "repos/$Repo/actions/runners/registration-token" --jq .token
if ($LASTEXITCODE -ne 0 -or -not $Token) { throw "GitHub did not issue a runner registration token. Check repository admin permission." }
Push-Location $RunnerRoot
try {
    if (Test-Path ".runner") { throw "A runner is already configured in $RunnerRoot." }
    .\config.cmd --url "https://github.com/$Repo" --token $Token --name "EverythingHub-$env:COMPUTERNAME" --labels "everythinghub,windows" --work "_work" --unattended --replace
    if ($LASTEXITCODE -ne 0) { throw "Runner configuration failed." }
} finally { Pop-Location }
$Token = $null
Write-Host "BRIDGE REGISTERED." -ForegroundColor Green
Write-Host "Start it with: cd $RunnerRoot ; .\run.cmd"
Write-Host "Keep the runner window open while a dispatched job runs."
