# -*- coding: utf-8 -*-
"""[MW0601 483차 후속3 / P1-3·P2-1] `.git/index.lock` 스테일 판정·회수 단일 파일 유틸.

## 왜 있는가

2026-08-21 08:59:53에 생긴 **0바이트 `.git/index.lock`이 53.5시간** 남아 futures
저장소가 커밋 불가였는데 **어떤 계측에도 안 걸렸다.** 같은 사고가 같은 날 fuoption
(09:08:53)에도 났고 그쪽은 2일간 커밋이 봉쇄됐다.

무증상인 이유가 구조적이다 — 스테일 락에서

    git status           rc=0 · stderr 무출력 (조용히 통과)
    git add / commit     fatal: Unable to create index.lock (rc=128)

즉 **읽기는 멀쩡하고 쓰기만 죽는다.** 커밋을 시도하지 않는 날은 아무도 모른다.

## 0바이트의 의미 (483차 강제종료 실험으로 확정)

git은 락을 **빈 파일로 먼저 만들고** 새 인덱스를 **맨 마지막에 한 번에** 쓴다.
따라서 **0바이트 = 인덱스 쓰기 명령이 중간에 죽었다**는 지문이다.

    git add -A  중단  ->  0바이트 락 잔존 4/4   (락을 먼저 잡고 작업 내내 쥔다)
    git status  중단  ->  잔존 0/5              (락을 마지막에 ms 단위로만 잡는다)

## 자동 삭제 금지 — 3중 조건

락은 **정상 동작 중에도 존재한다.** 진짜로 도는 git의 락을 지우면 그 git이 인덱스를
깨뜨린다. 그래서 회수는 셋을 **모두** 만족할 때만 한다:

    (1) size == 0         인덱스 쓰기 명령이 죽은 지문
    (2) age  > 600초      정상 명령이 10분씩 락을 쥐지 않는다
    (3) git 프로세스 0개   지금 도는 git이 없다

(2)의 임계는 이 저장소들 규모 기준이다(futures 783파일 · fuoption 447파일에서
`git status` 0.08~0.13초). 거대 저장소에 쓰려면 `--min-age` 로 올릴 것.
(3)을 못 세면 `git_procs=None`(**미측정**)이며 0으로 위장하지 않는다 — 미측정이면
stale로 판정하지 않는다(계측 4원칙 (2)).

## 쓰는 법

    python scripts/git_lock_guard.py --check
    python scripts/git_lock_guard.py --check --all
    python scripts/git_lock_guard.py --reclaim
    python scripts/git_lock_guard.py --check --json

종료코드: 0 정상 · 2 스테일 발견 · 3 락 존재하나 판정보류 · 1 실행 오류.
`--reclaim` 은 회수에 성공하면 0.

**커밋 전 프리플라이트로 쓴다**(P1-3). 커밋이 `fatal: Unable to create ...` 로 실패한
뒤에 원인을 찾지 말고, 실패하기 전에 상태를 알아야 한다. git hook 으로는 못 막는다 —
훅은 락을 잡은 **뒤에** 돌기 때문이다.

⚠ py3.7(futures 런타임) · py3.10(fuoption) 양쪽에서 도는 **의존성 없는 단일 파일**로
유지할 것. f-string·pathlib·dataclass 를 쓰지 말 것 — 복사본이 두 저장소에 있다.
"""

from __future__ import print_function

import argparse
import json
import os
import subprocess
import sys
import time

#: 3중 조건 (2)의 기본 임계(초).
DEFAULT_MIN_AGE_SEC = 600

#: `--all` 이 훑을 기본 뿌리. 형제 저장소가 여기 나란히 있다.
DEFAULT_SCAN_ROOT = os.path.join(os.path.expanduser("~"), "PycharmProjects")


