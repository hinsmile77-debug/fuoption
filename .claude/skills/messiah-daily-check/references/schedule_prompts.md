# 예약 실행 프롬프트 (Cowork Scheduled Tasks)

Cowork 예약 작업은 **매 실행이 빈 세션에서 시작한다.** 대화 맥락이 하나도 남지 않으므로,
프롬프트는 국면·날짜·리포 경로·산출 위치·금지사항까지 전부 자기완결적으로 적혀 있어야 한다.
이 파일이 그 정본이다 — 예약을 등록하거나 고칠 때 여기서 복사해 쓴다.

편지에 비유하면: SKILL.md는 "우리 집 요리법"이고, 이 파일은 "그 요리법을 모르는 사람에게
보내는 심부름 쪽지"다. 쪽지에 주소를 안 적으면 심부름꾼은 집을 못 찾는다.

## 등록 현황

| 국면 | taskId | cron (KST) | 상태 |
|---|---|---|---|
| 장전 | `messiah-premarket-check` | `45 8 * * 1-5` | 등록됨 (2026-08-12) |
| 장중 | `messiah-intraday-check` | `30 13 * * 1-5` | 등록됨 (2026-08-12) |
| 장후 | `messiah-postmarket-check` | `50 15 * * 1-5` | 등록됨 (2026-08-12) |

실측 지터(등록 시점): 장전 +305초 → 08:50, 장중 +328초 → 13:35, 장후 +429초 → 15:57.
지터는 등록마다 고정값으로 잡히므로 위 시각이 실제 기동 시각이다.

주의사항 셋:

- **cron은 사용자 로컬 시간(Asia/Seoul)으로 평가된다.** UTC 변환하지 않는다.
- 실행에는 **최대 5분가량 지터(jitter)** 가 붙는다. 08:45 등록이 실제로는 08:50 전후에 뜬다.
  개장(09:00)과 기동 창(08:30~15:35)을 감안해 여유를 두고 잡는다.
- 앱이 꺼져 있으면 그 회차는 **다음 앱 실행 직후**에 돈다(건너뛰지 않는다).

시각 선정 근거:

- **장전 08:45** — 기동 창(08:30~) 이후, 개장(09:00) 이전. 지터를 얹어도 08:50이라 여유가 있다.
- **장중 13:30** — 개장 4시간 반. 오전 결손이 드러날 만큼 쌓였고, 장 마감(15:35) 전에
  사람이 운영 조치를 취할 시간이 남는다.
- **장후 15:50** — 15:45 `run_postmarket.bat` 5단계 절차가 통상 1~2분에 끝난다
  (실측: 8/7·8/10 15:46 종료, 8/11 15:47). 지터로 15:57이라 배치 산출물이 나온 뒤에 본다.
  그래도 프롬프트는 **요약 블록 확인 후 판정**하도록 못 박아 뒀다 — 배치가 느린 날에
  "산출물 없음"을 결함으로 오보하지 않기 위해서다.

---

## 장전 (pre) — `messiah-premarket-check`, 평일 08:45 KST

