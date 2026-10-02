# Archived public task-branch cleanup, V1

This independent workflow removes one public `lab/` task branch pointer after a
successful module runtime and owner-verified private copyback. It neither runs
MATLAB nor executes module or artifact code. The existing generic smoke and
module runtime workflows are unchanged.

## Deliberate owner request

First independently copy and verify the exact module source bytes, source
manifest, runtime receipts, and diagnostics in the intended private archive.
Keep the actual private proof privately. Do not place private repository names,
URLs, paths, tokens, or private data into this public request.

Then create a new open issue whose title starts `[MATLAB_MODULE_CLEANUP] `.
Its body must be exactly one strict JSON object:

```json
{
  "schema": "matlab-module-cleanup-request-v1",
  "private_copy_verified": true,
  "module_issue_number": 5,
  "module_run_id": "36969341536",
  "runtime_artifact_id": 11211455171,
  "runtime_artifact_sha256": "<64 lowercase hex characters>",
  "source_manifest_sha256": "<64 lowercase hex characters>",
  "verified_receipt_sha256": "<64 lowercase hex characters>"
}
```

The hashes refer to the exact downloaded runtime ZIP, its `source-manifest.json`
bytes, and its `verified-module-receipt.json` bytes. `private_copy_verified` must
be the literal boolean `true`. It is an owner attestation, not a claim that this
public workflow accessed or independently inspected the private archive.

The owner then posts exactly:

```text
/matlab-module cleanup <full 40-character lowercase module SHA> lab/<task-id>-<numeric-run-id>
```

Only `yinghanyang44-yyh` may create the issue and command or act as either workflow
actor. The repository must remain the public
`yinghanyang44-yyh/PUBLIC_MATLAB_CI_SMOKE`, with default branch `main`.
The command accepts no options, arbitrary URLs, paths, shell fragments, or
alternate repositories. It cannot target `main`, tags, or non-`lab/` refs.

## Admission and proof

A read-only-contents admission job verifies the owner request, the original
owner input issue and source comment, and the original module bot claim. Each
comment must belong to its expected issue. The original runtime must be a
successful, first-attempt `PUBLIC_MATLAB_MODULE_V1` issue-comment run. Both jobs,
actual candidate execution, receipt binding, and artifact upload must have
succeeded. Failed, skipped, cancelled, expired, missing, or ambiguous evidence
blocks V1 cleanup; retain that branch and handle it manually.

The exact immutable artifact is downloaded with bounded size. GitHub's token is
never forwarded to artifact storage. ZIP members are inspected as data, with no
extraction or execution; only five fixed JSON evidence files are retained.
The archive hash, source-manifest hash, verified receipt hash, raw MATLAB
receipt, all required runtime gates, original owner input hashes, and actual
module SHA must agree. The original infrastructure SHA is checked against the
original run. It is distinct from the current cleanup infrastructure SHA.
The module commit's parent and eleven-file source manifest are independently
rechecked through GitHub's read-only API.

A one-use cleanup claim is placed on the **original module issue** and binds the
cleanup issue, source comment, run, exact module identity, and frozen evidence.
A second cleanup issue cannot reuse that original run. A claim is consumed even
if a later operation fails. Administrators who can change bot comments or
trusted main code are inside the trust boundary; the marker is not protection
against a malicious repository administrator.

## Narrow deletion and the remaining race

The separate short write job receives only ephemeral `contents: write`,
`actions: read`, and `issues: read` permissions. It checks the evidence and live
owner approval again, requires an unprotected branch at the approved SHA, and
requires that no other repository workflow is active. A final exact-ref GET is
immediately followed by **one normal REST DELETE** of that exact branch.
No force push, force-with-lease, ref update, default-branch change, credential
configuration, private transport, artifact deletion, or source execution occurs.

GitHub's REST delete-reference endpoint has **no expected-SHA compare-and-delete
parameter**. Therefore the GET and DELETE are not atomic: an external writer
could move the branch in the small interval between them. The active-workflow
check is also a snapshot and cannot prevent a later writer. Do not mutate the
approved task branch during cleanup. The workflow reports this residual risk
explicitly and never claims atomic CAS or locking. Any observed SHA mismatch,
protected status, changed approval, active workflow, or uncertain evidence stops
before deletion.

Only HTTP 204 is accepted for deletion. A single subsequent GET must confirm
404 before the receipt claims success. Network uncertainty does not trigger a
second DELETE. Do not rerun the workflow or manually repeat a consumed request;
inspect the retained result and actual branch state first. This workflow has no
automatic retry or fallback. It removes a branch reference only; it does not
purge public Git objects, history, artifacts, or third-party copies, and is not
a privacy-erasure mechanism.

Both jobs always retain their evidence for 30 days. Success is reported in
`cleanup-receipt.json` as `DELETED_AND_ABSENCE_CONFIRMED`. Owner private-copy
attestation and public runtime PASS remain explicitly separate. The original
runtime receipt and its `PUBLIC_BRANCH_CLEANUP: NOT_RUN` historical entry are
not rewritten; the new cleanup receipt provides the later event.

## Local checks and execution status

Run `python3 -B -m unittest discover -s tests/module_cleanup -p 'test_*.py' -v`.
Tests use mocked API calls and local artifact data; they do not delete GitHub
branches. Hosted cleanup is NOT_RUN until the owner deliberately posts a valid
command after remote private-copy verification. Publishing these four files
alone does not request deletion.

Official references:
- [GitHub delete-reference API](https://docs.github.com/en/rest/git/refs#delete-a-reference)
- [GitHub artifact download API](https://docs.github.com/en/rest/actions/artifacts#download-an-artifact)
- [GitHub workflow token permissions](https://docs.github.com/en/actions/security-for-github-actions/security-guides/automatic-token-authentication)
