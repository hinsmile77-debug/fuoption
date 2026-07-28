# MESSIAH one-command install/deploy script.
# Master Plan Ver 1.1 SS7.3 item 4: "Install is one command: `docker compose up -d` or a single
# `install.ps1` script that starts Redis, installs dependencies, validates config, runs a
# replay smoke test, and reaches a running state."
#
# English-only comments/output by design (Ver 2.0 SS9 W37~38 release packaging) -- a prior
# incident (dev_memory/NEXT_TODO.md, 2026-07-24) showed that Korean text in a Windows batch
# file gets mis-decoded under the system codepage (cp949) and breaks the script outright.
# That was specifically about cmd.exe/.bat, but this file plays it safe the same way.
#
# Usage: powershell -ExecutionPolicy Bypass -File install.ps1 [-ConfigsDir configs] [-SkipSmoke]

param(
    [string]$ConfigsDir = "configs",
    [switch]$SkipSmoke
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RepoRoot

function Write-Step($msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}

function Fail($msg) {
    Write-Host "INSTALL FAILED: $msg" -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------- 1) Docker + Redis

Write-Step "Checking Docker availability"
$docker = Get-Command docker -ErrorAction SilentlyContinue
if (-not $docker) {
    Fail "docker not found on PATH -- install Docker Desktop first (Ver 1.1 SS7.3)"
}

Write-Step "Starting Redis via docker compose (messiah-redis, port 6380)"
docker compose up -d
if ($LASTEXITCODE -ne 0) {
    Fail "docker compose up -d failed"
}

Write-Step "Waiting for Redis healthcheck"
$healthy = $false
for ($i = 0; $i -lt 20; $i++) {
    $status = docker inspect --format "{{.State.Health.Status}}" messiah-redis 2>$null
    if ($status -eq "healthy") {
        $healthy = $true
        break
    }
    Start-Sleep -Seconds 2
}
if (-not $healthy) {
    Fail "Redis did not become healthy within ~40s -- check 'docker logs messiah-redis'"
}
Write-Host "Redis is healthy." -ForegroundColor Green

# ---------------------------------------------------------------- 2) Python venv + deps

Write-Step "Creating virtual environment (.venv) if missing"
if (-not (Test-Path ".venv")) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        Fail "python -m venv .venv failed -- is Python 3.11+ on PATH?"
    }
}

Write-Step "Installing dependencies (base + ml + ui + dev extras)"
& .venv\Scripts\python.exe -m pip install --upgrade pip
& .venv\Scripts\python.exe -m pip install -e ".[ml,ui,dev]"
if ($LASTEXITCODE -ne 0) {
    Fail "pip install failed"
}

# ---------------------------------------------------------------- 3) Config validation

Write-Step "Validating configuration (self_check.py, without Redis first)"
& .venv\Scripts\python.exe scripts\self_check.py --configs $ConfigsDir --skip-redis
if ($LASTEXITCODE -ne 0) {
    Fail "self_check.py failed on config-only checks -- fix configs/instance.yaml and .env first"
}

Write-Step "Validating configuration (self_check.py, with Redis)"
& .venv\Scripts\python.exe scripts\self_check.py --configs $ConfigsDir
if ($LASTEXITCODE -ne 0) {
    Fail "self_check.py failed with Redis connected"
}

# ---------------------------------------------------------------- 4) Replay smoke test

if ($SkipSmoke) {
    Write-Step "Skipping replay smoke test (-SkipSmoke passed)"
}
else {
    Write-Step "Running replay smoke test (scripts/run_replay.py)"
    & .venv\Scripts\python.exe scripts\run_replay.py
    if ($LASTEXITCODE -ne 0) {
        Fail "replay smoke test failed -- do not deploy this build (Ver 1.1 SS7.3)"
    }
}

Write-Host ""
Write-Host "INSTALL COMPLETE -- Redis up, dependencies installed, config valid, smoke test passed." -ForegroundColor Green
Write-Host "Next: review configs/instance.yaml (mode/capital/universe) before running scripts\run_l1_daily.py or scripts\run_g2_paper_trading.py." -ForegroundColor Green
