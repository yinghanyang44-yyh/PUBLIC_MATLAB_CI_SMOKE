"""Offline authorization, Git snapshot, oracle and exact-receipt regressions."""
import base64
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
import yaml

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'ci/module_lab'))
import module_gate as gate
import module_contract as contract
MAIN='a'*40
MODULE='b'*40
BRANCH='lab/generic-test-1'


def source_files():
    c={'schema':'cpp-module-v1','module_id':'test-module','version':'1.0.0',
       'input_width':1,'state_width':1,'output_width':1,'parameters':[], 'initial_state':[0],
       'sample_time':1,'sample_count':2,'input_units':['1'],'state_units':['1'],'output_units':['1'],
       'reset_input_index':None,'reset_timing':'none','output_timing':'output_and_next_from_pre_state','event_flag_bits':{}}
    files={name:b'Review-only generic fixture\n' for name in contract.FILES}
    files['contract/contract.json']=gate.encoded(c)
    files['include/module.h']=contract.HEADER.encode()
    files['src/module.cpp']=b'#include "module.h"\nextern "C" void module_step_v1(const double*, const double*, const double*, double*, double*, std::uint32_t*) noexcept {}\n'
    files['sfunction/sfun_module.cpp']=b'#define S_FUNCTION_NAME sfun_module\n#define S_FUNCTION_LEVEL 2\n#include "simstruc.h"\n#include "module.h"\n// module_step_v1 fixed ABI\n'
    files['tests/test_vectors.csv']=b'sample,u0\n0,1\n1,2\n'
    files['expected_outputs.csv']=b'sample,y0,x_next0,event_flags\n0,1,1,0\n1,3,3,0\n'
    return files


def fixture(files=None):
    files=files or source_files()
    body=gate.encoded({'schema':'matlab-module-request-v1','frozen_inputs':{p:contract.digest(files[p]) for p in contract.ORACLE_FILES}}).decode()
    event={'repository':{'full_name':gate.REPOSITORY,'private':False,'default_branch':'main','owner':{'login':gate.OWNER}},
           'sender':{'login':gate.OWNER},'action':'created',
           'issue':{'number':9,'state':'open','title':'[MATLAB_MODULE] Generic route','user':{'login':gate.OWNER},'body':body},
           'comment':{'id':40,'user':{'login':gate.OWNER},'body':f'/matlab-module run {MODULE} {BRANCH}'}}
    context={'repository':gate.REPOSITORY,'actor':gate.OWNER,'triggering_actor':gate.OWNER,'attempt':'1',
             'event':'issue_comment','ref':'refs/heads/main','sha':MAIN,'server_url':'https://github.com',
             'api_url':'https://api.github.com','run_id':'123'}
    return event,context


class FakeAPI:
    def __init__(self,files=None):
        self.files=files or source_files();self.event,self.context=fixture(self.files)
        self.calls=[];self.comments=[];self.fail_post=False;self.branch_sha=MODULE;self.parents=[MAIN]
        self.blobs={}
        self.base_entries={'README.md':self.entry('README.md',b'Trusted main readme\n'),
                           '.github/workflows/trusted.yml':self.entry('.github/workflows/trusted.yml',b'Trusted workflow\n')}
        self.module_entries={**self.base_entries,**{p:self.entry(p,d) for p,d in self.files.items()}}
    def entry(self,path,data):
        sha=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
        self.blobs[sha]=data
        return {'path':path,'type':'blob','mode':'100644','size':len(data),'sha':sha}
    def request(self,method,path,payload=None):
        self.calls.append((method,path,payload))
        if method=='POST':
            if self.fail_post:raise ValueError('Uncertain POST; no retry')
            claim={'id':99,'body':payload['body'],'user':{'login':'github-actions[bot]','type':'Bot'}}
            self.comments.append(claim);return claim
        if path==f'/repos/{gate.REPOSITORY}':return self.event['repository']
        if '/git/ref/heads/' in path:return {'ref':'refs/heads/'+BRANCH,'object':{'type':'commit','sha':self.branch_sha}}
        if '/git/commits/' in path:
            sha=path.rsplit('/',1)[1]
            return {'sha':sha,'parents':[{'sha':s} for s in self.parents] if sha==MODULE else [],'tree':{'sha':'module-tree' if sha==MODULE else 'base-tree'}}
        if '/git/trees/' in path:return {'truncated':False,'tree':list((self.module_entries if 'module-tree' in path else self.base_entries).values())}
        if '/git/blobs/' in path:
            data=self.blobs[path.rsplit('/',1)[1]]
            return {'encoding':'base64','content':base64.b64encode(data).decode()}
        if '/issues/comments/' in path:return self.event['comment']
        if '/comments?' in path:return self.comments
        if '/issues/' in path:return self.event['issue']
        raise AssertionError(path)


