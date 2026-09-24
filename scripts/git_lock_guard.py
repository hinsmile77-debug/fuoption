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
import stat
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
        info["verdict"] = "index.lock 없음"
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
        fails.append("크기 %s바이트(0 아님 - 인덱스 쓰기가 진행됐다)" % info["size"])
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
        info["verdict"] = "판정보류 - " + " / ".join(fails)
    else:
        info["stale"] = True
        info["verdict"] = (
            "스테일 확정 - 0바이트 · %.1f시간 · git 프로세스 0개 "
            "-> 이 저장소는 커밋 불가 상태다" % (info["age_sec"] / 3600.0)
        )
    return info


def _force_remove(path):
    """읽기전용 특성을 걷어내고 지운다.

    git 은 loose object 를 0444 로 만든다. Windows 는 읽기전용 파일의 삭제를
    WinError 5(액세스 거부)로 막는다 — **잠금이 아니라 특성 문제다**
    (2026-09-24 실측: attrib 가 'A  R' 을 보여 줬다). 그래서 "삭제 권한이
    있는 쪽(Windows)"에서 돌려도 회수가 실패했다. 쓰기 비트를 돌려준 뒤
    한 번 더 시도한다. 그래도 안 되면 그 예외를 그대로 올린다 -- 진짜 잠금과
    특성 문제를 뭉뚱그리지 않기 위해서다.
    """
    try:
        os.remove(path)
        return
    except OSError:
        pass
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    os.remove(path)


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
            info["verdict"] = "회수 취소 - 판정 직후 크기가 %s바이트로 변했다" % st.st_size
            info["stale"] = False
            return False, info
        _force_remove(lock)
    except Exception as e:
        info["verdict"] = "회수 실패: %s" % e
        return False, info
    info["verdict"] = "회수 완료 - " + info["verdict"]
    return True, info


# ── [MW0601 603차 후속4] `index.lock` 만으로는 부족했다 ────────────────────────
#
# 2026-09-23 실측: 이 저장소에 `.git/HEAD.lock`(9/22 23:45) · `.git/index.lock`
# (23:46) · `.git/index.lock.stale_20260920` · `tmp_obj_*` **40개**가 남아 있었다.
# 위쪽 `inspect()` 는 `index.lock` 하나만 본다 — **HEAD.lock 은 커밋을 똑같이
# 막는데 이 도구가 보지 않았다.** 프리플라이트가 통과시킨 채 커밋이 죽는다.
#
# 두 갈래로 나눈다. 같은 「잔존물」이어도 **막는 것과 어지르는 것은 다르다.**
#   TIER1 잠금  — 쓰기를 **막는다**. 종료코드 2/3 에 반영한다.
#   TIER2 부스러기 — 아무것도 안 막고 쌓이기만 한다. 종료코드를 바꾸지 않는다.
#
# 🔴 조건이 `index.lock` 과 **다르다.** 0바이트 조건을 쓰지 않는다 —
#   `HEAD.lock`·ref 락은 새 sha 를 **써 넣은 뒤** rename 하므로 정상적으로도
#   0바이트가 아니다. 0바이트를 요구하면 진짜 스테일을 놓친다. 대신 나이와
#   git 프로세스 0개, 둘만으로 판정한다(계측 4원칙 ② — 미측정이면 판정 안 함).
TIER1_FIXED = ("HEAD.lock", "config.lock", "packed-refs.lock", "ORIG_HEAD.lock")
TIER1_DIRS = ("refs", "logs")
TIER2_PREFIX = ("tmp_obj_", "tmp_pack_")

# ── [MW0601 630차] 가드가 못 보던 잔존물 두 종류 ─────────────────────────────
#
# 2026-09-24 실측: futures·options 에 잠금 5개가 남아 있었는데 `--all` 이 둘 다
# 「OK」로 보고했다. 전부 아래 두 자리였다 —
#   objects/maintenance.lock      9/17·9/20 이후 그대로. **자동 gc 가 그때부터 멈췄다.**
#   _stale_20260920/HEAD.lock 등  지난 세션이 못 지워 「폴더째 이름만 바꿔 둔」 것.
# 둘 다 커밋은 막지 않는다 → TIER2(종료코드 무변경). 그러나 치우지 않으면 영영 남는다.
TIER2_FIXED = (os.path.join("objects", "maintenance.lock"),)
TIER2_STALE_DIR_PREFIX = "_stale_"


