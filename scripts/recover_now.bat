@echo off
REM MESSIAH morning recovery - one command instead of ad-hoc keystrokes (2026-08-11 G-5).
REM
REM WHY THIS EXISTS
REM   2026-08-10: the collector did not come up on time. At 08:50:09 a human tried to restart
REM   it by hand; that attempt died 18 seconds later and left NO trace in the app log, because
REM   the process was killed before it wrote its first line. Only the Windows event log knew.
REM   38 minutes of ticks / investor flow / option chain were lost - none of them backfillable.
REM   The recovery itself is still a human decision (see below), but it should not also be an
REM   improvised sequence of commands typed under time pressure.
REM
REM WHY THIS IS NOT AUTOMATIC
REM   Two WebSocket connections on the same KIS account cut each other off (measured
REM   2026-07-29, see src/messiah/data/collector.py module docstring). A watchdog that
REM   relaunches the collector cannot tell "the old process is dead" from "the old process is
REM   alive but silent" - it IS the process being asked about. So the alert is automatic
REM   (CollectorFirstTickOverdue + l1.collector CRITICAL) and the action is human.
REM   This file makes the action one command; it does not make it automatic.
REM
REM WHAT IT DOES
REM   1. Refuses outside the launch window (08:15-15:35) - the same window run_l1_daily.py
REM      enforces. Outside it, restarting achieves nothing and can only cause confusion.
REM   2. Stops any surviving MESSIAH collector / G2 processes, matched BY COMMAND LINE, not
REM      window title (same lesson as stop_l1_daily.bat: a title-only match silently missed a
REM      hand-started process on the mahdi project, 2026-07-21).
REM      The Command Center UI is deliberately NOT killed - it is the thing you are watching.
REM   3. Relaunches run_l1_daily.bat and run_g2_paper_trading.bat, each in its own window.
REM
REM   Step 2 must complete before step 3: relaunching while the old collector still holds the
REM   WebSocket is exactly the double-connection failure above.
REM
REM NOTE: keep this file ASCII-only. cmd.exe reads .bat using the system ANSI codepage
REM (CP949 on Korean Windows), so non-ASCII bytes get misparsed as commands (confirmed by
REM hand on run_l1_daily.bat, 2026-07-24).

setlocal
chcp 65001 >nul

for %%I in ("%~dp0..") do set "PROJECT_DIR=%%~fI"
set "LOG_FILE=%PROJECT_DIR%\logs\recover_now.log"
if not exist "%PROJECT_DIR%\logs" mkdir "%PROJECT_DIR%\logs"

echo [%date% %time%] ===== MESSIAH recover_now start ===== >> "%LOG_FILE%"

REM Launch-window gate. configs\scheduled_tasks.json is the source of truth for the trigger
REM times; 08:15 is those minus launch_window_margin_minutes, and 15:35 is REGULAR_SESSION_STOP.
REM Hardcoded here rather than parsed because this script must work when Python cannot start -
REM that is one of the failure modes it exists for. Override with MESSIAH_FORCE_RECOVER=1.
powershell -NoProfile -Command "$now = Get-Date; $start = Get-Date -Hour 8 -Minute 15 -Second 0; $stop = Get-Date -Hour 15 -Minute 35 -Second 0; if ($env:MESSIAH_FORCE_RECOVER -eq '1') { Write-Output 'forced: MESSIAH_FORCE_RECOVER=1'; exit 0 }; if ($now -lt $start -or $now -gt $stop) { Write-Output ('skip: outside launch window 08:15-15:35 (now {0}) - nothing done. Set MESSIAH_FORCE_RECOVER=1 to override.' -f $now.ToString('HH:mm:ss')); exit 1 }; Write-Output ('inside launch window (now {0})' -f $now.ToString('HH:mm:ss')); exit 0" >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    type "%LOG_FILE%" | more +0
    echo [recover_now.bat] outside launch window - nothing done ^(see %LOG_FILE%^) >&2
    endlocal & exit /b 1
)

REM Stop survivors. Stop-Process -Id $p.ProcessId (not piping CIM objects) is deliberate:
REM Win32_Process exposes "ProcessId", not "Id", so a straight pipe binds nothing and kills
REM nothing - silently (confirmed by hand 2026-07-24, see stop_l1_daily.bat).
powershell -NoProfile -Command "$procs = Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and ($_.CommandLine -like '*run_l1_daily.py*' -or $_.CommandLine -like '*run_g2_paper_trading.py*') }; if ($procs) { foreach ($p in $procs) { Write-Output ('stopping: PID {0}' -f $p.ProcessId); Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue } ; Start-Sleep -Seconds 3 } else { Write-Output 'no surviving collector/G2 process' }" >> "%LOG_FILE%" 2>&1

REM Relaunch. Separate windows so each keeps its own live console output, same as the
REM scheduler does.
start "MESSIAH L1" /D "%PROJECT_DIR%" "%PROJECT_DIR%\scripts\run_l1_daily.bat"
start "MESSIAH G2" /D "%PROJECT_DIR%" "%PROJECT_DIR%\scripts\run_g2_paper_trading.bat"

echo [%date% %time%] relaunched run_l1_daily.bat + run_g2_paper_trading.bat >> "%LOG_FILE%"
echo [recover_now.bat] relaunched - watch logs\l1_daily_YYYYMMDD.log for CollectorFirstTick
echo [%date% %time%] ===== MESSIAH recover_now done ===== >> "%LOG_FILE%"

endlocal & exit /b 0
