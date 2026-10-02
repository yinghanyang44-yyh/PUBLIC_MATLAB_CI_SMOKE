"""Public task-branch admission, fixed-file materialization, and receipt binding."""
from __future__ import annotations
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Load only the trusted main-commit validator, never candidate Python or helpers.
sys.path.insert(0,str(Path(__file__).resolve().parent))
from module_contract import (FILES,ORACLE_FILES,MAX_BYTES,SOURCE_MAX_BYTES,digest,
    exact_keys,strict_json,validate_package_bytes,read_snapshot)

OWNER='yinghanyang44-yyh'
REPOSITORY=OWNER+'/PUBLIC_MATLAB_CI_SMOKE'
COMMAND=re.compile(r'/matlab-module run ([0-9a-f]{40}) (lab/[a-z0-9][a-z0-9-]{0,63}-[0-9]{1,20})')
CLAIM_MARKER='<!-- public-matlab-module-v1-claim -->'


def require(ok,message):
    if not ok: raise ValueError(message)


def encoded(value):
    return (json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n').encode()


class API:
    def __init__(self,token):
        require(bool(token),'Missing scoped GitHub job token')
        self.token=token
    def request(self,method,path,payload=None):
        request=Request('https://api.github.com'+path,
            data=None if payload is None else encoded(payload),method=method,
            headers={'Authorization':'Bearer '+self.token,'Accept':'application/vnd.github+json',
                     'X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/json',
                     'User-Agent':'public-matlab-module-v1'})
        try:
            with urlopen(request,timeout=20) as response:return json.load(response)
        except HTTPError as error:
            raise ValueError(f'GitHub API HTTP {error.code}; no retry') from None
        except (URLError,TimeoutError,json.JSONDecodeError):
            raise ValueError('GitHub API result uncertain; no retry or execution') from None


def validate_request(event,context):
    repository=event.get('repository',{})
    require(context.get('repository')==REPOSITORY and repository.get('full_name')==REPOSITORY,'Wrong repository')
    require(repository.get('private') is False and repository.get('default_branch')=='main' and
            repository.get('owner',{}).get('login')==OWNER,'Repository must be owner-held public main')
    require(context.get('actor')==OWNER and context.get('triggering_actor')==OWNER and
            event.get('sender',{}).get('login')==OWNER,'Owner identity mismatch')
    require(context.get('attempt')=='1','Only first attempt is allowed')
    require(context.get('event')=='issue_comment' and event.get('action')=='created','Only a new owner issue command is accepted')
    require(context.get('ref')=='refs/heads/main' and re.fullmatch('[0-9a-f]{40}',context.get('sha','')),'Trusted main identity missing')
    require(context.get('server_url')=='https://github.com' and context.get('api_url')=='https://api.github.com','Unexpected GitHub endpoint')
    issue=event.get('issue',{});comment=event.get('comment',{})
    require('pull_request' not in issue and issue.get('state')=='open' and
            issue.get('user',{}).get('login')==OWNER,'An open owner-created issue is required')
    require(str(issue.get('title','')).startswith('[MATLAB_MODULE] '),'Required issue title prefix missing')
    require(comment.get('user',{}).get('login')==OWNER,'Comment author is not owner')
    match=COMMAND.fullmatch(comment.get('body',''))
    require(match is not None,'Command must be exact module SHA plus lab task branch')
    require(type(issue.get('number')) is int and issue['number']>0 and type(comment.get('id')) is int and comment['id']>0,'Invalid issue/comment identifier')
    body=issue.get('body','')
    require(isinstance(body,str) and 0<len(body.encode())<=16384,'Issue body must be bounded strict JSON')
    request=strict_json(body);exact_keys(request,{'schema','frozen_inputs'})
    require(request['schema']=='matlab-module-request-v1','Wrong owner request schema')
    exact_keys(request['frozen_inputs'],ORACLE_FILES)
    for value in request['frozen_inputs'].values():require(isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value),'Invalid frozen input hash')
    return {'schema':'public-matlab-module-admission-v1','repository':REPOSITORY,
            'infrastructure_sha':context['sha'],'module_sha':match.group(1),'module_branch':match.group(2),
            'module_parent_sha':context['sha'],'issue_number':issue['number'],'source_comment_id':comment['id'],
            'issue_body_sha256':digest(body.encode()),'inputs_sha256':request['frozen_inputs'],
            'run_id':context['run_id'],'run_attempt':context['attempt']}