```text
MESSIAH(메시아) 파생 AI 프로그램의 **장전 점검**을 수행하라.

## 실행 지시

1. `messiah-daily-check` 스킬을 Skill 도구로 호출하고, 그 SKILL.md 절차를 처음부터 끝까지 따른다.
   - 스킬 원본 경로: `C:\Users\82108\PycharmProjects\fuoption\.claude\skills\messiah-daily-check`
   - 스킬 호출이 안 되면 위 경로의 `SKILL.md`를 Read로 직접 읽고 그 절차대로 진행한다.
2. **국면은 `pre`(장전), 날짜는 실행 당일(KST)** 이다. 인자 추론에 시간 쓰지 말고 이 값을 확정값으로 쓴다.
3. 리포 루트는 `C:\Users\82108\PycharmProjects\fuoption` (bash에서는 `/sessions/<세션>/mnt/fuoption`).

## 절차 요약 (SKILL.md가 정본, 아래는 이탈 방지용)

- 증거 수집을 먼저 한다. 로그 원본을 통째로 읽지 않는다:
  `cd <repo> && python3 .claude/skills/messiah-daily-check/scripts/collect_evidence.py --root . --phase pre`
  출력이 크면 `--out logs/dailycheck/evidence_<YYYYMMDD>_pre.md` 로 받아 그 파일을 읽는다.
- 다이제스트에서 걸린 지점만 원본 로그를 좁게 grep으로 되짚는다.
- 기준 네 군데와 대조한다: `SYSTEM.md`(불변원칙·R1~R18·금지 15계명), `Derivatives_AI_Master_Plan_Ver2.0.md`, `dev_memory/DECISION_LOG.md`·`NEXT_TODO.md`, 최근 커밋 이력.
  - `status_snapshot.json`의 `code_version.stale`, `FixVerificationRecurred` 태그, 산출물 누락은 반드시 확인한다.
  - dev_memory에 이미 있는 항목을 새 발견인 양 보고하지 않는다. 반대로 "고쳤다고 기록된 것의 재발"은 최우선 보고 대상이다.
- 장전 국면 체크리스트는 `references/phases.md`를 읽고 빠짐없이 통과시킨다.
- 보고서 형식은 `references/report_template.md`를 따른다. 산출은 항상 세 부분, 순서 고정:
  1. 이상점 정리보고 (장전 작업흐름)
  2. Fix 작업 구현계획 (P0/P1/P2, 파일·함수 수준까지)
  3. 고도화 방안 (당일 관측 근거 필수)
- 이상점 하나 = 증상 → 근거(로그 인용 + 시각) → 기준 위반(SYSTEM.md 조항/설계 문구) → 영향. 근거 없으면 쓰지 않는다.
- 확정된 결함과 "확인 필요"를 섞지 않는다.

## 중요 — 코드 변경 금지

이 예약은 **08:45 KST, 장 시작 직전**에 돈다. 09:00 개장이 임박했으므로 **코드 변경·커밋·배포·재기동을 절대 하지 않는다** (SYSTEM.md R11 / 금지계명 3·4). Fix는 계획만 세우고 적용 시점은 장후로 명시한다.

단, **오늘 거래 자격에 직접 위험(P0)** 이 발견되면 보고서 맨 위에 `⚠ P0` 로 올리고, 사람이 개장 전에 판단할 수 있도록 조치 선택지와 각각의 손익을 한 줄씩 제시한다.

## 산출

- 보고서: `logs/dailycheck/<YYYY-MM-DD>_pre_report.md`
- 증거 다이제스트를 파일로 받았다면 같은 폴더에 둔다.
- 보고서 파일을 `present_files`로 사용자에게 전달한다.
- 채팅 본문에는 **P0 유무 한 줄 + 이상점 건수(P0/P1/P2) + 가장 시급한 것 3줄** 만 쓴다. 전문은 파일에 있다.

## dev_memory 갱신 (생략 금지)

- `dev_memory/DECISION_LOG.md`에 **append**한다. 헤딩 첫 단어는 `[MW0601]`. 기존 내용을 덮어쓰지 않는다(누적 300KB 파일이다).
- 새 fix/고도화 항목은 `dev_memory/NEXT_TODO.md`에 체크박스로 추가한다.
- 항목 구성: 증상 → 원인 → 결정 → Why → How to apply → 검증.

## 마지막 자체 검증

- [ ] 모든 이상점에 로그 시각과 인용이 붙었는가
- [ ] 각 이상점이 SYSTEM.md 조항 또는 설계 문구에 대응되는가
- [ ] dev_memory 기존 항목을 중복 보고하지 않았는가
- [ ] `FixVerificationRecurred` / `code_version.stale` / 산출물 누락을 빠뜨리지 않았는가
- [ ] Fix 계획이 파일·함수 수준까지 구체적인가
- [ ] 고도화 방안이 당일 관측에 근거하는가
- [ ] 코드를 변경하지 않았는가 (장전 금지)
- [ ] dev_memory 갱신을 마쳤는가
```

