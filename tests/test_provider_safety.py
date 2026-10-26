"""Conservative provider evidence and malformed-response regressions."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock

from agenttxn import ActionSpec, EffectClass, Engine, Policy, ReconcileOutcome, TransactionStatus
from agenttxn.adapters import DemoAdapter, GitHubIssuesAdapter, AdapterContext


class ProviderSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.world = self.root / 'provider.json'
        self.adapter = DemoAdapter(self.world)
        self.engine = Engine.open(self.root / 'state.db', policy=Policy(require_approval_for_reversible=False),
                                  adapters=[self.adapter])

    def transaction(self):
        tx = self.engine.create(intent='provider safety', idempotency_key='tx')
        tx.add(ActionSpec('demo.create', {'name': 'x'}, EffectClass.REVERSIBLE, 'action'))
        return tx

    def test_corrupt_provider_file_cannot_prove_nonexecution(self):
        tx = self.transaction()
        self.world.write_text('not JSON', encoding='utf-8')
        with self.assertRaises((ValueError, OSError)):
            DemoAdapter(self.world)

    def test_reconcile_reads_current_provider_state(self):
        tx = self.transaction()
        step = tx.steps[0]
        other = DemoAdapter(self.world)
        other.execute(AdapterContext(tx.id, step.id, 'external'), step.action, {})
        outcome, result = self.adapter.reconcile(AdapterContext(tx.id, step.id, 'query'), step.action, 'forward', None, {})
        self.assertEqual(outcome, ReconcileOutcome.SUCCEEDED)
        self.assertEqual(result['name'], 'x')

    def test_scalar_execution_result_is_unknown(self):
        tx = self.transaction()
        tx.prepare()
        self.adapter.execute = Mock(return_value=['not an object'])
        self.assertEqual(tx.commit().status, TransactionStatus.NEEDS_RECONCILIATION)

    def test_scalar_compensation_result_is_unknown(self):
        tx = self.transaction()
        tx.add(ActionSpec('demo.fail', {}, EffectClass.READ_ONLY, 'fail'))
        tx.prepare()
        self.adapter.compensate = Mock(return_value=True)
        self.assertEqual(tx.commit().status, TransactionStatus.NEEDS_RECONCILIATION)

    def test_irreversible_demo_action_cannot_compensate(self):
        action = ActionSpec('demo.create', {'name': 'x'}, EffectClass.IRREVERSIBLE, 'irreversible')
        self.assertFalse(self.adapter.can_compensate(action))

    def test_missing_labels_do_not_prove_write_never_happened(self):
        adapter = GitHubIssuesAdapter('fake')
        adapter._request = Mock(return_value=[])
        action = ActionSpec('github.issue.add_labels', {'repository': 'owner/repo', 'issue_number': 1,
                            'labels': ['new']}, EffectClass.REVERSIBLE, 'labels')
        outcome, _ = adapter.reconcile(AdapterContext('tx', 'step', 'query'), action, 'forward', None,
                                        {'previous_labels': []})
        self.assertEqual(outcome, ReconcileOutcome.UNKNOWN)

    def test_reopened_issue_does_not_prove_compensation_never_happened(self):
        adapter = GitHubIssuesAdapter('fake')
        adapter._request = Mock(return_value={'state': 'open'})
        action = ActionSpec('github.issue.create', {'repository': 'owner/repo', 'title': 'x'},
                            EffectClass.REVERSIBLE, 'issue')
        outcome, _ = adapter.reconcile(AdapterContext('tx', 'step', 'query'), action, 'compensation',
                                        {'issue_number': 1}, {})
        self.assertEqual(outcome, ReconcileOutcome.UNKNOWN)

    def test_step_status_tampering_is_detected(self):
        tx = self.transaction()
        tx.prepare()
        tx.commit()
        with closing(sqlite3.connect(self.root / 'state.db')) as connection, connection:
            connection.execute("UPDATE steps SET status='EXECUTING'")
        self.assertFalse(tx.verify().valid)

    def test_attempt_status_tampering_is_detected(self):
        tx = self.transaction()
        tx.prepare()
        tx.commit()
        with closing(sqlite3.connect(self.root / 'state.db')) as connection, connection:
            connection.execute("UPDATE attempts SET status='FAILED'")
        self.assertFalse(tx.verify().valid)

    def test_provider_file_must_be_an_object(self):
        self.world.write_text(json.dumps([]), encoding='utf-8')
        with self.assertRaises(ValueError):
            DemoAdapter(self.world)

    def test_recovered_preparation_verifies_latest_preflight_digest(self):
        tx = self.transaction()
        self.engine._transition(tx.id, TransactionStatus.PREPARING)
        self.engine.store.update_step(tx.steps[0].id, preflight={'exists': False, 'revision': 1},
                                      event_type='STEP_PREFLIGHTED')
        self.adapter.preflight = Mock(return_value={'exists': False, 'revision': 2})
        tx.resume()
        self.assertTrue(tx.verify().valid, tx.verify().messages)


if __name__ == '__main__':
    unittest.main()
