<#
.SYNOPSIS
  MESSIAH Windows 작업 스케줄러 항목을 정본대로 등록/갱신한다 (2026-08-06 P0-2).

.NOTES
  **이 파일은 UTF-8 BOM으로 저장해야 한다.** Windows PowerShell 5.1은 BOM 없는 .ps1을
  시스템 ANSI 코드페이지(한국어 Windows는 CP949)로 읽는다 — 아래 한글 주석이 깨진 바이트로
  들어가면서 파서가 죽는다(2026-08-06 실측: "/c"를 나눗셈 연산자로 읽고 문자열 종결자를
  못 찾는다). `run_l1_daily.bat` 머리말의 "keep this file ASCII-only"와 같은 계열의 함정이고,
  .ps1은 BOM으로 풀 수 있어서 한글 근거를 그대로 남긴다.

.DESCRIPTION
  ## 왜 이 스크립트가 생겼나

  2026-08-06 10:03:49에 호스트가 재부팅됐다(이벤트 1074, RuntimeBroker.exe, "기타(계획되지
  않음)"). OS는 10:05:03에 올라왔는데 MESSIAH는 안 올라왔다 — 등록된 트리거가 평일 08:35
  하나뿐이었기 때문이다. 사람이 10:25에 손으로 띄울 때까지 21분간 죽어 있었고 1분봉 21개와
  (당시 아카이버 결함까지 겹쳐) 옵션체인·수급 오전치가 영구 소실됐다.

  그때 등록 상태 실측:

      Messiah          MSFT_TaskWeeklyTrigger 08:35  RestartCount=0  StartWhenAvailable=False
      Messiah-G2       MSFT_TaskWeeklyTrigger 08:36  RestartCount=0  StartWhenAvailable=False
      Messiah-Shutdown MSFT_TaskWeeklyTrigger 15:40

  at-startup 트리거도, 실패 시 재시작도, StartWhenAvailable도 없었다.

  ## 왜 스크립트로 만드나

  그 설정들은 **손으로 등록돼 있었고 아무 데도 적혀 있지 않았다.** 어떤 값이 왜 그런지
  알려면 Get-ScheduledTask를 쳐 보는 수밖에 없었고, 그래서 "at-startup이 없다"는 사실을
  사고가 난 뒤에야 알았다. 등록 상태를 코드로 두면 그 자체가 문서이자 회귀 검사가 된다.

  ## 무엇을 바꾸나

  - Messiah / Messiah-G2 에 **at-startup 트리거**를 추가한다(부팅 후 1분 지연 — 부팅
    직후에는 네트워크·Docker가 아직 안 올라와 self_check가 실패한다).
  - 두 작업에 **실패 시 재시작**(1분 간격 3회)과 StartWhenAvailable을 켠다.
  - MultipleInstances=IgnoreNew — 08:34 부팅처럼 두 트리거가 겹쳐도 두 번 안 뜬다.
  - Messiah-Postmarket(평일 15:45, run_postmarket.bat)을 새로 등록한다.

  기동 시각이 아무 때나가 되는 부작용은 진입점의 기동 창 검사가 막는다
  (`src/messiah/ops/session_guard.py` `launch_window_verdict`, 08:30~15:35).

  ## 안전

  바꾸기 전에 현재 정의를 XML로 백업한다(-BackupDir, 기본 logs/task_backup_<타임스탬프>).
  되돌리려면:  schtasks /Create /TN "Messiah" /XML <백업파일> /F

.PARAMETER DryRun
  아무것도 바꾸지 않고 계획만 출력한다.

.PARAMETER BackupDir
  기존 정의 XML을 저장할 디렉터리. 기본값은 logs\task_backup_<yyyyMMdd-HHmmss>.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\install_scheduled_tasks.ps1 -DryRun
  powershell -ExecutionPolicy Bypass -File scripts\install_scheduled_tasks.ps1
#>

