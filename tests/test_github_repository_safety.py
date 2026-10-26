import unittest
from unittest.mock import Mock

from agenttxn import ActionSpec, EffectClass, ReconcileOutcome
from agenttxn.adapters import GitHubIssuesAdapter
from agenttxn.errors import ActionExecutionError


class GitHubRepositorySafetyTests(unittest.TestCase):
    def setUp(self):
        self.adapter=GitHubIssuesAdapter('fake',allow_label_compensation=True)
        self.create=ActionSpec('github.issue.create',{'repository':'owner/old','title':'x'},EffectClass.REVERSIBLE,'create')
        self.labels=ActionSpec('github.issue.add_labels',{'repository':'owner/old','issue_number':7,
                              'labels':['existing-on-page-two']},EffectClass.REVERSIBLE,'labels')

    def test_reused_repository_name_cannot_receive_compensation(self):
        self.adapter._request=Mock(return_value={'id':202})
        with self.assertRaises(ActionExecutionError):
            self.adapter.compensate(None,self.create,{'issue_number':7},{'repository_id':101})
        self.assertFalse(any(call.args[0]=='PATCH' for call in self.adapter._request.call_args_list))

    def test_reconciliation_rejects_different_repository_identity(self):
        self.adapter._request=Mock(return_value={'id':202,'state':'closed'})
        outcome,_=self.adapter.reconcile(None,self.create,'compensation',{'issue_number':7},{'repository_id':101})
        self.assertEqual(outcome,ReconcileOutcome.UNKNOWN)

    def test_label_preflight_freezes_identity_and_all_pages(self):
        def request(method,path,*args,**kwargs):
            if path=='/repos/owner/old': return {'id':101}
            if '/labels' not in path: return {'state':'open'}
            if '&page=1' in path: return [{'name':f'label-{i}'} for i in range(100)]
            if '&page=2' in path: return [{'name':'existing-on-page-two'}]
            return [{'name':f'label-{i}'} for i in range(30)]
        self.adapter._request=Mock(side_effect=request)
        preflight=self.adapter.preflight(None,self.labels)
        self.assertEqual(preflight['repository_id'],101)
        self.assertIn('existing-on-page-two',preflight['previous_labels'])

    def test_existing_second_page_label_is_not_owned_by_transaction(self):
        previous=[f'label-{i}' for i in range(100)]+['existing-on-page-two']
        def request(method,path,*args,**kwargs):
            if path=='/repos/owner/old': return {'id':101}
            if method=='POST': return [{'name':name} for name in previous]
            if '&page=1' in path: return [{'name':name} for name in previous[:100]]
            if '&page=2' in path: return [{'name':name} for name in previous[100:]]
            return [{'name':name} for name in previous[:30]]
        self.adapter._request=Mock(side_effect=request)
        result=self.adapter.execute(None,self.labels,{'repository_id':101,'previous_labels':previous})
        self.assertEqual(result['added_labels'],[])

    def test_compensation_query_does_not_ignore_second_page(self):
        def request(method,path,*args,**kwargs):
            if path=='/repos/owner/old': return {'id':101}
            if '&page=1' in path: return [{'name':f'label-{i}'} for i in range(100)]
            if '&page=2' in path: return [{'name':'existing-on-page-two'}]
            return [{'name':f'label-{i}'} for i in range(30)]
        self.adapter._request=Mock(side_effect=request)
        outcome,_=self.adapter.reconcile(None,self.labels,'compensation',{'added_labels':['existing-on-page-two']},
                                        {'repository_id':101})
        self.assertEqual(outcome,ReconcileOutcome.UNKNOWN)


if __name__=='__main__': unittest.main()
