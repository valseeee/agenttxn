"""Offline regression for real GitHub's observed read-after-create visibility delay."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from agenttxn import TransactionStatus
from scripts.github_integration import reconcile_with_visibility_delay


class LiveValidationTests(unittest.TestCase):
    @patch('scripts.github_integration.time.sleep')
    def test_visibility_delay_only_repeats_reconciliation(self, sleep):
        unknown = SimpleNamespace(status=TransactionStatus.NEEDS_RECONCILIATION)
        success = SimpleNamespace(status=TransactionStatus.COMMITTED)
        tx = Mock()
        tx.reconcile.side_effect = [unknown, unknown, success]
        self.assertIs(reconcile_with_visibility_delay(tx), success)
        self.assertEqual(tx.reconcile.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        tx.commit.assert_not_called()

    @patch('scripts.github_integration.time.sleep')
    def test_exhausted_visibility_wait_keeps_unknown_without_replay(self, sleep):
        unknown = SimpleNamespace(status=TransactionStatus.NEEDS_RECONCILIATION)
        tx = Mock()
        tx.reconcile.return_value = unknown
        self.assertIs(reconcile_with_visibility_delay(tx), unknown)
        self.assertEqual(tx.reconcile.call_count, 6)
        self.assertEqual(sleep.call_count, 5)
        tx.commit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
