# Autonomous public CI dispatch — candidate

GitHub remains the control plane. CircleCI executes only public, generic MATLAB/Simulink/Python smoke code from `PUBLIC_MATLAB_CI_SMOKE`. No cloud desktop or CircleCI API token is needed for a GitHub App push trigger.

## Agent dispatch

- Python: push a **new commit** to `ci/auto-python` with exact subject pattern `[UPC_CI_PY] <task-id>`.
- MATLAB: push a **new commit** to `ci/auto-matlab` with exact subject pattern `[UPC_CI_MATLAB] <task-id>`. One carefully selected test at a time; no retries without failure review.
- Each lane checks the branch, current commit SHA, event SHA, public repository origin and exact intent marker before running code/ installing MATLAB.
- Other branch pushes and PR changes do not run automatic smoke jobs; manual workflows continue to require an explicit reviewed 40-hex SHA.
- Status claims require real CircleCI/GitHub status + artifact. A missing status is UNKNOWN, not PASS.

## Prerequisites, rollout, limits

1. CircleCI must have a **GitHub App All pushes** trigger enabled for this public repository. Project creation alone does not verify this. It is configured in CircleCI Project Settings → Project Setup. If missing, no automatic work begins.
2. First verify an AI-created Python dispatch commit and status; only after that try one MATLAB dispatch and check actual credit consumption.
3. Do not grant CircleCI access to either private thesis or `UPC-AwardLab` repo. Protect user secrets, IP, vehicle calibration, unpublished models and research code. Deleting public Git branches does not guarantee content is erased.
4. Synthetic CI smoke is engineering infrastructure validation only; scientific acceptance remains in SOT and formal review.
5. CI credits are finite. This is not an unrestricted background MATLAB runner; AI dispatch is reserved for a bounded reviewed public task.

Status: CANDIDATE. Do not claim unattended operation until a real push→CircleCI execution is confirmed.
