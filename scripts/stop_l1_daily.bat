@echo off
REM MESSIAH daily shutdown safety net.
REM
REM scripts\run_l1_daily.py already handles its own graceful shutdown internally
REM (daily_close() at 15:35 KST + a hard deadline at 15:40 KST, see that script's module
REM docstring) - this batch file is an INDEPENDENT watchdog, meant to be run separately
REM (e.g. its own later Task Scheduler trigger) to make sure nothing MESSIAH-related is still
REM running past that, in case the process hung somewhere its own internal deadline logic
REM couldn't reach.
REM
REM Also matches the Command Center UI (Streamlit) process that run_l1_daily.py launches as a
REM separate background process on trading days (2026-07-29 addition, see that script's
REM _launch_ui()) - the UI is independent of the collection process and would otherwise keep
REM running forever with no matching shutdown trigger of its own.
REM
REM Also matches scripts\run_g2_paper_trading.py (2026-07-29 addition, "Messiah-G2" Task
REM Scheduler entry) - it has its own internal daily_close()/hard-deadline logic
REM identical in shape to run_l1_daily.py's, so this is purely the same safety-net role for
REM the second process, not a primary shutdown mechanism.
REM
REM Matches by command line content, not window title - the Streamlit process has no useful
REM window title for matching either. This mirrors a lesson learned the hard way on the sibling
REM mahdi project (2026-07-21 ops review, item 3-1): a window-title-only kill missed a process
REM that had been restarted by hand outside the normal launch convention, so both automated
REM shutdowns silently did nothing while the process kept running. Matching on what a process is
REM actually executing catches it regardless of how it was started.
REM
REM 15:35 KST time gate (2026-07-29 incident response): this file's own header above says it is
REM meant to run separately via its own later Task Scheduler trigger (15:40) - but in practice it
REM also gets invoked off-schedule (manually, or via Task Scheduler's "Run" button) for ad-hoc
REM cleanup, e.g. after a manual verification run of run_g2_paper_trading.py. Confirmed by hand,
REM 2026-07-29 13:08:03 (logs\shutdown_watchdog.log): exactly that kind of off-schedule run killed
REM the Command Center UI (messiah\ui\app.py) mid-session, dropping the WebSocket under anyone who
REM had the dashboard open in a browser ("Connection error - Is Streamlit still running?"). The UI
REM has to stay in the command-line match set (see block above) because nothing else cleans it up,
REM so the fix is not to narrow the match - it's to make this script actually behave like the
REM "15:40 safety net" it already claims to be: skip the kill entirely before 15:35 KST (five
REM minutes ahead of run_l1_daily.py's HARD_SHUTDOWN_DEADLINE, matching its REGULAR_SESSION_STOP),
REM unless MESSIAH_FORCE_SHUTDOWN=1 is set (same override-env-var convention as MESSIAH_SKIP_UI in
REM core/ui_launcher.py) for the rare case of a genuine intentional full ad-hoc teardown.
REM
REM NOTE: keep this file ASCII-only. cmd.exe interprets .bat files using the system ANSI
REM codepage (CP949 on Korean Windows), not UTF-8 - a UTF-8-saved file with non-ASCII text
REM (comments OR string literals) gets its multi-byte sequences misparsed as bogus commands
REM (confirmed by hand, 2026-07-24 - see run_l1_daily.bat for the same lesson).

setlocal
chcp 65001 >nul

REM Resolve the project root from this script's own location, not a hardcoded path - works
REM regardless of which machine/directory this is checked out to.
for %%I in ("%~dp0..") do set "PROJECT_DIR=%%~fI"
set "LOG_FILE=%PROJECT_DIR%\logs\shutdown_watchdog.log"

if not exist "%PROJECT_DIR%\logs" mkdir "%PROJECT_DIR%\logs"

echo [%date% %time%] ===== MESSIAH shutdown watchdog start ===== >> "%LOG_FILE%"

REM -ne $PID excludes this PowerShell process itself, since the search string below would
REM otherwise match its own command line too. Kept on one line rather than using a caret (^)
REM continuation, just to avoid that whole class of risk (an earlier draft of this file DID
REM show garbled "not recognized as an internal or external command" fragment errors when run
REM - but tracked that down to leftover non-ASCII text elsewhere in this file, same class of
REM bug as the ASCII-only note above, not the continuation itself. Confirmed by hand,
REM 2026-07-24: the errors disappeared entirely once the file was verified byte-for-byte ASCII.
REM
REM Stop-Process -Id $p.ProcessId (NOT "$procs | Stop-Process") is deliberate: Win32_Process
REM CIM objects expose "ProcessId", not "Id", so piping them straight into Stop-Process fails
REM to bind by property name and silently kills nothing at all - no error, the process just
REM keeps running. Confirmed by hand, 2026-07-24 (log said "stopping: PID 24556" and it was
REM still alive afterward).
powershell -NoProfile -Command "$cutoff = Get-Date -Hour 15 -Minute 35 -Second 0; $forced = ($env:MESSIAH_FORCE_SHUTDOWN -eq '1'); if (-not $forced -and (Get-Date) -lt $cutoff) { Write-Output ('skip: before 15:35 KST safety-net window (now {0}) - nothing killed. Set MESSIAH_FORCE_SHUTDOWN=1 to override.' -f (Get-Date -Format 'HH:mm:ss')); exit 0 }; $procs = Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and ($_.CommandLine -like '*run_l1_daily.py*' -or $_.CommandLine -like '*run_g2_paper_trading.py*' -or $_.CommandLine -like '*messiah\ui\app.py*') }; if ($procs) { foreach ($p in $procs) { Write-Output ('command-line match, stopping: PID {0} - {1}' -f $p.ProcessId, $p.CommandLine); Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue } } else { Write-Output 'command-line match: no leftover process found' }" >> "%LOG_FILE%" 2>&1

echo [%date% %time%] ===== MESSIAH shutdown watchdog done (Redis left running) ===== >> "%LOG_FILE%"

endlocal
