#Requires -Version 5.1
<#
  PROJECT APEX launcher for Windows.

  1. Checks that Docker Desktop is installed and its engine is running (starts it if needed).
  2. Creates a local .env on first run (random secret key, simulated market data).
  3. Starts PostgreSQL, Redis, the API, the worker and the web dashboard with Docker Compose.
  4. Waits until the API and dashboard answer, then opens http://localhost:3000.

  Nothing here places, modifies or cancels orders. PROJECT APEX is analysis-only.
#>
# 'Continue': Windows PowerShell 5.1 would turn Docker's normal progress output (stderr) into fatal
# errors under 'Stop'. Failures are checked explicitly via $LASTEXITCODE and try/catch instead.
$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Dashboard = 'http://localhost:3000'
$ApiHealth = 'http://localhost:8000/api/health/live'
$Log = Join-Path $PSScriptRoot 'apex-launcher.log'

function Say([string]$Message, [string]$Color = 'Gray') {
    Write-Host $Message -ForegroundColor $Color
    Add-Content -Path $Log -Value ("{0}  {1}" -f (Get-Date -Format s), $Message)
}

function Fail([string]$Message) {
    Say ''
    Say "PROBLEM: $Message" 'Red'
    Say "Details are in $Log" 'DarkGray'
    Read-Host 'Press Enter to close this window'
    exit 1
}

function Test-Url([string]$Url) {
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 4 -MaximumRedirection 0 -ErrorAction Stop
        return ($r.StatusCode -ge 200 -and $r.StatusCode -lt 400)
    } catch {
        $resp = $_.Exception.Response
        if ($resp -and [int]$resp.StatusCode -ge 300 -and [int]$resp.StatusCode -lt 400) { return $true }
        return $false
    }
}

function Wait-Url([string]$Url, [string]$Name, [int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    Write-Host -NoNewline "  Waiting for $Name "
    while ((Get-Date) -lt $deadline) {
        if (Test-Url $Url) { Write-Host ' ready' -ForegroundColor Green; return $true }
        Write-Host -NoNewline '.'
        Start-Sleep -Seconds 3
    }
    Write-Host ' timed out' -ForegroundColor Red
    return $false
}

function Test-PortBusy([int]$Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        return ($async.AsyncWaitHandle.WaitOne(300) -and $client.Connected)
    } catch { return $false } finally { $client.Close() }
}

function New-SecretKey {
    $bytes = New-Object byte[] 48
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return ([Convert]::ToBase64String($bytes)).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

Clear-Host
Say '==============================================' 'Cyan'
Say '  PROJECT APEX - analysis-only trading terminal' 'Cyan'
Say '==============================================' 'Cyan'
Say "Folder: $Root" 'DarkGray'

# --- 1. Docker Desktop -------------------------------------------------------------------------
Say ''
Say '[1/5] Checking Docker Desktop...'
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Fail ("Docker Desktop is not installed. Install it from https://www.docker.com/products/docker-desktop/ " +
          "(keep the 'Use WSL 2' option), restart Windows, then run this launcher again.")
}
& docker compose version *> $null
if ($LASTEXITCODE -ne 0) { Fail 'Docker Compose v2 is missing. Update Docker Desktop to a current version.' }

& docker info *> $null
if ($LASTEXITCODE -ne 0) {
    $desktop = Join-Path $Env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
    if (Test-Path $desktop) {
        Say '  Docker engine is not running - starting Docker Desktop (this can take a minute)...' 'Yellow'
        Start-Process -FilePath $desktop | Out-Null
    } else {
        Fail 'Docker engine is not running. Start Docker Desktop from the Start menu, then run this launcher again.'
    }
    $deadline = (Get-Date).AddSeconds(240)
    do {
        Start-Sleep -Seconds 4
        & docker info *> $null
    } while ($LASTEXITCODE -ne 0 -and (Get-Date) -lt $deadline)
    if ($LASTEXITCODE -ne 0) { Fail 'Docker Desktop did not start within 4 minutes. Open it manually, wait for "Engine running", then retry.' }
}
Say '  Docker engine is running.' 'Green'

