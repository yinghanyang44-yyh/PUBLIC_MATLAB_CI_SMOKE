# Public task-branch MATLAB module route v1

This is a separate route from the established generic smoke workflow. The generic workflow, its fixtures and its entrypoint are unchanged.

Status: source and offline checks prepared. The new module route has not yet passed a hosted MATLAB run. Its newly authored generic candidate has passed local C++ tests only. Do not infer module-runtime success from the earlier generic infrastructure run.

## Deliberate owner request

Only a new owner comment on an open owner-created issue with title prefix `[MATLAB_MODULE] ` can request a run. The issue body must be strict JSON:

```json
{
  "schema": "matlab-module-request-v1",
  "frozen_inputs": {
    "contract/contract.json": "<reviewed-original-contract-SHA256>",
    "tests/test_vectors.csv": "<reviewed-original-vectors-SHA256>",
    "expected_outputs.csv": "<reviewed-original-oracle-SHA256>"
  }
}
```

The three hashes must come from independently frozen reviewer-approved inputs. Never replace them automatically with hashes computed from a candidate's proposed oracle.

The owner then posts exactly:

```text
/matlab-module run <full-lowercase-40-character-module-SHA> lab/<task-id>-<numeric-run-id>
```

The repository must remain public and owner-held, with default branch `main`. The event sender, original actor, triggering actor, issue author and comment author must all be the owner. Only attempt 1 is accepted. Source-command edits/deletion, issue-body changes and branch movement fail closed before execution. No push, pull-request, schedule, automatic retry or old-issue rerun starts MATLAB.

The issue is claimed once with a trusted bot comment after all source/input checks. The claim binds the issue-body digest, source-comment ID, module branch/SHA, trusted main SHA, source manifest and run ID. A uncertain claim POST is not retried. Preserve claim comments; administrative deletion/editing can defeat any comment-based replay ledger. This is an operational guard within the owner's trusted repository administration, not an adversarial administrator boundary.

## Exact branch and source identity

1. Publish this trusted module workflow and its helpers to `main` through normal review
2. Freeze that trusted main SHA
3. Create `lab/<task-id>-<numeric-run-id>` with one candidate commit whose only parent is that exact main SHA
4. The candidate commit may change only the eleven paths below; all are required regular non-executable Git blobs. Inherited baseline files must remain unchanged
5. Create the owner issue/body/command after the branch is ready. If `main` changed before the command event, v1 rejects the old-parent task commit. Make a newly reviewed task identity rather than changing admission assumptions

The authorization job reads the branch ref and Git objects at the explicit candidate SHA. It materializes only the eleven approved files into a source snapshot; it never substitutes the generic main fixture or checks out an entire unreviewed candidate tree. The runtime job checks out trusted infrastructure at the frozen event SHA and downloads that audited snapshot separately.

`infrastructure_sha` and `module_sha` are distinct receipt fields. The module source manifest records each path, byte count, SHA-256 and Git blob SHA. Hashes and live owner approval are checked before installation and again immediately before native execution; source hashes are checked afterward. Changes during an already running native step cannot be atomically revoked by these snapshots. The post-verifier rejects receipts that bind the infrastructure SHA where the module SHA belongs.

## Eleven-file contract

```text
contract/contract.json
include/module.h
src/module.cpp
sfunction/sfun_module.cpp
tests/test_module.cpp
tests/test_vectors.csv
expected_outputs.csv
matlab/build_mex.m
matlab/run_harness.m
README.md
HANDOFF.md
```

Package limits: one MiB total; non-CSV files at most 256 KiB; CSVs at most one MiB each; contract at most 16 KiB. Every file must be nonempty UTF-8 without NUL. No symlinks, submodules, special/executable files, binary files, extra configuration or source paths are accepted. Candidate C++ includes use a fixed small header allowlist; no unreviewed transitive local headers are fetched. Candidate code still requires review: filenames, hashes and static checks do not sandbox native code running inside MATLAB.

### JSON schema

The contract contains exactly these fields:

- `schema`: `cpp-module-v1`
- `module_id`: lowercase identifier matching `[a-z][a-z0-9_-]{0,47}`
- `version`: numeric `x.y.z`, without prerelease/build suffix or leading zeroes
- `input_width`, `state_width`, `output_width`: integers 1–16
- `parameters`: 0–16 finite doubles
- `initial_state`: finite doubles with length `state_width`
- `sample_time`: finite, greater than zero and at most 1000
- `sample_count`: integer 1–2000
- `input_units`, `state_units`, `output_units`: nonempty strings of at most 32 characters, matching their widths
- `reset_input_index`: null or a zero-based valid input index
- `reset_timing`: `none` for null reset index, otherwise `before_step`
- `output_timing`: `output_and_next_from_pre_state`
- `event_flag_bits`: an object mapping decimal bit positions `0`–`31` to nonempty meanings of at most 80 characters; an empty object is allowed

