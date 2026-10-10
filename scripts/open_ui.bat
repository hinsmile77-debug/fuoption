@echo off
REM Open the MESSIAH Command Center in the default browser - by hand, when you want to look.
REM
REM WHY THIS EXISTS (2026-10-10)
REM   The auto-start (Task Scheduler "Messiah" -> run_l1_daily.bat) now launches Streamlit with
REM   --server.headless true, so the UI SERVER runs in the background but no browser window pops
REM   up - not at 08:20, and not on every watchdog restart either. Collection, G2 and the
REM   headless status board do not depend on a browser being attached.
REM
REM   The UI may sit on a fallback port (8512-8514) if 8511 was taken by someone else
REM   (core/ui_launcher.py, 2026-08-11 F-6). The port actually used is recorded in
REM   logs\command_center_ui.json, so read it from there instead of assuming 8511.
REM
REM NOTE: keep this file ASCII-only (see run_l1_daily.bat header).

setlocal
cd /d "%~dp0.."

set PORT=8511
for /f %%p in ('powershell -NoProfile -Command "try { (Get-Content 'logs\command_center_ui.json' -Raw | ConvertFrom-Json).port } catch { }"') do set PORT=%%p

powershell -NoProfile -Command "try { $c = New-Object Net.Sockets.TcpClient; $c.Connect('localhost', %PORT%); $c.Close(); exit 0 } catch { exit 1 }"
if not %ERRORLEVEL%==0 (
    echo [open_ui.bat] port %PORT% is not responding - the UI server is not running.
    echo [open_ui.bat] It is started by run_l1_daily.bat on trading days; check logs\ui_*.log.
    endlocal & exit /b 1
)

echo [open_ui.bat] opening http://localhost:%PORT%
start "" "http://localhost:%PORT%"
endlocal & exit /b 0