class ModuleRouteTests(unittest.TestCase):
    def test_exact_owner_issue_and_body_digest(self):
        event,context=fixture();request=gate.validate_request(event,context)
        self.assertEqual(request['module_sha'],MODULE)
        self.assertNotEqual(request['module_sha'],request['infrastructure_sha'])
        self.assertEqual(request['issue_body_sha256'],contract.digest(event['issue']['body'].encode()))
        for key,value in [('actor','other'),('triggering_actor','other'),('attempt','2'),('sha','bad'),('ref','refs/heads/other')]:
            changed=dict(context);changed[key]=value
            with self.assertRaises(ValueError):gate.validate_request(event,changed)
    def test_reject_command_injection_and_wrong_scope(self):
        event,context=fixture()
        for body in [event['comment']['body']+'\n',event['comment']['body']+';run anything',
                     f'/matlab-module run {MODULE} main',f'/matlab-module run {MODULE} lab/../main']:
            changed=copy.deepcopy(event);changed['comment']['body']=body
            with self.assertRaises(ValueError):gate.validate_request(changed,context)
        changed=copy.deepcopy(event);changed['issue']['pull_request']={}
        with self.assertRaises(ValueError):gate.validate_request(changed,context)
    def test_strict_json_duplicate_and_nonfinite(self):
        for text in ['{"schema":1,"schema":2}','{"value":NaN}','{"value":Infinity}']:
            with self.assertRaises(ValueError):contract.strict_json(text)
    def test_csv_numeric_grammar_matches_both_replay_routes(self):
        for value in ['1_000',' 1','1 ','0x1','١','NaN','Inf']:
            data=('sample,u0\n0,'+value+'\n').encode()
            with self.assertRaises(ValueError):contract.parse_csv(data,['sample','u0'],1)
        self.assertEqual(contract.parse_csv(b'"sample","u0"\n0,"1.25e+1"\n',['sample','u0'],1),[[12.5]])

    def test_frozen_oracle_cannot_be_replaced(self):
        files=source_files();event,context=fixture(files);request=gate.validate_request(event,context)
        files['expected_outputs.csv']=files['expected_outputs.csv'].replace(b'1,3,3,0',b'1,4,4,0')
        with self.assertRaises(ValueError):contract.validate_package_bytes(files,request['inputs_sha256'])
    def test_task_source_is_from_candidate_not_main(self):
        api=FakeAPI();request=gate.validate_request(api.event,api.context)
        gate.verify_live(request,api);files,manifest=gate.fetch_candidate(request,api)
        self.assertEqual(files,api.files)
        self.assertEqual(manifest['module_sha'],MODULE)
        self.assertIn(('GET',f'/repos/{gate.REPOSITORY}/git/commits/{MODULE}',None),api.calls)
        self.assertEqual(set(f['path'] for f in manifest['files']),contract.FILES)
        self.assertNotIn('.github/workflows/trusted.yml',files)
    def test_task_must_be_single_child_of_frozen_main(self):
        api=FakeAPI();api.parents=['c'*40]
        with self.assertRaises(ValueError):gate.fetch_candidate(gate.validate_request(api.event,api.context),api)
    def test_task_cannot_change_workflow_or_other_paths(self):
        api=FakeAPI();api.module_entries['.github/workflows/trusted.yml']=api.entry('.github/workflows/trusted.yml',b'Changed workflow\n')
        with self.assertRaises(ValueError):gate.fetch_candidate(gate.validate_request(api.event,api.context),api)
    def test_symlink_executable_and_submodule_rejected(self):
        for mode,kind in [('120000','blob'),('100755','blob'),('160000','commit')]:
            api=FakeAPI();api.module_entries['src/module.cpp'].update(mode=mode,type=kind)
            with self.assertRaises(ValueError):gate.fetch_candidate(gate.validate_request(api.event,api.context),api)
    def test_binary_and_unapproved_include_rejected(self):
        for suffix in [b'\0',b'\n#include "../../outside.h"\n']:
            files=source_files();event,context=fixture(files);files['src/module.cpp']+=suffix
            with self.assertRaises(ValueError):contract.validate_package_bytes(files,gate.validate_request(event,context)['inputs_sha256'])
    def test_live_branch_and_owner_body_changes_rejected(self):
        api=FakeAPI();request=gate.validate_request(api.event,api.context);api.branch_sha='c'*40
        with self.assertRaises(ValueError):gate.verify_live(request,api)
        api=FakeAPI();request=gate.validate_request(api.event,api.context);api.event['issue']['body']+=' '
        with self.assertRaises(ValueError):gate.verify_live(request,api)
    def test_one_claim_and_uncertain_post_never_retried(self):
        api=FakeAPI();request=gate.validate_request(api.event,api.context);request['source_manifest_sha256']='d'*64
        self.assertEqual(gate.claim_once(request,api),99)
        with self.assertRaises(ValueError):gate.claim_once(request,api)
        self.assertEqual(sum(m=='POST' for m,p,d in api.calls),1)
        api=FakeAPI();api.fail_post=True
        with self.assertRaises(ValueError):gate.claim_once(request,api)
        self.assertEqual(sum(m=='POST' for m,p,d in api.calls),1)
    def test_materialized_source_hashes_and_module_identity(self):
        api=FakeAPI();request=gate.validate_request(api.event,api.context);files,manifest=gate.fetch_candidate(request,api)
        raw=gate.encoded(manifest);request['source_manifest_sha256']=contract.digest(raw)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'source-manifest.json').write_bytes(raw)
            for name,data in files.items():
                p=root/'module'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
            self.assertEqual(gate.verify_snapshot(request,root)['module_sha'],MODULE)
            (root/'module/src/module.cpp').write_bytes(b'changed')
            with self.assertRaises(ValueError):gate.verify_snapshot(request,root)
    def test_runtime_receipt_must_bind_actual_module_sha(self):
        request=gate.validate_request(*fixture());request['claim_comment_id']=99;request['source_manifest_sha256']='d'*64
        raw=gate.encoded(request);manifest=b'{}\n'
        passes=['MATLAB_STARTUP','MODULE_RUNTIME_INPUTS','MATLAB_RELEASE','SIMULINK_AVAILABILITY','MEX_COMPILER',
                'DIRECT_CORE_MEX_BUILD','DIRECT_CORE_MEX','DIRECT_DETERMINISTIC_REPLAY','DIRECT_ORACLE_EQUIVALENCE',
                'SFUNCTION_BUILD','SFUNCTION_LOAD','SIMULINK_HARNESS','CPP_SFUNCTION_EQUIVALENCE','MODULE_RUNTIME_DIAGNOSTICS','PUBLIC_MODULE_RUNTIME']
        untouched=['CLAUDE_GENERATION','MODEL_REQUESTED','MODEL_ACTUAL','CPP_CORE_BUILD','CPP_UNIT_TEST','DETERMINISTIC_REPLAY',
                   'PRIVATE_PROMOTION','PRIVATE_PR','PUBLIC_BRANCH_CLEANUP','MAIN_CODEX_INTEGRATION','SYSTEM_VALIDATION','FORMAL_PROJECT_PROMOTION']
        ledger=[{'name':n,'status':'PASS'} for n in passes]+[{'name':n,'status':'NOT_RUN'} for n in untouched]+[{'name':'PUBLIC_MATLAB_MODULE_ACCEPTANCE','status':'WAITING'}]
        receipt={'schema':'public-matlab-module-runtime-receipt-v1','status':'PASS','module_sha':MODULE,'infrastructure_sha':MAIN,
                 'admission_json_verbatim':raw.decode(),'source_manifest_json_verbatim':manifest.decode(),
                 'candidate_wrapper_execution':'SIMULATION_PASSED','candidate_matlab_helpers_executed':False,'candidate_test_sources_invoked_by_runner':False,'ledger':ledger}
        gate.validate_runtime_receipt(receipt,request,raw,manifest)
        receipt['module_sha']=MAIN
        with self.assertRaises(ValueError):gate.validate_runtime_receipt(receipt,request,raw,manifest)
    def test_runtime_receipt_consumer_matches_emitted_matlab_schema(self):
        consumer=(ROOT/'ci/module_lab/module_gate.py').read_text()
        producer=(ROOT/'ci/module_lab/run_module_lab.m').read_text()
        schema=producer.split('function receipt = initialReceipt',1)[1].split('function ',1)[0]
        declared=set(re.findall(r"'([a-z][a-z0-9_]*)'\s*,",schema))
        consumed=set(re.findall(r"runtime\.get\('([^']+)'",consumer))
        self.assertLessEqual(consumed,declared)
        self.assertIn("receipt.candidate_wrapper_execution = 'SIMULATION_PASSED';",producer)
        self.assertIn("'candidate_test_sources_invoked_by_runner',false",schema)

    def test_workflow_trusted_checkout_read_only_runtime_and_no_secrets(self):
        text=(ROOT/'.github/workflows/public-matlab-module-v1.yml').read_text()
        workflow=yaml.load(text,Loader=yaml.BaseLoader)
        self.assertEqual(workflow['on'],{'issue_comment':{'types':['created']}})
        self.assertNotIn('secrets.',text)
        run=workflow['jobs']['module-matlab'];self.assertEqual(run['permissions'],{'contents':'read'})
        self.assertEqual(run['runs-on'],'windows-2022');self.assertIn('github.run_attempt == 1',run['if'])
        self.assertEqual(run['steps'][0]['with']['ref'],'${{ github.sha }}')
        self.assertEqual(run['steps'][0]['with']['path'],'trusted')
        self.assertEqual(run['steps'][-1]['if'],'always()')
        for job in workflow['jobs'].values():
            self.assertNotIn('runner.',str(job.get('env',{})))
            for step in job['steps']:
                if 'uses' in step:self.assertRegex(step['uses'],r'^[\w-]+/[\w-]+@[0-9a-f]{40}$')
        matlab=next(s for s in run['steps'] if s.get('id')=='matlab')
        self.assertEqual(run['steps'][run['steps'].index(matlab)-1]['id'],'execution_identity')
        self.assertNotIn('GH_TOKEN',matlab['env'])
        self.assertNotIn('${{',matlab['with']['command'])
    def test_terminal_observation_repeats_last_input_and_only_checks_prestate(self):
        source=(ROOT/'ci/module_lab/run_module_lab.m').read_text()
        self.assertIn("assignin(workspace,'moduleInput',[time,[inputs;inputs(end,:)]]);",source)
        unpack=re.split(r'\nfunction\b',source.split('function [times,values,terminalPrestate] = unpackSimulation',1)[1],maxsplit=1)[0]
        self.assertIn('values = allRows(1:end-1,:);',unpack)
        self.assertIn('validateTrace(values,c.sample_count,c);',unpack)
        self.assertIn('terminalPrestate = allRows(end,c.output_width+(1:c.state_width));',unpack)
        self.assertNotIn('validateTrace(allRows',unpack)
        self.assertIn("'terminal_output_assessed',false",source)
        self.assertIn("'terminal_event_assessed',false",source)

    def test_trusted_runner_uses_actual_wrapper_and_root_oracle(self):
        source=(ROOT/'ci/module_lab/run_module_lab.m').read_text()
        self.assertIn("fullfile(sourceDir,'sfunction','sfun_module.cpp')",source)
        self.assertIn("fullfile(sourceDir,'expected_outputs.csv')",source)
        self.assertIn("'SFUNCTION_LOAD'",source)
        self.assertIn("'candidate_matlab_helpers_executed',false",source)
        self.assertNotRegex(source,r'struct\(\s*\[\s*\]\s*\)')


if __name__=='__main__':unittest.main(verbosity=2)