---

## 장중 (intra) — `messiah-intraday-check`, 평일 13:30 KST

```text
MESSIAH(메시아) 파생 AI 프로그램의 **장중 점검**을 수행하라.

## 실행 지시

1. `messiah-daily-check` 스킬을 Skill 도구로 호출하고, 그 SKILL.md 절차를 처음부터 끝까지 따른다.
   - 스킬 원본 경로: `C:\Users\82108\PycharmProjects\fuoption\.claude\skills\messiah-daily-check`
   - 스킬 호출이 안 되면 위 경로의 `SKILL.md`를 Read로 직접 읽고 그 절차대로 진행한다.
2. **국면은 `intra`(장중), 날짜는 실행 당일(KST)** 이다. 인자 추론에 시간 쓰지 말고 이 값을 확정값으로 쓴다.
3. 리포 루트는 `C:\Users\82108\PycharmProjects\fuoption` (bash에서는 `/sessions/<세션>/mnt/fuoption`).

## 이 국면이 묻는 것

**"지금 설계대로 돌고 있는가."** 장전의 "오늘 거래할 자격이 되는가"와 다르다.

**하루가 아직 끝나지 않았다.** 관측 구간이 09:00~실행시각(약 13:30)임을 보고서 첫 줄에 명시하고,
아직 일어날 차례가 아닌 것을 결함으로 적지 않는다. 장후 산출물 부재, 종가 기반 지표 미산출,
15:35 이후에만 나오는 태그의 부재는 **결함이 아니다.** "판단 불가"와 "결함"을 구분하라.

## 절차 요약 (SKILL.md가 정본, 아래는 이탈 방지용)

- 증거 수집을 먼저 한다. 로그 원본을 통째로 읽지 않는다:
  `cd <repo> && python3 .claude/skills/messiah-daily-check/scripts/collect_evidence.py --root . --phase intra`
  출력이 크면 `--out logs/dailycheck/evidence_<YYYYMMDD>_intra.md` 로 받아 그 파일을 읽는다.
- 다이제스트에서 걸린 지점만 원본 로그를 좁게 grep으로 되짚는다.
- 기준 네 군데와 대조한다: `SYSTEM.md`(불변원칙·R1~R18·금지 15계명), `Derivatives_AI_Master_Plan_Ver2.0.md`, `dev_memory/DECISION_LOG.md`·`NEXT_TODO.md`, 최근 커밋 이력.
  - `status_snapshot.json`의 `code_version.stale`, `FixVerificationRecurred` 태그, 산출물 누락은 반드시 확인한다.
  - dev_memory에 이미 있는 항목을 새 발견인 양 보고하지 않는다. 반대로 "고쳤다고 기록된 것의 재발"은 최우선 보고 대상이다.
- 장중 국면 체크리스트는 `references/phases.md`를 읽고 빠짐없이 통과시킨다.
- 데이터 연속성을 특히 본다 — 1분봉 결손, 수집기 UNKNOWN 구간, 로그 공백, `AggregatorLateTickDropped`,
  `delivery_latency`. 장중에 끊긴 것은 장후에 되메울 수 없는 경우가 많다.
- 보고서 형식은 `references/report_template.md`를 따른다. 산출은 항상 세 부분, 순서 고정:
  1. 이상점 정리보고 (장중 작업흐름)
  2. Fix 작업 구현계획 (P0/P1/P2, 파일·함수 수준까지)
  3. 고도화 방안 (당일 관측 근거 필수)
- 이상점 하나 = 증상 → 근거(로그 인용 + 시각) → 기준 위반(SYSTEM.md 조항/설계 문구) → 영향. 근거 없으면 쓰지 않는다.
- 확정된 결함과 "확인 필요"를 섞지 않는다.

## 중요 — 코드 변경 금지

**장중이다. 코드 변경·커밋·배포·재기동을 절대 하지 않는다** — SYSTEM.md R11 / 금지계명 3·4
(장중 학습 금지·장중 배포 금지). 장전과 이유는 다르지만 결론은 같다: **계획만 세우고 적용은 장후로 미룬다.**
Fix 계획마다 "적용 시점: 장후(15:35 이후)"를 명기한다.

지금 당장 사람이 개입해야 할 정도의 P0(예: 잘못된 포지션, 수집 전면 중단, 리스크 한도 이탈)가
보이면 보고서 맨 위에 `⚠ P0` 로 올리고, **코드를 고치는 대신** 사람이 취할 수 있는 운영 조치
(수동 정지, 파라미터 조정, 관망)와 각각의 손익을 한 줄씩 제시한다.

## 산출

- 보고서: `logs/dailycheck/<YYYY-MM-DD>_intra_report.md`
- 증거 다이제스트를 파일로 받았다면 같은 폴더에 둔다.
- 보고서 파일을 `present_files`로 사용자에게 전달한다.
- 채팅 본문에는 **P0 유무 한 줄 + 이상점 건수(P0/P1/P2) + 가장 시급한 것 3줄** 만 쓴다. 전문은 파일에 있다.

## dev_memory 갱신 (생략 금지)

- `dev_memory/DECISION_LOG.md`에 **append**한다. 헤딩 첫 단어는 `[MW0601]`. 기존 내용을 덮어쓰지 않는다(누적 300KB 파일이다).
- 새 fix/고도화 항목은 `dev_memory/NEXT_TODO.md`에 체크박스로 추가한다.
- 항목 구성: 증상 → 원인 → 결정 → Why → How to apply → 검증.
- 문서 갱신은 코드 변경이 아니다 — 장중에도 한다. 다만 **커밋은 하지 않는다.**

## 마지막 자체 검증

- [ ] 관측 구간(09:00~실행시각)을 첫 줄에 명시했는가
- [ ] 아직 일어날 차례가 아닌 것을 결함으로 적지 않았는가
- [ ] 모든 이상점에 로그 시각과 인용이 붙었는가
- [ ] 각 이상점이 SYSTEM.md 조항 또는 설계 문구에 대응되는가
- [ ] dev_memory 기존 항목을 중복 보고하지 않았는가
- [ ] `FixVerificationRecurred` / `code_version.stale` / 산출물 누락을 빠뜨리지 않았는가
- [ ] Fix 계획이 파일·함수 수준까지 구체적이고, 적용 시점을 장후로 명기했는가
- [ ] 고도화 방안이 당일 관측에 근거하는가
- [ ] 코드를 변경하지 않았는가 (장중 금지)
- [ ] dev_memory 갱신을 마쳤는가
```

