@echo off
REM G2 paper trading daily entrypoint - wraps scripts\run_g2_paper_trading.py.
REM Registered in Task Scheduler as "Messiah-G2", shortly after "Messiah"/run_l1_daily.bat -
REM see run_g2_paper_trading.py module docstring: this script has no TickCollector of its own,
REM it only subscribes to the bar.*/feat.* that run_l1_daily.py already publishes to the shared
REM bus, so the gap is just to avoid both scripts' startup work (self_check, Docker bootstrap,
REM symbol master download) landing in the exact same instant - it is not required for
REM correctness.
REM
REM DO NOT write the trigger time here - configs\scheduled_tasks.json is the source of truth.
REM See the same note in run_l1_daily.bat for what a stale time in this comment cost on
REM 2026-08-10.
REM
REM NOTE: keep this file ASCII-only. cmd.exe interprets .bat files using the system ANSI
REM codepage (CP949 on Korean Windows), not UTF-8 - a UTF-8-saved file with non-ASCII comments
REM gets its multi-byte sequences misparsed as bogus commands (confirmed by hand on
REM run_l1_daily.bat, 2026-07-24 - same class of bug applies here).

setlocal
cd /d "%~dp0.."

REM Switch this console to UTF-8 so the script's Korean log lines render correctly live,
REM not just when the log file is opened later in an editor.
chcp 65001 >nul

REM %date% format is locale-dependent - use PowerShell for a safe, unambiguous yyyyMMdd.
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set TODAY=%%i

set LOGDIR=logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set LOGFILE=%LOGDIR%\g2_daily_%TODAY%.log

if not exist ".venv\Scripts\python.exe" (
    echo [run_g2_paper_trading.bat] .venv not found - run "uv sync" first >&2
    exit /b 1
)

REM cmd.exe has no built-in tee, so route through PowerShell to show output live in this
REM console AND append it to the log file (same pattern as run_l1_daily.bat).
REM
REM The stderr merge (2>&1) is done by cmd /c, NOT by PowerShell - see run_l1_daily.bat for
REM the full reasoning (PS 5.1 wraps a native command's first stderr line in a
REM NativeCommandError block, which mangled the crash_forensics arming marker every day and
REM produced a false "recurred" verdict on 2026-08-04).
powershell -NoProfile -Command ^
    "& cmd /c '.venv\Scripts\python.exe -u scripts\run_g2_paper_trading.py 2>&1' | ForEach-Object { $_ | Out-File -FilePath '%LOGFILE%' -Append -Encoding utf8; $_ }; exit $LASTEXITCODE"
set EXITCODE=%ERRORLEVEL%

REM Record the exit code IN THE LOG (2026-08-10 A-2) - see the same line in run_l1_daily.bat.
REM This entrypoint is the one that made the axis necessary: on 2026-08-10 it logged a clean
REM SessionEnd at 15:35:00 and the scheduler recorded return code 2147942655 (= Win32 255)
REM two seconds later. Nothing in the daily report read that number.
echo [exit] run_g2_paper_trading.py code=%EXITCODE%>>"%LOGFILE%"

if not %EXITCODE%==0 (
    echo [run_g2_paper_trading.bat] exit code %EXITCODE% - check %LOGFILE% >&2
)

endlocal & exit /b %EXITCODE%
