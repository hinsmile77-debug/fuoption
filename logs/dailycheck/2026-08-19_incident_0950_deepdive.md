# MESSIAH 사망 딥다이브 — 2026-08-19 09:50:29

- 조사 시각: 12:10 KST · 사망 후 **2시간 20분 경과, 여전히 죽어 있음**
- 대상: 08:20 기동 ~ 현재. `l1_daily_20260819.log`(286행) · `g2_daily_20260819.log`(128행) · `status_snapshot.json` · Windows 이벤트로그 · Docker
- 직전 산출물: `2026-08-19_pre_report.md` (08:50, "P0 없음")

## 0. 한 줄 결론

**Windows Update가 장중에 WSL을 갈아끼웠고, 그 위에 있던 Redis가 27초간 사라졌고, MESSIAH는 그 27초를 견디지 못하고 죽었다. 그리고 죽었다는 사실을 아무도 말하지 않았다.**

죽인 것은 외부 사건이지만, **죽게 놔둔 것은 세 겹의 자체 결함**이다.

## 1. 사망 타임라인 (실측)

| 시각(KST) | 사건 | 출처 |
|---|---|---|
| 08:20:20 / 08:25:25 | Messiah / Messiah-G2 기동, self-check PASS | 스케줄러 · 일일로그 |
| 09:00 | 정규장 수집 시작. 09:00~09:49 정상 — `FeaturePublish` 112건, `OptionChainPolled` 35건, 합성봉 47개 | l1 로그 |
| 09:49:52 | Windows Update 다운로드 시작 (3건) | System 이벤트 44 |
| 09:50:12 | **WSL(Store 앱) 설치 시작** | 이벤트 43 |
| 09:50:14 | `Linux용 Windows 하위 시스템` 서비스 시작유형 **자동 → 사용 안 함**, 서비스 재설치 | 이벤트 7040 / 7045 |
| 09:50:15 | WSL 설치 성공. (같은 초에 `status_snapshot.json` 마지막 기록 — 전 컴포넌트 `OK`) | 이벤트 19 |
| **09:50:29** | **Hyper-V VmSwitch: WSL VM의 NIC `Delete` 성공** → 같은 순간 `CollectorProcessingError: 틱 발행 실패: Connection closed by server.` | 이벤트 233/234 · l1 로그 |
| 09:50:32 | `Messiah` 종료 — 스케줄러 반환코드 **2147942401**(=0x80070001, Win32 error 1) | 스케줄러 이벤트 201 |
| 09:50:36 | `Messiah-G2` 종료 — 동일 코드 | 스케줄러 이벤트 201 |
| 09:50:45~51 | 새 WSL 서비스 설치 · `wslservice`/`vmmem` 재기동 · NIC 재생성 | 이벤트 7045/102/232 |
| **09:50:56** | **Redis 컨테이너 3종 전부 재시작 완료** (`messiah-redis`·`mahdi_redis`·`mahdi_timescaledb` StartedAt 동일: `00:50:56Z`) | `docker inspect` · redis 로그 |
| 09:50:56 ~ 현재 | **인프라는 27초 만에 스스로 복구했고, MESSIAH는 복구되지 않았다** | — |

`RestartCount=0` · `OOMKilled=false` — 컨테이너가 죽은 게 아니라 **컨테이너를 담은 VM이 통째로 교체**됐다. `restart: unless-stopped`가 제 일을 해서 Redis는 살아 돌아왔다.

## 2. 사인(死因) — 왜 27초를 못 견뎠나

### 근인 A. 버스 구독 루프가 연결 예외를 못 막는다 (P0)

두 프로세스의 스택 최종 프레임이 같다:

```
run_l1_daily.py:743   await asyncio.gather(...)
  data/last_price.py:116  await bus.subscribe(...)
    core/bus.py:208         async for item in pubsub.listen():
      redis/.../retry.py:81   raise error
redis.exceptions.ConnectionError: Connection closed by server.   ← L1
redis.exceptions.TimeoutError: Timeout reading from localhost:6380  ← G2
```

`core/bus.py:208`의 방어는 **루프 안**에 있다. `decode` 실패도, 핸들러 예외도 삼킨다 — 그 `try/except`는 2026-08-07 사고(1시간 54분 유실) 뒤에 붙었고 주석이 *"이 try/except가 있었다면 손실은 0이었다"*라고 적고 있다. 그런데 **`async for`가 도는 이터레이터 자신이 던지는 예외는 그 밖**이다. 08-07과 **정확히 같은 형태의 결함이 한 칸 위에서 반복**됐다: 보호막을 루프 본문에만 쳤다.

`aioredis.from_url(self._url, decode_responses=False)` (`bus.py:152`) — `retry`·`health_check_interval`·`socket_keepalive` 어느 것도 지정하지 않았다. redis-py 기본 재시도 3회는 27초짜리 단절엔 턱없이 짧고, 소진되면 그대로 위로 던진다.

