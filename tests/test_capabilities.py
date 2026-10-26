from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from agenttxn import ActionSpec, EffectClass, Engine, Policy, ReconcileOutcome, TransactionStatus
from agenttxn.adapters import (DemoAdapter, GitHubIssuesAdapter, EvidenceLevel,
                               ProviderCapabilities, ReconciliationEvidence)
from agenttxn.adapters.capabilities import normalize_evidence, provider_capabilities


class CapabilitiesTests(unittest.TestCase):
    def test_legacy_adapter_defaults_to_no_native_guarantees(self):
        self.assertEqual(provider_capabilities(object(), None), ProviderCapabilities())

    def test_github_does_not_claim_native_idempotency(self):
        self.assertEqual(provider_capabilities(GitHubIssuesAdapter('fake'), None), ProviderCapabilities())

    def test_demo_declares_simulated_capabilities(self):
        self.assertEqual(provider_capabilities(DemoAdapter(), None), ProviderCapabilities(True, True))

    def test_invalid_capability_declaration_is_rejected(self):
        with self.assertRaises(ValueError):
            provider_capabilities(Mock(capabilities=Mock(return_value={})), None)

    def test_inconclusive_nonexecution_stays_unknown(self):
        outcome, _, _ = normalize_evidence(ReconciliationEvidence(ReconcileOutcome.NOT_EXECUTED))
        self.assertEqual(outcome, ReconcileOutcome.UNKNOWN)

    def test_inconclusive_success_stays_unknown(self):
        outcome, _, _ = normalize_evidence(ReconciliationEvidence(ReconcileOutcome.SUCCEEDED))
        self.assertEqual(outcome, ReconcileOutcome.UNKNOWN)

    def test_confirmed_nonexecution_and_legacy_tuples_are_supported(self):
        for response in ((ReconcileOutcome.NOT_EXECUTED, None),
                         ReconciliationEvidence(ReconcileOutcome.NOT_EXECUTED, level=EvidenceLevel.CONFIRMED)):
            self.assertEqual(normalize_evidence(response)[0], ReconcileOutcome.NOT_EXECUTED)

    def test_invalid_evidence_level_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_evidence(ReconciliationEvidence(ReconcileOutcome.NOT_EXECUTED, level='typo'))

    def test_engine_records_evidence_and_never_retries_on_capability_hint(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = DemoAdapter()
            engine = Engine.open(Path(directory)/'state.db', policy=Policy(require_approval_for_reversible=False),
                                 adapters=[adapter])
            tx = engine.create(intent='evidence', idempotency_key='tx')
            action = ActionSpec('demo.unknown', {'name': 'x'}, EffectClass.REVERSIBLE, 'action')
            tx.add(action)
            tx.prepare()
            adapter.execute = Mock(wraps=adapter.execute)
            tx.commit()
            self.assertTrue(engine.capabilities_for(action).native_idempotency)
            adapter.reconcile = Mock(return_value=ReconciliationEvidence(ReconcileOutcome.NOT_EXECUTED))
            for _ in range(2):
                self.assertEqual(tx.resume().status, TransactionStatus.NEEDS_RECONCILIATION)
            self.assertEqual(adapter.execute.call_count, 1)
            adapter.reconcile = Mock(return_value=ReconciliationEvidence(ReconcileOutcome.SUCCEEDED,
                                        adapter.effects['action'], EvidenceLevel.CONFIRMED))
            self.assertEqual(tx.reconcile().status, TransactionStatus.COMMITTED)
            self.assertEqual(adapter.execute.call_count, 1)
            evidence = [event for event in engine.store.get_events(tx.id)
                        if event.event_type == 'RECONCILIATION_EVIDENCE']
            self.assertEqual([event.payload['evidence_level'] for event in evidence],
                             ['INCONCLUSIVE', 'INCONCLUSIVE', 'CONFIRMED'])
            self.assertTrue(tx.verify().valid)


if __name__ == '__main__':
    unittest.main()