def _git_process_count():
    """실행 중 git 프로세스 수. 셀 수 없으면 None(미측정) — 0으로 위장하지 않는다."""
    try:
        p = subprocess.Popen(
            ["tasklist", "/FI", "IMAGENAME eq git.exe", "/NH"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        out = p.communicate(timeout=10)[0].decode("utf-8", "replace")
        return out.lower().count("git.exe")
    except Exception:
        pass
    try:  # POSIX 폴백 (코웍 리눅스 샌드박스)
        p = subprocess.Popen(["pgrep", "-c", "git"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, _ = p.communicate(timeout=10)
        return int(out.decode("ascii", "replace").strip() or "0")
    except Exception:
        return None


def inspect(repo, min_age_sec=DEFAULT_MIN_AGE_SEC, git_procs=None):
    """저장소 하나의 `.git/index.lock` 상태.

    git_procs: 미리 센 값을 넘기면 재사용한다(`--all` 이 저장소마다 tasklist 를
        돌리지 않게 하기 위함 — 프로세스 수는 저장소별 값이 아니라 시스템 값이다).
    """
    info = {
        "repo": os.path.abspath(repo),
        "is_repo": False,
        "present": False,
        "size": None,
        "age_sec": None,
        "git_procs": git_procs,
        "stale": False,
        "verdict": "",
        "min_age_sec": min_age_sec,
    }
    gitdir = os.path.join(repo, ".git")
    if not os.path.isdir(gitdir):
        info["verdict"] = "git 저장소 아님"
        return info
    info["is_repo"] = True
    lock = os.path.join(gitdir, "index.lock")
    if not os.path.exists(lock):
        info["verdict"] = "정상 — 락 없음"
        return info
    info["present"] = True
    try:
        st = os.stat(lock)
    except Exception as e:
        info["verdict"] = "락 stat 실패: %s" % e
        return info
    info["size"] = st.st_size
    info["age_sec"] = max(0.0, time.time() - st.st_mtime)
    if info["git_procs"] is None:
        info["git_procs"] = _git_process_count()

    fails = []
    if info["size"] != 0:
        fails.append("크기 %s바이트(0 아님 — 인덱스 쓰기가 진행됐다)" % info["size"])
    if info["age_sec"] <= min_age_sec:
        fails.append(
            "나이 %.0f초 <= 임계 %d초(아직 실행 중일 수 있다)" % (info["age_sec"], min_age_sec)
        )
    if info["git_procs"] is None:
        fails.append("git 프로세스 **미측정**(0으로 간주하지 않는다)")
    elif info["git_procs"] != 0:
        fails.append("git 프로세스 %d개 실행 중" % info["git_procs"])

    if fails:
        info["stale"] = False
        info["verdict"] = "판정보류 — " + " / ".join(fails)
    else:
        info["stale"] = True
        info["verdict"] = (
            "스테일 확정 — 0바이트 · %.1f시간 · git 프로세스 0개 "
            "-> 이 저장소는 커밋 불가 상태다" % (info["age_sec"] / 3600.0)
        )
    return info


def reclaim(repo, min_age_sec=DEFAULT_MIN_AGE_SEC, git_procs=None):
    """3중 조건을 **다시 확인한 뒤에만** 제거한다.

    반환 (removed: bool, info: dict). 판정보류면 손대지 않는다 — 이 함수는
    "지워도 되는가"를 되묻는 자리이지 강제 삭제 도구가 아니다.
    """
    info = inspect(repo, min_age_sec=min_age_sec, git_procs=git_procs)
    if not info["stale"]:
        return False, info
    lock = os.path.join(repo, ".git", "index.lock")
    try:
        st = os.stat(lock)  # 경합 방어: 판정 이후 바뀌지 않았는지 재확인
        if st.st_size != 0:
            info["verdict"] = "회수 취소 — 판정 직후 크기가 %s바이트로 변했다" % st.st_size
            info["stale"] = False
            return False, info
        os.remove(lock)
    except Exception as e:
        info["verdict"] = "회수 실패: %s" % e
        return False, info
    info["verdict"] = "회수 완료 — " + info["verdict"]
    return True, info


def discover_repos(scan_root=DEFAULT_SCAN_ROOT):
    """`scan_root` 바로 아래의 git 저장소들."""
    out = []
    try:
        for name in sorted(os.listdir(scan_root)):
            p = os.path.join(scan_root, name)
            if os.path.isdir(os.path.join(p, ".git")):
                out.append(p)
    except Exception:
        pass
    return out


def _fmt(info):
    name = os.path.basename(info["repo"]) or info["repo"]
    if not info["is_repo"]:
        return "%-24s %s" % (name, info["verdict"])
    if not info["present"]:
        return "%-24s OK    %s" % (name, info["verdict"])
    mark = "STALE" if info["stale"] else "HOLD "
    return "%-24s %s %s" % (name, mark, info["verdict"])


def main(argv=None):
    ap = argparse.ArgumentParser(description="`.git/index.lock` 스테일 판정·회수 (3중 조건)")
    ap.add_argument(
        "--repo",
        action="append",
        default=[],
        help="검사할 저장소(반복 가능). 기본은 현재 작업 디렉터리",
    )
    ap.add_argument("--all", action="store_true", help="형제 저장소 전수 (--scan-root 아래)")
    ap.add_argument("--scan-root", default=DEFAULT_SCAN_ROOT)
    ap.add_argument("--check", action="store_true", help="검사만 한다(기본 동작 — 명시용)")
    ap.add_argument("--reclaim", action="store_true", help="3중 조건 충족 시에만 제거한다")
    ap.add_argument(
        "--min-age",
        type=int,
        default=DEFAULT_MIN_AGE_SEC,
        help="조건 (2) 임계 초 (기본 %d)" % DEFAULT_MIN_AGE_SEC,
    )
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    repos = list(a.repo)
    if a.all:
        repos.extend(r for r in discover_repos(a.scan_root) if r not in repos)
    if not repos:
        repos = [os.getcwd()]

    procs = _git_process_count()  # 시스템 값이므로 한 번만 센다
    rows, n_stale, n_hold, n_removed = [], 0, 0, 0
    for r in repos:
        if a.reclaim:
            removed, info = reclaim(r, min_age_sec=a.min_age, git_procs=procs)
            info["removed"] = removed
            n_removed += 1 if removed else 0
        else:
            info = inspect(r, min_age_sec=a.min_age, git_procs=procs)
            info["removed"] = False
        rows.append(info)
        if info["present"]:
            if info["stale"] and not info["removed"]:
                n_stale += 1
            elif not info["stale"]:
                n_hold += 1

    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        for info in rows:
            print(_fmt(info))
        if a.reclaim and n_removed:
            print("")
            print("회수 %d건. 회수 직후 `git status` 로 인덱스가 갱신되는지 확인할 것." % n_removed)
        if n_hold:
            print("")
            print(
                "⚠ 판정보류가 있다 — **지우지 말 것**. 실행 중인 git 일 수 있다. "
                "몇 분 뒤 재판정하라."
            )

    if n_stale:
        return 2
    if n_hold:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
