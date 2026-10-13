"""Protect isolated public CircleCI AI-dispatch branches against accidental builds.

Only a tagged GitHub push on an exact branch can run the corresponding smoke
job. No private repository access or scientific PASS is implied.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys

REPO = "yinghanyang44-yyh/PUBLIC_MATLAB_CI_SMOKE"
BRANCH_TO_PREFIX = {
    "ci/auto-python": "[UPC_CI_PY]",
    "ci/auto-matlab": "[UPC_CI_MATLAB]",
}
SHA40 = re.compile(r"[0-9a-f]{40}\Z")


def git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], text=True, stderr=subprocess.DEVNULL, timeout=12
    ).strip()


def check(env=None) -> dict:
    e = os.environ if env is None else env
    branch = e.get("CIRCLE_BRANCH", "")
    if branch not in BRANCH_TO_PREFIX:
        raise ValueError("Not an approved AI dispatch branch")
    if e.get("CI_EXPECTED_COMMIT", ""):
        raise ValueError("AI branch dispatch must not carry manual SHA parameter")
    head = git("rev-parse", "HEAD")
    if not SHA40.fullmatch(head):
        raise ValueError("Checkout HEAD is not a full immutable Git SHA")
    event_sha = e.get("CIRCLE_SHA1", "")
    if event_sha != head:
        raise ValueError("CircleCI event SHA must match checked-out commit")
    remote = git("remote", "get-url", "origin")
    allowed = (r"^(?:https://(?:[^/@]+@)?github\.com/|git@github\.com:)"
               + re.escape(REPO) + r"(?:\.git)?/?$")
    if re.fullmatch(allowed, remote) is None:
        raise ValueError("Checkout source is not the public experiment repository")
    subject = git("log", "-1", "--format=%s")
    prefix = BRANCH_TO_PREFIX[branch]
    if not subject.startswith(prefix + " ") or len(subject) > 180:
        raise ValueError("A deliberate AI dispatch commit subject is required")
    task_id = subject[len(prefix):].strip()
    if not re.fullmatch(r"[a-zA-Z0-9_./:-]{3,100}", task_id):
        raise ValueError("Invalid or empty public task identifier")
    return {
        "status": "AI_PUBLIC_DISPATCH_PASS",
        "repository": REPO,
        "branch": branch,
        "commit_sha": head,
        "dispatch_subject": subject,
        "task_id": task_id,
        "scope": "public_synthetic_ci_only",
    }


if __name__ == "__main__":
    try:
        info = check()
        output = Path("evidence/circleci")
        output.mkdir(parents=True, exist_ok=True)
        (output / "ai-dispatch.json").write_text(
            json.dumps(info, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print("AI_PUBLIC_DISPATCH_PASS", info["branch"], info["commit_sha"])
    except Exception as ex:
        print("AI_PUBLIC_DISPATCH_FAIL", str(ex), file=sys.stderr)
        sys.exit(4)
