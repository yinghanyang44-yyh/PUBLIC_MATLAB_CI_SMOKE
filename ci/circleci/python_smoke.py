"""Tiny deterministic public Python smoke; no private research code."""
from __future__ import annotations
import json
from pathlib import Path


def run():
    inputs = (0.25, 1.0, -0.5, -2.0, 0.25, 3.0)
    expected = (0.25, 1.0, 0.5, -1.0, -0.75, 1.0)
    x = 0.0
    outputs = []
    for u in inputs:
        x = min(1.0, max(-1.0, x + u))
        outputs.append(x)
    assert tuple(outputs) == expected, (outputs, expected)
    assert abs(sum(outputs) - 1.0) < 1e-12
    return {
        "status": "PASS",
        "suite": "generic_public_python_smoke",
        "inputs": list(inputs),
        "outputs": outputs,
        "claim": "software_smoke_only_not_research_validation",
    }


if __name__ == "__main__":
    result = run()
    path = Path("evidence/circleci")
    path.mkdir(parents=True, exist_ok=True)
    (path / "python-smoke.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    print("PUBLIC_PYTHON_SMOKE_PASS")
