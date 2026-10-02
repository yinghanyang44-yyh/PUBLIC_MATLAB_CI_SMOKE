"""Fail-closed owner authorization and one-use issue claim; no MATLAB execution."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

OWNER = 'yinghanyang44-yyh'
REPOSITORY = OWNER + '/PUBLIC_MATLAB_CI_SMOKE'
REF = 'refs/heads/main'
CLAIM_MARKER = '<!-- public-matlab-smoke-v1-claim -->'
COMMAND = re.compile(r'/matlab-smoke run ([0-9a-f]{40})', re.ASCII)


class AuthorizationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AuthorizationError(message)


def authorize(event: dict, context: dict) -> dict:
    """Validate data without evaluating any issue text or calling external tools."""
    repository = event.get('repository', {})
    require(context.get('repository') == REPOSITORY and repository.get('full_name') == REPOSITORY,
            'Repository does not match the public smoke repository.')
    require(repository.get('private') is False and repository.get('default_branch') == 'main',
            'Repository must be public with default branch main.')
    require(repository.get('owner', {}).get('login') == OWNER, 'Repository owner mismatch.')
    require(context.get('actor') == OWNER and context.get('triggering_actor') == OWNER and
            event.get('sender', {}).get('login') == OWNER, 'Owner identity checks failed.')
    require(context.get('attempt') == '1', 'Only the first run attempt is allowed.')
    require(context.get('ref') == REF, 'Only the main default-branch ref is allowed.')
    sha = context.get('sha', '')
    require(re.fullmatch(r'[0-9a-f]{40}', sha) is not None, 'Invalid event commit SHA.')
    require(context.get('server_url') == 'https://github.com' and
            context.get('api_url') == 'https://api.github.com', 'Unexpected GitHub service endpoint.')
    result = {'event': context.get('event'), 'repository': REPOSITORY, 'actor': OWNER,
              'ref': REF, 'sha': sha, 'attempt': '1', 'run_id': context.get('run_id')}
    if context.get('event') == 'workflow_dispatch':
        inputs = event.get('inputs', {})
        require(inputs.get('confirmation') == 'RUN_PUBLIC_MATLAB_CI_SMOKE', 'Manual confirmation mismatch.')
        require(inputs.get('expected_commit') == sha, 'Reviewed SHA differs from the dispatched SHA.')
        return result
    require(context.get('event') == 'issue_comment' and event.get('action') == 'created',
            'Only a manual dispatch or newly created owner issue command is allowed.')
    issue = event.get('issue', {})
    comment = event.get('comment', {})
    require('pull_request' not in issue, 'Pull request comments are not accepted.')
    require(issue.get('state') == 'open' and issue.get('user', {}).get('login') == OWNER,
            'The issue must be open and created by the repository owner.')
    require(isinstance(issue.get('title'), str) and issue['title'].startswith('[MATLAB_SMOKE] '),
            'The issue title must start with [MATLAB_SMOKE] followed by a space.')
    require(comment.get('user', {}).get('login') == OWNER, 'Comment author must be the owner.')
    body = comment.get('body', '')
    match = COMMAND.fullmatch(body) if isinstance(body, str) else None
    require(match is not None, 'Expected exactly /matlab-smoke run followed by a lowercase full SHA.')
    require(match.group(1) == sha, 'Comment SHA must equal the event default-branch head SHA.')
    require(type(issue.get('number')) is int and issue['number'] > 0 and
            type(comment.get('id')) is int and comment['id'] > 0, 'Invalid issue or comment identifier.')
    result.update(issue_number=issue['number'], source_comment_id=comment['id'])
    return result


class GitHubAPI:
    def __init__(self, token: str):
        require(bool(token), 'The narrowly scoped job token is missing.')
        self._token = token

    def request(self, method: str, path: str, payload: dict | None = None):
        # Fixed endpoint; untrusted text cannot choose an URL or a command.
        data = None if payload is None else json.dumps(payload).encode('utf-8')
        request = Request('https://api.github.com' + path, data=data, method=method,
                          headers={'Authorization': 'Bearer ' + self._token,
                                   'Accept': 'application/vnd.github+json',
                                   'X-GitHub-Api-Version': '2022-11-28',
                                   'Content-Type': 'application/json',
                                   'User-Agent': 'public-matlab-smoke-authorization'})
        try:
            with urlopen(request, timeout=20) as response:
                return json.load(response)
        except HTTPError as error:
            raise AuthorizationError(f'GitHub authorization API failed with HTTP {error.code}; no retry.') from None
        except (URLError, TimeoutError, json.JSONDecodeError):
            raise AuthorizationError('GitHub authorization API outcome is uncertain; no retry or MATLAB execution.') from None


def verify_current_repository(api) -> None:
    repository = api.request('GET', f'/repos/{REPOSITORY}')
    require(repository.get('full_name') == REPOSITORY and repository.get('private') is False and
            repository.get('default_branch') == 'main' and
            repository.get('owner', {}).get('login') == OWNER,
            'Current repository identity, public visibility or default branch changed.')


def verify_live_issue(authorization: dict, api) -> None:
    base = f"/repos/{REPOSITORY}/issues/{authorization['issue_number']}"
    current = api.request('GET', base)
    require('pull_request' not in current and current.get('state') == 'open' and
            current.get('user', {}).get('login') == OWNER and
            str(current.get('title', '')).startswith('[MATLAB_SMOKE] '),
            'Issue is no longer an eligible open owner smoke issue.')
    comment = api.request('GET', f"/repos/{REPOSITORY}/issues/comments/{authorization['source_comment_id']}")
    require(comment.get('user', {}).get('login') == OWNER and
            comment.get('body') == '/matlab-smoke run ' + authorization['sha'],
            'The original owner command was changed or is no longer valid.')


def claim_issue(authorization: dict, api) -> dict:
    """One persistent claim per issue. Workflow-wide concurrency serializes callers."""
    number = authorization['issue_number']
    base = f'/repos/{REPOSITORY}/issues/{number}'
    verify_live_issue(authorization, api)
    # Bounded reads; an unexpectedly long conversation fails closed.
    for page in range(1, 11):
        comments = api.request('GET', base + f'/comments?per_page=100&page={page}')
        require(isinstance(comments, list), 'Unexpected comments response.')
        for comment in comments:
            author = comment.get('user', {})
            if (author.get('login') == 'github-actions[bot]' and author.get('type') == 'Bot'
                    and CLAIM_MARKER in str(comment.get('body', ''))):
                raise AuthorizationError('This issue has already been claimed. No repeat execution is allowed.')
        if len(comments) < 100:
            break
    else:
        raise AuthorizationError('Comment history exceeds the bounded claim scan; no execution.')
    body = (CLAIM_MARKER + '\nOwner-authorized public MATLAB smoke claimed once.\n'
            f"Commit: {authorization['sha']}\nRun: {authorization['run_id']}\n"
            f"Source command comment: {authorization['source_comment_id']}\n"
            'Automatic retries are disabled. This issue cannot start another run, even if this run fails.')
    # Deliberately no retry: the POST may have succeeded even if its response is lost.
    claim = api.request('POST', base + '/comments', {'body': body})
    require(type(claim.get('id')) is int and claim['id'] > 0, 'Claim creation was not confirmed; no execution.')
    return {'claim_comment_id': claim['id'], 'issue_number': number,
            'source_comment_id': authorization['source_comment_id'], 'sha': authorization['sha']}


def main() -> int:
    artifact_dir = Path(os.environ['MATLAB_CI_ARTIFACT_DIR'])
    artifact_dir.mkdir(parents=True, exist_ok=True)
    status = artifact_dir / 'status.txt'
    status.write_text('AWAITING_OWNER_AUTHORIZATION\n', encoding='utf-8')
    context = {name: os.environ.get(variable, '') for name, variable in {
        'event':'GITHUB_EVENT_NAME', 'repository':'GITHUB_REPOSITORY', 'actor':'GITHUB_ACTOR',
        'triggering_actor':'GITHUB_TRIGGERING_ACTOR', 'attempt':'GITHUB_RUN_ATTEMPT',
        'ref':'GITHUB_REF', 'sha':'GITHUB_SHA', 'run_id':'GITHUB_RUN_ID',
        'server_url':'GITHUB_SERVER_URL', 'api_url':'GITHUB_API_URL'}.items()}
    try:
        with open(os.environ['GITHUB_EVENT_PATH'], encoding='utf-8') as file:
            event = json.load(file)
        authorization = authorize(event, context)
        api = GitHubAPI(os.environ.get('GH_TOKEN', ''))
        verify_current_repository(api)
        require(sys.argv[1:] in ([], ['--verify-execution']), 'Unexpected authorization mode.')
        execution_check = sys.argv[1:] == ['--verify-execution']
        if execution_check:
            admitted = json.loads((artifact_dir/'dispatch.json').read_text(encoding='utf-8'))
            require(admitted == authorization, 'Restored authorization does not match this event/run.')
            if authorization['event'] == 'issue_comment':
                verify_live_issue(authorization, api)
                claim = json.loads((artifact_dir/'claim.json').read_text(encoding='utf-8'))
                require(type(claim.get('claim_comment_id')) is int and
                        claim.get('issue_number') == authorization['issue_number'] and
                        claim.get('sha') == authorization['sha'], 'Claim evidence mismatch.')
                saved = api.request('GET', f"/repos/{REPOSITORY}/issues/comments/{claim['claim_comment_id']}")
                author = saved.get('user', {})
                body = str(saved.get('body', ''))
                require(author.get('login') == 'github-actions[bot]' and author.get('type') == 'Bot' and
                        CLAIM_MARKER in body and f"Commit: {authorization['sha']}" in body and
                        f"Run: {authorization['run_id']}" in body, 'The one-use claim was removed or changed.')
            (artifact_dir/'execution-authorization.json').write_text(json.dumps(authorization, indent=2)+'\n', encoding='utf-8')
        else:
            if authorization['event'] == 'issue_comment':
                claim = claim_issue(authorization, api)
                (artifact_dir/'claim.json').write_text(json.dumps(claim, indent=2)+'\n', encoding='utf-8')
            (artifact_dir/'dispatch.json').write_text(json.dumps(authorization, indent=2)+'\n', encoding='utf-8')
            with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as file:
                file.write(f"sha={authorization['sha']}\n")
        status.write_text('OWNER_AUTHORIZED_PREFLIGHT_PENDING\n', encoding='utf-8')
        print('Owner authorization confirmed for one immutable public smoke run.')
        return 0
    except (AuthorizationError, OSError, ValueError) as error:
        # Report only our bounded errors or the exception type; never dump event/env/token contents.
        message = str(error) if isinstance(error, AuthorizationError) else type(error).__name__
        status.write_text('AUTHORIZATION_BLOCKED\n', encoding='utf-8')
        (artifact_dir/'authorization-error.txt').write_text(message+'\n', encoding='utf-8')
        print('Authorization blocked: ' + message, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
