"""Local unit tests for CircleCI public AI dispatch; no paid cloud execution."""
import importlib.util
from pathlib import Path
from unittest import mock

p = Path(__file__).resolve().with_name("auto_guard.py")
spec = importlib.util.spec_from_file_location("ci_auto_guard", p)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
sha = "e" * 40
remote = "https://github.com/yinghanyang44-yyh/PUBLIC_MATLAB_CI_SMOKE.git"

def fake_git(*args):
    return {
        ("rev-parse", "HEAD"): sha,
        ("remote", "get-url", "origin"): remote,
        ("log", "-1", "--format=%s"): "[UPC_CI_PY] smoke-20261013-1",
    }[args]

with mock.patch.object(mod, "git", side_effect=fake_git):
    assert mod.check({"CIRCLE_BRANCH": "ci/auto-python", "CIRCLE_SHA1": sha})["status"] == "AI_PUBLIC_DISPATCH_PASS"
    for e in (
        {"CIRCLE_BRANCH": "main", "CIRCLE_SHA1": sha},
        {"CIRCLE_BRANCH": "ci/auto-python", "CIRCLE_SHA1": "f"*40},
        {"CIRCLE_BRANCH": "ci/auto-python", "CIRCLE_SHA1": sha, "CI_EXPECTED_COMMIT": sha},
    ):
        try:
            mod.check(e)
        except ValueError:
            pass
        else:
            raise AssertionError("Fail-closed gate allowed invalid dispatch: " + str(e))
print("AI_DISPATCH_GUARD_LOCAL_TEST_PASS")
