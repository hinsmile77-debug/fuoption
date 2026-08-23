@echo off
REM MESSIAH pre-market clock resync - wraps "w32tm /resync".
REM Registered in Task Scheduler as "Messiah-ClockResync" (weekday 08:10 KST trigger,
REM see scripts\install_scheduled_tasks.ps1 and configs\scheduled_tasks.json).
REM
REM Why this exists (2026-08-23): the system clock reached +8.638s while w32time reported
REM "Running". Three causes stacked (see dev_memory\DECISION_LOG.md, 2026-08-23):
REM   1. The oscillator runs at +-40ppm and flips sign across reboots (TSC calibration on
REM      a Hyper-V root partition - WSL2/Docker force the hypervisor on).
REM   2. The RTC loses ~44ppm while the PC is off, so a weekend alone costs ~8s.
REM   3. SpecialPollInterval was 32768s (9.1h). Source selection needs two polls, so the
REM      first sync landed at boot+9.1h - after the market had already closed.
REM
REM Poll interval is now 1024s, which bounds inter-poll drift to ~41ms. This task closes
REM the remaining gap: the ~20 minutes after a cold boot or a hibernate resume, when the
REM service has not yet selected a source. 08:10 is five minutes before the launch window
REM (08:15) so the correction lands before self_check reads the offset.
REM
REM MUST RUN ELEVATED. "w32tm /resync" returns 0x80070005 (access denied) for a limited
REM user - measured 2026-08-23. install_scheduled_tasks.ps1 registers this one task with a
REM SYSTEM principal because of that ("run_as_system": true in the canonical JSON).
REM
REM Failure never blocks the trading day: self_check.check_clock is the gate, and it reads
REM the actual offset rather than trusting this task. This only makes the gate likelier to
REM pass honestly.
REM
REM NOTE: keep this file ASCII-only. cmd.exe interprets .bat files using the system ANSI
REM codepage (CP949 on Korean Windows), not UTF-8 - same constraint as run_l1_daily.bat.

setlocal
cd /d "%~dp0.."

REM No chcp here. Measured 2026-08-23: w32tm emits its localized strings in the system
REM ANSI codepage (CP949) whatever the console codepage is, so setting 65001 only made the
REM comment look responsible without changing a byte. The numbers we actually care about -
REM stripchart samples like "08:10:00, +00.0061s" - are ASCII either way, and nothing
REM parses this log; it is for a human with a terminal.

if not exist "logs" mkdir "logs"
set LOG_FILE=logs\clock_resync.log

echo. >> "%LOG_FILE%"
powershell -NoProfile -Command "'[{0}] ===== MESSIAH clock resync start =====' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss.ff')" >> "%LOG_FILE%" 2>&1

REM Record the offset BEFORE correcting. Without this line the log cannot answer
REM "how far had it drifted since yesterday" - only "we ran a resync", which is the
REM difference between a measurement and a receipt.
w32tm /stripchart /computer:time.windows.com /samples:1 /period:1 /dataonly >> "%LOG_FILE%" 2>&1

w32tm /resync >> "%LOG_FILE%" 2>&1
set RESYNC_RC=%ERRORLEVEL%

w32tm /stripchart /computer:time.windows.com /samples:1 /period:1 /dataonly >> "%LOG_FILE%" 2>&1

powershell -NoProfile -Command "'[{0}] ===== MESSIAH clock resync done (rc=%RESYNC_RC%) =====' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss.ff')" >> "%LOG_FILE%" 2>&1

endlocal & exit /b %RESYNC_RC%
