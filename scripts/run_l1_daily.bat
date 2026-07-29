@echo off
REM L1 data pipeline daily entrypoint - wraps scripts\run_l1_daily.py.
REM Registered in Task Scheduler as "Messiah" (weekday 08:35 KST trigger, confirmed by audit
REM 2026-07-29 - see run_l1_daily.py module docstring for the full warmup -> collect ->
REM daily_close lifecycle). "Messiah-G2" (run_g2_paper_trading.bat, 08:36) runs alongside it.
REM
REM NOTE: keep this file ASCII-only. cmd.exe interprets .bat files using the system ANSI
REM codepage (CP949 on Korean Windows), not UTF-8 - a UTF-8-saved file with non-ASCII comments
REM gets its multi-byte sequences misparsed as bogus commands (confirmed by hand, 2026-07-24).

setlocal
cd /d "%~dp0.."

REM Switch this console to UTF-8 so the script's Korean log lines render correctly live,
REM not just when the log file is opened later in an editor.
chcp 65001 >nul

REM %date% format is locale-dependent - use PowerShell for a safe, unambiguous yyyyMMdd.
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set TODAY=%%i

set LOGDIR=logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set LOGFILE=%LOGDIR%\l1_daily_%TODAY%.log

if not exist ".venv\Scripts\python.exe" (
    echo [run_l1_daily.bat] .venv not found - run "uv sync" first >&2
    exit /b 1
)

REM cmd.exe has no built-in tee, so route through PowerShell to show output live in this
REM console AND append it to the log file. Windows PowerShell 5.1's Tee-Object defaults to
REM UTF-16LE for -FilePath and (unlike PS 7+) has no -Encoding parameter at all (both confirmed
REM by hand, 2026-07-24) - so tee manually per line via Out-File -Encoding utf8 instead, which
REM keeps the log consistent with everything else this project writes (UTF-8 JSON log lines).
powershell -NoProfile -Command ^
    "& '.venv\Scripts\python.exe' 'scripts\run_l1_daily.py' 2>&1 | ForEach-Object { $_ | Out-File -FilePath '%LOGFILE%' -Append -Encoding utf8; $_ }; exit $LASTEXITCODE"
set EXITCODE=%ERRORLEVEL%

if not %EXITCODE%==0 (
    echo [run_l1_daily.bat] exit code %EXITCODE% - check %LOGFILE% >&2
)

endlocal & exit /b %EXITCODE%