Additional/duplicate JSON keys and nonfinite JSON numbers are rejected. If a reset channel is declared, its vector values must be 0 or 1. Event flags may use only declared bits.

### Canonical C++ API

`include/module.h` must exactly match the header constant in the trusted validator:

```cpp
#ifndef CPP_MODULE_V1_H
#define CPP_MODULE_V1_H
#include <cstdint>
extern "C" void module_step_v1(const double* input, const double* state,
    const double* parameters, double* output, double* next_state,
    std::uint32_t* event_flags) noexcept;
#endif
```

The core uses the declared widths and is independent of MATLAB/Simulink. It is a deterministic pure step: input/state/parameters are read-only; outputs and next state are written explicitly; initial state comes from the frozen contract. No initialization callback or candidate build command is called. When the parameter vector is empty, the caller passes a null pointer; candidate code must not dereference it.

CSV input header: `sample,u0,...`. Expected-output header: `sample,y0,...,x_next0,...,event_flags`. Sample indices are exactly 0 through N−1. Numeric values use finite ASCII decimal/scientific notation, without underscores or surrounding whitespace. Standard CSV quoting is accepted and decoded before numeric validation. Event flags are unsigned decimal uint32 values. Output/next-state max absolute error and RMSE are fixed at `1e-12`; event equality is exact. These thresholds cannot be changed by a candidate contract.

### Actual candidate S-function

The public job compiles and executes the candidate's actual `sfunction/sfun_module.cpp`, linked to its actual core. It does not replace that file with a trusted template. The S-function must define `S_FUNCTION_NAME sfun_module` and `S_FUNCTION_LEVEL 2`.

Its six dialog parameters are, in order:

1. Input width
2. State width
3. Output width
4. Parameter vector
5. Initial-state vector
6. Sample time

It has one contiguous real-double input vector and one real-double output vector: `[output, actual_pre_state, predicted_next_state, event_flags]`. State is initialized from the contract. `mdlOutputs` must not commit state; `mdlUpdate` commits one pure step. The trusted fixed-step harness adds a terminal observation that repeats the last frozen input. It checks only the actual incoming pre-update state to verify the last meaningful commit; terminal output, next-state and event are unassessed. The extra row does not increase sample_count and is not an added frozen-oracle sample. No new zero-input-validity requirement is imposed.

Candidate `matlab/build_mex.m` and `matlab/run_harness.m` remain reviewable proposals. They are not put on the MATLAB path or invoked. Candidate unit-test source is also not executed by this public job; its independent private/native evidence remains separate.

## Trusted runtime and receipts

The runtime stays on explicit Windows 2022, MATLAB R2025a and VS2022, with only Simulink requested and installation caching disabled. Existing Windows/compiler/headroom guards remain active. There is no private checkout, cross-repository credential, custom license token, LLM execution or automatic retry. The issue-write token exists only in the short admission job. The MATLAB job has read-only repository permission, and no GitHub token is passed to the MATLAB execution step.

The trusted main-branch MATLAB entrypoint controls compile arguments, direct-MEX adapter, model creation and comparisons. It records separate S-function build, model-update/load and harness-simulation gates. It compares direct-core output/state/events against the frozen oracle and candidate S-function traces, including deterministic repetition and final committed state.

Two 30-day artifacts retain admitted source and runtime evidence. Ordinary failures preserve available diagnostics; runner loss, timeout or artifact-service failure can still prevent upload.

- `matlab-receipt.json` records observed runtime gates, separate module/infrastructure identities and verbatim admission/manifest JSON
- `verified-module-receipt.json` is written only after a successful runtime report, unchanged source hashes, matching identities and all required runtime gates are verified
- `PUBLIC_MODULE_RUNTIME=PASS` means only the complete public runtime subset passed
- `PUBLIC_MATLAB_MODULE_ACCEPTANCE=WAITING` remains until independently verified private generation, unit and replay evidence is combined with the public receipt
- Private promotion, PR creation, branch cleanup, system integration and formal project validation are never claimed by this public runner

Any later copy-back uses the exact allowlisted source and verified receipts through the owner's authorized repository tools. This workflow does not contain a cross-repository token or perform promotion itself.

## Offline checks

```sh
python -B -m unittest discover -s tests/module_lab -p 'test_*.py' -v
python -B -m unittest discover -s tests/matlab -p 'test_*.py' -v
```

These tests cover owner commands, frozen input hashes, strict schema, task-parent and path restrictions, Git object/hash identity, live branch/body changes, one-use claims, source materialization and receipt binding to the actual module SHA. They do not substitute for the new route's first hosted MATLAB execution.
