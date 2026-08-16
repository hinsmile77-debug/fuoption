---
description: MESSIAH 장전/장중/장후 일일 운영 점검 — 이상점·Fix계획·고도화방안 보고서
argument-hint: "[pre|intra|post] [YYYY-MM-DD]"
---

`messiah-daily-check` 스킬을 사용해 MESSIAH 일일 운영 점검을 수행하라.

인자: $ARGUMENTS
- 첫 번째 인자가 `pre`/`intra`/`post` 중 하나면 그것을 국면으로 쓴다. 없으면 현재 KST 시각으로 추론한다(~09:00 pre / 09:00~15:35 intra / 15:35~ post).
- 두 번째 인자가 날짜면 그 날을 대상으로 한다. 없으면 오늘.

절차는 스킬 문서를 그대로 따른다. 요약하면:

1. `python scripts/collect_evidence.py --phase <국면> --date <날짜>` 로 증거 다이제스트를 먼저 만든다 (스킬 폴더의 스크립트를 써도 된다: `.claude/skills/messiah-daily-check/scripts/collect_evidence.py`).
2. 걸린 지점만 원본 로그를 좁게 되짚는다.
3. `SYSTEM.md` · 마스터플랜 · `dev_memory/` · 커밋 이력과 대조해 이상점을 확정한다. `references/phases.md` 체크리스트를 빠짐없이 통과시킨다.
4. `references/report_template.md` 형식으로 **① 이상점 정리보고 ② Fix 작업 구현계획 ③ 고도화 방안** 세 부분 보고서를 `logs/dailycheck/<날짜>_<국면>_report.md` 에 쓴다.
5. `dev_memory/DECISION_LOG.md` 와 `NEXT_TODO.md` 를 갱신한다. 헤딩 첫 단어는 `[MW0601]`.

장중(`intra`) 국면에서는 코드 변경을 **제안만 하고 실행하지 않는다** (SYSTEM.md R11 · 금지계명 3·4).
