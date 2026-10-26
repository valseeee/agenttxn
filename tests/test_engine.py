import tempfile, unittest
from pathlib import Path
from agenttxn import ActionSpec, EffectClass, Engine, Policy, TransactionStatus, StepStatus
from agenttxn.adapters import DemoAdapter
from agenttxn.errors import ApprovalRequiredError, DuplicateIdempotencyKeyError, InvalidTransitionError, PlanFrozenError

class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.adapter=DemoAdapter(self.root/"provider.json")
        self.engine=Engine.open(self.root/"state.db",policy=Policy(require_approval_for_reversible=False),adapters=[self.adapter])
    def action(self,key="action:x",kind="demo.create",effect=EffectClass.REVERSIBLE):
        return ActionSpec(kind,{"name":"x"} if kind!="demo.fail" else {},effect,key)
    def test_success_and_persistence(self):
        tx=self.engine.create(intent="create",idempotency_key="tx:success"); tx.add(self.action())
        tx.prepare(); r=tx.commit()
        self.assertEqual(r.status,TransactionStatus.COMMITTED); self.assertEqual(tx.steps[0].status,StepStatus.SUCCEEDED)
        self.assertTrue(tx.verify().valid); self.assertEqual(self.engine.load(tx.id).record.status,TransactionStatus.COMMITTED)
    def test_duplicate_transaction_key(self):
        self.engine.create(intent="one",idempotency_key="same")
        with self.assertRaises(DuplicateIdempotencyKeyError): self.engine.create(intent="two",idempotency_key="same")
    def test_duplicate_action_key(self):
        tx=self.engine.create(intent="a",idempotency_key="tx:a"); tx.add(self.action("same-action"))
        other=self.engine.create(intent="b",idempotency_key="tx:b")
        with self.assertRaises(DuplicateIdempotencyKeyError): other.add(self.action("same-action"))
    def test_plan_frozen(self):
        tx=self.engine.create(intent="frozen",idempotency_key="tx:frozen"); tx.add(self.action())
        tx.prepare()
        with self.assertRaises(PlanFrozenError): tx.add(self.action("another"))

    def test_store_rejects_invalid_step_state_jump(self):
        tx=self.engine.create(intent="step-state",idempotency_key="tx:step-state")
        step=tx.add(self.action("action:step-state"))
        with self.assertRaises(InvalidTransitionError):
            self.engine.store.update_step(step.id,status=StepStatus.COMPENSATED)
    def test_compensation_after_known_failure(self):
        tx=self.engine.create(intent="partial",idempotency_key="tx:partial")
        tx.add(self.action("first"))
        tx.add(self.action("second","demo.fail",EffectClass.READ_ONLY))
        tx.prepare(); report=tx.commit()
        self.assertEqual(report.status,TransactionStatus.COMPENSATED)
        self.assertEqual(tx.steps[0].status,StepStatus.COMPENSATED)
        self.assertNotIn("first",self.adapter.effects)
    def test_approval_required_by_default(self):
        e=Engine.open(self.root/"approval.db",policy=Policy.default_safe(),adapters=[DemoAdapter()])
        tx=e.create(intent="approval",idempotency_key="tx:approval"); tx.add(self.action("approval-action"))
        self.assertEqual(tx.prepare().status,TransactionStatus.AWAITING_APPROVAL)
        with self.assertRaises(ApprovalRequiredError): tx.commit()
        tx.approve(approved_by="tester"); self.assertEqual(tx.commit().status,TransactionStatus.COMMITTED)
if __name__=="__main__": unittest.main()