---

## 장후 (post) — `messiah-postmarket-check`, 평일 15:50 KST

````text
MESSIAH(메시아) 파생 AI 프로그램의 **장후 점검**을 수행하라.

## 실행 지시

1. `messiah-daily-check` 스킬을 Skill 도구로 호출하고, 그 SKILL.md 절차를 처음부터 끝까지 따른다.
   - 스킬 원본 경로: `C:\Users\82108\PycharmProjects\fuoption\.claude\skills\messiah-daily-check`
   - 스킬 호출이 안 되면 위 경로의 `SKILL.md`를 Read로 직접 읽고 그 절차대로 진행한다.
2. **국면은 `post`(장후), 날짜는 실행 당일(KST)** 이다. 인자 추론에 시간 쓰지 말고 이 값을 확정값으로 쓴다.
3. 리포 루트는 `C:\Users\82108\PycharmProjects\fuoption` (bash에서는 `/sessions/<세션>/mnt/fuoption`).

## 이 국면이 묻는 것

**"오늘 하루가 설계대로였는가."** 세 국면 중 유일하게 하루 전체를 볼 수 있고,
유일하게 코드를 고칠 수 있는 국면이다.

## 장후 배치보다 먼저 결론 내지 않는다

15:45에 `run_postmarket.bat`(Windows 작업 `Messiah-Postmarket`)이 5단계 절차를 돈다 —
조각 통합 · 상위 Horizon 재합성 · 공식 분봉 대비 거래량 대조 · 변동성 축 채점 · 무결성 리포트 재생성.
평소 1~2분이면 끝나지만, **산출물이 없다고 결함으로 단정하기 전에 반드시 배치 완료 여부부터 확인한다.**