그리고 `asyncio.gather(...)`는 한 태스크가 던지면 전부 취소된다. 틱 수집기 하나의 연결 예외가 **아카이버·합성기·피처엔진·워치독까지 통째로** 끌고 내려갔다.

> **주목**: 재연결 공학은 이미 이 프로젝트에 있다 — 다만 전부 **KIS WebSocket(상류)** 쪽이다. `CollectorTickStall` → 강제 재연결, `CollectorWSReconnected`, `reconnect_first_tick_grace_seconds`(08-13 P0). **내부 메시지 버스(Redis)에는 재연결이라는 개념 자체가 없다.** 상류 단절은 3중으로 감시하면서, 그 관측 결과를 나르는 파이프는 무방비였다.

### 근인 B. 「실패 시 3회 재시작」이 이 실패에는 발동하지 않는다 (P0)

`configs/scheduled_tasks.json`이 `"restart": true`를 선언하고, 등록 실측도 `RestartCount=3 / Interval=PT1M`으로 살아 있다. 2026-08-06 사고(부팅 후 21분 정지) 뒤에 붙인 안전장치다.

**한 번도 발동하지 않았다.** 스케줄러 이벤트는 이렇게 적혀 있다:

```
09:50:32  [201] Task Scheduler successfully completed task "\Messiah" ... with return code 2147942401
09:50:32  [102] Task Scheduler successfully finished ... instance of the "\Messiah" task
```

`successfully finished`(102) — 스케줄러는 **작업이 성공적으로 끝났다고 판정**했다. Windows의 "실패 시 재시작"은 *작업(action)을 띄우지 못했을 때*만 걸리고, **띄운 프로그램이 0이 아닌 코드로 끝나는 것은 '실패'로 세지 않는다.** `Get-ScheduledTaskInfo`는 `LastTaskResult=1`을 보여주지만 그 값은 재시작 판정에 쓰이지 않는다.

즉 **오늘이 이 안전장치의 첫 실전 시험이었고, 설계 자체가 이 고장 형태를 덮지 않았다.** 상태는 `State=Ready`, `NextRun=2026-08-20 08:20` — 스케줄러 입장에선 오늘 할 일을 다 한 것이다.

### 근인 C. 종료코드를 기록하는 줄이 하필 종료코드 0과 1을 먹는다 (P1)

로그 마지막 줄:

```
[exit] run_l1_daily.py code=
```

값이 비었다. 스케줄러는 `1`이라고 했는데 로그는 침묵한다. `.bat`의 해당 줄이 원인이다:

```bat
echo [exit] run_l1_daily.py code=%EXITCODE%>>"%LOGFILE%"
```

`%EXITCODE%`가 **한 자리 숫자면 cmd가 그 숫자를 리다이렉트 핸들 번호로 파싱**한다. 직접 재현했다:

| 종료코드 | 로그에 남는 것 |
|---|---|
| `1` | `[exit] run.py code=` — **값이 사라짐** (`1>>` = stdout 리다이렉트) |
| `0` | **줄 자체가 없음** (`0>>` = stdin 리다이렉트, 출력이 파일로 안 감) |
| `255` | `[exit] run.py code=255` ✅ |
| `-1` | `[exit] run.py code=-1` ✅ |

실증: 08-12~08-18 로그에 `[exit]` 줄이 **0건**(그날들은 코드 0으로 끝남), 08-11만 `code=-1`로 남아 있다.

이 줄은 2026-08-10에 *"스케줄러 콘솔은 아무도 안 읽고 아무것도 보관 안 하니 로그에 남기자"*는 이유로 추가됐다. **가장 흔한 두 종료코드(정상 0, 오류 1)에서만 침묵하는 계기**가 됐고, 오늘이 정확히 그 코드 1이었다.

### 근인 D. 2시간 20분간 아무도 죽음을 말하지 않았다 (P0)

- `status_snapshot.json`은 **L1 프로세스 자신이 쓴다.** 죽으면 파일이 09:50:15에 얼어붙고, 그 내용은 **전 컴포넌트 `OK`**다. 신선도를 밖에서 재는 주체가 없다.
- Streamlit UI(pid 22776, 08:20 기동)는 **지금도 살아 있다.** 죽은 파이프라인의 09:50 스냅샷을 계속 화면에 띄우고 있다 — 유일한 사람 대면 창구가 가장 오래 거짓말을 한다.
- `self_check`는 **기동 시 1회**만 돈다. `host_health`의 6개 축(`disk`/`power`/`docker`/`cpu`/`boot_recovery`/`schedule_drift`) 중 장중 생존을 보는 축은 없다. `host_health.py:215` 주석이 *"오늘 Docker가 몇 번 죽었나를 사후에 물을 수단이 없었다"*고 적어 놨는데, **오늘 그게 사인의 절반**이었다.
- `Messiah-Shutdown`(15:40)·`Messiah-Postmarket`(15:45)은 시각 트리거 전용 — 그때까지 아무도 안 온다.

