@echo off
REM L1 data pipeline daily entrypoint - wraps scripts\run_l1_daily.py.
REM Registered in Task Scheduler as "Messiah" - see run_l1_daily.py module docstring for the
REM full warmup -> collect -> daily_close lifecycle. "Messiah-G2" (run_g2_paper_trading.bat)
REM runs alongside it.
REM
REM DO NOT write the trigger time here. The source of truth is configs\scheduled_tasks.json,
REM read by both scripts\install_scheduled_tasks.ps1 (registration) and
REM src\messiah\ops\session_guard.py (the launch-window guard). On 2026-08-10 this comment said
REM "08:35" while the registered trigger was already 08:20 - the guard refused the on-time
REM launch, exited 0, and the scheduler logged success. Stale times in comments are what sent
REM the investigation down the wrong path first. Run install_scheduled_tasks.ps1 to see or
REM change the registered times.
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
REM
REM The stderr merge (2>&1) is done by cmd /c, NOT by PowerShell (2026-08-05). Reason: in
REM PS 5.1, redirecting a NATIVE command's stderr inside the pipeline turns each stderr line
REM into an ErrorRecord, and the FIRST one gets rendered with a "python.exe : " prefix plus a
REM four-line "At line:1 char:1 / + CategoryInfo ... NativeCommandError" block. The very first
REM stderr line this process emits is crash_forensics' arming marker, so it was mangled every
REM single day - which made ops/crash_dumps.py report "no arming marker" and the fix-verification
REM registry raise a false "recurred" ERROR on 2026-08-04. Worse, a real faulthandler crash dump
REM would be corrupted the same way. Letting cmd merge the streams keeps them plain text.
REM Verified by hand 2026-08-05: marker intact, Korean UTF-8 intact, exit code propagated.
REM -u keeps stdout unbuffered so stdout/stderr interleave in real order through the pipe.
powershell -NoProfile -Command ^
    "& cmd /c '.venv\Scripts\python.exe -u scripts\run_l1_daily.py 2>&1' | ForEach-Object { $_ | Out-File -FilePath '%LOGFILE%' -Append -Encoding utf8; $_ }; exit $LASTEXITCODE"
set EXITCODE=%ERRORLEVEL%

if not %EXITCODE%==0 (
    echo [run_l1_daily.bat] exit code %EXITCODE% - check %LOGFILE% >&2
)

endlocal & exit /b %EXITCODE%
