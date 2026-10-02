"""Offline cleanup admission, exact evidence and single REST deletion tests."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile, ZipInfo
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'ci/module_lab'))
import module_cleanup as cleanup
import module_gate as gate
spec = importlib.util.spec_from_file_location('route_test_helpers', ROOT / 'tests/module_lab/test_module_route.py')
route = importlib.util.module_from_spec(spec); spec.loader.exec_module(route)


def zip_bundle(bundle):
    target = io.BytesIO()
    with ZipFile(target, 'w') as archive:
        for name, raw in bundle.items():
            archive.writestr(name, raw)
    return target.getvalue()


class FakeAPI(route.FakeAPI):
    def __init__(self):
        super().__init__()
        self.original_event = copy.deepcopy(self.event)
        self.original_event['repository']['id'] = 7
        self.original_event['comment'].update(issue_url='https://api.github.com' + cleanup.ROOT + '/issues/9')
        self.original_event['comment']['user']['type'] = 'User'
        self.admission = gate.validate_request(self.event, self.context)
        _, manifest = gate.fetch_candidate(self.admission, self)
        self.admission.update(claim_comment_id=99, source_manifest_sha256=gate.digest(gate.encoded(manifest)))
        passes = ['MATLAB_STARTUP','MODULE_RUNTIME_INPUTS','MATLAB_RELEASE','SIMULINK_AVAILABILITY','MEX_COMPILER',
            'DIRECT_CORE_MEX_BUILD','DIRECT_CORE_MEX','DIRECT_DETERMINISTIC_REPLAY','DIRECT_ORACLE_EQUIVALENCE',
            'SFUNCTION_BUILD','SFUNCTION_LOAD','SIMULINK_HARNESS','CPP_SFUNCTION_EQUIVALENCE','MODULE_RUNTIME_DIAGNOSTICS','PUBLIC_MODULE_RUNTIME']
        untouched = ['CLAUDE_GENERATION','MODEL_REQUESTED','MODEL_ACTUAL','CPP_CORE_BUILD','CPP_UNIT_TEST','DETERMINISTIC_REPLAY',
            'PRIVATE_PROMOTION','PRIVATE_PR','PUBLIC_BRANCH_CLEANUP','MAIN_CODEX_INTEGRATION','SYSTEM_VALIDATION','FORMAL_PROJECT_PROMOTION']
        runtime = {'schema':'public-matlab-module-runtime-receipt-v1','status':'PASS', 'module_sha':route.MODULE,
            'infrastructure_sha':route.MAIN,'admission_json_verbatim':gate.encoded(self.admission).decode(),
            'source_manifest_json_verbatim':gate.encoded(manifest).decode(), 'candidate_wrapper_execution':'SIMULATION_PASSED',
            'candidate_matlab_helpers_executed':False,'candidate_test_sources_invoked_by_runner':False,
            'ledger':[{'name':name,'status':'PASS'} for name in passes] + [{'name':name,'status':'NOT_RUN'} for name in untouched] +
                     [{'name':'PUBLIC_MATLAB_MODULE_ACCEPTANCE','status':'WAITING'}],
            # MATLAB normalizes this convenience object. It is not authoritative.
            'admission':{'inputs_sha256':{'contract_contract_json':'normalized'}}}
        receipt = {'schema':'public-matlab-module-verified-receipt-v1','status':'PASS','admission':self.admission,
            'source_manifest':manifest,'runtime_receipt':runtime,'runtime_receipt_sha256':gate.digest(gate.encoded(runtime))}
        self.bundle = {'admission.json':gate.encoded(self.admission),'source-manifest.json':gate.encoded(manifest),
            'matlab-receipt.json':gate.encoded(runtime),'verified-module-receipt.json':gate.encoded(receipt),
            'step-outcomes.json':gate.encoded({key:'success' for key in ['identity','execution_identity','platform','setup','matlab','receipt']})}
        self.zip = zip_bundle(self.bundle)
        owner = {'schema':'matlab-module-cleanup-request-v1','private_copy_verified':True,'module_issue_number':9,
            'module_run_id':'123','runtime_artifact_id':456,'runtime_artifact_sha256':gate.digest(self.zip),
            'source_manifest_sha256':gate.digest(self.bundle['source-manifest.json']),
            'verified_receipt_sha256':gate.digest(self.bundle['verified-module-receipt.json'])}
        self.event = copy.deepcopy(self.original_event)
        self.event['issue'].update(number=10,title='[MATLAB_MODULE_CLEANUP] Generic fixture',body=gate.encoded(owner).decode())
        self.event['comment'].update(id=50, body=f'/matlab-module cleanup {route.MODULE} {route.BRANCH}',
            issue_url='https://api.github.com' + cleanup.ROOT + '/issues/10')
        self.context.update(sha='c'*40, run_id='999')
        self.item = cleanup.validate_request(self.event, self.context)
        self.run = {'id':123,'status':'completed','conclusion':'success','run_attempt':1,'event':'issue_comment',
            'path':cleanup.WORKFLOW,'head_branch':'main','head_sha':route.MAIN,'actor':{'login':gate.OWNER},
            'triggering_actor':{'login':gate.OWNER},'repository':{'full_name':gate.REPOSITORY,'id':7},
            'head_repository':{'full_name':gate.REPOSITORY,'id':7}}
        self.jobs = {'total_count':2,'jobs':[{'name':'admit','conclusion':'success'}, {'name':'module-matlab','conclusion':'success',
            'steps':[{'name':name,'conclusion':'success'} for name in ['Build and test the actual candidate core and S-function',
             'Bind successful runtime receipt to unchanged candidate bytes','Retain runtime evidence on success or failure']]}]}
        self.artifact = {'id':456,'expired':False,'name':'public-module-runtime-123-attempt-1','size_in_bytes':len(self.zip),
            'digest':'sha256:'+gate.digest(self.zip),'workflow_run':{'id':123,'head_sha':route.MAIN,'head_branch':'main',
                'repository_id':7,'head_repository_id':7}}
        self.comments = [{'id':99,'body':gate.claim_body(self.admission),'user':{'login':'github-actions[bot]','type':'Bot'},
            'issue_url':'https://api.github.com'+cleanup.ROOT+'/issues/9'}]
        self.protected = False; self.active_runs = []; self.deleted = False; self.uncertain_delete = False
        self.calls = []

    def request(self, method, path, payload=None, missing_ok=False):
        # Tree setup during base construction must use its original handler.
        if not hasattr(self, 'item'):
            return super().request(method, path, payload)
        self.calls.append((method, path, payload))
        if method == 'POST':
            if self.fail_post: raise ValueError('Uncertain claim')
            claim = {'id':100,'body':payload['body'],'user':{'login':'github-actions[bot]','type':'Bot'},
                'issue_url':'https://api.github.com'+cleanup.ROOT+'/issues/9'}
            self.comments.append(claim); return claim
        if method == 'DELETE':
            if self.uncertain_delete: raise ValueError('Uncertain deletion')
            self.deleted = True; return None
        if path == cleanup.ROOT: return self.event['repository']
        if path.endswith('/issues/10'): return self.event['issue']
        if path.endswith('/issues/9'): return self.original_event['issue']
        if path.endswith('/issues/comments/50'): return self.event['comment']
        if path.endswith('/issues/comments/40'): return self.original_event['comment']
        if path.endswith('/issues/comments/99'): return self.comments[0]
        if '/comments?' in path: return self.comments
        if '/actions/runs?' in path: return {'total_count':len(self.active_runs),'workflow_runs':self.active_runs}
        if path.endswith('/actions/runs/123'): return self.run
        if '/attempts/1/jobs?' in path: return self.jobs
        if path.endswith('/actions/artifacts/456'): return self.artifact
        if '/branches/' in path: return {'name':route.BRANCH,'protected':self.protected,'commit':{'sha':self.branch_sha}}
        if '/git/ref/heads/' in path:
            if self.deleted and missing_ok: return None
            return {'ref':'refs/heads/'+route.BRANCH,'object':{'type':'commit','sha':self.branch_sha}}
        return super().request(method, path, payload)

    def artifact_zip(self, artifact_id):
        assert artifact_id == 456
        return self.zip


class CleanupTests(unittest.TestCase):
    def test_valid_full_admit_then_delete_offline(self):
        api = FakeAPI()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); event = root/'event.json'; event.write_bytes(gate.encoded(api.event))
            env = {'MATLAB_CLEANUP_EVIDENCE_DIR':str(root/'evidence'),'GITHUB_EVENT_PATH':str(event),'GH_TOKEN':'fake-never-transmitted'}
            with patch.dict(os.environ, env), patch.object(cleanup, 'API', return_value=api), patch.object(cleanup, 'context_from_env', return_value=api.context):
                with patch.object(sys, 'argv', ['module_cleanup.py','admit']): self.assertEqual(cleanup.main(), 0)
                with patch.object(sys, 'argv', ['module_cleanup.py','delete']): self.assertEqual(cleanup.main(), 0)
            receipt = json.loads((root/'evidence/cleanup-receipt.json').read_text())
            self.assertEqual(receipt['status'],'DELETED_AND_ABSENCE_CONFIRMED')
            self.assertFalse(receipt['atomic_compare_and_delete'])
        self.assertEqual(sum(method=='POST' for method,_,_ in api.calls),1)
        self.assertEqual(sum(method=='DELETE' for method,_,_ in api.calls),1)

    def test_failed_delete_confirmation_preserves_honest_uncertainty(self):
        api = FakeAPI()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); event = root/'event.json'; event.write_bytes(gate.encoded(api.event))
            env = {'MATLAB_CLEANUP_EVIDENCE_DIR':str(root/'evidence'),'GITHUB_EVENT_PATH':str(event),'GH_TOKEN':'fake'}
            with patch.dict(os.environ, env), patch.object(cleanup, 'API', return_value=api), patch.object(cleanup, 'context_from_env', return_value=api.context):
                with patch.object(sys, 'argv', ['module_cleanup.py','admit']): self.assertEqual(cleanup.main(), 0)
                api.uncertain_delete = True
                with patch.object(sys, 'argv', ['module_cleanup.py','delete']): self.assertEqual(cleanup.main(), 1)
            operation=json.loads((root/'evidence/cleanup-operation.json').read_text())
            self.assertEqual(operation['status'],'DELETE_ATTEMPT_MAY_HAVE_BEEN_SENT_OUTCOME_UNCONFIRMED')
            self.assertFalse((root/'evidence/cleanup-receipt.json').exists())
            self.assertEqual(sum(method=='DELETE' for method,_,_ in api.calls),1)

    def test_owner_event_attempt_and_repository_are_exact(self):
        api=FakeAPI()
        for key,value in [('actor','other'),('triggering_actor','other'),('attempt','2'),('event','push'),('sha','bad'),('ref','refs/heads/lab/x-1')]:
            context=dict(api.context);context[key]=value
            with self.assertRaises(ValueError): cleanup.validate_request(api.event,context)
        for field,value in [('private',True),('default_branch','dev'),('full_name','other/repo')]:
            event=copy.deepcopy(api.event);event['repository'][field]=value
            with self.assertRaises(ValueError): cleanup.validate_request(event,api.context)
        for field,value in [('state','closed'),('pull_request',{})]:
            event=copy.deepcopy(api.event);event['issue'][field]=value
            with self.assertRaises(ValueError): cleanup.validate_request(event,api.context)

    def test_command_is_single_fixed_lab_ref(self):
        api=FakeAPI()
        for body in [api.event['comment']['body']+'\n',api.event['comment']['body']+'; echo x',
                     f'/matlab-module cleanup {route.MODULE} main',f'/matlab-module cleanup {route.MODULE} lab/../main',
                     f'/matlab-module cleanup {route.MODULE.upper()} lab/test-1']:
            event=copy.deepcopy(api.event);event['comment']['body']=body
            with self.assertRaises(ValueError): cleanup.validate_request(event,api.context)

    def test_attestation_is_literal_true_and_strict_schema(self):
        api=FakeAPI()
        for key,value in [('private_copy_verified',False),('private_copy_verified','true'),('private_copy_verified',1),
                          ('runtime_artifact_id',True),('module_run_id',123),('source_manifest_sha256','bad'),('extra','no')]:
            event=copy.deepcopy(api.event);body=json.loads(event['issue']['body']);body[key]=value;event['issue']['body']=json.dumps(body)
            with self.assertRaises(ValueError): cleanup.validate_request(event,api.context)
        for body in ['{"schema":1,"schema":2}','{"value":NaN}']:
            event=copy.deepcopy(api.event);event['issue']['body']=body
            with self.assertRaises(ValueError): cleanup.validate_request(event,api.context)

    def test_live_approval_and_comment_issue_association(self):
        for where,key,value in [('issue','body','{}'),('issue','state','closed'),('comment','issue_url','https://api.github.com/repos/other/repo/issues/10'),('comment','body','changed')]:
            api=FakeAPI();api.event[where][key]=value
            with self.assertRaises(ValueError): cleanup.verify_owner_live(api.item,api)

    def test_run_must_have_original_infrastructure_not_module_sha(self):
        api=FakeAPI();cleanup.verify_run_artifact(api.item,api)
        for key,value in [('run_attempt',2),('conclusion','failure'),('status','in_progress'),('path','other.yml'),('event','push')]:
            changed=FakeAPI();changed.run[key]=value
            with self.assertRaises(ValueError): cleanup.verify_run_artifact(changed.item,changed)
        api=FakeAPI();api.run['head_sha']=route.MODULE
        with self.assertRaises(ValueError): cleanup.verify_bundle(api.item,api.bundle,api.run)

    def test_required_job_and_receipt_step_must_pass(self):
        api=FakeAPI();api.jobs['jobs'][1]['steps'][1]['conclusion']='skipped'
        with self.assertRaises(ValueError): cleanup.verify_run_artifact(api.item,api)
        api=FakeAPI();api.jobs['total_count']=3
        with self.assertRaises(ValueError): cleanup.verify_run_artifact(api.item,api)

    def test_artifact_identity_digest_and_expiry(self):
        for key,value in [('expired',True),('id',457),('name','unrelated'),('digest','sha256:'+'0'*64)]:
            api=FakeAPI();api.artifact[key]=value
            with self.assertRaises(ValueError): cleanup.verify_run_artifact(api.item,api)

    def test_receipt_hash_scope_and_final_outcomes(self):
        api=FakeAPI();cleanup.verify_bundle(api.item,api.bundle,api.run)
        for name in ['source-manifest.json','verified-module-receipt.json','matlab-receipt.json','admission.json']:
            changed=dict(api.bundle);changed[name]+=b' '
            with self.assertRaises(ValueError): cleanup.verify_bundle(api.item,changed,api.run)
        changed=dict(api.bundle);changed['step-outcomes.json']=gate.encoded({'matlab':'success','receipt':'failure'})
        with self.assertRaises(ValueError): cleanup.verify_bundle(api.item,changed,api.run)

    def test_original_claim_and_source_comment_bind_original_issue(self):
        api=FakeAPI();admission,manifest=cleanup.verify_bundle(api.item,api.bundle,api.run)
        cleanup.verify_original(admission,manifest,api)
        api.comments[0]['issue_url']='https://api.github.com'+cleanup.ROOT+'/issues/10'
        with self.assertRaises(ValueError): cleanup.verify_original(admission,manifest,api)
        api=FakeAPI();api.original_event['comment']['body']='changed'
        with self.assertRaises(ValueError): cleanup.verify_original(api.admission,manifest,api)

    def test_claim_consumption_survives_a_different_cleanup_issue(self):
        api=FakeAPI();api.item['cleanup_claim_comment_id']=cleanup.claim_once(api.item,api)
        cleanup.verify_claim(api.item,api)
        different=copy.deepcopy(api.item);different['cleanup_issue_number']=20;different.pop('cleanup_claim_comment_id')
        with self.assertRaises(ValueError): cleanup.claim_once(different,api)
        self.assertEqual(sum(method=='POST' for method,_,_ in api.calls),1)

    def test_uncertain_claim_has_one_post_and_no_delete(self):
        api=FakeAPI();api.fail_post=True
        with self.assertRaises(ValueError): cleanup.claim_once(api.item,api)
        self.assertEqual(sum(method=='POST' for method,_,_ in api.calls),1)
        self.assertFalse(any(method=='DELETE' for method,_,_ in api.calls))

    def test_protected_moved_and_active_workflow_block(self):
        for field,value in [('protected',True),('branch_sha','d'*40),('active_runs',[{'id':888}])]:
            api=FakeAPI();setattr(api,field,value)
            with self.assertRaises(ValueError): cleanup.verify_branch_and_quiescence(api.item,api)
            self.assertFalse(any(method=='DELETE' for method,_,_ in api.calls))

    def test_final_sha_read_is_immediately_before_single_delete(self):
        api=FakeAPI();cleanup.delete_once(api.item,api)
        self.assertEqual([(m,p) for m,p,_ in api.calls], [
            ('GET',cleanup.ROOT+'/git/ref/heads/'+route.BRANCH),
            ('DELETE',cleanup.ROOT+'/git/refs/heads/'+route.BRANCH),
            ('GET',cleanup.ROOT+'/git/ref/heads/'+route.BRANCH)])
        api=FakeAPI();api.branch_sha='d'*40
        with self.assertRaises(ValueError): cleanup.delete_once(api.item,api)
        self.assertFalse(any(method=='DELETE' for method,_,_ in api.calls))

    def test_uncertain_delete_is_never_repeated(self):
        api=FakeAPI();api.uncertain_delete=True
        with self.assertRaises(ValueError): cleanup.delete_once(api.item,api)
        self.assertEqual(sum(method=='DELETE' for method,_,_ in api.calls),1)

    def test_zip_is_bounded_data_and_not_extracted(self):
        api=FakeAPI();self.assertEqual(cleanup.read_bundle(api.zip,gate.digest(api.zip)),api.bundle)
        with self.assertRaises(ValueError): cleanup.read_bundle(api.zip,'0'*64)
        for name in ['../escape','/absolute','a/../alias','a\\file','./alias']:
            data=zip_bundle({**api.bundle,name:b'data'})
            with self.assertRaises(ValueError): cleanup.read_bundle(data,gate.digest(data))
        target=io.BytesIO()
        with ZipFile(target,'w') as z:
            symlink=ZipInfo('badlink');symlink.create_system=3;symlink.external_attr=(stat.S_IFLNK|0o777)<<16
            z.writestr(symlink,b'target')
        data=target.getvalue()
        with self.assertRaises(ValueError): cleanup.read_bundle(data,gate.digest(data))

    def test_delete_204_is_not_json_decoded(self):
        class Response:
            status=204
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,*args):raise AssertionError('Must not parse 204')
        api=cleanup.API('fake')
        with patch.object(api,'open',return_value=Response()):self.assertIsNone(api.request('DELETE',cleanup.ROOT+'/git/refs/heads/'+route.BRANCH))

    def test_workflow_permissions_pins_and_no_code_execution(self):
        raw=(ROOT/'.github/workflows/public-matlab-module-cleanup-v1.yml').read_text();workflow=yaml.load(raw,Loader=yaml.BaseLoader)
        self.assertEqual(workflow['on'],{'issue_comment':{'types':['created']}})
        self.assertNotIn('secrets.',raw);self.assertNotIn('matlab-actions/',raw)
        jobs=workflow['jobs'];self.assertEqual(jobs['delete-archived-branch']['permissions'],{'contents':'write','actions':'read','issues':'read'})
        self.assertEqual(jobs['admit-cleanup']['permissions'],{'contents':'read','actions':'read','issues':'write'})
        for job in jobs.values():
            self.assertIn('github.run_attempt == 1',job['if']);self.assertNotIn('runner.',str(job.get('env',{})))
            self.assertEqual(job['steps'][0]['with']['ref'],'${{ github.sha }}')
            self.assertEqual(job['steps'][0]['with']['persist-credentials'],'false')
            self.assertEqual(job['steps'][-1]['if'],'always()')
            for step in job['steps']:
                if 'uses' in step:self.assertRegex(step['uses'],r'^[\w-]+/[\w-]+@[0-9a-f]{40}$')
                if 'run' in step:self.assertNotIn('${{',step['run'])
        source=(ROOT/'ci/module_lab/module_cleanup.py').read_text()
        self.assertNotIn('extractall',source);self.assertNotIn('subprocess',source)
        self.assertEqual(source.count("api.request('DELETE'"),1)


if __name__=='__main__':unittest.main(verbosity=2)
