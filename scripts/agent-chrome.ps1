# Starts a separate Chrome just for agents (Browser Harness / browser-use).
#
# - Own profile folder: none of your everyday logins, cookies or extensions.
# - Remote debugging on 127.0.0.1:9222 only (not reachable from the network).
# - Opens the POC app. Sign in to test accounts only in this window.
#
# Usage (PowerShell):   .\scripts\agent-chrome.ps1
# Then, for the agent:  $env:BU_CDP_URL = "http://127.0.0.1:9222"
# Delete the profile:   Remove-Item -Recurse "$env:LOCALAPPDATA\AgentChrome"

param(
    [int]$Port = 9222,
    [string]$ProfileDir = "$env:LOCALAPPDATA\AgentChrome",
    [string]$StartUrl = "http://127.0.0.1:5173"
)

$chrome = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $chrome) { Write-Error "Chrome not found. Install Google Chrome first."; exit 1 }

if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "Port $Port is already in use - the agent Chrome is probably already running."
    exit 0
}

New-Item -ItemType Directory -Force -Path $ProfileDir | Out-Null

Start-Process -FilePath $chrome -ArgumentList @(
    "--user-data-dir=`"$ProfileDir`"",
    "--remote-debugging-port=$Port",
    "--remote-debugging-address=127.0.0.1",
    "--no-first-run",
    "--no-default-browser-check",
    $StartUrl
)

Start-Sleep -Seconds 2
try {
    $v = Invoke-RestMethod "http://127.0.0.1:$Port/json/version" -TimeoutSec 5
    Write-Host "Agent Chrome running: $($v.Browser)"
    Write-Host "Profile: $ProfileDir"
    Write-Host "Point the agent at it:  `$env:BU_CDP_URL = 'http://127.0.0.1:$Port'"
} catch {
    Write-Warning "Chrome started but http://127.0.0.1:$Port did not answer yet. Wait a few seconds and retry."
}
