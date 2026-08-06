@echo off
REM MESSIAH post-market procedure entrypoint - wraps scripts\run_postmarket.py.
REM Registered in Task Scheduler as "Messiah-Postmarket" (weekday 15:45 KST trigger,
REM see scripts\install_scheduled_tasks.ps1). Runs AFTER "Messiah-Shutdown" (15:40) so the
REM collection processes are fully down and their archive fragments are already compacted.
REM
REM Why this exists (2026-08-06): the two tools that clear the integrity report's
REM "unmeasured" axes were documented in dev_memory\NEXT_TODO.md as a manual post-market
REM step - and were skipped two trading days in a row (08-05, 08-06). A step that only
REM exists in prose does not run. See run_postmarket.py's module docstring.
REM
REM NOTE: keep this file ASCII-only. cmd.exe interprets .bat files using the system ANSI
REM codepage (CP949 on Korean Windows), not UTF-8 - same constraint as run_l1_daily.bat.

setlocal
cd /d "%~dp0.."

chcp 65001 >nul

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set TODAY=%%i

set LOGDIR=logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set LOGFILE=%LOGDIR%\postmarket_%TODAY%.log

if not exist ".venv\Scripts\python.exe" (
    echo [run_postmarket.bat] .venv not found - run "uv sync" first >&2
    exit /b 1
)

REM Same stream handling as run_l1_daily.bat: cmd /c does the 2>&1 merge, NOT PowerShell.
REM PS 5.1 wraps a native command's first stderr line in a NativeCommandError block, which
REM corrupts exactly the lines we most need to read when a step fails.
powershell -NoProfile -Command ^
    "& cmd /c '.venv\Scripts\python.exe -u scripts\run_postmarket.py 2>&1' | ForEach-Object { $_ | Out-File -FilePath '%LOGFILE%' -Append -Encoding utf8; $_ }; exit $LASTEXITCODE"
set EXITCODE=%ERRORLEVEL%

if not %EXITCODE%==0 (
    echo [run_postmarket.bat] exit code %EXITCODE% - check %LOGFILE% >&2
)

endlocal & exit /b %EXITCODE%
