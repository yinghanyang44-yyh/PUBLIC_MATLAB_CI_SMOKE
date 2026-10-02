"""Owner-attested, PASS-only cleanup of one archived public lab branch.

REST deletion is deliberately NOT an atomic compare-and-delete. The exact ref is
read immediately before one DELETE request; a concurrent writer can race it.
"""
from __future__ import annotations
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zipfile import ZipFile, BadZipFile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from module_gate import (OWNER, REPOSITORY, require, encoded, context_from_env,
    claim_body, fetch_candidate, validate_runtime_receipt)
from module_contract import digest, strict_json, exact_keys, ORACLE_FILES

COMMAND = re.compile(r'/matlab-module cleanup ([0-9a-f]{40}) (lab/[a-z0-9][a-z0-9-]{0,63}-[0-9]{1,20})')
MARKER = '<!-- public-matlab-module-cleanup-v1-claim -->'
WORKFLOW = '.github/workflows/public-matlab-module-v1.yml'
BUNDLE_FILES = frozenset({'admission.json', 'source-manifest.json', 'matlab-receipt.json',
                          'verified-module-receipt.json', 'step-outcomes.json'})
MAX_ZIP = 64 * 1024 * 1024
ROOT = '/repos/' + REPOSITORY


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class API:
    """Bounded, no-retry GitHub requests; credentials never follow redirects."""
    def __init__(self, token):
        require(bool(token), 'Missing scoped job token')
        self.token = token
        self.opener = build_opener(NoRedirect())

    def open(self, path, method='GET', payload=None):
        require(path.startswith(ROOT + '/') or path == ROOT, 'Unexpected API target')
        return self.opener.open(Request('https://api.github.com' + path,
            data=None if payload is None else encoded(payload), method=method,
            headers={'Authorization': 'Bearer ' + self.token,
                     'Accept': 'application/vnd.github+json',
                     'X-GitHub-Api-Version': '2022-11-28',
                     'Content-Type': 'application/json',
                     'User-Agent': 'public-matlab-module-cleanup-v1'}), timeout=20)

    def request(self, method, path, payload=None, missing_ok=False):
        try:
            with self.open(path, method, payload) as response:
                if method == 'DELETE':
                    require(response.status == 204, 'Deletion response is uncertain; no retry')
                    return None
                raw = response.read(8 * 1024 * 1024 + 1)
                require(len(raw) <= 8 * 1024 * 1024, 'API response exceeds bound')
                return strict_json(raw)
        except HTTPError as error:
            if error.code == 404 and missing_ok and method == 'GET':
                return None
            raise ValueError(f'GitHub API HTTP {error.code}; no retry') from None
        except (URLError, TimeoutError, OSError):
            raise ValueError('GitHub API result uncertain; no retry') from None

    def artifact_zip(self, artifact_id):
        try:
            try:
                with self.open(f'{ROOT}/actions/artifacts/{artifact_id}/zip'):
                    raise ValueError('Expected a bounded artifact redirect')
            except HTTPError as error:
                require(error.code == 302, 'Artifact redirect unavailable; no retry')
                location = error.headers.get('Location', '')
            parsed = urlsplit(location)
            require(parsed.scheme == 'https' and parsed.port in (None, 443) and
                    not parsed.username and not parsed.password and not parsed.fragment and
                    parsed.hostname and (parsed.hostname.endswith('.blob.core.windows.net') or
                                         parsed.hostname.endswith('.githubusercontent.com')),
                    'Unexpected artifact storage endpoint')
            # New request intentionally has NO Authorization header and no redirects.
            with self.opener.open(Request(location, headers={'User-Agent': 'public-module-cleanup-v1'}), timeout=30) as response:
                data = response.read(MAX_ZIP + 1)
            require(len(data) <= MAX_ZIP, 'Artifact ZIP exceeds bound')
            return data
        except (HTTPError, URLError, TimeoutError, OSError):
            raise ValueError('Artifact download unavailable or uncertain; no retry') from None


