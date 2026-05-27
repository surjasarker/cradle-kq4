#!/usr/bin/env pwsh
# Launch Simon Says game and AI agent on Windows PowerShell

$ErrorActionPreference = "Stop"

# Configuration
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = "python"

Write-Host "================================"
Write-Host "Simon Says - AI Agent Launcher"
Write-Host "================================"
Write-Host ""

# Check if Game.py exists
$GamePath = Join-Path $ProjectDir "Game.py"
if (-not (Test-Path $GamePath)) {
    Write-Error "ERROR: Game.py not found at $GamePath"
    exit 1
}

Write-Host "Starting Simon Says game..."
Write-Host "Make sure conda environment is activated: conda activate cradle"
Write-Host ""

# Launch Simon Says in the background
$GameProcess = Start-Process `
    -FilePath $Python `
    -ArgumentList $GamePath `
    -PassThru



Write-Host "Game launched (PID: $($GameProcess.Id))"
Write-Host "Waiting 3 seconds for game window to appear..."
Start-Sleep -Seconds 3

# Launch agent
Write-Host ""
Write-Host "Starting AI agent..."
Push-Location $ProjectDir
try {
    & $Python runner.py `
        --llmProviderConfig "./conf/qwen_config.json" `
        --embedProviderConfig "./conf/qwen_config.json" `
        --envConfig "./conf/env_config_simon_says.json"
} finally {
    Pop-Location
}

# Cleanup
Write-Host ""
Write-Host "Cleaning up..."
try {
    $GameProcess | Stop-Process -Force -ErrorAction SilentlyContinue
}
catch {
    # Process already stopped
}

Write-Host "Done!"