```bash
tail -30 logs/postmarket_<YYYYMMDD>.log   # "=== 장후 절차 요약 ===" 블록이 나왔는가
```

요약 블록이 아직 없으면 배치가 도는 중이다. 2~3분 기다렸다 다시 보고, 그래도 없으면
**그때** 배치 미기동/실패를 이상점으로 보고한다. 요약 블록의 `⚠` 단계는 그 자체가 1급 증거다.

## 절차 요약 (SKILL.md가 정본, 아래는 이탈 방지용)

- 증거 수집을 먼저 한다. 로그 원본을 통째로 읽지 않는다:
  `cd <repo> && python3 .claude/skills/messiah-daily-check/scripts/collect_evidence.py --root . --phase post`
  출력이 크면 `--out logs/dailycheck/evidence_<YYYYMMDD>_post.md` 로 받아 그 파일을 읽는다.
- 다이제스트에서 걸린 지점만 원본 로그를 좁게 grep으로 되짚는다.
- 기준 네 군데와 대조한다: `SYSTEM.md`(불변원칙·R1~R18·금지 15계명), `Derivatives_AI_Master_Plan_Ver2.0.md`, `dev_memory/DECISION_LOG.md`·`NEXT_TODO.md`, 최근 커밋 이력.
  - `status_snapshot.json`의 `code_version.stale`, `FixVerificationRecurred` 태그, 산출물 누락은 반드시 확인한다.
  - dev_memory에 이미 있는 항목을 새 발견인 양 보고하지 않는다. 반대로 "고쳤다고 기록된 것의 재발"은 최우선 보고 대상이다.
- 장후 국면 체크리스트는 `references/phases.md`를 읽고 빠짐없이 통과시킨다.
- 오늘 장전·장중 점검 보고서가 `logs/dailycheck/`에 있으면 읽는다. **그때 "확인 필요"로 남긴 것이
  지금 결론 나는가**가 장후의 고유 수확이다.
- 보고서 형식은 `references/report_template.md`를 따른다. 산출은 항상 세 부분, 순서 고정:
  1. 이상점 정리보고 (장전·장중·장후 전 구간)
  2. Fix 작업 구현계획 (P0/P1/P2, 파일·함수 수준까지)
  3. 고도화 방안 (당일 관측 근거 필수)
- 이상점 하나 = 증상 → 근거(로그 인용 + 시각) → 기준 위반(SYSTEM.md 조항/설계 문구) → 영향. 근거 없으면 쓰지 않는다.
- 확정된 결함과 "확인 필요"를 섞지 않는다.

## 코드 변경 — 가능하지만 자동으로 하지 않는다

장후는 적용 가능한 유일한 국면이다. 그러나 **이 예약 실행은 보고까지만 한다.**
Fix를 실제로 구현하는 것은 사용자가 "구현해"라고 이어서 지시했을 때다.

사용자가 구현을 지시하면:

1. 항목별 구현계획을 먼저 쓴다 — 변경 파일, 변경 내용, 회귀 위험, 검증 방법.
2. 변경 후 `pytest`(해당 범위)와 replay 검증을 거친다 — 금지계명 2.
3. 커밋 메시지 첫 단어는 `[MW0601]`.
4. 미커밋 변경을 실전에 반입하지 않는다 — 금지계명 10.