# --- 2. Local configuration (.env) -------------------------------------------------------------
Say ''
Say '[2/5] Checking configuration...'
$envFile = Join-Path $Root '.env'
if (-not (Test-Path $envFile)) {
    $key = New-SecretKey
    $lines = @(
        '# PROJECT APEX local settings (created by windows\start-apex.ps1). Never share this file.',
        "APEX_SECRET_KEY=$key",
        '# simulated = clearly labelled synthetic prices. For real CME data set databento and add the key below.',
        'APEX_MARKET_DATA_PROVIDER=simulated',
        'APEX_DATABENTO_API_KEY=',
        '# Optional: Claude API key for the AI copilot (server-side only).',
        'APEX_ANTHROPIC_API_KEY=',
        '# Host port for the bundled PostgreSQL (avoids clashing with a PostgreSQL already installed on 5432).',
        'APEX_PG_HOST_PORT=55432'
    )
    # UTF-8 without BOM so Docker Compose reads the first key correctly.
    [System.IO.File]::WriteAllLines($envFile, $lines, (New-Object System.Text.UTF8Encoding($false)))
    Say '  Created .env with a new random secret key and simulated market data.' 'Green'
} else {
    $content = Get-Content $envFile -Raw
    if ($content -notmatch '(?m)^APEX_SECRET_KEY=\S{16,}' -or $content -match 'change-me-to-a-long-random-value') {
        Fail ".env exists but APEX_SECRET_KEY is missing or still the example value. Edit $envFile (or delete it to regenerate)."
    }
    Say '  Using existing .env.' 'Green'
}

# --- 3. Ports ----------------------------------------------------------------------------------
Say ''
Say '[3/5] Checking ports 3000 and 8000...'
$running = (& docker compose ps --status running --services 2>$null) -join ' '
foreach ($p in @(@{Port = 3000; Svc = 'web'}, @{Port = 8000; Svc = 'api'})) {
    if ((Test-PortBusy $p.Port) -and ($running -notmatch "\b$($p.Svc)\b")) {
        Fail ("Port $($p.Port) is already used by another program. Close it (for example another dev server) " +
              "and run the launcher again.")
    }
}
Say '  Ports are available.' 'Green'

# --- 4. Start the stack ------------------------------------------------------------------------
Say ''
Say '[4/5] Starting database, cache, API, worker and dashboard...'
Say '  The first start downloads and builds everything: allow 5-15 minutes. Later starts take seconds.' 'Yellow'
& docker compose up -d --build 2>&1 | ForEach-Object { Add-Content -Path $Log -Value $_; Write-Host "  $_" -ForegroundColor DarkGray }
if ($LASTEXITCODE -ne 0) { Fail 'docker compose up failed. See the log for the build error.' }

if (-not (Wait-Url $ApiHealth 'API' 300)) { & docker compose logs --tail 60 api 2>&1 | Add-Content -Path $Log; Fail 'The API did not become healthy.' }
if (-not (Wait-Url "$Dashboard/login" 'dashboard' 300)) { & docker compose logs --tail 60 web 2>&1 | Add-Content -Path $Log; Fail 'The dashboard did not respond.' }

Say ''
Say '  Service status:'
$status = & docker compose ps --format '{{.Service}}|{{.State}}|{{.Status}}' 2>$null
foreach ($line in $status) {
    $f = $line -split '\|'
    $color = 'Green'
    if ($f[1] -ne 'running') { $color = 'Red' }
    Say ("    {0,-9} {1}" -f $f[0], $f[2]) $color
}
foreach ($svc in @('postgres', 'redis', 'api', 'worker', 'web')) {
    if (($status -join ' ') -notmatch "\b$svc\|running") { Fail "Service '$svc' is not running. See: docker compose logs $svc" }
}

# --- 5. Open the dashboard ---------------------------------------------------------------------
Say ''
Say '[5/5] Opening the dashboard...'
Start-Process $Dashboard
Say ''
Say "PROJECT APEX is running at $Dashboard" 'Cyan'
Say '  First visit: create your administrator account on the sign-in page.' 'Gray'
Say '  Prices are SIMULATED unless you configure a licensed data provider in .env.' 'Yellow'
Say '  The services keep running after you close this window. Use "Stop PROJECT APEX" to stop them.' 'Gray'
Start-Sleep -Seconds 8