def _walk(root, pick):
    out = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            if pick(fn):
                out.append(os.path.join(dirpath, fn))
    return out


def scan_extra(repo):
    """`index.lock` **이외의** 잔존물. (tier1_locks, tier2_debris) 경로 목록."""
    gitdir = os.path.join(repo, ".git")
    t1, t2 = [], []
    if not os.path.isdir(gitdir):
        return t1, t2
    for name in TIER1_FIXED:
        p = os.path.join(gitdir, name)
        if os.path.exists(p):
            t1.append(p)
    for sub in TIER1_DIRS:
        d = os.path.join(gitdir, sub)
        if os.path.isdir(d):
            t1.extend(_walk(d, lambda fn: fn.endswith(".lock")))
    objs = os.path.join(gitdir, "objects")
    if os.path.isdir(objs):
        t2.extend(_walk(objs, lambda fn: fn.startswith(TIER2_PREFIX)))
    # 지난 세션이 「지우지 못해 이름만 바꿔 둔」 것들 — 코웍 마운트의 흔적이다.
    t2.extend(_walk(gitdir, lambda fn: ".lock.stale_" in fn))
    # [630차] 폴더째 옮겨 둔 형태와 maintenance.lock.
    for name in TIER2_FIXED:
        p = os.path.join(gitdir, name)
        if os.path.exists(p):
            t2.append(p)
    for d in _stale_dirs(gitdir):
        t2.extend(_walk(d, lambda fn: True))
    return sorted(set(t1)), sorted(set(t2))


def _stale_dirs(gitdir):
    try:
        return [
            os.path.join(gitdir, n)
            for n in sorted(os.listdir(gitdir))
            if n.startswith(TIER2_STALE_DIR_PREFIX) and os.path.isdir(os.path.join(gitdir, n))
        ]
    except Exception:
        return []


def _prune_stale_dirs(repo):
    """비워진 `_stale_*` 폴더를 지운다. 안에 뭐라도 남았으면(판정보류) 그대로 둔다."""
    n = 0
    for d in _stale_dirs(os.path.join(repo, ".git")):
        for dirpath, _dirnames, _filenames in os.walk(d, topdown=False):
            try:
                os.rmdir(dirpath)  # 비어 있지 않으면 OSError — 의도한 동작이다
                n += 1 if dirpath == d else 0
            except OSError:
                pass
    return n


def sweep_extra(repo, min_age_sec=DEFAULT_MIN_AGE_SEC, git_procs=None, reclaim_it=False):
    """(rows, n_stale_t1, n_hold_t1, n_removed). 판정보류는 손대지 않는다."""
    if git_procs is None:
        git_procs = _git_process_count()
    t1, t2 = scan_extra(repo)
    rows, n_stale, n_hold, n_removed = [], 0, 0, 0
    now = time.time()
    for path, tier in [(p, 1) for p in t1] + [(p, 2) for p in t2]:
        row = {
            "path": path,
            "tier": tier,
            "age_sec": None,
            "git_procs": git_procs,
            "stale": False,
            "removed": False,
            "verdict": "",
        }
        try:
            row["age_sec"] = max(0.0, now - os.stat(path).st_mtime)
        except Exception as e:
            row["verdict"] = "stat 실패: %s" % e
            rows.append(row)
            continue
        fails = []
        if row["age_sec"] <= min_age_sec:
            fails.append("나이 %.0f초 <= 임계 %d초" % (row["age_sec"], min_age_sec))
        if git_procs is None:
            fails.append("git 프로세스 **미측정**(0으로 간주하지 않는다)")
        elif git_procs != 0:
            fails.append("git 프로세스 %d개 실행 중" % git_procs)
        if fails:
            row["verdict"] = "판정보류 - " + " / ".join(fails)
            if tier == 1:
                n_hold += 1
        else:
            row["stale"] = True
            row["verdict"] = "스테일 - %.1f시간" % (row["age_sec"] / 3600.0)
            if reclaim_it:
                try:
                    _force_remove(path)
                    row["removed"] = True
                    n_removed += 1
                    row["verdict"] = "회수 완료 - " + row["verdict"]
                except Exception as e:
                    # 🔴 코웍 마운트에서는 여기가 EPERM 으로 떨어진다. **성공한 척하지 않는다.**
                    row["verdict"] = "회수 실패(%s) - 삭제 권한이 없는 환경일 수 있다" % e
                    if tier == 1:
                        n_stale += 1
            elif tier == 1:
                n_stale += 1
        rows.append(row)
    if reclaim_it and n_removed:
        _prune_stale_dirs(repo)
    return rows, n_stale, n_hold, n_removed


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


