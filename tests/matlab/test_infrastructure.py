"""Public generic example policy checks. No MATLAB or network execution."""
from pathlib import Path
import shutil
import re
import subprocess
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]

class PublicSmokeTests(unittest.TestCase):
    def test_public_deliberate_workflow(self):
        text = (ROOT/'.github/workflows/public-matlab-ci-smoke.yml').read_text()
        workflow = yaml.load(text, Loader=yaml.BaseLoader)
        self.assertEqual(set(workflow['on']), {'workflow_dispatch','issue_comment'})
        self.assertEqual(workflow['on']['issue_comment']['types'], ['created'])
        self.assertEqual(workflow['permissions'], {'contents':'read'})
        self.assertEqual(workflow['concurrency']['cancel-in-progress'],'false')
        self.assertIn('public-matlab-smoke-rejected-{0}',workflow['concurrency']['group'])
        self.assertIn('github.actor == github.repository_owner',workflow['concurrency']['group'])
        authorization=workflow['jobs']['authorize']
        self.assertEqual(authorization['permissions'],{'contents':'read','issues':'write'})
        self.assertEqual(authorization['outputs']['sha'],'${{ steps.authorize.outputs.sha }}')
        self.assertEqual(authorization['steps'][0]['with']['ref'],'${{ github.sha }}')
        self.assertIn('github.run_attempt == 1',authorization['if'])
        job = workflow['jobs']['matlab-gates']
        self.assertEqual(job['needs'],'authorize')
        self.assertIn('github.run_attempt == 1',job['if'])
        self.assertEqual(job['permissions'],{'contents':'read'})
        live=next(s for s in job['steps'] if '--verify-execution' in s.get('run',''))
        self.assertLess(job['steps'].index(live),next(i for i,s in enumerate(job['steps']) if s.get('id')=='setup'))
        self.assertEqual(job['runs-on'], 'windows-2022')
        self.assertLessEqual(int(job['timeout-minutes']),60)
        checkout=next(s for s in job['steps'] if s.get('uses','').startswith('actions/checkout@'))
        self.assertEqual(checkout['with']['ref'],'${{ needs.authorize.outputs.sha }}')
        self.assertNotIn('secrets.',text)
        self.assertNotIn('MLM_LICENSE_TOKEN',text)
        for selected_job in workflow['jobs'].values():
            for step in selected_job['steps']:
                if 'uses' in step:
                    self.assertRegex(step['uses'],r'^[\w-]+/[\w-]+@[0-9a-f]{40}$')
        setup=next(s for s in job['steps'] if s.get('id')=='setup')
        self.assertEqual(setup['with'],{'release':'R2025a','products':'Simulink','cache':'false'})
        self.assertEqual(job['steps'][-1]['if'],'always()')
        self.assertEqual(job['steps'][-1]['with']['retention-days'],'30')

    def test_source_only(self):
        for pattern in ['*.slx','*.mdl','*.lic','*.mexw64','license.dat']:
            self.assertEqual(list(ROOT.rglob(pattern)),[])
        source=(ROOT/'ci/matlab/run_public_smoke.m').read_text()
        self.assertIn('final_committed_state_error',source)
        self.assertLess(source.index("'installed-compilers-'"),source.index('assert(numel(matches)==1'))
        self.assertIn("'reset',double(mod(n,17)==0 & n>0)",source)
        self.assertIn("'rmse'",source)
        self.assertIn("'event_mismatches'",source)

    def test_explicit_windows_2022_retains_vs2022_guard(self):
        preflight = (ROOT/'ci/matlab/runner_preflight.ps1').read_text()
        self.assertIn("if ($os.Caption -notmatch 'Windows Server 2022')", preflight)
        self.assertIn("-version '[17.0,18.0)'", preflight)
        self.assertIn('Microsoft.VisualStudio.Component.VC.Tools.x86.x64', preflight)
        self.assertIn("if ($compilers.Count -eq 0) { throw", preflight)
        self.assertNotIn("-notmatch 'Windows Server 2025'", preflight)

    def test_github_context_availability(self):
        # GitHub rejects runner context in job.env before allocating any jobs.
        # It is supported in steps.env; YAML parsing alone does not catch this.
        workflow = yaml.load((ROOT/'.github/workflows/public-matlab-ci-smoke.yml').read_text(), Loader=yaml.BaseLoader)
        allowed = {'github','needs','strategy','matrix','vars','secrets','inputs'}
        for name, job in workflow['jobs'].items():
            for value in job.get('env', {}).values():
                for expression in re.findall(r'\$\{\{(.*?)\}\}', value, re.S):
                    contexts = set(re.findall(r'\b([a-zA-Z_]\w*)\.', expression))
                    self.assertLessEqual(contexts, allowed, name)
            for step in job['steps']:
                if 'run' in step or step.get('id') == 'matlab':
                    self.assertEqual(step.get('env', {}).get('MATLAB_CI_ARTIFACT_DIR'),
                                     '${{ runner.temp }}/public-matlab-ci-smoke', step['name'])

    @unittest.skipUnless(shutil.which('g++'),'g++ unavailable')
    def test_portable_core(self):
        with tempfile.TemporaryDirectory(prefix='public-smoke-core-') as temp:
            executable=Path(temp)/'core_test'
            subprocess.run(['g++','-std=c++11','-Wall','-Wextra','-Werror','-pedantic',
                '-I',str(ROOT/'ci/matlab/core'),str(ROOT/'ci/matlab/core/accumulator.cpp'),
                str(ROOT/'tests/matlab/core_test.cpp'),'-o',str(executable)],check=True,capture_output=True,text=True)
            result=subprocess.run([str(executable)],check=True,capture_output=True,text=True)
            self.assertIn('252 deterministic accumulator checks',result.stdout)

if __name__=='__main__': unittest.main(verbosity=2)
