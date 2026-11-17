#Requires -Version 5.1
# Creates "PROJECT APEX" and "Stop PROJECT APEX" shortcuts on the current user's desktop.
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$root = Split-Path -Parent $here
$desktop = [Environment]::GetFolderPath('Desktop')
$shell = New-Object -ComObject WScript.Shell

function New-Shortcut([string]$Name, [string]$Target, [string]$Icon, [string]$Description) {
    $lnk = $shell.CreateShortcut((Join-Path $desktop "$Name.lnk"))
    $lnk.TargetPath = $Target
    $lnk.WorkingDirectory = $root
    $lnk.IconLocation = $Icon
    $lnk.Description = $Description
    $lnk.Save()
    Write-Host "  Created: $(Join-Path $desktop "$Name.lnk")" -ForegroundColor Green
}

$icon = Join-Path $here 'apex.ico'
if (-not (Test-Path $icon)) { $icon = "$Env:SystemRoot\System32\imageres.dll,1" }
New-Shortcut 'PROJECT APEX' (Join-Path $here 'Start PROJECT APEX.bat') $icon 'Start PROJECT APEX and open the dashboard'
New-Shortcut 'Stop PROJECT APEX' (Join-Path $here 'Stop PROJECT APEX.bat') "$Env:SystemRoot\System32\imageres.dll,93" 'Stop PROJECT APEX'
Write-Host ''
Write-Host 'Done. Double-click "PROJECT APEX" on your desktop to start it.' -ForegroundColor Cyan
Start-Sleep -Seconds 5