def verify_live(admission,api):
    repository=api.request('GET',f'/repos/{REPOSITORY}')
    require(repository.get('full_name')==REPOSITORY and repository.get('private') is False and
            repository.get('owner',{}).get('login')==OWNER and repository.get('default_branch')=='main','Live repository identity changed')
    issue=api.request('GET',f"/repos/{REPOSITORY}/issues/{admission['issue_number']}")
    require('pull_request' not in issue and issue.get('state')=='open' and issue.get('user',{}).get('login')==OWNER and
            str(issue.get('title','')).startswith('[MATLAB_MODULE] '),'Live issue no longer eligible')
    require(digest(str(issue.get('body','')).encode())==admission['issue_body_sha256'],'Owner issue body/frozen input approval changed')
    comment=api.request('GET',f"/repos/{REPOSITORY}/issues/comments/{admission['source_comment_id']}")
    command=f"/matlab-module run {admission['module_sha']} {admission['module_branch']}"
    require(comment.get('user',{}).get('login')==OWNER and comment.get('body')==command,'Owner source command changed')
    ref=api.request('GET',f"/repos/{REPOSITORY}/git/ref/heads/{admission['module_branch']}")
    require(ref.get('ref')=='refs/heads/'+admission['module_branch'] and ref.get('object',{}).get('type')=='commit' and
            ref['object'].get('sha')==admission['module_sha'],'Task branch head differs from approved module SHA')


def tree_for(api,sha):
    commit=api.request('GET',f'/repos/{REPOSITORY}/git/commits/{sha}')
    require(commit.get('sha')==sha,'Commit identity mismatch')
    tree=api.request('GET',f"/repos/{REPOSITORY}/git/trees/{commit['tree']['sha']}?recursive=1")
    require(not tree.get('truncated') and isinstance(tree.get('tree'),list) and len(tree['tree'])<=3000,'Truncated or excessive tree')
    entries={}
    for item in tree['tree']:
        path=item['path']
        require(path not in entries,'Duplicate tree path')
        if item['type']!='tree':entries[path]=item
    return commit,entries


def fetch_candidate(admission,api):
    commit,candidate=tree_for(api,admission['module_sha'])
    require([p.get('sha') for p in commit.get('parents',[])]==[admission['infrastructure_sha']],
            'V1 task commit must be a single child of the frozen trusted main commit')
    _,baseline=tree_for(api,admission['infrastructure_sha'])
    changed={path for path in set(candidate)|set(baseline) if
             (candidate.get(path,{}).get('sha'),candidate.get(path,{}).get('mode'),candidate.get(path,{}).get('type')) !=
             (baseline.get(path,{}).get('sha'),baseline.get(path,{}).get('mode'),baseline.get(path,{}).get('type'))}
    require(changed and changed<=FILES,'Task branch changes paths outside the eleven-file contract')
    files={};manifest=[];total=0
    for path in sorted(FILES):
        entry=candidate.get(path,{})
        limit=MAX_BYTES if path.endswith('.csv') else SOURCE_MAX_BYTES
        if path=='contract/contract.json':limit=16384
        require(entry.get('type')=='blob' and entry.get('mode')=='100644' and type(entry.get('size')) is int and
                0<entry['size']<=limit,'Candidate path is absent, linked, executable, binary-sized or unsupported')
        total+=entry['size'];require(total<=MAX_BYTES,'Module package exceeds one MiB')
        blob=api.request('GET',f"/repos/{REPOSITORY}/git/blobs/{entry['sha']}")
        require(blob.get('encoding')=='base64','Unsupported blob encoding')
        data=base64.b64decode(''.join(blob['content'].split()),validate=True)
        identity=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
        require(len(data)==entry['size'] and identity==entry['sha'],'Blob contents do not match Git object identity')
        files[path]=data
        manifest.append({'path':path,'sha256':digest(data),'bytes':len(data),'git_blob_sha':entry['sha']})
    validate_package_bytes(files,admission['inputs_sha256'])
    return files,{'schema':'public-module-source-manifest-v1','module_sha':admission['module_sha'],'files':manifest}


def claim_body(admission):
    binding={key:admission[key] for key in ['module_sha','module_branch','infrastructure_sha','issue_number',
        'source_comment_id','issue_body_sha256','source_manifest_sha256','run_id']}
    return CLAIM_MARKER+'\nOne owner-authorized module run claimed. No automatic retries.\n'+encoded(binding).decode()


