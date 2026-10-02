# PUBLIC_MATLAB_CI_SMOKE

A newly authored, generic public MATLAB/Simulink smoke example. It contains no imported models, research implementation, private parameters, private test vectors, or other project assets. Models are generated from these public source files at runtime.

**Current validation:** local workflow-policy checks and portable C++ tests only. MATLAB, Simulink, MEX, Windows execution and GitHub artifact upload have not yet run. No MATLAB PASS is claimed.

The repository `yinghanyang44-yyh/PUBLIC_MATLAB_CI_SMOKE` is verified public with default branch `main`. Its workflows become registered after these source files are published to that branch.

## One deliberate run

Only two deliberate owner triggers are accepted. There is no push, pull-request, schedule or chained execution.

- Manual dispatch: supply `RUN_PUBLIC_MATLAB_CI_SMOKE` and the exact reviewed 40-character `expected_commit` in the Run workflow form on `main`
- Issue command: the owner creates an open issue whose title begins with `[MATLAB_SMOKE] `, then posts exactly `/matlab-smoke run <full-lowercase-40-character-SHA>` as a new comment. The SHA must equal GitHub's immutable default-branch head SHA for that comment event. No whitespace suffix, option, other command or pull-request comment is accepted

Both paths require owner `yinghanyang44-yyh` as original actor, triggering actor and event sender; exact repository, public visibility, default branch `main`, first run attempt and reviewed commit are checked before MATLAB installation. The issue path additionally requires the issue author and comment author to be the owner.

A short Linux authorization job has `contents: read` and `issues: write`, solely to record a one-use bot claim on an eligible issue. It reads at most 1,000 prior comments, checks for any existing trusted bot claim, and writes exactly one claim before admitting the MATLAB job. A shared concurrency group serializes candidate owner runs; nonowner, unrelated-comment and rerun events use separate groups so they cannot replace a queued owner run. If the POST outcome is uncertain, execution stops without retry. Each claimed issue can start only one run, including when installation or testing fails. Do not delete claim comments; use a new eligible issue for a separately deliberate new run. Repository administrators can always edit workflows or delete records, so this is an operational replay guard rather than an adversarial administrative boundary.

Repository identity and visibility are checked live before the claim and again immediately before installation. A changed/deleted source command or changed/deleted claim blocks execution. The downstream Windows/MATLAB job has only `contents: read`, uses the checked immutable SHA, and uses its separate read-only job token solely for these final reads. Rerun attempts cannot start either job. Checkout credentials are not persisted. Owner authorization/rejection evidence is retained in its own 30-day artifact and restored into the MATLAB evidence bundle on an admitted run.

The [official MATLAB actions](https://github.com/matlab-actions/setup-matlab#licensing) automatically license supported products for public projects. This example uses MATLAB and Simulink only. It has no MATLAB license secret, user-supplied token, agent, LLM API call, transformation product, server product, external MATLAB Engine client, or code-generation step. It does not establish availability of any private-repository licensing route.

## Runtime policy

- `windows-latest`, with an explicit runtime requirement for Windows Server 2025 and Visual Studio 2022 with x64 C/C++ tools
- MATLAB R2025a (latest update), plus only Simulink; setup cache disabled
- Setup action v3.1 commit `f9e43010f1ae678f7cfa0542fe2a4f60f7d1ad8d`; run-command v3.3 commit `bfa857648f4895aa98a446fe41c85e0788421ed5`
- No silent platform/compiler fallback and no automatic retry
- 60-minute job budget, 30-minute install limit, 15-minute execution limit
- Before/after disk, temp, workspace, actual CPU/RAM/image snapshots; project safety thresholds of 10 GiB before install and 2 GiB afterward on relevant volumes. These thresholds are not vendor minimums or an installation-size guarantee

## Seven gates

1. Record and verify actual R2025a/win64 MATLAB and Simulink products and runtime licenses
2. Generate and simulate a tiny Constant/Gain Simulink model
3. Run an asserted deterministic numeric command
4. Select and re-query exact VS2022 C and C++ MEX configurations; compile/load/execute a small MEX in each language
5. Compile a pure portable C++ accumulator behind a direct MEX wrapper; compare it with an independent MATLAB oracle
6. Compile a thin Level-2 S-function linked to that same C++ core; compare identical generic vectors for outputs, actual pre-update states, predicted post-update states, final committed state, reset events and saturation events; report max absolute error and RMSE
7. Verify required diary and structured diagnostics are present; upload is a separate workflow step whose failure fails the job

The bounded accumulator resets its base to zero when requested, adds an increment, and clamps to generic bounds. Event bits are reset=1, lower saturation=2 and upper saturation=4. Exact boundaries are not saturation events. The S-function never mutates state in `mdlOutputs`; `mdlUpdate` commits it. An extra terminal zero-input sample exposes the last committed state. Known integer fixtures and a deterministic dyadic sequence are authored solely for this example.

These are normal-mode software smoke tests, not validation of a research algorithm, real-time application, hardware or code generation.

## Evidence

An always-run artifact step retains evidence for 30 days: allowlisted dispatch/step metadata, disk and compiler inventories, full MATLAB build/test diary, failure report, product versions, generated example models, comparison CSVs, error/RMSE metrics and gate status. No environment dump, private repository checkout, license-file collection or credential collection is performed.

Unreached gates are `NOT_RUN`. A startup error may precede the MATLAB diary; consult the workflow step outcomes and GitHub's installation/startup logs. Hard runner loss, cancellation, timeout or artifact-service failure may prevent upload. Only an observed successful run of all gates and its upload can establish runtime success.

## Token-free local verification

With Python 3, PyYAML and a C++11 compiler, run:

```sh
python -m unittest discover -s tests/matlab -p 'test_*.py' -v
```

This checks both deliberate trigger paths, owner/SHA/visibility/attempt guards, one-use claims, uncertain-POST rejection and bounded pagination, then compiles/runs 252 portable C++ assertions. It does not execute MATLAB or certify GitHub/PowerShell/MATLAB semantics.

## Official references

- [Setup MATLAB](https://github.com/matlab-actions/setup-matlab) and [Run MATLAB Command](https://github.com/matlab-actions/run-command)
- [R2025a supported compilers](https://www.mathworks.com/content/dam/mathworks/mathworks-dot-com/support/sysreq/files/system-requirements-release-2025a-supported-compilers.pdf)
- [R2025a Windows requirements](https://www.mathworks.com/content/dam/mathworks/mathworks-dot-com/support/sysreq/files/system-requirements-release-2025a-windows.pdf)
- [Windows 2025 runner image](https://github.com/actions/runner-images/blob/main/images/windows/Windows2025-Readme.md)
- [GitHub manual dispatch](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_dispatch)
- [Compiler configuration API](https://www.mathworks.com/help/matlab/ref/mex.getcompilerconfigurations.html)
- [From Workspace behavior](https://www.mathworks.com/help/simulink/slref/fromworkspace.html)