def _ensure_utf8_console():
    """표준 출력·오류를 UTF-8 로 재구성한다. **판정보다 출력이 먼저 죽는 것을 막는다.**

    [MW0601 491차] cp949 콘솔이 em dash(—) 하나에 UnicodeEncodeError 를 내고,
    그 예외가 `main()` 밖으로 나가 **rc=1** 로 죽는다. 판정 자체는 이미 끝난
    뒤라 결과가 정상(rc=0)이어도 호출자는 실패로 읽는다 — 하필 이 스크립트는
    SKILL.md 가 **매 커밋 전 프리플라이트**로 지정한 도구다(478차 후속 §8-2 ·
    480차 F-2 와 같은 원인). fuoption 쪽은 같은 사고를 2026-08-31 에 따로 겪고
    따로 고쳤다 — [630차] 두 사본의 고침을 이 함수 하나로 합쳤다.

    `errors="replace"`: 재구성이 부분적으로만 먹는 환경에서도 판정 결과는 나와야 한다.
    `hasattr` 가드: 파이프로 감싸인 스트림은 `reconfigure()` 가 없을 수 있다.
    출력 리터럴 자체도 cp949 로 찍히는 글자만 쓴다(fuoption tests/ops 가 고정) — 이중 방어.
    """
    try:
        from utils.analysis_db import utf8_console

        utf8_console()
        return
    except Exception:
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            # 재구성 실패가 이 도구의 본업(락 판정)을 막아서는 안 된다.
            pass


def main(argv=None):
    _ensure_utf8_console()  # [630차] parse_args 보다 앞 — `--help` 도 보호한다
    ap = argparse.ArgumentParser(description="`.git/index.lock` 스테일 판정·회수 (3중 조건)")
    ap.add_argument(
        "--repo",
        action="append",
        default=[],
        help="검사할 저장소(반복 가능). 기본은 현재 작업 디렉터리",
    )
    ap.add_argument("--all", action="store_true", help="형제 저장소 전수 (--scan-root 아래)")
    ap.add_argument("--scan-root", default=DEFAULT_SCAN_ROOT)
    ap.add_argument("--check", action="store_true", help="검사만 한다(기본 동작 - 명시용)")
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
    n_debris = 0
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
        # [603차 후속4] index.lock 이외의 잔존물. tier1 은 쓰기를 막으므로 종료코드에
        # 반영하고, tier2(부스러기)는 세어서 보여만 준다 — 아무것도 안 막는다.
        xrows, xs, xh, xr = sweep_extra(
            r, min_age_sec=a.min_age, git_procs=procs, reclaim_it=bool(a.reclaim)
        )
        info["extra"] = xrows
        n_stale += xs
        n_hold += xh
        n_removed += xr
        n_debris += sum(1 for x in xrows if x["tier"] == 2)

    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        for info in rows:
            # 🔴 `_fmt` 는 index.lock 만 본다. 그 줄만 믿으면 HEAD.lock 이 막고 있는
            #   저장소가 「OK」로 보인다 — 2026-09-23 에 실제로 그럴 뻔했다.
            _x = info.get("extra", [])
            _bad = [y for y in _x if y["tier"] == 1 and not y["removed"]]
            _line = _fmt(info)
            if _bad and " OK    " in _line:
                _line = _line.replace(" OK    ", " LOCK  ", 1)
            print(_line)
            for x in _x:
                rel = os.path.relpath(x["path"], info["repo"])
                mark = "T%d" % x["tier"]
                print("    %s %-44s %s" % (mark, rel, x["verdict"]))
        if n_debris:
            print("")
            print(
                "부스러기(T2) %d개 - 커밋을 막지는 않는다. 코웍 마운트가 git 의 "
                "unlink 를 막아 쌓인 것이다. 삭제 권한이 있는 쪽(Windows)에서 "
                "`--reclaim` 으로 치운다." % n_debris
            )
        if a.reclaim and n_removed:
            print("")
            print("회수 %d건. 회수 직후 `git status` 로 인덱스가 갱신되는지 확인할 것." % n_removed)
        if n_hold:
            print("")
            print(
                "[!] 판정보류가 있다 - **지우지 말 것**. 실행 중인 git 일 수 있다. "
                "몇 분 뒤 재판정하라."
            )

    if n_stale:
        return 2
    if n_hold:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