[CmdletBinding()]
param(
    [switch]$DryRun,
    [string]$BackupDir
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot

if (-not $BackupDir) {
    $BackupDir = Join-Path $repo ("logs\task_backup_" + (Get-Date -Format 'yyyyMMdd-HHmmss'))
}

# 부팅 직후 1분 지연 — self_check의 docker/redis 항목이 부팅 직후에는 아직 못 뜬다.
# run_l1_daily.py가 Docker Desktop을 스스로 띄우고 최대 2분 기다리므로(_ensure_docker_ready)
# 여기서 길게 잡을 이유는 없다. 1분은 "네트워크 스택이 올라올 시간"이다.
$bootDelay = 'PT1M'

$plan = @(
    @{
        Name    = 'Messiah'
        Bat     = 'scripts\run_l1_daily.bat'
        Weekly  = '08:35'
        AtBoot  = $true
        Restart = $true
    },
    @{
        Name    = 'Messiah-G2'
        Bat     = 'scripts\run_g2_paper_trading.bat'
        Weekly  = '08:36'
        AtBoot  = $true
        Restart = $true
    },
    @{
        # 안전망은 부팅 트리거가 필요 없다 — 정리할 프로세스가 애초에 없다.
        Name    = 'Messiah-Shutdown'
        Bat     = 'scripts\stop_l1_daily.bat'
        Weekly  = '15:40'
        AtBoot  = $false
        Restart = $false
    },
    @{
        # 2026-08-06 신설 — 장후 절차가 이틀 연속 사람 손에서 빠졌다(P0-3b).
        # Messiah-Shutdown(15:40)보다 뒤여야 한다: 수집 프로세스가 완전히 내려간 뒤에 돈다.
        Name    = 'Messiah-Postmarket'
        Bat     = 'scripts\run_postmarket.bat'
        Weekly  = '15:45'
        AtBoot  = $false
        Restart = $false
    }
)

$weekdays = @('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')

Write-Host "=== MESSIAH 작업 스케줄러 등록 ===" -ForegroundColor Cyan
Write-Host "  저장소 : $repo"
Write-Host "  백업   : $BackupDir"
if ($DryRun) { Write-Host "  모드   : DryRun (아무것도 바꾸지 않는다)" -ForegroundColor Yellow }
Write-Host ""

# ---------------------------------------------------------------- 1. 백업
$existing = @()
foreach ($item in $plan) {
    $task = Get-ScheduledTask -TaskName $item.Name -ErrorAction SilentlyContinue
    if ($task) { $existing += $item.Name }
}
if ($existing.Count -gt 0 -and -not $DryRun) {
    New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
    foreach ($name in $existing) {
        $xml = Export-ScheduledTask -TaskName $name
        $out = Join-Path $BackupDir "$name.xml"
        $xml | Out-File -FilePath $out -Encoding utf8
        Write-Host "  [backup] $name -> $out"
    }
    Write-Host ""
}

# ---------------------------------------------------------------- 2. 등록/갱신
foreach ($item in $plan) {
    $bat = Join-Path $repo $item.Bat
    if (-not (Test-Path $bat)) {
        Write-Host "  [SKIP] $($item.Name) — $($item.Bat) 없음" -ForegroundColor Yellow
        continue
    }

    $triggers = @(New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At $item.Weekly)
    $summary = "평일 $($item.Weekly)"
    if ($item.AtBoot) {
        $boot = New-ScheduledTaskTrigger -AtStartup
        $boot.Delay = $bootDelay
        $triggers += $boot
        $summary += " + 부팅 시($bootDelay 지연)"
    }

    # ExecutionTimeLimit=0 : 무제한. 수집 프로세스는 자체 하드 데드라인(15:40)을 갖고 있고,
    # 스케줄러가 중간에 끊으면 종료 절차(통합/재합성/리포트)가 통째로 날아간다.
    $settingsArgs = @{
        StartWhenAvailable         = $true
        MultipleInstances          = 'IgnoreNew'
        ExecutionTimeLimit         = (New-TimeSpan -Seconds 0)
        AllowStartIfOnBatteries    = $true
        DontStopIfGoingOnBatteries = $true
    }
    if ($item.Restart) {
        $settingsArgs['RestartCount']    = 3
        $settingsArgs['RestartInterval'] = (New-TimeSpan -Minutes 1)
        $summary += " + 실패 시 1분 간격 3회 재시도"
    }
    $settings = New-ScheduledTaskSettingsSet @settingsArgs

    # 인자를 미리 조립한다 — PS 5.1에서 백틱 줄이음 + 이스케이프 따옴표를 한 줄에 섞으면
    # 파서가 "/c"를 나눗셈 연산자로 읽는다(2026-08-06 실측).
    $argument = '/c "' + $bat + '"'
    $action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument $argument -WorkingDirectory $repo

    # **실행 주체는 기존 것을 그대로 쓴다.** `-Force` 재등록은 Principal도 새로 쓰는데,
    # 기본값에 맡기면 LogonType/RunLevel이 조용히 바뀔 수 있고 그 사실은 **다음 거래일
    # 08:35에 아무것도 안 뜨는 것으로만** 드러난다. 2026-08-06 실측 기존값은
    # LogonType=Interactive / RunLevel=Limited이고, 새 작업도 같은 값으로 맞춘다.
    $current = Get-ScheduledTask -TaskName $item.Name -ErrorAction SilentlyContinue
    if ($current) {
        $principal = $current.Principal
    }
    else {
        $me = [Security.Principal.WindowsIdentity]::GetCurrent().Name
        $principal = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited
    }

    Write-Host "  [$($item.Name)] $summary"
    Write-Host "      $bat"
    Write-Host "      실행 주체: $($principal.UserId) / $($principal.LogonType) / $($principal.RunLevel)"
    if ($DryRun) { continue }

    Register-ScheduledTask -TaskName $item.Name -Action $action -Trigger $triggers -Settings $settings -Principal $principal -Force | Out-Null
    Write-Host "      등록 완료" -ForegroundColor Green
}

# ---------------------------------------------------------------- 3. 확인
Write-Host ""
Write-Host "=== 등록 결과 ===" -ForegroundColor Cyan
$rows = @()
foreach ($task in (Get-ScheduledTask -TaskName 'Messiah*')) {
    $labels = @()
    foreach ($trigger in $task.Triggers) {
        if ($trigger.CimClass.CimClassName -eq 'MSFT_TaskBootTrigger') {
            $labels += '부팅'
        }
        else {
            $labels += ([datetime]$trigger.StartBoundary).ToString('HH:mm')
        }
    }
    $rows += [PSCustomObject]@{
        Task      = $task.TaskName
        State     = $task.State
        Triggers  = ($labels -join ' / ')
        Restart   = $task.Settings.RestartCount
        WhenAvail = $task.Settings.StartWhenAvailable
    }
}
$rows | Format-Table -AutoSize

if ($DryRun) {
    Write-Host "DryRun 이었다 — 위 '등록 결과'는 변경 전 상태다." -ForegroundColor Yellow
}
