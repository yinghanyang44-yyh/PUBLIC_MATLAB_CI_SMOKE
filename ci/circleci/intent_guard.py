"""Fail closed if the checked-out public Git commit is not the approved 40-char SHA.

This is a workflow guard, not a proof of the triggerer's human identity.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys

EXPECTED_REMOTE = "yinghanyang44-yyh/PUBLIC_MATLAB_CI_SMOKE"
SHA40 = re.compile(r"[0-9a-f]{40}\Z")


def git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], text=True, stderr=subprocess.DEVNULL, timeout=10
    ).strip()


def check() -> dict:
    expected = os.getenv("CI_EXPECTED_COMMIT", "")
    if SHA40.fullmatch(expected) is None:
        raise ValueError("A reviewed lowercase 40-character expected_commit is required")
    head = git("rev-parse", "HEAD")
    if expected != head:
        raise ValueError("Checked-out HEAD is not the reviewed public commit")
    event_sha = os.getenv("CIRCLE_SHA1", "")
    if event_sha and event_sha != head:
        raise ValueError("CircleCI event commit differs from checkout")
    remote = git("remote", "get-url", "origin")
    pattern = (r"^(?:https://(?:[^/@]+@)?github\.com/|git@github\.com:)"
               + re.escape(EXPECTED_REMOTE) + r"(?:\.git)?/?$")
    if re.fullmatch(pattern, remote) is None:
        raise ValueError("Checkout remote does not match the public experiment repo")
    return {
        "status": "SOURCE_INTENT_PASS",
        "repository": EXPECTED_REMOTE,
        "head_sha": head,
        "branch": os.getenv("CIRCLE_BRANCH", ""),
        "workflow": os.getenv("CIRCLE_WORKFLOW_ID", ""),
        "scope": "generic_public_smoke_only",
    }


if __name__ == "__main__":
    try:
        evidence = check()
        p = Path("evidence/circleci")
        p.mkdir(parents=True, exist_ok=True)
        (p / "intent.json").write_text(
            json.dumps(evidence, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        print("SOURCE_INTENT_PASS", evidence["head_sha"])
    except Exception as exc:
        print("SOURCE_INTENT_FAIL", str(exc), file=sys.stderr)
        sys.exit(4)
