#Requires -Version 5.1
# Stops PROJECT APEX containers. Data (database, settings, journal) is kept in Docker volumes.
# 'Continue': Windows PowerShell 5.1 would turn Docker's normal progress output (stderr) into fatal
# errors under 'Stop'. Failures are checked explicitly via $LASTEXITCODE and try/catch instead.
$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Write-Host 'Docker Desktop is not installed.' -ForegroundColor Red; Read-Host 'Press Enter'; exit 1 }
& docker info *> $null
if ($LASTEXITCODE -ne 0) { Write-Host 'Docker engine is not running, so PROJECT APEX is already stopped.' -ForegroundColor Yellow; Start-Sleep 4; exit 0 }
Write-Host 'Stopping PROJECT APEX...' -ForegroundColor Cyan
& docker compose stop
if ($LASTEXITCODE -eq 0) { Write-Host 'Stopped. Your data is kept; start again with the desktop shortcut.' -ForegroundColor Green }
Start-Sleep -Seconds 4