### 원인 E. 장중 Windows Update가 열려 있다 (P0 · 진짜 방아쇠)

WSL은 **Microsoft Store 앱**이라 Windows Update 정책과 별개로 자동 갱신된다. 오늘 09:49~09:53 사이에 WSL·Store·OpenAI.Codex 세 건이 연달아 설치됐다. 하필 **개장 50분 뒤**였다.

이 축은 어느 점검 항목에도 없다. 장전 리포트가 아침에 "P0 없음"이라고 쓴 것은 **볼 수 있는 범위 안에서는 옳았다** — 다만 그 범위에 "이 호스트가 오늘 자기를 갈아엎을 예정인가"가 들어 있지 않았다.

## 3. 손실 실측

| 계열 | 09:00~09:49 | 09:50 이후 | 소급 |
|---|---|---|---|
| 틱 | `data/ticks/A05609/2026-08-19/08.parquet`(124KB) · `09.parquet`(1.1MB, 09:49까지) **보존됨** | **영구 소실** | **불가** — 체결 단위 과거 조회 API 없음 |
| 1·3·5·10·15·30분봉 | 샤드 디렉터리에 증분 기록됨(09:50까지) | 소실 | **가능** — KIS 분봉 차트 API 백필 |
| 옵션체인 | 35회 폴링 | 소실 | **불가** — 스냅샷 폴링 |
| 투자자 수급 | 정상 | 소실 | **불가**(장중 시계열) |

**현재까지 2시간 20분. 재기동 없이 15:35을 맞으면 5시간 45분** — 정규장의 **77%**다. 08-07 사고(1시간 54분)를 이미 넘었고, 이 저장소 역사상 **최대 단일 관측 손실**이 되는 중이다.

또한 09:50에 죽었으므로 `daily_close`(통합·재합성·리포트)가 실행되지 않았다. 15:45 `Messiah-Postmarket`은 반나절짜리 불완전 입력으로 정상 실행될 예정이다 — **결함 있는 하루를 정상 산출물로 봉인**한다.

## 4. 조치 (우선순위)

### 즉시 (장중, 코드 변경 아님)
1. **재기동** — Redis는 09:50:56부터 정상. `schtasks /Run /TN "Messiah"` → `Messiah-G2`. 기동 창(08:30~15:35) 안이라 `session_guard`가 막지 않는다. 남은 3시간 20분을 회수한다.
2. 재기동 시각을 기록 — 오늘 리포트의 소급불가 구간을 확정하기 위해.
3. **Store 앱 자동 업데이트 중단** (장중 재발 방지 · 설정 변경).

### 장후 (15:35 이후, 코드)
| # | 내용 | 대상 |
|---|---|---|
| **F-A** | `bus.subscribe`의 `async for`를 재연결 루프로 감싼다. `ConnectionError`/`TimeoutError`는 백오프 재구독, `BusReconnected`/`BusReconnectFailed` 태그 신설. `from_url`에 `health_check_interval`·`socket_keepalive`·`retry` 지정 | `core/bus.py` |
| **F-B** | 외부 생존 감시. `status_snapshot.json` mtime을 **다른 프로세스**가 60초마다 보고, N분 정지면 경보 + 자동 재기동 | 신규 워치독 |
| **F-C** | `.bat`의 `echo ... code=%EXITCODE%>>` → `echo ... code=%EXITCODE% >>` (공백 하나) 또는 `[%EXITCODE%]`로 감싼다. 0·1이 사라지는 것을 회귀 테스트로 못박는다 | `run_l1_daily.bat` · `run_g2_paper_trading.bat` · `run_postmarket.bat` |
| **F-D** | 스케줄러 `RestartCount`가 이 고장을 덮지 않는다는 사실을 명시하고, 대체 수단(F-B)에 위임. `install_scheduled_tasks.ps1` 주석의 *"실패 시 1분 간격 3회 재시도"* 문구가 사실과 다름을 정정 | `install_scheduled_tasks.ps1` |
| **F-E** | `host_health`에 `pending_updates` 축 신설 — 장전 자가점검에서 대기 중인 Windows/Store 업데이트를 인쇄 | `ops/host_health.py` |
| **F-F** | UI에 스냅샷 신선도 배지 — `generated_at_kst`가 N분 지나면 화면 전체를 회색 처리. **죽은 데이터를 살아 있는 것처럼 그리지 않는다** | `command_center` |

## 5. 남는 질문

- `verdict.ok=false / no_expert_contribution`(09:50:15 스냅샷)은 **사인이 아니다** — 08-18부터 이어진 별건이며 F-0818I-1 계측 대상이다. 사망과 섞지 않는다.
- 08-11 `code=-1` 종료가 무엇이었는지 미확인. F-C가 계기를 고치면 다음부터 답이 남는다.