def claim_once(admission,api):
    base=f"/repos/{REPOSITORY}/issues/{admission['issue_number']}/comments"
    for page in range(1,11):
        comments=api.request('GET',base+f'?per_page=100&page={page}')
        require(isinstance(comments,list),'Unexpected issue history')
        for comment in comments:
            author=comment.get('user',{})
            require(not(author.get('login')=='github-actions[bot]' and author.get('type')=='Bot' and
                        CLAIM_MARKER in str(comment.get('body',''))),'Issue already consumed by a module run')
        if len(comments)<100:break
    else:raise ValueError('Issue history exceeds bounded claim scan')
    claim=api.request('POST',base,{'body':claim_body(admission)})
    require(type(claim.get('id')) is int and claim['id']>0,'Claim response uncertain; no execution')
    return claim['id']


def verify_snapshot(admission,staging):
    raw=(staging/'source-manifest.json').read_bytes()
    require(digest(raw)==admission['source_manifest_sha256'],'Source manifest hash changed')
    manifest=strict_json(raw);exact_keys(manifest,{'schema','module_sha','files'})
    require(manifest['schema']=='public-module-source-manifest-v1' and manifest['module_sha']==admission['module_sha'],'Source manifest binds the wrong module')
    entries={}
    for item in manifest['files']:
        exact_keys(item,{'path','sha256','bytes','git_blob_sha'})
        require(item['path'] not in entries,'Duplicate manifest path');entries[item['path']]=item
    exact_keys(entries,FILES)
    files=read_snapshot(staging/'module')
    for path,data in files.items():
        item=entries[path]
        require(len(data)==item['bytes'] and digest(data)==item['sha256'],'Materialized source differs from the admitted module')
        identity=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
        require(identity==item['git_blob_sha'],'Materialized Git blob identity mismatch')
    validate_package_bytes(files,admission['inputs_sha256'])
    return manifest


def validate_runtime_receipt(runtime,admission,admission_raw,manifest_raw):
    require(runtime.get('schema')=='public-matlab-module-runtime-receipt-v1' and runtime.get('status')=='PASS',
            'Public MATLAB runtime did not pass')
    require(runtime.get('module_sha')==admission['module_sha'] and
            runtime.get('infrastructure_sha')==admission['infrastructure_sha'],
            'Runtime receipt binds the infrastructure commit instead of the approved module')
    require(runtime.get('admission_json_verbatim')==admission_raw.decode() and
            runtime.get('source_manifest_json_verbatim')==manifest_raw.decode(),
            'Runtime receipt source bindings differ from admitted bytes')
    require(runtime.get('candidate_wrapper_execution')=='SIMULATION_PASSED' and
            runtime.get('candidate_matlab_helpers_executed') is False and
            runtime.get('candidate_test_sources_invoked_by_runner') is False,
            'Runtime execution scope differs from the fixed candidate-wrapper route')
    ledger=runtime.get('ledger',[]);by_name={}
    require(isinstance(ledger,list),'Runtime ledger is missing')
    for item in ledger:
        require(item.get('name') not in by_name,'Repeated runtime gate')
        by_name[item.get('name')]=item.get('status')
    required={'MATLAB_STARTUP','MODULE_RUNTIME_INPUTS','MATLAB_RELEASE','SIMULINK_AVAILABILITY',
        'MEX_COMPILER','DIRECT_CORE_MEX_BUILD','DIRECT_CORE_MEX','DIRECT_DETERMINISTIC_REPLAY',
        'DIRECT_ORACLE_EQUIVALENCE','SFUNCTION_BUILD','SFUNCTION_LOAD','SIMULINK_HARNESS',
        'CPP_SFUNCTION_EQUIVALENCE','MODULE_RUNTIME_DIAGNOSTICS','PUBLIC_MODULE_RUNTIME'}
    require(all(by_name.get(name)=='PASS' for name in required),'Required public runtime gate is not PASS')
    require(by_name.get('PUBLIC_MATLAB_MODULE_ACCEPTANCE')=='WAITING',
            'Public runtime must not self-certify full private/public acceptance')
    for name in ['CLAUDE_GENERATION','MODEL_REQUESTED','MODEL_ACTUAL','CPP_CORE_BUILD','CPP_UNIT_TEST',
                 'DETERMINISTIC_REPLAY','PRIVATE_PROMOTION','PRIVATE_PR','PUBLIC_BRANCH_CLEANUP',
                 'MAIN_CODEX_INTEGRATION','SYSTEM_VALIDATION','FORMAL_PROJECT_PROMOTION']:
        require(by_name.get(name)=='NOT_RUN','Public runtime claims an unobserved private or downstream gate')


