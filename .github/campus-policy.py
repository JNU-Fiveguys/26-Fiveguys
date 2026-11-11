"""캠퍼스 승인 규칙 — main 보호 규칙의 필수 검사 `campus-policy` 가 실행한다.

- 자기 팀 폴더 하나만 바꾼 PR      → 승인 없이 통과 (작성자가 바로 merge)
- 그 밖(두 폴더 이상 · 다른 팀 폴더 · 공통 폴더) → 바뀐 팀 폴더마다 그 팀원 1명 + 팀장 승인이 있어야 통과
  · 작성자의 팀 폴더는 작성자가 그 팀 몫을 대신한다. 작성자가 팀장이면 팀장 승인도 이미 된 것으로 본다
  · 승인은 지금 PR 의 마지막 커밋에 한 것만 센다 (새 커밋이 올라오면 다시 승인)
- 공통 = 팀 폴더가 아닌 전부 (campus/server · mobile · docs · shared · README, .github 등)

팀 폴더와 팀원은 .github/campus-owners.json. 워크플로가 main 을 체크아웃해서 실행하므로
PR 이 이 파일이나 명단을 고쳐도 그 PR 의 판정에는 쓰이지 않는다.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

COMMON = "공통"


def area_of(path: str, folders: dict) -> str:
    for prefix in folders:
        if path.startswith(prefix):
            return prefix
    return COMMON


def approvers_on(reviews: list[dict], head_sha: str) -> set[str]:
    """사람마다 마지막 리뷰 상태를 보고, 마지막 커밋에 남긴 승인만 돌려준다 (댓글만 단 리뷰는 상태를 바꾸지 않는다)."""
    last: dict[str, tuple[str, str]] = {}
    for r in sorted(reviews, key=lambda r: r.get("submitted_at") or ""):
        login = (r.get("user") or {}).get("login")
        state = r.get("state")
        if login and state in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            last[login] = (state, r.get("commit_id") or "")
    return {u for u, (state, commit) in last.items() if state == "APPROVED" and commit == head_sha}


def evaluate(cfg: dict, files: list[str], reviews: list[dict], author: str, head_sha: str, org: str) -> tuple[bool, list[str]]:
    owner, folders = cfg["owner"], cfg["folders"]
    if not files:
        return True, ["바뀐 파일이 없습니다."]
    areas = {area_of(f, folders) for f in files}
    mine = {p for p, t in folders.items() if author in t["members"]}
    touched = ", ".join(sorted(areas))
    if len(areas) == 1 and areas <= mine:
        return True, [f"바뀐 곳: {touched}", "자기 팀 폴더만 바꿨습니다 — 승인 없이 merge 할 수 있습니다."]

    approved = approvers_on(reviews, head_sha) - {author}
    lines, missing = [f"바뀐 곳: {touched}", "자기 팀 폴더만 바꾼 PR 이 아니라 아래 승인이 모두 필요합니다."], 0
    for p in sorted(a for a in areas if a in folders):
        t = folders[p]
        if p in mine:
            lines.append(f"✅ {p} — 작성자가 이 팀({t['team']})")
        elif approved & set(t["members"]):
            lines.append(f"✅ {p} — {', '.join(sorted(approved & set(t['members'])))} 승인")
        else:
            lines.append(f"❌ {p} — @{org}/{t['team']} 팀원 1명의 승인 필요")
            missing += 1
    if author == owner:
        lines.append(f"✅ 팀장 — 작성자가 팀장(@{owner})")
    elif owner in approved:
        lines.append(f"✅ 팀장 — @{owner} 승인")
    else:
        lines.append(f"❌ 팀장 — @{owner} 의 승인 필요")
        missing += 1
    return missing == 0, lines


def _get_all(repo: str, path: str, token: str) -> list:
    out, page = [], 1
    while True:
        url = f"https://api.github.com/repos/{repo}/{path}?per_page=100&page={page}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}",
                                                   "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            batch = json.load(r)
        out += batch
        if len(batch) < 100:
            return out
        page += 1


def main() -> int:
    repo, pr, token = os.environ["REPO"], os.environ["PR_NUMBER"], os.environ["GITHUB_TOKEN"]
    cfg = json.loads((Path(__file__).parent / "campus-owners.json").read_text(encoding="utf-8"))
    files = [f["filename"] for f in _get_all(repo, f"pulls/{pr}/files", token)]
    reviews = _get_all(repo, f"pulls/{pr}/reviews", token)
    ok, lines = evaluate(cfg, files, reviews, os.environ["AUTHOR"], os.environ["HEAD_SHA"], repo.split("/")[0])
    text = "\n".join(lines)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("## 캠퍼스 승인 규칙\n\n" + "\n".join(f"- {line}" for line in lines) + "\n")
    if not ok:
        print("::error::승인이 더 필요합니다 — 위 ❌ 항목을 확인하세요.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
