# CircleCI public MATLAB + Python pilot (Draft candidate)

This proposal applies only to the already-public `yinghanyang44-yyh/PUBLIC_MATLAB_CI_SMOKE` repo. It does **not** grant access to the private thesis or `UPC-AwardLab` repositories, and does not include private source files, MATLAB licenses, tokens, proprietary parameters, vehicle models or research datasets.

## Free/official route

- CircleCI Free open-source Linux jobs: up to 400,000 monthly compute credits if eligible, **not unlimited minutes**. Check actual credit consumption in CircleCI. Linux VM MATLAB installation consumes more credits than a small Docker Python job.
- MathWorks official `mathworks/matlab@1` CircleCI orb installs MATLAB R2025a and Simulink; public project CI jobs are automatically licensed for supported products, but not transformation products such as MATLAB Coder/Compiler.
- Public visibility alone is not a copyright license. Choose an explicit open-source license separately if you later publish thesis algorithm implementations.

## Deliberate execution only

Both workflows default to *disabled*. Ordinary push/PR/schedule events create **no computing jobs**. Run a job only through CircleCI's authenticated **Trigger Pipeline** interface or API after checking the exact public commit.

1. Review this Draft PR and copy its final 40-character commit SHA from GitHub.
2. In CircleCI Free, connect **only** `PUBLIC_MATLAB_CI_SMOKE`, and enable approved third-party orbs. Keep the private repositories disconnected.
3. Select the corresponding configuration branch and matching checkout branch; supply pipeline parameters:
   - `expected_commit`: exact reviewed lowercase 40-char SHA (string);
   - `run_python_smoke`: true to run only a cheap Python test;
   - `run_matlab_smoke`: true to install MATLAB/Simulink and run the trivial simulation.
4. Run Python first with MATLAB false. Only run MATLAB after confirming account eligibility/credits. Record job URL, credits, actual compiler/OS/MATLAB version, artifacts and exit status.

The pre-install check rejects empty, malformed or mismatched SHAs. It compares checked-out HEAD, event SHA when present and repository remote. It does not prove the human requester's identity. Restrict who can trigger pipelines, never expose CircleCI API tokens, and do not enable schedules or unreviewed webhook triggers.

## Scope and retention

- MATLAB smoke creates a tiny Constant → Gain → To Workspace model in memory and checks the numeric output. Python tests a deterministic saturation accumulator. Neither is scientific validation.
- This route is independent of the existing GitHub Actions workflows and their own two owner-gated trigger mechanisms. Do not weaken those gates.
- CircleCI runner files are transient; public Git objects, provider logs/artifacts/caches and forks can outlive branch deletion. **Deleting after cloud execution does not make publicly posted private code confidential again.**
- Never upload proprietary DC-TVDS modules, private vehicle parameters, customer/academic unpublished data, secrets, or restricted dependencies. These remain local or in the existing private research system.

## Status

- [ ] CircleCI Free project access granted by the user.
- [ ] CI eligibility and manual-pipeline gating checked.
- [ ] Actual Python runner PASS and artifact readback.
- [ ] Actual MATLAB R2025a + Simulink runner PASS and artifact readback.
- [ ] Independent scientific evidence/acceptance remains separate.