## 재시동 판단 (장후 고유 항목)

보고서 말미에 재시동 권고를 낸다. 손익으로 비교한다:

- **재시동 없이** 얻는 것 — 오늘 관측의 연속성, 프로세스 상태 보존.
- **재시동으로** 얻는 것 — 커밋된 새 코드의 실제 적용.

판단 재료는 `status_snapshot.json`의 `code_version.stale`이다. true면 커밋은 됐는데
프로세스가 옛 코드로 돌고 있다는 뜻이고, 그 상태로 하루가 더 가면 그날 로그는
"어느 코드의 결과인지" 말할 수 없게 된다. 결론은 권고로 내되 근거를 함께 적는다.

## 산출

- 보고서: `logs/dailycheck/<YYYY-MM-DD>_post_report.md`
- 증거 다이제스트를 파일로 받았다면 같은 폴더에 둔다.
- 보고서 파일을 `present_files`로 사용자에게 전달한다.
- 채팅 본문에는 **P0 유무 한 줄 + 이상점 건수(P0/P1/P2) + 가장 시급한 것 3줄 + 재시동 권고 한 줄** 만 쓴다. 전문은 파일에 있다.

## dev_memory 갱신 (생략 금지)

- `dev_memory/DECISION_LOG.md`에 **append**한다. 헤딩 첫 단어는 `[MW0601]`. 기존 내용을 덮어쓰지 않는다(누적 300KB 파일이다).
- 새 fix/고도화 항목은 `dev_memory/NEXT_TODO.md`에 체크박스로 추가한다.
- 항목 구성: 증상 → 원인 → 결정 → Why → How to apply → 검증. "라이브 미검증"이면 검증 기한을 명기한다.

## 마지막 자체 검증

- [ ] 장후 배치(`logs/postmarket_<YYYYMMDD>.log`) 완료 여부를 확인한 뒤 산출물을 판정했는가
- [ ] 오늘 장전·장중 보고서의 "확인 필요" 항목에 결론을 냈는가
- [ ] 모든 이상점에 로그 시각과 인용이 붙었는가
- [ ] 각 이상점이 SYSTEM.md 조항 또는 설계 문구에 대응되는가
- [ ] dev_memory 기존 항목을 중복 보고하지 않았는가
- [ ] `FixVerificationRecurred` / `code_version.stale` / 산출물 누락을 빠뜨리지 않았는가
- [ ] Fix 계획이 파일·함수 수준까지 구체적인가
- [ ] 고도화 방안이 당일 관측에 근거하는가
- [ ] 재시동 권고를 손익 비교와 함께 냈는가
- [ ] dev_memory 갱신을 마쳤는가
````

### 세 국면의 차이 한눈에

| | 장전 | 장중 | 장후 |
|---|---|---|---|
| 묻는 것 | 거래할 자격이 되는가 | 지금 설계대로 도는가 | 하루가 설계대로였는가 |
| 관측 구간 | 전일 종료~기동 | 09:00~실행시각 | 하루 전체 |
| 코드 변경 | 금지 (개장 임박) | 금지 (R11/금지계명 3·4) | 가능 (사용자 지시 시) |
| 고유 항목 | 개장 전 P0 조치 선택지 | 데이터 연속성 | 재시동 손익 판단 |

---

## 등록·수정 방법

새로 등록: `create_scheduled_task` — `taskId`, `cronExpression`(로컬 시간), `prompt`(위 블록 전문).
기존 수정: `update_scheduled_task` — 프롬프트를 고칠 때는 **이 파일을 먼저 고치고 그 내용을 반영한다.**
등록된 프롬프트 원본은 `C:\Users\82108\Claude\Scheduled\<taskId>\SKILL.md` 에 있다.

첫 회차는 `Run now`로 수동 실행해 두는 편이 낫다 — 도구 승인이 그때 저장돼서
이후 자동 실행이 권한 프롬프트에서 멈추지 않는다.
