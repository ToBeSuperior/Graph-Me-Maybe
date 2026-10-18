# Graph-Me-Maybe installer for Windows.
#
#   powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/ToBeSuperior/Graph-Me-Maybe/main/install.ps1 | iex"
#
# Run again to update. Options, in the environment ($env:NAME = 'value' before the line):
#   GRAPHME_VERSION=v0.1.0      a release tag (default: the latest)
#   GRAPHME_INSTALL_SOURCE=DIR  take the release files from a folder or URL instead
#   GRAPHME_HOME=DIR            the app's folder (settings, graph data, web app, viewer)
#   GRAPHME_YES=1               answer yes (installs Docker Desktop when it is missing)
#   GRAPHME_NO_DOCKER=1  GRAPHME_NO_SHORTCUT=1  GRAPHME_NO_LAUNCH=1
#   GRAPHME_UNINSTALL=1         remove the app; GRAPHME_PURGE=1 also its settings and data
#   UV_NO_MODIFY_PATH=1         leave your user PATH alone
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest is many times slower with it
$Repo = 'ToBeSuperior/Graph-Me-Maybe'
$Tool = 'graph-me-maybe'
$Raw = "https://raw.githubusercontent.com/$Repo/main/install.ps1"
$UninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Graph-Me-Maybe'
$Shortcut = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Graph-Me-Maybe.lnk'

function Say([string]$Message) { Write-Host "graph-me-maybe: $Message" }
function Fail([string]$Message) { Write-Error "graph-me-maybe: $Message"; exit 1 }
function Ask([string]$Question) {
    if ($env:GRAPHME_YES) { return $true }
    if (-not [Environment]::UserInteractive) { return $false }
    try { return (Read-Host "$Question [y/N]") -match '^(y|yes)$' } catch { return $false }
}
# Windows PowerShell 5.1 turns a native command's redirected stderr into a terminating error
# under 'Stop'; commands whose output is thrown away run here instead.
function Quiet([scriptblock]$Run) { $ErrorActionPreference = 'Continue'; & $Run *> $null }

$DefaultHome = Join-Path $env:LOCALAPPDATA 'Graph-Me-Maybe'
$AppHome = if ($env:GRAPHME_HOME) { $env:GRAPHME_HOME } else { $DefaultHome }

function Ensure-Uv {
    if (Get-Command uv -ErrorAction SilentlyContinue) { return }
    Say 'installing uv (https://docs.astral.sh/uv/)'
    powershell -NoProfile -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $uvBin = if ($env:UV_INSTALL_DIR) { $env:UV_INSTALL_DIR } else { "$env:USERPROFILE\.local\bin" }
    $env:Path = "$uvBin;$env:Path"
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { Fail 'uv did not install' }
}

function Get-Tag {
    if ($env:GRAPHME_VERSION) { return $env:GRAPHME_VERSION }
    return (Invoke-RestMethod "https://api.github.com/repos/$Repo/releases/latest").tag_name
}

function Fetch([string]$Source, [string]$Name, [string]$Into) {
    $target = Join-Path $Into $Name
    if (Test-Path -LiteralPath $Source -PathType Container) {
        $from = Join-Path $Source $Name
        if (-not (Test-Path -LiteralPath $from)) { Fail "$Name is not in $Source" }
        Copy-Item -LiteralPath $from $target
    } else {
        try { Invoke-WebRequest -UseBasicParsing "$Source/$Name" -OutFile $target }
        catch { Fail "could not download $Name" }
    }
}

function Verify([string]$Name, [string]$Dir) {
    $line = Get-Content (Join-Path $Dir 'SHA256SUMS') | Where-Object { ($_ -split '\s+')[1] -eq $Name }
    if (-not $line) { Fail "$Name is not in SHA256SUMS" }
    $want = (@($line)[0] -split '\s+')[0].ToLower()
    $have = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $Dir $Name)).Hash.ToLower()
    if ($have -ne $want) { Fail "${Name}: checksum does not match SHA256SUMS" }
}

function Has-Chrome {
    foreach ($root in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA)) {
        if ($root -and (Test-Path (Join-Path $root 'Google\Chrome\Application\chrome.exe'))) { return $true }
    }
    return $false
}