def positive(value):
    return type(value) is int and 0 < value < 10**20


def hash256(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def validate_request(event, context):
    repo = event.get('repository', {})
    require(context.get('repository') == REPOSITORY and repo.get('full_name') == REPOSITORY and
            repo.get('private') is False and repo.get('default_branch') == 'main' and
            repo.get('owner', {}).get('login') == OWNER, 'Wrong public repository identity')
    require(context.get('actor') == OWNER and context.get('triggering_actor') == OWNER and
            event.get('sender', {}).get('login') == OWNER, 'Only the owner may request cleanup')
    require(context.get('attempt') == '1' and context.get('event') == 'issue_comment' and
            event.get('action') == 'created', 'Only a new first-attempt owner comment is accepted')
    require(context.get('ref') == 'refs/heads/main' and re.fullmatch('[0-9a-f]{40}', context.get('sha', '')) and
            re.fullmatch('[1-9][0-9]{0,19}', context.get('run_id', '')), 'Invalid trusted run identity')
    require(context.get('server_url') == 'https://github.com' and context.get('api_url') == 'https://api.github.com',
            'Unexpected GitHub endpoint')
    issue, comment = event.get('issue', {}), event.get('comment', {})
    require('pull_request' not in issue and issue.get('state') == 'open' and
            issue.get('user', {}).get('login') == OWNER and
            str(issue.get('title', '')).startswith('[MATLAB_MODULE_CLEANUP] '), 'Open owner cleanup issue required')
    require(comment.get('user', {}).get('login') == OWNER and positive(comment.get('id')) and positive(issue.get('number')),
            'Invalid owner issue/comment identity')
    match = COMMAND.fullmatch(comment.get('body', ''))
    require(match is not None, 'Cleanup command must bind one full SHA and one lab branch')
    body = issue.get('body', '')
    require(isinstance(body, str) and 0 < len(body.encode()) <= 16384, 'Bounded owner JSON required')
    request = strict_json(body)
    exact_keys(request, {'schema', 'private_copy_verified', 'module_issue_number', 'module_run_id',
                        'runtime_artifact_id', 'runtime_artifact_sha256', 'source_manifest_sha256', 'verified_receipt_sha256'})
    require(request['schema'] == 'matlab-module-cleanup-request-v1' and request['private_copy_verified'] is True,
            'Owner must explicitly attest verified private copyback')
    require(positive(request['module_issue_number']) and request['module_issue_number'] != issue['number'] and
            positive(request['runtime_artifact_id']) and isinstance(request['module_run_id'], str) and
            re.fullmatch('[1-9][0-9]{0,19}', request['module_run_id']), 'Invalid original evidence identifiers')
    for key in ['runtime_artifact_sha256', 'source_manifest_sha256', 'verified_receipt_sha256']:
        require(hash256(request[key]), 'Invalid frozen evidence hash')
    return {'schema': 'public-module-cleanup-admission-v1', 'repository': REPOSITORY,
            'cleanup_infrastructure_sha': context['sha'], 'cleanup_run_id': context['run_id'],
            'cleanup_issue_number': issue['number'], 'cleanup_comment_id': comment['id'],
            'cleanup_issue_body_sha256': digest(body.encode()), 'module_sha': match.group(1),
            'module_branch': match.group(2), 'owner_attestation': request}


def verify_owner_live(item, api):
    repo = api.request('GET', ROOT)
    require(repo.get('full_name') == REPOSITORY and repo.get('private') is False and
            repo.get('default_branch') == 'main' and repo.get('owner', {}).get('login') == OWNER,
            'Live repository identity changed')
    issue = api.request('GET', f"{ROOT}/issues/{item['cleanup_issue_number']}")
    require('pull_request' not in issue and issue.get('state') == 'open' and
            issue.get('user', {}).get('login') == OWNER and
            str(issue.get('title', '')).startswith('[MATLAB_MODULE_CLEANUP] ') and
            digest(str(issue.get('body', '')).encode()) == item['cleanup_issue_body_sha256'],
            'Cleanup approval changed or was revoked')
    command = api.request('GET', f"{ROOT}/issues/comments/{item['cleanup_comment_id']}")
    require(command.get('user', {}).get('login') == OWNER and
            command.get('issue_url') == 'https://api.github.com' + f"{ROOT}/issues/{item['cleanup_issue_number']}" and
            command.get('body') == f"/matlab-module cleanup {item['module_sha']} {item['module_branch']}",
            'Cleanup source command changed')


def verify_run_artifact(item, api):
    owner = item['owner_attestation']
    run = api.request('GET', f"{ROOT}/actions/runs/{owner['module_run_id']}")
    require(str(run.get('id')) == owner['module_run_id'] and run.get('status') == 'completed' and
            run.get('conclusion') == 'success' and run.get('run_attempt') == 1 and
            run.get('event') == 'issue_comment' and run.get('path') == WORKFLOW and
            run.get('head_branch') == 'main' and run.get('actor', {}).get('login') == OWNER and
            run.get('triggering_actor', {}).get('login') == OWNER and
            run.get('repository', {}).get('full_name') == REPOSITORY and
            run.get('head_repository', {}).get('full_name') == REPOSITORY and
            positive(run.get('repository', {}).get('id')) and
            run.get('head_repository', {}).get('id') == run['repository']['id'] and
            re.fullmatch('[0-9a-f]{40}', str(run.get('head_sha', ''))), 'Original public module run is not an eligible PASS')
    jobs = api.request('GET', f"{ROOT}/actions/runs/{owner['module_run_id']}/attempts/1/jobs?per_page=100")
    require(jobs.get('total_count') == 2 and len(jobs.get('jobs', [])) == 2 and
            {job.get('name') for job in jobs['jobs']} == {'admit', 'module-matlab'} and
            all(job.get('conclusion') == 'success' for job in jobs['jobs']), 'Original required jobs did not pass')
    runtime_job = next(job for job in jobs['jobs'] if job['name'] == 'module-matlab')
    required_steps = {'Build and test the actual candidate core and S-function',
                      'Bind successful runtime receipt to unchanged candidate bytes',
                      'Retain runtime evidence on success or failure'}
    require(required_steps <= {step.get('name') for step in runtime_job.get('steps', []) if step.get('conclusion') == 'success'},
            'Original native execution, receipt binding or artifact retention did not pass')
    artifact = api.request('GET', f"{ROOT}/actions/artifacts/{owner['runtime_artifact_id']}")
    binding = artifact.get('workflow_run', {})
    require(artifact.get('id') == owner['runtime_artifact_id'] and artifact.get('expired') is False and
            artifact.get('name') == f"public-module-runtime-{owner['module_run_id']}-attempt-1" and
            type(artifact.get('size_in_bytes')) is int and 0 < artifact['size_in_bytes'] <= MAX_ZIP and
            binding.get('id') == run['id'] and binding.get('head_sha') == run.get('head_sha') and
            binding.get('head_branch') == 'main' and
            binding.get('repository_id') == run['repository'].get('id') and
            binding.get('head_repository_id') == run['repository'].get('id'), 'Artifact identity is not bound to the successful public run')
    if artifact.get('digest') is not None:
        require(artifact['digest'] == 'sha256:' + owner['runtime_artifact_sha256'], 'Artifact digest differs from owner-approved archive')
    return run, artifact


def read_bundle(zip_bytes, expected_hash):
    require(digest(zip_bytes) == expected_hash, 'Downloaded artifact differs from archived ZIP hash')
    result, names, total = {}, set(), 0
    try:
        with ZipFile(io.BytesIO(zip_bytes)) as archive:
            require(len(archive.infolist()) <= 500, 'Too many archive members')
            for info in archive.infolist():
                name = info.filename
                pure = PurePosixPath(name)
                require(name == info.orig_filename and '\0' not in name and name not in names and '\\' not in name and not pure.is_absolute() and
                        all(part not in {'', '.', '..'} for part in name.rstrip('/').split('/')),
                        'Archive contains duplicate or unsafe member paths')
                names.add(name)
                mode = (info.external_attr >> 16) & 0o170000
                require(mode in {0, stat.S_IFREG, stat.S_IFDIR}, 'Archive contains a special member')
                total += info.file_size
                require(total <= 128 * 1024 * 1024 and info.file_size <= 32 * 1024 * 1024,
                        'Archive expansion exceeds bound')
                if name in BUNDLE_FILES:
                    require(not info.is_dir() and info.file_size <= 2 * 1024 * 1024, 'Receipt member exceeds bound')
                    result[name] = archive.read(info)
    except (BadZipFile, RuntimeError, OSError):
        raise ValueError('Invalid artifact archive') from None
    exact_keys(result, BUNDLE_FILES)
    return result


def verify_bundle(item, bundle, run):
    exact_keys(bundle, BUNDLE_FILES)
    owner = item['owner_attestation']
    admission = strict_json(bundle['admission.json'])
    manifest = strict_json(bundle['source-manifest.json'])
    runtime = strict_json(bundle['matlab-receipt.json'])
    receipt = strict_json(bundle['verified-module-receipt.json'])
    require(digest(bundle['source-manifest.json']) == owner['source_manifest_sha256'] and
            digest(bundle['verified-module-receipt.json']) == owner['verified_receipt_sha256'], 'Frozen source/receipt hash mismatch')
    require(admission.get('schema') == 'public-matlab-module-admission-v1' and admission.get('repository') == REPOSITORY and
            admission.get('module_sha') == item['module_sha'] and admission.get('module_branch') == item['module_branch'] and
            admission.get('issue_number') == owner['module_issue_number'] and admission.get('run_id') == owner['module_run_id'] and
            admission.get('run_attempt') == '1' and admission.get('infrastructure_sha') == run.get('head_sha') and
            admission.get('module_parent_sha') == admission.get('infrastructure_sha') and
            admission.get('source_manifest_sha256') == owner['source_manifest_sha256'] and
            positive(admission.get('source_comment_id')) and positive(admission.get('claim_comment_id')),
            'Admission does not bind the selected module and successful run')
    exact_keys(admission['inputs_sha256'], ORACLE_FILES)
    require(all(hash256(value) for value in admission['inputs_sha256'].values()), 'Invalid original oracle hashes')
    validate_runtime_receipt(runtime, admission, bundle['admission.json'], bundle['source-manifest.json'])
    require(receipt.get('schema') == 'public-matlab-module-verified-receipt-v1' and receipt.get('status') == 'PASS' and
            receipt.get('admission') == admission and receipt.get('source_manifest') == manifest and
            receipt.get('runtime_receipt') == runtime and receipt.get('runtime_receipt_sha256') == digest(bundle['matlab-receipt.json']),
            'Verified receipt does not match original raw evidence')
    outcomes = strict_json(bundle['step-outcomes.json'])
    require(all(outcomes.get(key) == 'success' for key in ['identity', 'execution_identity', 'platform', 'setup', 'matlab', 'receipt']),
            'Original final step evidence is not PASS')
    return admission, manifest


def verify_original(admission, manifest, api, inspect_source=True):
    issue = api.request('GET', f"{ROOT}/issues/{admission['issue_number']}")
    require('pull_request' not in issue and issue.get('user', {}).get('login') == OWNER and
            str(issue.get('title', '')).startswith('[MATLAB_MODULE] ') and
            digest(str(issue.get('body', '')).encode()) == admission['issue_body_sha256'], 'Original owner approval changed')
    original = strict_json(issue['body']); exact_keys(original, {'schema', 'frozen_inputs'})
    require(original['schema'] == 'matlab-module-request-v1' and original['frozen_inputs'] == admission['inputs_sha256'],
            'Original frozen inputs changed')
    for comment_id, body, login, kind in [
        (admission['source_comment_id'], f"/matlab-module run {admission['module_sha']} {admission['module_branch']}", OWNER, 'User'),
        (admission['claim_comment_id'], claim_body(admission), 'github-actions[bot]', 'Bot')]:
        comment = api.request('GET', f'{ROOT}/issues/comments/{comment_id}')
        require(comment.get('user', {}).get('login') == login and comment.get('user', {}).get('type') == kind and
                comment.get('issue_url') == 'https://api.github.com' + f"{ROOT}/issues/{admission['issue_number']}" and
                comment.get('body') == body, 'Original command or one-use claim changed')
    if inspect_source:
        _, actual = fetch_candidate(admission, api)
        require(actual == manifest, 'Live immutable module source differs from archived manifest')


def cleanup_claim_body(item):
    binding = {key: value for key, value in item.items() if key != 'cleanup_claim_comment_id'}
    return MARKER + '\nOne archived task-branch cleanup attempt claimed. No retries.\n' + encoded(binding).decode()


def scan_claims(item, api):
    issue = item['owner_attestation']['module_issue_number']
    claims = []
    for page in range(1, 11):
        comments = api.request('GET', f'{ROOT}/issues/{issue}/comments?per_page=100&page={page}')
        require(isinstance(comments, list), 'Unexpected original issue history')
        claims.extend(comment for comment in comments if comment.get('user', {}).get('login') == 'github-actions[bot]' and
                      comment.get('user', {}).get('type') == 'Bot' and MARKER in str(comment.get('body', '')))
        if len(comments) < 100:
            return claims
    raise ValueError('Original issue history exceeds bounded claim scan')


def claim_once(item, api):
    require(not scan_claims(item, api), 'Original module issue already consumed by cleanup')
    claim = api.request('POST', f"{ROOT}/issues/{item['owner_attestation']['module_issue_number']}/comments",
                        {'body': cleanup_claim_body(item)})
    require(positive(claim.get('id')), 'Cleanup claim uncertain; no deletion')
    return claim['id']


def verify_claim(item, api):
    claims = scan_claims(item, api)
    require(len(claims) == 1 and claims[0].get('id') == item['cleanup_claim_comment_id'] and
            claims[0].get('body') == cleanup_claim_body(item), 'Cleanup claim changed or duplicated')


def verify_branch_and_quiescence(item, api):
    branch = api.request('GET', f"{ROOT}/branches/{quote(item['module_branch'], safe='')}")
    require(branch.get('name') == item['module_branch'] and branch.get('protected') is False and
            branch.get('commit', {}).get('sha') == item['module_sha'], 'Task branch moved or is protected')
    # Conservative V1: no other active repository workflow, even for another task.
    for status in ['queued', 'in_progress', 'waiting', 'pending', 'requested']:
        runs = api.request('GET', f'{ROOT}/actions/runs?status={status}&per_page=100')
        require(type(runs.get('total_count')) is int and runs['total_count'] <= 100 and
                len(runs.get('workflow_runs', [])) == runs['total_count'], 'Active run inventory incomplete')
        require(all(str(run.get('id')) == item['cleanup_run_id'] for run in runs['workflow_runs']),
                'Another repository workflow is active; cleanup stops')


def delete_once(item, api):
    """Last API operation before DELETE is the exact-ref SHA check (not a CAS)."""
    ref = 'refs/heads/' + item['module_branch']
    current = api.request('GET', f"{ROOT}/git/ref/heads/{item['module_branch']}")
    require(current.get('ref') == ref and current.get('object', {}).get('type') == 'commit' and
            current['object'].get('sha') == item['module_sha'], 'Final exact branch head differs or is unknown')
    api.request('DELETE', f"{ROOT}/git/refs/heads/{item['module_branch']}")
    require(api.request('GET', f"{ROOT}/git/ref/heads/{item['module_branch']}", missing_ok=True) is None,
            'Deletion is not confirmed; do not retry')


def main():
    mode = sys.argv[1] if len(sys.argv) == 2 else ''
    require(mode in {'admit', 'delete'}, 'Expected a fixed cleanup mode')
    evidence = Path(os.environ['MATLAB_CLEANUP_EVIDENCE_DIR']); evidence.mkdir(parents=True, exist_ok=True)
    try:
        expected = validate_request(strict_json(Path(os.environ['GITHUB_EVENT_PATH']).read_bytes()), context_from_env())
        api = API(os.environ.get('GH_TOKEN', ''))
        verify_owner_live(expected, api)
        run, artifact = verify_run_artifact(expected, api)
        if mode == 'admit':
            bundle = read_bundle(api.artifact_zip(expected['owner_attestation']['runtime_artifact_id']),
                                 expected['owner_attestation']['runtime_artifact_sha256'])
            for name, raw in bundle.items():
                (evidence / name).write_bytes(raw)
            admission, manifest = verify_bundle(expected, bundle, run)
            verify_original(admission, manifest, api)
            verify_branch_and_quiescence(expected, api)
            verify_owner_live(expected, api)
            expected['cleanup_claim_comment_id'] = claim_once(expected, api)
            (evidence / 'cleanup-admission.json').write_bytes(encoded(expected))
            (evidence / 'original-artifact-metadata.json').write_bytes(encoded(artifact))
            print('Cleanup admitted; deletion requires the separate isolated write job.')
        else:
            restored = strict_json((evidence / 'cleanup-admission.json').read_bytes())
            exact_keys(restored, set(expected) | {'cleanup_claim_comment_id'})
            require(all(restored.get(key) == value for key, value in expected.items()) and
                    positive(restored.get('cleanup_claim_comment_id')), 'Restored cleanup admission differs from this run')
            bundle = {name: (evidence / name).read_bytes() for name in BUNDLE_FILES}
            admission, manifest = verify_bundle(restored, bundle, run)
            verify_original(admission, manifest, api)
            verify_claim(restored, api)
            verify_branch_and_quiescence(restored, api)
            verify_owner_live(restored, api)
            (evidence / 'cleanup-operation.json').write_bytes(encoded({'status': 'DELETE_ATTEMPT_MAY_HAVE_BEEN_SENT_OUTCOME_UNCONFIRMED',
                'module_sha': restored['module_sha'], 'module_branch': restored['module_branch'],
                'atomic_compare_and_delete': False, 'automatic_retry': False}))
            delete_once(restored, api)
            (evidence / 'cleanup-receipt.json').write_bytes(encoded({
                'schema': 'public-module-cleanup-receipt-v1', 'status': 'DELETED_AND_ABSENCE_CONFIRMED',
                'admission': restored, 'private_copy_evidence': 'Owner attestation; independently verified outside this public workflow',
                'public_runtime_status': 'PASS', 'atomic_compare_and_delete': False,
                'race_limitation': 'REST read then delete has an unavoidable concurrent-writer window',
                'automatic_retry': False, 'scope': 'Only the task branch reference was deleted; commits and artifacts were not deleted'}))
            print('Approved task branch deleted; a subsequent exact-ref lookup confirms absence.')
        return 0
    except (ValueError, KeyError, TypeError, OSError) as error:
        # Never include server response bodies, tokens, signed URLs or environments.
        message = str(error) if isinstance(error, ValueError) else type(error).__name__
        (evidence / ('cleanup-' + mode + '-error.txt')).write_text(message + '\n')
        print('Cleanup blocked or uncertain: ' + message, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
