import unittest
from unittest.mock import Mock, patch
from agenttxn import ActionSpec, EffectClass
from agenttxn.adapters import DemoAdapter, GitHubIssuesAdapter
from agenttxn.errors import ActionExecutionError, OutcomeUnknownError, PreflightError

class AdapterValidationTests(unittest.TestCase):
    def setUp(self):
        self.github=GitHubIssuesAdapter("fake-token")
        self.demo=DemoAdapter()

    def test_github_repo_path_injection_rejected(self):
        action=ActionSpec("github.issue.create",{"repository":"owner/repo?state=all","title":"test"},EffectClass.REVERSIBLE,"gh:bad-repo")
        with self.assertRaises(PreflightError): self.github.validate(action)

    def test_github_write_cannot_be_read_only(self):
        action=ActionSpec("github.issue.create",{"repository":"owner/repo","title":"test"},EffectClass.READ_ONLY,"gh:read-only")
        with self.assertRaises(PreflightError): self.github.validate(action)

    def test_github_issue_number_must_be_positive_integer(self):
        action=ActionSpec("github.issue.add_labels",{"repository":"owner/repo","issue_number":True,"labels":["bug"]},EffectClass.REVERSIBLE,"gh:bool-number")
        with self.assertRaises(PreflightError): self.github.validate(action)

    def test_demo_create_cannot_be_read_only(self):
        action=ActionSpec("demo.create",{"name":"fake"},EffectClass.READ_ONLY,"demo:readonly")
        with self.assertRaises(PreflightError): self.demo.validate(action)

    def test_unreadable_github_write_response_is_unknown(self):
        response=Mock()
        response.read.return_value=b"not-json"
        response.__enter__=Mock(return_value=response)
        response.__exit__=Mock(return_value=False)
        with patch("agenttxn.adapters.github.urlopen",return_value=response):
            with self.assertRaises(OutcomeUnknownError):
                self.github._request("POST","/repos/owner/repo/issues",{"title":"x"},write=True)

    def test_unreadable_github_read_response_is_known_error(self):
        response=Mock()
        response.read.return_value=b"not-json"
        response.__enter__=Mock(return_value=response)
        response.__exit__=Mock(return_value=False)
        with patch("agenttxn.adapters.github.urlopen",return_value=response):
            with self.assertRaises(ActionExecutionError):
                self.github._request("GET","/repos/owner/repo",write=False)

    def test_issue_create_missing_result_id_is_unknown(self):
        action=ActionSpec("github.issue.create",{"repository":"owner/repo","title":"test"},EffectClass.REVERSIBLE,"gh:missing-id")
        with patch.object(self.github,"_request",side_effect=[{"id":101},{"title":"test"}]):
            with self.assertRaises(OutcomeUnknownError):
                self.github.execute(None,action,{"repository_id":101,"marker":"<!-- marker -->"})

if __name__=="__main__": unittest.main()