function Offer-Docker {
    if ($env:GRAPHME_NO_DOCKER) { return }
    if (Get-Command docker -ErrorAction SilentlyContinue) {
        Quiet { docker info }
        if ($LASTEXITCODE -ne 0) { Say 'Docker is installed but not running: start Docker Desktop before building a graph' }
        return
    }
    Say 'Docker runs the graph database (Neo4j). Docker Desktop is free for personal use and small'
    Say 'companies; larger ones need a paid plan. The first time, Windows may ask for administrator'
    Say 'rights to turn on WSL 2 and to restart; open Docker Desktop once afterwards and accept its terms.'
    if ((Get-Command winget -ErrorAction SilentlyContinue) -and (Ask 'Install Docker Desktop now with winget?')) {
        winget install -e --id Docker.DockerDesktop --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) { Say "warning: winget could not install Docker Desktop (exit $LASTEXITCODE); get it from https://www.docker.com/products/docker-desktop/" }
        return
    }
    Say 'get Docker Desktop from https://www.docker.com/products/docker-desktop/, start it once, then restart the app'
}

function Make-Shortcut([string]$Graphme) {
    if ($env:GRAPHME_NO_SHORTCUT) { return }
    $link = (New-Object -ComObject WScript.Shell).CreateShortcut($Shortcut)
    # The window app, which has no console. A shortcut cannot set GRAPHME_HOME: another home
    # is its argument.
    $link.TargetPath = $Graphme
    if ($AppHome -ne $DefaultHome) { $link.Arguments = "`"$AppHome`"" }
    $link.WorkingDirectory = $AppHome
    $link.Save()
    Say "shortcut: Start menu > Graph-Me-Maybe"
}

function Register-Uninstall([string]$Tag) {
    $script = Join-Path $AppHome 'install.ps1'
    if ($PSCommandPath) { Copy-Item -LiteralPath $PSCommandPath $script -Force }
    else { Invoke-WebRequest -UseBasicParsing $Raw -OutFile $script }
    New-Item -Path $UninstallKey -Force | Out-Null
    $home_ = $AppHome.Replace("'", "''")
    $script_ = $script.Replace("'", "''")
    $command = "powershell -NoProfile -ExecutionPolicy Bypass -Command `"`$env:GRAPHME_HOME='$home_'; `$env:GRAPHME_UNINSTALL='1'; & '$script_'`""
    Set-ItemProperty $UninstallKey DisplayName 'Graph-Me-Maybe'
    Set-ItemProperty $UninstallKey DisplayVersion $Tag.TrimStart('v')
    Set-ItemProperty $UninstallKey Publisher 'Graph-Me-Maybe'
    Set-ItemProperty $UninstallKey UninstallString $command
    Set-ItemProperty $UninstallKey NoModify 1 -Type DWord
    Set-ItemProperty $UninstallKey NoRepair 1 -Type DWord
}

function Uninstall {
    if ($env:GRAPHME_PURGE) {
        if (-not ([IO.Path]::IsPathRooted($AppHome) -and $AppHome -match '^([A-Za-z]:[\\/]|\\\\)')) {
            Fail "GRAPHME_PURGE: GRAPHME_HOME must be an absolute path, not '$AppHome'"
        }
        $full = [IO.Path]::GetFullPath($AppHome)
        $root = [IO.Path]::GetPathRoot($full).TrimEnd('\', '/')
        $full = $full.TrimEnd('\', '/')
        # Neither a drive nor the profile, LOCALAPPDATA or APPDATA, nor any folder holding one.
        $kept = @($env:USERPROFILE, $env:LOCALAPPDATA, $env:APPDATA) | Where-Object { $_ } |
                ForEach-Object { [IO.Path]::GetFullPath($_).TrimEnd('\', '/') }
        $holds = $kept | Where-Object {
            $_ -eq $full -or $_.StartsWith($full + '\', 'OrdinalIgnoreCase') -or $_.StartsWith($full + '/', 'OrdinalIgnoreCase')
        }
        if ($full -eq $root -or $holds) {
            Fail "GRAPHME_PURGE: refusing to delete $AppHome (set GRAPHME_HOME to the app's own folder)"
        }
    }
    Ensure-Uv
    Quiet { uv tool uninstall $Tool }
    $bin = (uv tool dir --bin).Trim()
    if ((Test-Path -LiteralPath (Join-Path $bin 'graphme.exe')) -or (Test-Path -LiteralPath (Join-Path $bin 'graphme-app.exe'))) {
        Fail 'close Graph-Me-Maybe first (it is still running), then uninstall again'
    }
    foreach ($path in @((Join-Path $AppHome 'web'), (Join-Path $AppHome 'viewer'), $Shortcut)) {
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }
    }
    if (Test-Path $UninstallKey) { Remove-Item $UninstallKey -Force }
    if ($env:GRAPHME_PURGE -and (Test-Path -LiteralPath $AppHome)) {
        # Only what the app made; the folder goes only if nothing else is left in it.
        foreach ($part in @('.env', '.graphme', 'app.log', 'install.ps1', 'webview')) {
            $path = Join-Path $AppHome $part
            if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }
        }
        if (@((Get-Item -LiteralPath $AppHome -Force).GetFileSystemInfos()).Count -eq 0) {
            Remove-Item -LiteralPath $AppHome -Force
            Say "removed $AppHome"
        } else { Say "removed the app's files; kept $AppHome, which holds files it did not make" }
    }
    Say 'removed. The graph database stays in Docker: docker rm -f graphme-neo4j; docker volume rm graphme-neo4j-data graphme-neo4j-logs'
}

function Install {
    Ensure-Uv
    $tag = Get-Tag
    $source = if ($env:GRAPHME_INSTALL_SOURCE) { $env:GRAPHME_INSTALL_SOURCE } else { "https://github.com/$Repo/releases/download/$tag" }
    $work = Join-Path ([IO.Path]::GetTempPath()) ("graph-me-maybe-" + [Guid]::NewGuid())
    New-Item -ItemType Directory $work | Out-Null
    try {
        Fetch $source 'SHA256SUMS' $work
        $wheel = (Get-Content (Join-Path $work 'SHA256SUMS') | ForEach-Object { ($_ -split '\s+')[1] } |
                  Where-Object { $_ -like 'graph_me_maybe-*.whl' } | Select-Object -First 1)
        if (-not $wheel) { Fail 'no graph_me_maybe-*.whl in SHA256SUMS' }
        foreach ($name in @($wheel, 'web.zip', 'viewer.zip')) { Fetch $source $name $work; Verify $name $work }
        # A Graph-Me-Maybe already answering holds its files open; updating under it fails.
        $port = if ($env:GRAPHME_PORT) { $env:GRAPHME_PORT } else { '8765' }
        try { $health = Invoke-RestMethod -UseBasicParsing -TimeoutSec 2 "http://127.0.0.1:$port/health" } catch { $health = $null }
        # One that names another home -- WSL's app, reached by its localhost forwarding -- is not this one.
        if ($health -and $health.status -eq 'ok' -and (-not $health.home -or $health.home -eq $AppHome)) {
            Fail 'Graph-Me-Maybe is running -- quit it first (close its window, or end graphme-app.exe in Task Manager), then run the installer again'
        }

        Say "installing Graph-Me-Maybe $tag"
        uv tool install --force --python 3.11 (Join-Path $work $wheel)
        if ($LASTEXITCODE -ne 0) { Fail 'uv tool install failed (close Graph-Me-Maybe if it is running, then run the installer again)' }
        if (-not $env:UV_NO_MODIFY_PATH) { Quiet { uv tool update-shell } }
        $bin = (uv tool dir --bin).Trim()
        $python = Join-Path (uv tool dir).Trim() "$Tool\Scripts\python.exe"
        $graphme = Join-Path $bin 'graphme-app.exe'

        New-Item -ItemType Directory -Force $AppHome | Out-Null
        foreach ($part in @('web', 'viewer')) {
            $into = Join-Path $AppHome $part
            if (Test-Path -LiteralPath $into) { Remove-Item -LiteralPath $into -Recurse -Force }
            Expand-Archive -LiteralPath (Join-Path $work "$part.zip") -DestinationPath $into
        }
    } finally {
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (-not (Has-Chrome)) {
        Say "no Google Chrome: fetching Playwright's Chromium to draw pages with"
        & $python -m playwright install chromium --only-shell
        if ($LASTEXITCODE -ne 0) { Say "warning: Playwright's Chromium did not download; install Google Chrome, or run: & '$python' -m playwright install chromium --only-shell" }
    }
    Offer-Docker
    Make-Shortcut $graphme
    Register-Uninstall $tag
    $open = if ($env:GRAPHME_HOME) { "`$env:GRAPHME_HOME='$($AppHome.Replace("'", "''"))'; graphme app" } else { 'graphme app' }
    Say "installed. Open it from the Start menu, or: $open   (a new terminal finds the command)"
    if (-not $env:GRAPHME_NO_LAUNCH) {
        if ($AppHome -eq $DefaultHome) { Start-Process -FilePath $graphme -WorkingDirectory $AppHome }
        else { Start-Process -FilePath $graphme -ArgumentList "`"$AppHome`"" -WorkingDirectory $AppHome }
        Say 'starting -- the app opens in a moment'
    }
}

if ($env:GRAPHME_UNINSTALL) { Uninstall } else { Install }
