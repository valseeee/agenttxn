import unittest
from agenttxn.errors import InvalidTransitionError
from agenttxn.models import StepStatus, TransactionStatus as S
from agenttxn.state_machine import assert_step_transition, assert_transition

class StateMachineTests(unittest.TestCase):
    def test_allowed_transaction_transition(self):
        assert_transition(S.CREATED,S.PREPARING)

    def test_invalid_transaction_transition(self):
        with self.assertRaises(InvalidTransitionError): assert_transition(S.CREATED,S.COMMITTED)

    def test_allowed_step_reconciliation_transition(self):
        assert_step_transition(StepStatus.EXECUTING,StepStatus.PENDING)

    def test_invalid_step_transition(self):
        with self.assertRaises(InvalidTransitionError): assert_step_transition(StepStatus.COMPENSATED,StepStatus.EXECUTING)

if __name__=="__main__": unittest.main()
