"""Offline tests of the owner gate and one-use issue claim. No network calls."""
import copy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('authorize_smoke', ROOT/'ci/authorization/authorize_smoke.py')
auth = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(auth)
SHA = 'a'*40


def fixture(mode='issue_comment'):
    context = {'event':mode,'repository':auth.REPOSITORY,'actor':auth.OWNER,
               'triggering_actor':auth.OWNER,'attempt':'1','ref':auth.REF,'sha':SHA,
               'run_id':'123','server_url':'https://github.com','api_url':'https://api.github.com'}
    event = {'repository':{'full_name':auth.REPOSITORY,'private':False,'default_branch':'main',
                           'owner':{'login':auth.OWNER}},'sender':{'login':auth.OWNER},
             'inputs':{'confirmation':'RUN_PUBLIC_MATLAB_CI_SMOKE','expected_commit':SHA},
             'action':'created','issue':{'number':4,'state':'open','user':{'login':auth.OWNER},
                                        'title':'[MATLAB_SMOKE] Generic example smoke'},
             'comment':{'id':9,'user':{'login':auth.OWNER},'body':'/matlab-smoke run '+SHA}}
    return event, context


class FakeAPI:
    def __init__(self, comments=None, fail_post=False, endless=False):
        self.comments = comments or []
        self.fail_post = fail_post
        self.endless = endless
        self.calls = []

    def request(self, method, path, payload=None):
        self.calls.append((method,path,payload))
        if method == 'POST':
            if self.fail_post:
                raise auth.AuthorizationError('Uncertain POST; no retry.')
            return {'id':100}
        if path == '/repos/' + auth.REPOSITORY:
            return fixture()[0]['repository']
        if '/issues/comments/' in path:
            return fixture()[0]['comment']
        if '/comments?' in path:
            return [{}]*100 if self.endless else self.comments
        return fixture()[0]['issue']


class AuthorizationTests(unittest.TestCase):
    def test_exact_owner_command_and_manual_dispatch(self):
        for mode in ['workflow_dispatch','issue_comment']:
            event,context=fixture(mode)
            result=auth.authorize(event,context)
            self.assertEqual(result['sha'],SHA)
            self.assertEqual('issue_number' in result,mode=='issue_comment')

    def test_owner_branch_attempt_and_endpoint_checks(self):
        mutations={'actor':'another-person','triggering_actor':'another-person','attempt':'2',
                   'repository':'another/repository','ref':'refs/heads/other','sha':'bad',
                   'server_url':'https://example.com','api_url':'https://example.com'}
        for key,value in mutations.items():
            with self.subTest(key=key):
                event,context=fixture();context[key]=value
                with self.assertRaises(auth.AuthorizationError): auth.authorize(event,context)

    def test_reject_event_and_identity_changes(self):
        changes=[lambda e:e['repository'].update(private=True),
                 lambda e:e['repository'].update(default_branch='other'),
                 lambda e:e['sender'].update(login='another-person'),
                 lambda e:e['comment']['user'].update(login='another-person'),
                 lambda e:e['issue']['user'].update(login='another-person'),
                 lambda e:e['issue'].update(state='closed'),
                 lambda e:e['issue'].update(pull_request={}),
                 lambda e:e['issue'].update(title='Ordinary issue'),
                 lambda e:e.update(action='edited')]
        for mutate in changes:
            event,context=fixture();mutate(event)
            with self.assertRaises(auth.AuthorizationError): auth.authorize(event,context)

    def test_exact_command_no_whitespace_options_or_code(self):
        for body in ['/matlab-smoke run '+'b'*40,'/matlab-smoke run '+SHA+'\n',
                     '/matlab-smoke run '+SHA+' --retry','/matlab-smoke run '+SHA+'; echo bad',
                     '/matlab-smoke run '+'A'*40,' /matlab-smoke run '+SHA,'/matlab-smoke run latest']:
            with self.subTest(body=body):
                event,context=fixture();event['comment']['body']=body
                with self.assertRaises(auth.AuthorizationError): auth.authorize(event,context)

    def test_manual_confirmation_and_sha_are_required(self):
        for field,value in [('confirmation','yes'),('expected_commit','b'*40)]:
            event,context=fixture('workflow_dispatch');event['inputs'][field]=value
            with self.assertRaises(auth.AuthorizationError): auth.authorize(event,context)

    def test_live_repository_change_is_rejected(self):
        api=FakeAPI()
        auth.verify_current_repository(api)
        class ChangedAPI(FakeAPI):
            def request(self,method,path,payload=None):
                data=super().request(method,path,payload)
                if path=='/repos/'+auth.REPOSITORY: data['private']=True
                return data
        with self.assertRaises(auth.AuthorizationError): auth.verify_current_repository(ChangedAPI())

    def test_changed_source_command_is_rejected_before_claim(self):
        class ChangedCommandAPI(FakeAPI):
            def request(self,method,path,payload=None):
                data=super().request(method,path,payload)
                if '/issues/comments/' in path: data['body']='cancelled'
                return data
        api=ChangedCommandAPI()
        with self.assertRaises(auth.AuthorizationError): auth.claim_issue(auth.authorize(*fixture()),api)
        self.assertFalse(any(call[0]=='POST' for call in api.calls))

    def test_one_claim_prevents_a_second_execution(self):
        authorization=auth.authorize(*fixture())
        api=FakeAPI();claim=auth.claim_issue(authorization,api)
        self.assertEqual(claim['claim_comment_id'],100)
        post=[call for call in api.calls if call[0]=='POST']
        self.assertEqual(len(post),1)
        self.assertIn(auth.CLAIM_MARKER,post[0][2]['body'])
        comments=[{'user':{'login':'github-actions[bot]','type':'Bot'},'body':auth.CLAIM_MARKER}]
        api=FakeAPI(comments)
        with self.assertRaises(auth.AuthorizationError): auth.claim_issue(authorization,api)
        self.assertFalse(any(call[0]=='POST' for call in api.calls))

    def test_human_cannot_forge_a_bot_claim(self):
        comments=[{'user':{'login':auth.OWNER,'type':'User'},'body':auth.CLAIM_MARKER}]
        api=FakeAPI(comments)
        self.assertEqual(auth.claim_issue(auth.authorize(*fixture()),api)['claim_comment_id'],100)

    def test_uncertain_post_never_retries(self):
        api=FakeAPI(fail_post=True)
        with self.assertRaises(auth.AuthorizationError): auth.claim_issue(auth.authorize(*fixture()),api)
        self.assertEqual(sum(call[0]=='POST' for call in api.calls),1)

    def test_bounded_history_never_claims(self):
        api=FakeAPI(endless=True)
        with self.assertRaises(auth.AuthorizationError): auth.claim_issue(auth.authorize(*fixture()),api)
        self.assertEqual(sum('/comments?' in call[1] for call in api.calls),10)
        self.assertFalse(any(call[0]=='POST' for call in api.calls))


if __name__=='__main__': unittest.main(verbosity=2)
