"""Malformed provider evidence must never authorize a retry."""
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock

from agenttxn import ActionSpec, EffectClass, Engine, Policy, StepStatus, TransactionStatus
from agenttxn.adapters import DemoAdapter
from agenttxn.errors import OutcomeUnknownError
from agenttxn.models import ReconcileOutcome


class ReconciliationValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'state.db'
        self.adapter = DemoAdapter()
        self.engine = Engine.open(self.path, policy=Policy(require_approval_for_reversible=False),
                                  adapters=[self.adapter])

    def unknown_transaction(self, compensation=False):
        tx = self.engine.create(intent='lost response', idempotency_key='tx')
        tx.add(ActionSpec('demo.create', {'name': 'resource'}, EffectClass.REVERSIBLE, 'action'))
        if compensation:
            tx.add(ActionSpec('demo.fail', {}, EffectClass.READ_ONLY, 'fail'))
            original = self.adapter.compensate
            def lost_response(*args):
                original(*args)
                raise OutcomeUnknownError('private compensation response')
            self.adapter.compensate = Mock(side_effect=lost_response)
        else:
            original = self.adapter.execute
            def lost_response(*args):
                original(*args)
                raise OutcomeUnknownError('private forward response')
            self.adapter.execute = Mock(side_effect=lost_response)
        tx.prepare()
        self.assertEqual(tx.commit().status, TransactionStatus.NEEDS_RECONCILIATION)
        return tx

    def assert_rejected(self, response, compensation=False):
        tx = self.unknown_transaction(compensation)
        provider_call = self.adapter.compensate if compensation else self.adapter.execute
        self.adapter.reconcile = Mock(return_value=response)
        expected = StepStatus.COMPENSATION_UNKNOWN if compensation else StepStatus.UNKNOWN
        for _ in range(2):
            self.assertEqual(tx.resume().status, TransactionStatus.NEEDS_RECONCILIATION)
            self.assertEqual(tx.steps[0].status, expected)
        self.assertEqual(provider_call.call_count, 1)
        self.assertTrue(tx.verify().valid)
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM attempts WHERE step_id=?',
                                                (tx.steps[0].id,)).fetchone()[0],
                             2 if compensation else 1)

    def test_invalid_outcome_does_not_authorize_forward_retry(self):
        self.assert_rejected(('TYPO', None))

    def test_invalid_outcome_does_not_authorize_compensation_retry(self):
        self.assert_rejected((False, None), compensation=True)

    def test_unserializable_reconciliation_result_stays_unknown(self):
        self.assert_rejected((ReconcileOutcome.SUCCEEDED, {'bad': object()}))

    def test_nonobject_success_evidence_stays_unknown(self):
        self.assert_rejected((ReconcileOutcome.SUCCEEDED, ['invalid']))

    def test_nonfinite_reconciliation_result_stays_unknown(self):
        self.assert_rejected((ReconcileOutcome.SUCCEEDED, {'bad': float('nan')}))

    def test_malformed_response_stays_unknown(self):
        self.assert_rejected(None)


if __name__ == '__main__':
    unittest.main()