def context_from_env():
    return {key:os.environ.get(variable,'') for key,variable in {
        'repository':'GITHUB_REPOSITORY','actor':'GITHUB_ACTOR','triggering_actor':'GITHUB_TRIGGERING_ACTOR',
        'attempt':'GITHUB_RUN_ATTEMPT','event':'GITHUB_EVENT_NAME','ref':'GITHUB_REF','sha':'GITHUB_SHA',
        'server_url':'GITHUB_SERVER_URL','api_url':'GITHUB_API_URL','run_id':'GITHUB_RUN_ID'}.items()}


def main():
    mode=sys.argv[1] if len(sys.argv)==2 else ''
    require(mode in {'admit','verify-before','verify-after'},'Expected one fixed module gate mode')
    staging=Path(os.environ['MATLAB_MODULE_STAGING_DIR'])
    evidence=Path(os.environ['MATLAB_MODULE_EVIDENCE_DIR'])
    evidence.mkdir(parents=True,exist_ok=True)
    try:
        context=context_from_env()
        event=strict_json(Path(os.environ['GITHUB_EVENT_PATH']).read_bytes())
        expected=validate_request(event,context)
        if mode=='admit':
            api=API(os.environ.get('GH_TOKEN',''));verify_live(expected,api)
            files,manifest=fetch_candidate(expected,api)
            raw=encoded(manifest);expected['source_manifest_sha256']=digest(raw)
            staging.mkdir(parents=True,exist_ok=True)
            target=staging/'module';target.mkdir()
            for relative,data in files.items():
                path=target/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
            (staging/'source-manifest.json').write_bytes(raw)
            # Recheck approval immediately before the one-use external claim.
            verify_live(expected,api)
            expected['claim_comment_id']=claim_once(expected,api)
            (staging/'admission.json').write_bytes(encoded(expected))
            verify_snapshot(expected,staging)
            print('Public module admission confirmed: '+expected['module_sha'])
        else:
            admission=strict_json((staging/'admission.json').read_bytes())
            for key,value in expected.items():require(admission.get(key)==value,'Restored admission differs from this run/event')
            manifest=verify_snapshot(admission,staging)
            if mode=='verify-before':
                api=API(os.environ.get('GH_TOKEN',''));verify_live(admission,api)
                saved=api.request('GET',f"/repos/{REPOSITORY}/issues/comments/{admission['claim_comment_id']}")
                author=saved.get('user',{})
                require(author.get('login')=='github-actions[bot]' and author.get('type')=='Bot' and
                        saved.get('body')==claim_body(admission),'One-use claim was removed or changed')
                (evidence/'admission.json').write_bytes((staging/'admission.json').read_bytes())
                (evidence/'source-manifest.json').write_bytes((staging/'source-manifest.json').read_bytes())
                print('Module branch, claim, owner inputs and actual source hashes verified before execution.')
            else:
                runtime=strict_json((evidence/'matlab-receipt.json').read_bytes())
                validate_runtime_receipt(runtime,admission,(staging/'admission.json').read_bytes(),
                                         (staging/'source-manifest.json').read_bytes())
                # Raw runtime report retained; trusted binding uses reverified admission.
                receipt={'schema':'public-matlab-module-verified-receipt-v1','status':'PASS',
                         'admission':admission,'source_manifest':manifest,'runtime_receipt':runtime,
                         'runtime_receipt_sha256':digest((evidence/'matlab-receipt.json').read_bytes()),
                         'helper_execution':'candidate MATLAB helpers and candidate unit sources were not executed by this public job',
                         'scope':'Public runtime only; private prerequisites and full module acceptance require separate evidence'}
                (evidence/'verified-module-receipt.json').write_bytes(encoded(receipt))
                print('Verified public runtime receipt bound to actual module SHA '+admission['module_sha'])
        return 0
    except (ValueError,OSError,KeyError,TypeError) as error:
        message=str(error) if isinstance(error,ValueError) else type(error).__name__
        (evidence/('module-gate-'+mode+'-error.txt')).write_text(message+'\n')
        print('Module gate blocked: '+message,file=sys.stderr)
        return 1


if __name__=='__main__':raise SystemExit(main())
